"""Check-in question templates (Agent B).

These are FICTIONAL demo templates, presented as a pre-written library that the doctor reviews and
accepts when activating the plan (the CheckIn stores a snapshot). The agent never invents new
clinical questions at runtime. Each answer maps deterministically to a triage level:
  urgent  -> a discharge red flag the summary told the patient to report
  concern -> needs a nurse follow-up
"""
from __future__ import annotations

from typing import Any, Optional

YES_NO = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
TREND = [
    {"value": "better", "label": "Better"},
    {"value": "same", "label": "About the same"},
    {"value": "worse", "label": "Worse"},
]
MEDS = [
    {"value": "yes", "label": "Yes, all of them"},
    {"value": "missed", "label": "I missed some"},
    {"value": "not_sure", "label": "I'm not sure"},
]


def q(qid: str, text: str, options: list[dict], *, required: bool = True, help: Optional[str] = None,
      qtype: str = "single_choice") -> dict[str, Any]:
    return {"id": qid, "text": text, "type": qtype, "options": options, "required": required, "help": help}


TEMPLATES: dict[str, dict[str, Any]] = {
    "heart_failure_v1": {
        "version": 1,
        "label": "Heart failure daily check-in",
        "questions": [
            q("breathing_trend", "Compared with yesterday, how is your breathing?", TREND),
            q("breathless_flat", "Do you feel breathless when lying flat or when resting?", YES_NO),
            q("weight_gain", "Has your weight gone up by more than 2 kg in the last 2 days?",
              YES_NO + [{"value": "not_weighed", "label": "I haven't weighed myself"}]),
            q("swelling", "Is there new or worse swelling in your legs or ankles?", YES_NO),
            q("chest_pain", "Have you had chest pain or fainted since your last check-in?", YES_NO),
            q("medicines", "Have you taken your medicines as prescribed today?", MEDS),
        ],
        "rules": {
            ("breathing_trend", "worse"): ("concern", "Breathing worse than yesterday"),
            ("breathless_flat", "yes"): ("urgent", "Breathlessness at rest or lying flat (discharge red flag)"),
            ("weight_gain", "yes"): ("urgent", "Weight gain over 2 kg in 2 days (discharge red flag)"),
            ("weight_gain", "not_weighed"): ("concern", "Daily weight not recorded"),
            ("swelling", "yes"): ("concern", "New or worse leg/ankle swelling (discharge warning sign)"),
            ("chest_pain", "yes"): ("urgent", "Chest pain or fainting (discharge red flag)"),
            ("medicines", "missed"): ("concern", "Missed medicine doses"),
            ("medicines", "not_sure"): ("concern", "Unsure about medicines"),
        },
        "presets": {
            "all_good": {"breathing_trend": "better", "breathless_flat": "no", "weight_gain": "no",
                         "swelling": "no", "chest_pain": "no", "medicines": "yes"},
            "concern": {"breathing_trend": "same", "breathless_flat": "no", "weight_gain": "no",
                        "swelling": "yes", "chest_pain": "no", "medicines": "missed"},
            "red_flag": {"breathing_trend": "worse", "breathless_flat": "yes", "weight_gain": "no",
                         "swelling": "yes", "chest_pain": "no", "medicines": "yes"},
        },
        "preset_text": {"all_good": None, "concern": "I forgot my evening tablet yesterday and I am a bit worried.",
                        "red_flag": "I get breathless when I lie down at night."},
    },
    "post_surgery_v1": {
        "version": 1,
        "label": "After-surgery check-in",
        "questions": [
            q("pain_trend", "Compared with yesterday, how is your pain?", TREND),
            q("fever", "Have you had a temperature above 101°F (38.3°C)?", YES_NO),
            q("wound", "Is there redness, pus or discharge from the wound?", YES_NO),
            q("vomiting", "Have you had severe tummy pain or repeated vomiting?", YES_NO),
            q("medicines", "Have you taken your medicines as prescribed today?", MEDS),
        ],
        "rules": {
            ("pain_trend", "worse"): ("concern", "Pain worse than yesterday"),
            ("fever", "yes"): ("urgent", "Fever above 101°F (discharge red flag)"),
            ("wound", "yes"): ("concern", "Wound redness or discharge (discharge warning sign)"),
            ("vomiting", "yes"): ("urgent", "Severe abdominal pain or vomiting (discharge red flag)"),
            ("medicines", "missed"): ("concern", "Missed medicine doses"),
            ("medicines", "not_sure"): ("concern", "Unsure about medicines"),
        },
        "presets": {
            "all_good": {"pain_trend": "better", "fever": "no", "wound": "no", "vomiting": "no", "medicines": "yes"},
            "concern": {"pain_trend": "same", "fever": "no", "wound": "yes", "vomiting": "no", "medicines": "yes"},
            "red_flag": {"pain_trend": "worse", "fever": "yes", "wound": "yes", "vomiting": "no", "medicines": "yes"},
        },
        "preset_text": {"all_good": None, "concern": "The wound looks a little red.", "red_flag": "I feel hot and shivery."},
    },
    "diabetes_v1": {
        "version": 1,
        "label": "Diabetes check-in",
        "questions": [
            q("sugar_high", "Has your blood sugar been above 300 mg/dL?",
              YES_NO + [{"value": "not_checked", "label": "I haven't checked"}]),
            q("vomiting", "Are you vomiting or unable to keep fluids down?", YES_NO),
            q("drowsy", "Do you feel confused or unusually drowsy?", YES_NO),
            q("insulin", "Have you taken your insulin and tablets as prescribed?", MEDS),
            q("eating", "Are you able to eat and drink normally?", YES_NO),
        ],
        "rules": {
            ("sugar_high", "yes"): ("urgent", "Blood sugar above 300 mg/dL (discharge red flag)"),
            ("sugar_high", "not_checked"): ("concern", "Blood sugar not checked"),
            ("vomiting", "yes"): ("urgent", "Vomiting / unable to keep fluids down (discharge red flag)"),
            ("drowsy", "yes"): ("urgent", "Confusion or drowsiness (discharge red flag)"),
            ("insulin", "missed"): ("concern", "Missed insulin or tablets"),
            ("insulin", "not_sure"): ("concern", "Unsure about insulin or tablets"),
            ("eating", "no"): ("concern", "Not eating or drinking normally"),
        },
        "presets": {
            "all_good": {"sugar_high": "no", "vomiting": "no", "drowsy": "no", "insulin": "yes", "eating": "yes"},
            "concern": {"sugar_high": "not_checked", "vomiting": "no", "drowsy": "no", "insulin": "missed", "eating": "yes"},
            "red_flag": {"sugar_high": "yes", "vomiting": "yes", "drowsy": "no", "insulin": "yes", "eating": "no"},
        },
        "preset_text": {"all_good": None, "concern": "Forgot my night insulin.", "red_flag": "I keep throwing up."},
    },
    "general_v1": {
        "version": 1,
        "label": "General recovery check-in",
        "questions": [
            q("overall", "Compared with yesterday, how do you feel?", TREND),
            q("new_symptoms", "Do you have any of the warning signs listed in your discharge instructions?", YES_NO),
            q("medicines", "Have you taken your medicines as prescribed today?", MEDS),
        ],
        "rules": {
            ("overall", "worse"): ("concern", "Feeling worse than yesterday"),
            ("new_symptoms", "yes"): ("urgent", "Patient reports a discharge warning sign"),
            ("medicines", "missed"): ("concern", "Missed medicine doses"),
            ("medicines", "not_sure"): ("concern", "Unsure about medicines"),
        },
        "presets": {
            "all_good": {"overall": "better", "new_symptoms": "no", "medicines": "yes"},
            "concern": {"overall": "worse", "new_symptoms": "no", "medicines": "missed"},
            "red_flag": {"overall": "worse", "new_symptoms": "yes", "medicines": "yes"},
        },
        "preset_text": {"all_good": None, "concern": "Feeling a bit worse.", "red_flag": "I have the warning signs."},
    },
}


