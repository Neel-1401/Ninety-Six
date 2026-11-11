"""Fictional sample discharge summaries (Agent B). No real patient data.

Shape matches `GET /api/samples` (CONTRACTS.md §4.2). Phone numbers are fictional; live sends are
refused to anything except DEMO_PHONE. The heart-failure case is the primary tested demo.
"""
from __future__ import annotations

from typing import Any

HEART_FAILURE_SUMMARY = """DISCHARGE SUMMARY - Sunrise Multispeciality Hospital (FICTIONAL RECORD)
Diagnosis: Acute decompensated heart failure (NYHA III)
Allergies: Sulfa drugs
Social: Lives alone; daughter visits on weekends. Previous admission in June for the same condition.
Medications:
- Furosemide 40 mg once daily in the morning
- Carvedilol 6.25 mg twice daily - take with food
- Lisinopril 5 mg once daily
- Spironolactone 25 mg once daily
- Aspirin 75 mg once daily after lunch
Follow-up:
- Blood test for electrolytes and creatinine within 3 days (Hospital lab)
- Cardiology clinic review within 7 days (Dr. Mehta)
Red flags:
- Weight gain of more than 2 kg in 2 days
- Breathlessness at rest or when lying flat
- Swelling in the legs or ankles
- Chest pain or fainting
Instructions:
- Weigh yourself every morning before breakfast and write it down
- Limit fluids to 1.5 litres a day
"""

APPENDECTOMY_SUMMARY = """DISCHARGE SUMMARY - Sunrise Multispeciality Hospital (FICTIONAL RECORD)
Diagnosis: Acute appendicitis, laparoscopic appendectomy (post-operative day 2)
Allergies: None known
Medications:
- Paracetamol 650 mg every 6 hours as needed for pain
- Cefuroxime 500 mg twice daily for 5 days - complete the course
- Pantoprazole 40 mg once daily before breakfast
Follow-up:
- Surgical clinic wound check within 10 days (Dr. Iyer)
Red flags:
- Fever above 101 F
- Redness, pus or discharge from the wound
- Severe abdominal pain or vomiting
Instructions:
- Keep the wound dry for 48 hours
- No heavy lifting for 2 weeks
"""

# Deliberately missing a dose and the allergy line: shows "not provided" handling.
DIABETES_SUMMARY = """DISCHARGE SUMMARY - Sunrise Multispeciality Hospital (FICTIONAL RECORD)
Diagnosis: Type 2 diabetes with diabetic ketoacidosis (DKA), resolved
Medications:
- Insulin glargine 18 units at bedtime
- Metformin 500 mg twice daily - with meals
- Atorvastatin once daily at night
Follow-up:
- Endocrinology review within 5 days (Dr. Shetty)
Red flags:
- Blood sugar above 300 mg/dL
- Vomiting or unable to keep fluids down
- Confusion or drowsiness
- Fruity-smelling breath
"""

SAMPLES: list[dict[str, Any]] = [
    {
        "key": "heart-failure",
        "label": "Heart failure, 68 M (primary demo, high priority)",
        "primary": True,
        "patient": {
            "full_name": "Ramesh Kumar",
            "age": 68,
            "sex": "M",
            "phone_e164": "+919000000001",
            "preferred_language": "English",
            "caregiver_name": "Priya Kumar",
            "caregiver_relation": "Daughter",
            "caregiver_phone_e164": "+919000000011",
            "caregiver_authorised": True,
            "hospital_number": "SMH-DEMO-1001",
        },
        "risk_inputs": {"lives_alone": True, "prior_admission_6m": True, "caregiver_available": True},
        "discharge_hours_ago": 2,
        "summary_text": HEART_FAILURE_SUMMARY,
        "suggested_doctor_username": "dr.mehta",
    },
    {
        "key": "appendectomy",
        "label": "Appendectomy, 34 F (low priority)",
        "primary": False,
        "patient": {
            "full_name": "Ananya Rao",
            "age": 34,
            "sex": "F",
            "phone_e164": "+919000000002",
            "preferred_language": "English",
            "caregiver_name": None,
            "caregiver_relation": None,
            "caregiver_phone_e164": None,
            "caregiver_authorised": False,
            "hospital_number": "SMH-DEMO-1002",
        },
        "risk_inputs": {"lives_alone": False, "prior_admission_6m": False, "caregiver_available": False},
        "discharge_hours_ago": 20,
        "summary_text": APPENDECTOMY_SUMMARY,
        "suggested_doctor_username": "dr.iyer",
    },
    {
        "key": "diabetes",
        "label": "Diabetes / DKA, 59 M (medium priority; some fields not provided)",
        "primary": False,
        "patient": {
            "full_name": "Joseph D'Souza",
            "age": 59,
            "sex": "M",
            "phone_e164": "+919000000003",
            # With the AI on, the welcome message and concern/red-flag replies are translated (English
            # original shown in the approval card, doctor approves). Routine reminders stay English templates.
            "preferred_language": "Kannada",
            "caregiver_name": "Maria D'Souza",
            "caregiver_relation": "Wife",
            "caregiver_phone_e164": "+919000000013",
            "caregiver_authorised": False,
            "hospital_number": "SMH-DEMO-1003",
        },
        "risk_inputs": {"lives_alone": False, "prior_admission_6m": False, "caregiver_available": True},
        "discharge_hours_ago": 6,
        "summary_text": DIABETES_SUMMARY,
        "suggested_doctor_username": "dr.mehta",
    },
]


def get_sample(key: str) -> dict[str, Any] | None:
    return next((s for s in SAMPLES if s["key"] == key), None)
