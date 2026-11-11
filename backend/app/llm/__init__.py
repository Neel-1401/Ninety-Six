"""LLM provider layer (Agent B).

The LLM only PROPOSES structured data; deterministic code validates it and decides. Every caller
has a rule-based fallback, so a missing key, timeout, refusal or schema failure never blocks work.

- anthropic: official SDK `client.messages.parse(..., output_format=<Pydantic model>)`
- gemini:    plain REST with responseMimeType=application/json, validated with Pydantic
- mock:      no call (deterministic rules only)

Never call this inside an open write transaction: callers invoke it before writing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
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
    ms: int = 0  # wall-clock latency of the call (0 when served from cache)
    cached: bool = False
    attempts: int = 1

    @property
    def source(self) -> str:
        return f"llm:{self.provider}" if self.ok else "rules"


# Runtime kill switch (Settings page). None = follow env (LLM_PROVIDER); False = off instantly.
_runtime_enabled: Optional[bool] = None


def set_enabled(enabled: Optional[bool]) -> None:
    global _runtime_enabled
    _runtime_enabled = enabled


def runtime_enabled() -> bool:
    return _runtime_enabled is not False


def configured_provider() -> str:
    """Provider the env selects (ignores the runtime switch)."""
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return "anthropic"
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return "gemini"
    return "none"


def provider_name() -> str:
    if not runtime_enabled():
        return "none"
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return "anthropic"
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return "gemini"
    return "none"


def is_configured() -> bool:
    return provider_name() != "none"


def model_name(name: Optional[str] = None) -> Optional[str]:
    name = name or provider_name()
    return settings.anthropic_model if name == "anthropic" else settings.gemini_model if name == "gemini" else None


def status() -> dict:
    name = provider_name()
    env_name = configured_provider()
    model = model_name(name)
    if name != "none":
        detail = (f"{name} ({model}), hybrid: rules first, AI only where it adds value. "
                  f"{hard_timeout():g} s timeout, schema-validated, 1 retry, rule fallback")
    elif env_name != "none" and not runtime_enabled():
        detail = f"Turned OFF from Settings: rules only (env selects {env_name} / {model_name(env_name)})"
    else:
        detail = "LLM off (LLM_PROVIDER=mock or no key): deterministic rules for everything"
    return {
        "provider": settings.llm_provider,
        "mode": "live" if name != "none" else "simulated",
        "live": name != "none",
        "model": model,
        "configured": name != "none",
        "env_configured": env_name != "none",
        "env_model": model_name(env_name),
        "runtime_enabled": runtime_enabled(),
        "timeout_seconds": hard_timeout(),
        "cache_entries": len(_cache),
        "detail": detail,
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
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=hard_timeout(), max_retries=0)
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


def _gemini_config(model: str) -> dict:
    cfg: dict = {"responseMimeType": "application/json", "temperature": 0.1}
    if model.startswith("gemini-2.5"):  # 2.5 series: no thinking, so it answers well inside the timeout
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    return cfg


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
                "generationConfig": _gemini_config(model),
            },
            timeout=hard_timeout(),
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

HARD_TIMEOUT_CAP = 8.0  # seconds; a slow model must never stall a workflow step
NO_RETRY = {"timeout", "authentication failed", "model declined the request", "no LLM configured",
            "API error 400", "API error 401", "API error 403", "API error 404"}  # retrying can't fix these
_cache: dict[str, LLMResult] = {}
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")


def hard_timeout() -> float:
    return max(0.05, min(float(settings.llm_timeout or HARD_TIMEOUT_CAP), HARD_TIMEOUT_CAP))


def set_override(fn) -> None:  # noqa: ANN001
    """fn(system, user, schema, purpose) -> LLMResult | str | None. A str is treated as the raw
    model text (parsed + schema-validated like a real reply). None restores normal behaviour.
    Clears the cache so fakes never see each other's answers."""
    global _override
    _override = fn
    _cache.clear()


def clear_cache() -> None:
    _cache.clear()


def _cache_key(system: str, user: str, schema: type, purpose: str) -> str:
    name = "override" if _override is not None else provider_name()
    raw = "\x1f".join([name, model_name(name) or "", purpose, schema.__name__, system, user])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _call_once(system: str, user: str, schema: type[T], purpose: str) -> LLMResult[T]:
    name = provider_name()
    if _override is not None:
        out = _override(system, user, schema, purpose)
        if isinstance(out, str):  # raw text from a fake model: same strict parsing as Gemini
            raw = _extract_json(out)
            if raw is None:
                return LLMResult(False, "fake", "fake", error="no JSON returned")
            try:
                return LLMResult(True, "fake", "fake", data=schema.model_validate(raw))
            except ValidationError:
                return LLMResult(False, "fake", "fake", error="output failed schema validation")
        return out if out is not None else LLMResult(False, "fake", error="no output")
    if name == "anthropic":
        return _anthropic(system, user, schema)
    if name == "gemini":
        return _gemini(system, user, schema)
    return LLMResult(False, "none", error="no LLM configured")


def structured(system: str, user: str, schema: type[T], *, purpose: str) -> LLMResult[T]:
    """One guarded LLM call: cache -> hard timeout -> strict schema check -> at most 1 retry.
    Never raises; on any failure returns ok=False so the caller keeps its rule-based result."""
    if _override is None and provider_name() == "none":
        return LLMResult(False, "none", error="no LLM configured")
    key = _cache_key(system, user, schema, purpose)
    hit = _cache.get(key)
    if hit is not None:
        return LLMResult(True, hit.provider, hit.model, data=hit.data, ms=0, cached=True)
    res: LLMResult = LLMResult(False, provider_name(), model_name(), error="not called")
    t0 = time.perf_counter()
    for attempt in (1, 2):
        fut = _pool.submit(_call_once, system, user, schema, purpose)
        try:
            res = fut.result(timeout=hard_timeout())
        except FuturesTimeout:
            fut.cancel()
            res = LLMResult(False, provider_name(), model_name(), error="timeout")
        except Exception as e:  # noqa: BLE001 - never let the LLM break the workflow
            res = LLMResult(False, provider_name(), model_name(), error=type(e).__name__)
        if res.ok and not isinstance(res.data, schema):
            try:  # strict: whatever came back must validate against the schema
                res = LLMResult(True, res.provider, res.model, data=schema.model_validate(
                    res.data.model_dump() if isinstance(res.data, BaseModel) else res.data))
            except (ValidationError, TypeError):
                res = LLMResult(False, res.provider, res.model, error="output failed schema validation")
        res.attempts = attempt
        if res.ok or (res.error or "") in NO_RETRY:
            break
    res.ms = int((time.perf_counter() - t0) * 1000)
    log.info("llm %s purpose=%s ok=%s ms=%s attempts=%s err=%s", res.provider, purpose, res.ok, res.ms,
             res.attempts, res.error)
    if res.ok:
        _cache[key] = res
    return res