def choose_template(diagnosis_text: str) -> str:
    d = diagnosis_text.lower()
    if "heart failure" in d or "cardiac" in d:
        return "heart_failure_v1"
    if "ectomy" in d or "surgery" in d or "operative" in d or "laparoscopic" in d:
        return "post_surgery_v1"
    if "diabet" in d or "dka" in d or "ketoacidosis" in d:
        return "diabetes_v1"
    return "general_v1"


def questions_for(template_id: str) -> list[dict[str, Any]]:
    return [dict(x) for x in TEMPLATES[template_id]["questions"]]


def rules_for(template_id: str) -> dict:
    return TEMPLATES.get(template_id, TEMPLATES["general_v1"])["rules"]


def preset_answers(template_id: Optional[str], questions: list[dict[str, Any]], preset: str):
    tpl = TEMPLATES.get(template_id or "", None)
    if preset not in ("all_good", "concern", "red_flag"):
        raise ValueError("preset must be all_good, concern or red_flag")
    if tpl is None:  # ad-hoc check-in without a template: just free text
        return [], {"all_good": "Feeling fine today.", "concern": "I'm a bit worried.",
                    "red_flag": "I get breathless when I lie down."}[preset]
    chosen = tpl["presets"][preset]
    answers = []
    for qq in questions:
        if qq["id"] in chosen:
            answers.append({"question_id": qq["id"], "value": chosen[qq["id"]]})
    return answers, tpl["preset_text"].get(preset)
