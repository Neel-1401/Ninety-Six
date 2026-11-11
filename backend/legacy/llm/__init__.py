"""LLM provider abstraction. Every caller has a rule-based fallback, so if the
model is unavailable or returns bad JSON the agent keeps working."""
import json
import logging
import re
from typing import Optional

import httpx

from ..config import settings

log = logging.getLogger("ninety_six.llm")


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class LLM:
    name = "rules"
    live = False

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        return None


class AnthropicLLM(LLM):
    name = "anthropic"
    live = True

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 2500,
                    "system": system + "\nRespond with a single JSON object only.",
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=settings.llm_timeout,
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            return _extract_json(text)
        except Exception as exc:  # network, auth, parse
            log.warning("Anthropic call failed: %s", exc)
            return None


class GeminiLLM(LLM):
    name = "gemini"
    live = True

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
        )
        try:
            r = httpx.post(
                url,
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                },
                timeout=settings.llm_timeout,
            )
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return _extract_json("".join(p.get("text", "") for p in parts))
        except Exception as exc:
            log.warning("Gemini call failed: %s", exc)
            return None


def get_llm() -> LLM:
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicLLM()
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return GeminiLLM()
    return LLM()
