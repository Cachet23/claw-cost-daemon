"""Unit tests for provider parsers."""

import json
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claw_cost_daemon.parsers.providers import (
    detect_provider,
    parse_response,
    parse_openrouter_response,
    parse_openai_response,
    parse_anthropic_response,
    parse_google_response,
    is_ai_endpoint,
)


class TestDetectProvider:
    def test_openrouter(self):
        assert detect_provider("openrouter.ai") == "openrouter"
        assert detect_provider("api.openrouter.ai") == "openrouter"

    def test_openai(self):
        assert detect_provider("api.openai.com") == "openai"

    def test_anthropic(self):
        assert detect_provider("api.anthropic.com") == "anthropic"

    def test_google(self):
        assert detect_provider("generativelanguage.googleapis.com") == "google"

    def test_unknown(self):
        assert detect_provider("example.com") == ""
        assert detect_provider("random-api.io") == ""


class TestParseOpenRouter:
    def test_chat_completion(self):
        body = json.dumps({
            "id": "gen-123",
            "model": "openai/gpt-4o",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "cost": 0.001,
        }).encode()
        result = parse_openrouter_response(body)
        assert result["model"] == "openai/gpt-4o"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["native_cost"] == 0.001

    def test_invalid_json(self):
        result = parse_openrouter_response(b"not json")
        assert "error" in result

    def test_empty_usage(self):
        body = json.dumps({"model": "test"}).encode()
        result = parse_openrouter_response(body)
        assert result["input_tokens"] == 0


class TestParseOpenAI:
    def test_standard(self):
        body = json.dumps({
            "model": "gpt-4o",
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
            },
        }).encode()
        result = parse_openai_response(body)
        assert result["model"] == "gpt-4o"
        assert result["input_tokens"] == 200
        assert result["output_tokens"] == 80


class TestParseAnthropic:
    def test_standard(self):
        body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "usage": {
                "input_tokens": 500,
                "output_tokens": 200,
            },
        }).encode()
        result = parse_anthropic_response(body)
        assert result["model"] == "claude-3-5-sonnet-20241022"
        assert result["input_tokens"] == 500
        assert result["output_tokens"] == 200

    def test_with_cache(self):
        body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
            },
        }).encode()
        result = parse_anthropic_response(body)
        # Cache tokens counted as input
        assert result["input_tokens"] == 600  # 100 + 200 + 300
        assert result["output_tokens"] == 50


class TestParseGoogle:
    def test_standard(self):
        body = json.dumps({
            "modelVersion": "gemini-1.5-pro",
            "usageMetadata": {
                "promptTokenCount": 300,
                "candidatesTokenCount": 100,
                "totalTokenCount": 400,
            },
        }).encode()
        result = parse_google_response(body)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 100


class TestParseResponseDispatcher:
    def test_dispatches_correctly(self):
        body = json.dumps({
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()
        result = parse_response("openai", body)
        assert result["model"] == "gpt-4o"

    def test_unknown_provider(self):
        result = parse_response("unknown", b"{}")
        assert "error" in result


class TestIsAIEndpoint:
    def test_known_provider(self):
        assert is_ai_endpoint("openrouter.ai", "/api/v1/chat/completions")
        assert is_ai_endpoint("api.openai.com", "/v1/chat/completions")

    def test_unknown_with_ai_path(self):
        assert is_ai_endpoint("some-server.com", "/v1/chat/completions")
        assert is_ai_endpoint("some-server.com", "/v1/messages")

    def test_non_ai(self):
        assert not is_ai_endpoint("example.com", "/api/users")
