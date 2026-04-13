"""SQLite storage layer for captured API events."""

from __future__ import annotations

import sqlite3
import threading
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    host TEXT,
    endpoint TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    input_cost_usd REAL,
    output_cost_usd REAL,
    total_cost_usd REAL,
    status_code INTEGER,
    latency_ms REAL,
    pid INTEGER,
    process_name TEXT,
    cmdline TEXT,
    attribution_confidence TEXT DEFAULT 'low',
    created_at REAL DEFAULT (strftime('%%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_provider ON events(provider);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
CREATE INDEX IF NOT EXISTS idx_events_process_name ON events(process_name);
CREATE INDEX IF NOT EXISTS idx_events_total_cost ON events(total_cost_usd);

CREATE TABLE IF NOT EXISTS pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_price_per_1m REAL NOT NULL,
    output_price_per_1m REAL NOT NULL,
    valid_from REAL NOT NULL,
    source TEXT DEFAULT 'builtin',
    UNIQUE(provider, model, valid_from)
);

CREATE TABLE IF NOT EXISTS dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    raw_metadata TEXT,
    error_reason TEXT,
    created_at REAL DEFAULT (strftime('%%s','now'))
);
"""


class Storage:
    """Thread-safe SQLite wrapper for event persistence."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def init(self):
        """Create tables and seed pricing data."""
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        _seed_pricing(conn)
        conn.commit()
        logger.info("Database initialised at %s", self.db_path)

    # ── events ──────────────────────────────────────────────

    def insert_event(self, **kwargs) -> int:
        keys = list(kwargs.keys())
        vals = list(kwargs.values())
        placeholders = ", ".join("?" for _ in keys)
        cols = ", ".join(keys)
        sql = f"INSERT INTO events ({cols}) VALUES ({placeholders})"
        conn = self._conn()
        cur = conn.execute(sql, vals)
        conn.commit()
        return cur.lastrowid

    def insert_dead_letter(self, raw_metadata: str, error_reason: str):
        conn = self._conn()
        conn.execute(
            "INSERT INTO dead_letter (ts, raw_metadata, error_reason) VALUES (?, ?, ?)",
            (time.time(), raw_metadata[:2000], error_reason[:500]),
        )
        conn.commit()

    # ── aggregate queries ───────────────────────────────────

    def total_cost_since(self, ts: float) -> float:
        conn = self._conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd), 0) FROM events WHERE ts >= ?", (ts,)
        ).fetchone()
        return row[0]

    def costs_by_provider(self, ts_start: float, ts_end: Optional[float] = None) -> list[dict]:
        conn = self._conn()
        if ts_end is None:
            ts_end = time.time()
        rows = conn.execute(
            """
            SELECT provider, SUM(total_cost_usd) as total,
                   COUNT(*) as req_count
            FROM events WHERE ts BETWEEN ? AND ?
            GROUP BY provider ORDER BY total DESC
            """,
            (ts_start, ts_end),
        ).fetchall()
        return [dict(r) for r in rows]

    def costs_by_model(self, ts_start: float, ts_end: Optional[float] = None, limit: int = 20) -> list[dict]:
        conn = self._conn()
        if ts_end is None:
            ts_end = time.time()
        rows = conn.execute(
            """
            SELECT model, provider, SUM(total_cost_usd) as total,
                   SUM(input_tokens) as inp, SUM(output_tokens) as out,
                   COUNT(*) as req_count
            FROM events WHERE ts BETWEEN ? AND ?
            GROUP BY model ORDER BY total DESC LIMIT ?
            """,
            (ts_start, ts_end, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def costs_by_process(self, ts_start: float, ts_end: Optional[float] = None, limit: int = 20) -> list[dict]:
        conn = self._conn()
        if ts_end is None:
            ts_end = time.time()
        rows = conn.execute(
            """
            SELECT process_name, SUM(total_cost_usd) as total,
                   COUNT(*) as req_count
            FROM events WHERE ts BETWEEN ? AND ?
            GROUP BY process_name ORDER BY total DESC LIMIT ?
            """,
            (ts_start, ts_end, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def openrouter_by_clawcode(self, ts_start: float, ts_end: Optional[float] = None) -> dict:
        """OpenRouter costs attributed to claw-code / OpenClaw processes."""
        conn = self._conn()
        if ts_end is None:
            ts_end = time.time()
        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_cost_usd), 0) as total, COUNT(*) as req_count
            FROM events
            WHERE ts BETWEEN ? AND ?
              AND provider = 'openrouter'
              AND (process_name LIKE '%claw%' OR cmdline LIKE '%claw%' OR cmdline LIKE '%opencode%')
            """,
            (ts_start, ts_end),
        ).fetchone()
        return dict(row)

    def openrouter_total(self, ts_start: float, ts_end: Optional[float] = None) -> dict:
        conn = self._conn()
        if ts_end is None:
            ts_end = time.time()
        row = conn.execute(
            """
            SELECT COALESCE(SUM(total_cost_usd), 0) as total, COUNT(*) as req_count
            FROM events WHERE ts BETWEEN ? AND ? AND provider = 'openrouter'
            """,
            (ts_start, ts_end),
        ).fetchone()
        return dict(row)

    def recent_events(self, limit: int = 30) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT ts, provider, model, total_cost_usd, status_code,
                   process_name, latency_ms
            FROM events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def session_summary(self, ts_start: float) -> dict:
        """Summary suitable for the live dashboard header."""
        now = time.time()
        conn = self._conn()
        total_row = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0), COUNT(*) FROM events WHERE ts >= ?",
            (ts_start,),
        ).fetchone()
        providers = self.costs_by_provider(ts_start)
        or_total = self.openrouter_total(ts_start)
        or_claw = self.openrouter_by_clawcode(ts_start)
        return {
            "total_cost": total_row[0],
            "total_requests": total_row[1],
            "providers": providers,
            "openrouter_total": or_total,
            "openrouter_clawcode": or_claw,
        }


# ── pricing seed data ───────────────────────────────────────
# Prices per 1M tokens in USD. Source: public provider pricing pages.
_PRICING_SEED = [
    # OpenRouter – popular models
    ("openrouter", "openai/gpt-4o", 2.50, 10.00, 1700000000, "openrouter"),
    ("openrouter", "openai/gpt-4o-mini", 0.15, 0.60, 1700000000, "openrouter"),
    ("openrouter", "openai/gpt-4-turbo", 10.00, 30.00, 1700000000, "openrouter"),
    ("openrouter", "anthropic/claude-3.5-sonnet", 3.00, 15.00, 1700000000, "openrouter"),
    ("openrouter", "anthropic/claude-3-opus", 15.00, 75.00, 1700000000, "openrouter"),
    ("openrouter", "anthropic/claude-3-haiku", 0.25, 1.25, 1700000000, "openrouter"),
    ("openrouter", "anthropic/claude-3.5-haiku", 0.80, 4.00, 1700000000, "openrouter"),
    ("openrouter", "google/gemini-pro-1.5", 1.25, 5.00, 1700000000, "openrouter"),
    ("openrouter", "google/gemini-flash-1.5", 0.075, 0.30, 1700000000, "openrouter"),
    ("openrouter", "meta-llama/llama-3.1-70b-instruct", 0.52, 0.75, 1700000000, "openrouter"),
    ("openrouter", "meta-llama/llama-3.1-8b-instruct", 0.05, 0.05, 1700000000, "openrouter"),
    ("openrouter", "deepseek/deepseek-chat", 0.14, 0.28, 1700000000, "openrouter"),
    ("openrouter", "deepseek/deepseek-r1", 0.55, 2.19, 1700000000, "openrouter"),
    ("openrouter", "qwen/qwen-2.5-72b-instruct", 0.20, 0.60, 1700000000, "openrouter"),
    ("openrouter", "mistralai/mistral-large", 2.00, 6.00, 1700000000, "openrouter"),
    # Direct OpenAI
    ("openai", "gpt-4o", 2.50, 10.00, 1700000000, "openai"),
    ("openai", "gpt-4o-mini", 0.15, 0.60, 1700000000, "openai"),
    ("openai", "gpt-4-turbo", 10.00, 30.00, 1700000000, "openai"),
    ("openai", "gpt-4", 30.00, 60.00, 1700000000, "openai"),
    ("openai", "gpt-3.5-turbo", 0.50, 1.50, 1700000000, "openai"),
    ("openai", "o1", 15.00, 60.00, 1700000000, "openai"),
    ("openai", "o1-mini", 1.10, 4.40, 1700000000, "openai"),
    ("openai", "o3-mini", 1.10, 4.40, 1700000000, "openai"),
    # Direct Anthropic
    ("anthropic", "claude-3-5-sonnet-20241022", 3.00, 15.00, 1700000000, "anthropic"),
    ("anthropic", "claude-3-opus-20240229", 15.00, 75.00, 1700000000, "anthropic"),
    ("anthropic", "claude-3-haiku-20240307", 0.25, 1.25, 1700000000, "anthropic"),
    ("anthropic", "claude-3-5-haiku-20241022", 0.80, 4.00, 1700000000, "anthropic"),
    ("anthropic", "claude-sonnet-4-20250514", 3.00, 15.00, 1700000000, "anthropic"),
    ("anthropic", "claude-opus-4-20250514", 15.00, 75.00, 1700000000, "anthropic"),
    # Google
    ("google", "gemini-1.5-pro", 1.25, 5.00, 1700000000, "google"),
    ("google", "gemini-1.5-flash", 0.075, 0.30, 1700000000, "google"),
]


def _seed_pricing(conn: sqlite3.Connection):
    """Insert pricing data, ignoring duplicates."""
    conn.executemany(
        """INSERT OR IGNORE INTO pricing
           (provider, model, input_price_per_1m, output_price_per_1m, valid_from, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        _PRICING_SEED,
    )
