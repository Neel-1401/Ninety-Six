"""LLM provider layer (Agent B).

The LLM only PROPOSES structured data; deterministic code validates it and decides. Every caller
has a rule-based fallback, so a missing key, timeout, refusal or schema failure never blocks work.

- anthropic: official SDK `client.messages.parse(..., output_format=<Pydantic model>)`
- gemini:    plain REST with responseMimeType=application/json, validated with Pydantic
- mock:      no call (deterministic rules only)

Never call this inside an open write transaction: callers invoke it before writing.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import settings

log = logging.getLogger("ninety_six.llm")
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult(Generic[T]):
    ok: bool
    provider: str  # anthropic | gemini | none
    model: Optional[str] = None
    data: Optional[T] = None
    error: Optional[str] = None  # safe, short

    @property
    def source(self) -> str:
        return f"llm:{self.provider}" if self.ok else "rules"


def provider_name() -> str:
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return "anthropic"
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return "gemini"
    return "none"


def is_configured() -> bool:
    return provider_name() != "none"


def status() -> dict:
    name = provider_name()
    model = settings.anthropic_model if name == "anthropic" else settings.gemini_model if name == "gemini" else None
    return {
        "provider": settings.llm_provider,
        "mode": "live" if name != "none" else "simulated",
        "model": model,
        "configured": name != "none",
        "detail": (
            f"{name} ({model}); every call validated, rule-based fallback on failure"
            if name != "none"
            else "No LLM configured: deterministic rules are used for extraction, planning and triage"
        ),
    }


def _extract_json(text: str) -> Optional[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _anthropic(system: str, user: str, schema: type[T]) -> LLMResult[T]:
    import anthropic

    model = settings.anthropic_model
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout, max_retries=1)
        resp = client.messages.parse(
            model=model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            return LLMResult(False, "anthropic", model, error="model declined the request")
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            return LLMResult(False, "anthropic", model, error="no structured output returned")
        return LLMResult(True, "anthropic", model, data=parsed)
    except anthropic.APITimeoutError:
        return LLMResult(False, "anthropic", model, error="timeout")
    except anthropic.RateLimitError:
        return LLMResult(False, "anthropic", model, error="rate limited")
    except anthropic.AuthenticationError:
        return LLMResult(False, "anthropic", model, error="authentication failed")
    except anthropic.APIStatusError as e:
        return LLMResult(False, "anthropic", model, error=f"API error {e.status_code}")
    except anthropic.APIConnectionError:
        return LLMResult(False, "anthropic", model, error="connection error")
    except ValidationError:
        return LLMResult(False, "anthropic", model, error="output failed schema validation")
    except Exception as e:  # noqa: BLE001 - never let the LLM break the workflow
        log.warning("Anthropic call failed: %s", type(e).__name__)
        return LLMResult(False, "anthropic", model, error=type(e).__name__)


def _gemini(system: str, user: str, schema: type[T]) -> LLMResult[T]:
    model = settings.gemini_model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = httpx.post(
            url,
            params={"key": settings.gemini_api_key},
            json={
                "systemInstruction": {"parts": [{"text": system + "\nReturn only JSON matching this schema:\n"
                                                 + json.dumps(schema.model_json_schema())}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
            },
            timeout=settings.llm_timeout,
        )
        if r.status_code != 200:
            return LLMResult(False, "gemini", model, error=f"API error {r.status_code}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        raw = _extract_json("".join(p.get("text", "") for p in parts))
        if raw is None:
            return LLMResult(False, "gemini", model, error="no JSON returned")
        return LLMResult(True, "gemini", model, data=schema.model_validate(raw))
    except httpx.TimeoutException:
        return LLMResult(False, "gemini", model, error="timeout")
    except ValidationError:
        return LLMResult(False, "gemini", model, error="output failed schema validation")
    except Exception as e:  # noqa: BLE001
        return LLMResult(False, "gemini", model, error=type(e).__name__)


# Tests may replace this to simulate an LLM (e.g. one returning an invalid plan).
_override = None


def set_override(fn) -> None:  # noqa: ANN001
    """fn(system, user, schema, purpose) -> LLMResult | None. None restores normal behaviour."""
    global _override
    _override = fn


def structured(system: str, user: str, schema: type[T], *, purpose: str) -> LLMResult[T]:
    if _override is not None:
        return _override(system, user, schema, purpose)
    name = provider_name()
    if name == "anthropic":
        return _anthropic(system, user, schema)
    if name == "gemini":
        return _gemini(system, user, schema)
    return LLMResult(False, "none", error="no LLM configured")
