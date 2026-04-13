"""Provider detection and response parsing for AI API traffic."""

from __future__ import annotations

import json
import re
import logging

logger = logging.getLogger(__name__)

# ── domain → provider mapping ───────────────────────────────
PROVIDER_DOMAINS = {
    "openrouter.ai": "openrouter",
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
}


def detect_provider(host: str) -> str:
    """Map a hostname to a provider name. Returns empty string if unknown."""
    host_lower = host.lower()
    # First: exact subdomain/domain match
    for domain, provider in PROVIDER_DOMAINS.items():
        if domain in host_lower:
            return provider
    return ""


# ── Provider-specific parsers ───────────────────────────────


def parse_openrouter_response(body: bytes) -> dict:
    """
    Parse OpenRouter API response.
    OpenRouter follows OpenAI-compatible format but also wraps with its own fields.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "invalid json"}

    usage = data.get("usage", {})
    # OpenRouter may report usage at top level or nested
    if not usage and "data" in data and isinstance(data["data"], list):
        # Embeddings-style response
        usage = data["data"][0].get("usage", {}) if data["data"] else {}

    model = data.get("model", "")

    # OpenRouter sometimes includes cost info
    native_cost = data.get("cost", None)

    return {
        "model": model,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "native_cost": native_cost,
    }


def parse_openai_response(body: bytes) -> dict:
    """Parse OpenAI API response (ChatCompletion format)."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "invalid json"}

    usage = data.get("usage", {})
    model = data.get("model", "")

    return {
        "model": model,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def parse_anthropic_response(body: bytes) -> dict:
    """Parse Anthropic Messages API response."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "invalid json"}

    usage = data.get("usage", {})
    model = data.get("model", "")

    # Anthropic splits input into input_tokens + cache_read/input_tokens
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    # Cache tokens still cost (reduced), count them as input
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    inp += cache_creation + cache_read

    return {
        "model": model,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
    }


def parse_google_response(body: bytes) -> dict:
    """Parse Google Generative AI API response."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "invalid json"}

    usage = data.get("usageMetadata", {})
    model = data.get("modelVersion", "")

    return {
        "model": model,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "total_tokens": usage.get("totalTokenCount", 0),
    }


# ── SSE streaming parser ─────────────────────────────────────

def parse_sse_response(provider: str, body: bytes) -> dict:
    """
    Parse an accumulated SSE stream body (text/event-stream) for usage info.
    Looks for the last `data: {...}` chunk that contains a `usage` field.
    Works for OpenAI/OpenRouter/Anthropic streaming formats.
    """
    model = ""
    usage: dict = {}
    native_cost = None
    # Anthropic streaming uses a different event structure
    if provider == "anthropic":
        for line in body.split(b"\n"):
            if not line.startswith(b"data: "):
                continue
            payload = line[6:].strip()
            if not payload or payload == b"[DONE]":
                continue
            try:
                data = json.loads(payload)
                event_type = data.get("type", "")
                if not model and "message" in data:
                    model = data["message"].get("model", "")
                if native_cost is None:
                    native_cost = data.get("cost")
                if event_type == "message_delta":
                    u = data.get("usage", {})
                    if u:
                        usage = u
                elif event_type == "message_start" and "message" in data:
                    model = model or data["message"].get("model", "")
                    u = data["message"].get("usage", {})
                    if u:
                        usage = u
            except (json.JSONDecodeError, KeyError):
                continue
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        return {
            "model": model,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "native_cost": native_cost,
        }

    # OpenAI / OpenRouter SSE
    for line in body.split(b"\n"):
        if not line.startswith(b"data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            data = json.loads(payload)
            if not model:
                model = data.get("model", "")
            if native_cost is None:
                native_cost = data.get("cost")
            u = data.get("usage")
            if u:
                usage = u
                if native_cost is None and isinstance(u, dict):
                    native_cost = u.get("cost")
        except (json.JSONDecodeError, KeyError):
            continue

    return {
        "model": model,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "native_cost": native_cost,
    }


# ── dispatcher ──────────────────────────────────────────────


def parse_response(provider: str, body: bytes, is_sse: bool = False) -> dict:
    """Dispatch to the correct parser based on provider."""
    if is_sse:
        try:
            return parse_sse_response(provider, body)
        except Exception as exc:
            logger.error("SSE parser error for %s: %s", provider, exc)
            return {"error": str(exc)}
    parsers = {
        "openrouter": parse_openrouter_response,
        "openai": parse_openai_response,
        "anthropic": parse_anthropic_response,
        "google": parse_google_response,
    }
    parser = parsers.get(provider)
    if parser:
        try:
            return parser(body)
        except Exception as exc:
            logger.error("Parser error for %s: %s", provider, exc)
            return {"error": str(exc)}
    return {"error": f"no parser for provider: {provider}"}


def is_ai_endpoint(host: str, path: str) -> bool:
    """Quick check whether this request looks like an AI API call."""
    provider = detect_provider(host)
    if provider:
        return True
    # Fallback: check common path patterns
    ai_patterns = [
        r"/v1/(chat/completions|completions|embeddings)",
        r"/v1/messages",
        r"/models/",
        r"/generateContent",
        r"/streamGenerateContent",
    ]
    for pat in ai_patterns:
        if re.search(pat, path):
            return True
    return False
