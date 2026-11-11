"""Transparent DEMO prioritisation (Agent B). Defined inputs, visible reasons, no hidden score.
This is NOT a validated clinical prediction and must never be presented as one."""
from __future__ import annotations

import re
from typing import Any, Optional

DISCLAIMER = "Demo prioritisation from defined inputs; not a validated clinical prediction."
HIGH_RISK_TERMS = [
    "heart failure", "myocardial", "copd", "stroke", "sepsis", "pneumonia", "dka", "ketoacidosis",
    "kidney", "renal failure",
]


def compute_risk(
    *,
    age: Optional[int],
    risk_inputs: dict[str, Any],
    diagnosis_texts: list[str],
    medication_count: int,
    summary_text: str,
) -> dict[str, Any]:
    """Returns {"category", "reasons", "inputs"}. Each true input adds one point:
    age >= 65 · lives alone · admission in the last 6 months · >= 5 discharge medicines ·
    diagnosis in a defined higher-readmission list · no caregiver available.
    0 points -> low, 1-2 -> medium, >= 3 -> high."""
    text = summary_text.lower()
    diag = " ".join(diagnosis_texts).lower()
    lives_alone = risk_inputs.get("lives_alone")
    if lives_alone is None:
        lives_alone = bool(re.search(r"lives alone", text))
    prior = risk_inputs.get("prior_admission_6m")
    if prior is None:
        prior = bool(re.search(r"readmi|previous admission", text))
    caregiver = risk_inputs.get("caregiver_available")

    inputs = {
        "age_65_or_over": bool(age is not None and age >= 65),
        "lives_alone": bool(lives_alone),
        "prior_admission_6m": bool(prior),
        "five_or_more_medicines": medication_count >= 5,
        "higher_readmission_diagnosis": any(t in diag for t in HIGH_RISK_TERMS),
        "no_caregiver_available": caregiver is False,
    }
    labels = {
        "age_65_or_over": f"Age {age} (65 or over)",
        "lives_alone": "Lives alone",
        "prior_admission_6m": "Hospital admission in the last 6 months",
        "five_or_more_medicines": f"{medication_count} discharge medicines (5 or more)",
        "higher_readmission_diagnosis": "Diagnosis on the demo higher-readmission list",
        "no_caregiver_available": "No caregiver available",
    }
    reasons = [labels[k] for k, v in inputs.items() if v]
    points = len(reasons)
    category = "high" if points >= 3 else "medium" if points >= 1 else "low"
    return {"category": category, "reasons": reasons, "inputs": inputs, "points": points, "disclaimer": DISCLAIMER}
