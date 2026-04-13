"""Unit tests for cost engine and storage."""

import os
import sys
import time
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claw_cost_daemon.storage import Storage
from claw_cost_daemon.cost_engine import CostEngine


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    return str(db_path)


class TestStorage:
    def test_init_creates_tables(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        assert Path(tmp_db).exists()

    def test_insert_and_query_event(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()

        event_id = storage.insert_event(
            ts=time.time(),
            provider="openrouter",
            host="openrouter.ai",
            endpoint="/api/v1/chat/completions",
            model="openai/gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            input_cost_usd=0.00025,
            output_cost_usd=0.0005,
            total_cost_usd=0.00075,
            status_code=200,
            latency_ms=500.0,
            pid=1234,
            process_name="test-app",
            cmdline="test-app --run",
            attribution_confidence="high",
        )
        assert event_id > 0

        events = storage.recent_events(limit=1)
        assert len(events) == 1
        assert events[0]["provider"] == "openrouter"
        assert events[0]["model"] == "openai/gpt-4o"

    def test_dead_letter(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        storage.insert_dead_letter('{"host":"test"}', "parse error")
        # Should not crash

    def test_costs_by_provider(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        now = time.time()
        storage.insert_event(
            ts=now, provider="openai", model="gpt-4o",
            input_tokens=10, output_tokens=5, total_tokens=15,
            input_cost_usd=0.01, output_cost_usd=0.02, total_cost_usd=0.03,
            status_code=200, latency_ms=100,
        )
        storage.insert_event(
            ts=now, provider="anthropic", model="claude-3.5-sonnet",
            input_tokens=20, output_tokens=10, total_tokens=30,
            input_cost_usd=0.02, output_cost_usd=0.04, total_cost_usd=0.06,
            status_code=200, latency_ms=200,
        )
        rows = storage.costs_by_provider(now - 1)
        assert len(rows) == 2
        # Anthropic should be first (higher cost)
        assert rows[0]["provider"] == "anthropic"

    def test_openrouter_clawcode_filter(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        now = time.time()
        # OpenRouter from claw-code
        storage.insert_event(
            ts=now, provider="openrouter", model="openai/gpt-4o",
            input_tokens=10, output_tokens=5, total_tokens=15,
            input_cost_usd=0.01, output_cost_usd=0.02, total_cost_usd=0.03,
            status_code=200, latency_ms=100,
            pid=999, process_name="claw-code", cmdline="claw-code run",
        )
        # OpenRouter from other process
        storage.insert_event(
            ts=now, provider="openrouter", model="openai/gpt-4o",
            input_tokens=10, output_tokens=5, total_tokens=15,
            input_cost_usd=0.01, output_cost_usd=0.02, total_cost_usd=0.03,
            status_code=200, latency_ms=100,
            pid=888, process_name="curl", cmdline="curl https://...",
        )

        or_total = storage.openrouter_total(now - 1)
        assert or_total["req_count"] == 2

        or_claw = storage.openrouter_by_clawcode(now - 1)
        assert or_claw["req_count"] == 1


class TestCostEngine:
    def test_known_model(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        engine = CostEngine(tmp_db)

        result = engine.calculate("openrouter", "openai/gpt-4o", 1000, 500)
        # gpt-4o: $2.50/1M input, $10.00/1M output
        assert result["input_cost_usd"] == 0.0025  # 1000/1M * 2.50
        assert result["output_cost_usd"] == 0.005   # 500/1M * 10.00
        assert result["total_cost_usd"] == 0.0075

    def test_unknown_model_uses_defaults(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        engine = CostEngine(tmp_db)

        result = engine.calculate("openrouter", "unknown-model-xyz", 1000, 500)
        # Defaults: $3.00/1M input, $15.00/1M output
        assert result["input_cost_usd"] == 0.003
        assert result["output_cost_usd"] == 0.0075

    def test_zero_tokens(self, tmp_db):
        storage = Storage(tmp_db)
        storage.init()
        engine = CostEngine(tmp_db)

        result = engine.calculate("openai", "gpt-4o", 0, 0)
        assert result["total_cost_usd"] == 0.0
