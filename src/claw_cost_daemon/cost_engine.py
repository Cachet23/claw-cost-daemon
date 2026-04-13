"""Cost calculation engine with pricing lookup."""

from __future__ import annotations

import sqlite3
import time
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback prices per 1M tokens for unknown models (conservative estimates)
_DEFAULT_INPUT_PRICE = 3.00
_DEFAULT_OUTPUT_PRICE = 15.00


class CostEngine:
    """Looks up pricing and computes costs for a request."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_pricing(self, provider: str, model: str) -> tuple[float, float]:
        """
        Return (input_price_per_1m, output_price_per_1m) for the given
        provider + model, using the most recent valid pricing row.
        Falls back to defaults if not found.
        """
        conn = self._get_connection()
        try:
            now = time.time()

            def query_one(model_name: str):
                return conn.execute(
                    """
                    SELECT input_price_per_1m, output_price_per_1m
                    FROM pricing
                    WHERE provider = ? AND model = ? AND valid_from <= ?
                    ORDER BY valid_from DESC LIMIT 1
                    """,
                    (provider, model_name, now),
                ).fetchone()

            # 1) exact model first
            row = query_one(model)
            if row:
                return row["input_price_per_1m"], row["output_price_per_1m"]

            # 2) normalized variants (OpenRouter often appends date/version suffixes)
            variants = self._model_variants(model)
            for variant in variants:
                row = query_one(variant)
                if row:
                    logger.info(
                        "Pricing matched via normalized model: %s -> %s",
                        model,
                        variant,
                    )
                    return row["input_price_per_1m"], row["output_price_per_1m"]
        finally:
            conn.close()

        logger.warning(
            "No pricing found for %s / %s – using defaults", provider, model
        )
        return _DEFAULT_INPUT_PRICE, _DEFAULT_OUTPUT_PRICE

    def _model_variants(self, model: str) -> list[str]:
        """Generate normalized model identifiers for fallback pricing lookup."""
        variants: list[str] = []
        m = model.strip()
        if not m:
            return variants

        # Remove common dated/snapshot suffixes, e.g. "-20260315", "-2025-03-15".
        date_suffix_stripped = re.sub(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", "", m)
        if date_suffix_stripped != m:
            variants.append(date_suffix_stripped)

        # Some providers use version suffixes after ':'
        if ":" in m:
            variants.append(m.split(":", 1)[0])

        # Remove duplicate adjacent dashes if any transformation caused them.
        cleaned = [re.sub(r"-{2,}", "-", v).strip("-") for v in variants]

        # Deduplicate while preserving order.
        deduped: list[str] = []
        seen = set()
        for v in cleaned:
            if v and v not in seen and v != m:
                deduped.append(v)
                seen.add(v)
        return deduped

    def calculate(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> dict:
        """
        Calculate costs for a single request.

        Returns dict with:
            input_price_per_1m, output_price_per_1m,
            input_cost_usd, output_cost_usd, total_cost_usd
        """
        inp_price, out_price = self.get_pricing(provider, model)
        inp_cost = (input_tokens / 1_000_000) * inp_price
        out_cost = (output_tokens / 1_000_000) * out_price
        return {
            "input_price_per_1m": inp_price,
            "output_price_per_1m": out_price,
            "input_cost_usd": round(inp_cost, 8),
            "output_cost_usd": round(out_cost, 8),
            "total_cost_usd": round(inp_cost + out_cost, 8),
        }

    def update_pricing(
        self,
        provider: str,
        model: str,
        input_price: float,
        output_price: float,
        source: str = "manual",
    ):
        """Insert or update pricing for a model."""
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO pricing
                   (provider, model, input_price_per_1m, output_price_per_1m, valid_from, source)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, model, valid_from)
                   DO UPDATE SET input_price_per_1m=excluded.input_price_per_1m,
                                 output_price_per_1m=excluded.output_price_per_1m,
                                 source=excluded.source
                """,
                (provider, model, input_price, output_price, time.time(), source),
            )
            conn.commit()
        finally:
            conn.close()

    def list_pricing(self, provider: Optional[str] = None) -> list[dict]:
        """List all pricing entries, optionally filtered by provider."""
        conn = self._get_connection()
        try:
            if provider:
                rows = conn.execute(
                    "SELECT * FROM pricing WHERE provider = ? ORDER BY provider, model",
                    (provider,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pricing ORDER BY provider, model"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
