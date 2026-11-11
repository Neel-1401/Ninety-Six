import logging

import httpx
from sqlmodel import select

from ..config import settings
from ..memory import remember
from ..models import Medication
from .base import Tool, ToolContext

log = logging.getLogger("ninety_six.tools.drug_info")

# Small offline knowledge base so the demo works without network access.
LOCAL_WARNINGS = {
    "furosemide": "Can cause dehydration and low potassium; watch for dizziness and cramps.",
    "spironolactone": "Can raise potassium; regular blood tests needed.",
    "lisinopril": "Can raise potassium and cause dry cough or dizziness.",
    "carvedilol": "Take with food; may cause dizziness or slow heart rate.",
    "aspirin": "Increases bleeding risk; take after food.",
    "warfarin": "Bleeding risk; many food and drug interactions; INR monitoring needed.",
    "metformin": "Take with meals; stop and seek advice if vomiting or dehydrated.",
    "insulin glargine": "Risk of low blood sugar; check sugar before bed.",
    "atorvastatin": "Report unexplained muscle pain.",
    "paracetamol": "Do not exceed 4 g per day; avoid other paracetamol-containing products.",
    "cefuroxime": "Complete the full course; report rash or severe diarrhoea.",
    "pantoprazole": "Take 30 minutes before breakfast.",
    "clopidogrel": "Bleeding risk; do not stop without cardiologist advice.",
}

INTERACTIONS = [
    ({"lisinopril", "spironolactone"}, "High potassium risk (ACE inhibitor + potassium-sparing diuretic). Check electrolytes within 3 days."),
    ({"aspirin", "warfarin"}, "Major bleeding risk when combined."),
    ({"clopidogrel", "omeprazole"}, "Omeprazole may reduce clopidogrel effect."),
    ({"furosemide", "lisinopril"}, "Additive low blood pressure on standing; advise slow position changes."),
]


def _openfda_warning(name: str) -> str:
    try:
        r = httpx.get(
            "https://api.fda.gov/drug/label.json",
            params={"search": f'openfda.generic_name:"{name}"', "limit": 1},
            timeout=4,
        )
        if r.status_code != 200:
            return ""
        res = r.json().get("results", [{}])[0]
        for field in ("boxed_warning", "warnings_and_cautions", "warnings"):
            if res.get(field):
                return " ".join(res[field])[:280].strip() + "…"
    except Exception as exc:
        log.info("openFDA lookup failed for %s: %s", name, exc)
    return ""


class LookupDrugInfoTool(Tool):
    name = "lookup_drug_info"
    description = "Look up label warnings (openFDA) and check interactions across the discharge medications."
    args_doc = '{"medications": ["furosemide", "lisinopril"]}'
    irreversible = False

    def live(self) -> bool:
        return settings.openfda_enabled

    def run(self, ctx: ToolContext, args: dict) -> dict:
        meds = ctx.session.exec(select(Medication).where(Medication.patient_id == ctx.patient.id)).all()
        names = [n.lower() for n in (args.get("medications") or [m.name for m in meds])]
        drugs = []
        for med in meds:
            key = med.name.lower()
            if key not in names:
                continue
            warning, source = "", "local"
            if settings.openfda_enabled:
                warning = _openfda_warning(key.split()[0])
                source = "openFDA" if warning else "local"
            warning = warning or LOCAL_WARNINGS.get(key, LOCAL_WARNINGS.get(key.split()[0], ""))
            med.warnings = warning
            ctx.session.add(med)
            drugs.append({"name": med.name, "source": source, "warning": warning})
        ctx.session.commit()

        base_names = {n.split()[0] for n in names} | set(names)
        interactions = [msg for pair, msg in INTERACTIONS if pair <= base_names]

        followups = []
        if interactions:
            remember(ctx.session, ctx.patient.id, "medication_alerts", "; ".join(interactions), "tool:lookup_drug_info")
            followups.append({
                "title": "Ask pharmacist to review medication interaction",
                "tool": "escalate_to_care_team",
                "args": {"urgency": "normal", "reason": "Medication interaction flagged", "details": " ".join(interactions)},
                "rationale": "Interaction found during medication review — a clinician should confirm the regimen.",
            })
        return {"drugs": drugs, "interactions": interactions, "followups": followups}
