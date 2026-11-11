"""Medication information adapter (Agent B).

Two SEPARATE sources, always labelled:
1. openFDA drug label (public API, US labels) — label warning excerpts, where a label is found.
2. Local interaction rules — a small, fictional-demo rule table of known combinations to FLAG
   for clinician review.
Limitations are always returned. "No label found" or "lookup failed" is never a safety clearance,
and finding no interaction in the local table never means a combination is safe.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from ..config import settings
from .base import ToolResult, ToolStatus

log = logging.getLogger("ninety_six.tools.medication_info")

LIMITATIONS = (
    "openFDA labels are US product labels and may not match Indian brands or formulations. "
    "The local interaction table is a small demo rule set, not a complete interaction database. "
    "A missing label or an absent interaction flag does NOT mean a medicine or combination is safe. "
    "Findings are for clinician review only; Ninety-Six never changes doses or medicines."
)

LOCAL_NOTES = {
    "furosemide": "Diuretic; label warns about fluid and electrolyte loss.",
    "spironolactone": "Potassium-sparing diuretic; label warns about high potassium.",
    "lisinopril": "ACE inhibitor; label warns about high potassium and low blood pressure.",
    "carvedilol": "Beta-blocker; label advises taking with food.",
    "aspirin": "Antiplatelet; label warns about bleeding risk.",
    "metformin": "Label warns about lactic acidosis risk with dehydration.",
    "insulin glargine": "Label warns about low blood sugar.",
    "atorvastatin": "Label warns about muscle pain.",
    "paracetamol": "Label warns about liver injury above the maximum daily dose.",
    "cefuroxime": "Antibiotic; label advises completing the course.",
    "pantoprazole": "Proton pump inhibitor.",
}

INTERACTION_RULES = [
    ({"lisinopril", "spironolactone"},
     "ACE inhibitor + potassium-sparing diuretic: combination flagged for potassium monitoring (rule IR-01)."),
    ({"aspirin", "warfarin"}, "Antiplatelet + anticoagulant: combination flagged for bleeding risk (rule IR-02)."),
    ({"clopidogrel", "omeprazole"}, "Clopidogrel + omeprazole: combination flagged (rule IR-03)."),
    ({"furosemide", "lisinopril"}, "Loop diuretic + ACE inhibitor: combination flagged for low blood pressure (rule IR-04)."),
]

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 3600


def _base(name: str) -> str:
    n = name.lower().strip()
    return n if n in LOCAL_NOTES else n.split()[0] if n else n


def interaction_flags(names: list[str]) -> list[dict[str, Any]]:
    bases = {_base(n) for n in names} | {n.lower() for n in names}
    return [{"drugs": sorted(pair), "finding": msg, "source": "local interaction rules (demo)"}
            for pair, msg in INTERACTION_RULES if pair <= bases]


def _openfda_label(name: str) -> dict[str, Any]:
    with _cache_lock:
        hit = _cache.get(name)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    out: dict[str, Any]
    try:
        r = httpx.get(
            "https://api.fda.gov/drug/label.json",
            params={"search": f'openfda.generic_name:"{name}"', "limit": 1},
            timeout=httpx.Timeout(8.0, connect=4.0),
        )
        if r.status_code == 404:
            out = {"status": "not_found", "excerpt": None}
        elif r.status_code != 200:
            out = {"status": "lookup_failed", "excerpt": None, "error": f"HTTP {r.status_code}"}
        else:
            res = (r.json().get("results") or [{}])[0]
            excerpt = None
            for field in ("boxed_warning", "warnings_and_cautions", "warnings"):
                if res.get(field):
                    excerpt = " ".join(res[field])[:300].strip()
                    break
            out = {"status": "found", "excerpt": excerpt, "set_id": res.get("set_id"),
                   "effective_time": res.get("effective_time"),
                   "url": f"https://api.fda.gov/drug/label.json?search=set_id:{res.get('set_id')}" if res.get("set_id") else None}
    except httpx.HTTPError as e:
        out = {"status": "lookup_failed", "excerpt": None, "error": type(e).__name__}
    if out["status"] in ("found", "not_found"):
        with _cache_lock:
            _cache[name] = (time.time(), out)
    return out


class MedicationInfoAdapter:
    name = "medication_info"

    def mode(self) -> str:
        return "live" if settings.openfda_enabled else "simulated"

    def status(self) -> ToolStatus:
        if settings.openfda_enabled:
            return ToolStatus("medication_info", "Medication information (openFDA + local rules)", "live", True,
                              "openFDA drug label API (public, no key) plus a separate local interaction rule table")
        return ToolStatus("medication_info", "Medication information (openFDA + local rules)", "simulated", False,
                          "openFDA disabled: local reference notes and interaction rules only")

    def execute(self, request: dict[str, Any], *, idempotency_key: str) -> ToolResult:
        names: list[str] = [n for n in request.get("medications", []) if n]
        drugs = []
        live_calls, failures = 0, 0
        for n in names:
            base = _base(n)
            entry: dict[str, Any] = {"name": n}
            if settings.openfda_enabled and not request.get("local_only"):
                live_calls += 1
                lab = _openfda_label(base)
                entry.update(
                    {"source": "openFDA drug label", "label_status": lab["status"], "excerpt": lab.get("excerpt"),
                     "set_id": lab.get("set_id"), "url": lab.get("url")}
                )
                if lab["status"] == "lookup_failed":
                    failures += 1
                    entry["note"] = "openFDA lookup failed; no label information retrieved (not a safety clearance)."
                elif lab["status"] == "not_found":
                    entry["note"] = "No openFDA label found for this name (not a safety clearance)."
            else:
                entry.update({"source": "local reference note (demo)", "label_status": "not_checked",
                              "excerpt": LOCAL_NOTES.get(base), "note": "openFDA not queried."})
            drugs.append(entry)
        flags = interaction_flags(names)
        mode = "live" if live_calls else "simulated"
        provider = "openfda" if live_calls else "local"
        safe_error = f"{failures} of {live_calls} openFDA lookups failed" if failures else None
        return ToolResult("success", mode, provider, provider_status="completed", delivery_status="not_applicable",
                          safe_error=safe_error,
                          data={"drugs": drugs, "interactions": flags, "limitations": LIMITATIONS,
                                "openfda_calls": live_calls, "openfda_failures": failures})
