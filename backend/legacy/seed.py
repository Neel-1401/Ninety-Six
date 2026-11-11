"""Fictional sample discharge summaries for the demo. No real patient data."""

SAMPLES = [
    {
        "key": "heart-failure",
        "label": "Heart failure, 68 M (high risk)",
        "name": "Ramesh Kumar", "age": 68, "sex": "M", "phone": "+919000000001", "language": "English",
        "caregiver_name": "Priya (daughter)", "caregiver_phone": "+919000000011",
        "summary_text": """DISCHARGE SUMMARY
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
- Cardiology clinic review within 7 days (Dr. Mehta)
- Blood test for electrolytes and creatinine within 3 days (Hospital lab)
Red flags:
- Weight gain of more than 2 kg in 2 days
- Breathlessness at rest or when lying flat
- Swelling in the legs or ankles
- Chest pain or fainting
""",
    },
    {
        "key": "appendectomy",
        "label": "Appendectomy, 34 F (low risk)",
        "name": "Ananya Rao", "age": 34, "sex": "F", "phone": "+919000000002", "language": "English",
        "caregiver_name": "", "caregiver_phone": "",
        "summary_text": """DISCHARGE SUMMARY
Diagnosis: Acute appendicitis, laparoscopic appendectomy (day 2)
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
""",
    },
    {
        "key": "diabetes",
        "label": "Diabetes / DKA, 59 M (medium risk)",
        "name": "Joseph D'Souza", "age": 59, "sex": "M", "phone": "+919000000003", "language": "Kannada",
        "caregiver_name": "Maria (wife)", "caregiver_phone": "+919000000013",
        "summary_text": """DISCHARGE SUMMARY
Diagnosis: Type 2 diabetes with diabetic ketoacidosis (DKA), resolved
Allergies: Penicillin
Medications:
- Insulin glargine 18 units at bedtime
- Metformin 500 mg twice daily - with meals
- Atorvastatin 20 mg once daily at night
Follow-up:
- Endocrinology review within 5 days (Dr. Shetty)
Red flags:
- Blood sugar above 300 mg/dL
- Vomiting or unable to keep fluids down
- Confusion or drowsiness
- Fruity-smelling breath
""",
    },
]
