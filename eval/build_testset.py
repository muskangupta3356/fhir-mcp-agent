"""Build a 20-question test set with known answers from the loaded patients.

Ground truth is read straight from the FHIR server with raw queries, not through
the MCP tools, so a bug in a tool shows up as a wrong answer instead of being hidden.
Output: eval/testset.json
"""
import json
import random
from pathlib import Path

from fhir_mcp import fhir_client as fc
from fhir_mcp.reference_ranges import RANGES, flag

OUT = Path(__file__).parent / "testset.json"
random.seed(7)

SKIP_WORDS = {"hr", "mg", "ml", "oral", "tablet", "capsule", "extended", "release",
              "actuat", "injectable", "solution", "product", "pack", "day",
              "sodium", "potassium", "calcium", "chloride", "hydrochloride", "hcl", "sulfate"}
NOT_PRESENT_CONDITIONS = ["asthma", "hypertension", "diabetes", "epilepsy", "chronic kidney disease",
                          "atrial fibrillation", "osteoporosis", "heart failure"]
NOT_PRESCRIBED_MEDS = ["warfarin", "insulin", "atorvastatin", "metformin", "lisinopril",
                       "albuterol", "levothyroxine", "sertraline"]


def key_term(med: str) -> str:
    """The word that identifies a drug, e.g. 'lisinopril' from 'lisinopril 10 MG Oral Tablet'."""
    for w in med.replace("[", " ").replace("]", " ").replace("/", " ").split():
        w = w.strip(",.()").lower()
        if w.isalpha() and len(w) > 3 and w not in SKIP_WORDS:
            return w
    return med.split()[0].lower()


def clean_condition(name: str) -> str:
    return name.rsplit(" (", 1)[0]


def patient_facts(p: dict) -> dict:
    pid = p["id"]
    meds = fc.search("MedicationRequest", {"patient": pid, "_sort": "-authoredon"})
    latest_status: dict[str, str] = {}
    for m in meds:
        latest_status.setdefault(fc.medication_name(m), m.get("status"))
    conds = fc.search("Condition", {"patient": pid})
    labs = fc.search("Observation", {"patient": pid, "category": "laboratory", "_sort": "-date"},
                     max_results=1000)
    latest_lab: dict[str, dict] = {}
    for o in labs:
        loinc = fc.concept_code(o.get("code"), "loinc")
        if loinc in RANGES and "valueQuantity" in o and loinc not in latest_lab:
            latest_lab[loinc] = o
    return {
        "id": pid,
        "name": fc.patient_name(p),
        "birth_year": p.get("birthDate", "")[:4],
        "active_meds": [m for m, s in latest_status.items() if s == "active"],
        "inactive_meds": [m for m, s in latest_status.items() if s != "active"],
        "all_med_text": " ".join(latest_status).lower(),
        "active_disorders": [fc.concept_text(c["code"]) for c in conds
                             if fc.concept_code(c.get("clinicalStatus")) == "active"
                             and "(disorder)" in fc.concept_text(c["code"])],
        "all_condition_text": " ".join(fc.concept_text(c["code"]) for c in conds).lower(),
        "labs": latest_lab,
    }


def main() -> None:
    patients = [p for p in fc.search("Patient", {}, max_results=1000)
                if not p.get("deceasedDateTime")]
    # Names must be unique so each question has exactly one right patient.
    names = [fc.patient_name(p) for p in patients]
    patients = [p for p in patients if names.count(fc.patient_name(p)) == 1]
    random.shuffle(patients)
    print(f"Scanning {len(patients)} living patients...")

    facts = [patient_facts(p) for p in patients]
    questions: list[dict] = []
    used: set[str] = set()

    def pick(pred, n):
        chosen = [f for f in facts if f["id"] not in used and pred(f)][:n]
        used.update(f["id"] for f in chosen)
        return chosen

    for f in pick(lambda f: 1 <= len(f["active_meds"]) <= 4, 4):
        questions.append({
            "category": "active_medications",
            "question": f"What medications is {f['name']} currently taking?",
            "expected": [key_term(m) for m in f["active_meds"]],
            "grading": "all_terms",
            "notes": f["active_meds"],
        })

    for f in pick(lambda f: f["active_disorders"], 3):
        cond = clean_condition(random.choice(f["active_disorders"]))
        questions.append({
            "category": "condition_yes_no",
            "question": f"Does {f['name']} currently have {cond.lower()}?",
            "expected": "yes", "grading": "yes_no",
        })
    for f in pick(lambda f: any(c not in f["all_condition_text"] for c in NOT_PRESENT_CONDITIONS), 2):
        cond = next(c for c in NOT_PRESENT_CONDITIONS if c not in f["all_condition_text"])
        questions.append({
            "category": "condition_yes_no",
            "question": f"Does {f['name']} have {cond}?",
            "expected": "no", "grading": "yes_no",
        })

    for f in pick(lambda f: f["labs"], 4):
        o = random.choice(list(f["labs"].values()))
        questions.append({
            "category": "lab_value",
            "question": f"What was {f['name']}'s most recent {fc.concept_text(o['code'])} result?",
            "expected": round(o["valueQuantity"]["value"], 2), "grading": "number",
        })

    # Mix of abnormal and normal results.
    for want in ("abnormal", "abnormal", "normal"):
        def has(f, want=want):
            return any((flag(l, o["valueQuantity"]["value"], o)[0] in ("HIGH", "LOW")) == (want == "abnormal")
                       for l, o in f["labs"].items())
        for f in pick(has, 1):
            loinc, o = next((l, o) for l, o in f["labs"].items()
                            if (flag(l, o["valueQuantity"]["value"], o)[0] in ("HIGH", "LOW")) == (want == "abnormal"))
            questions.append({
                "category": "lab_flag",
                "question": f"Was {f['name']}'s most recent {fc.concept_text(o['code'])} high, low, or normal?",
                "expected": flag(loinc, o["valueQuantity"]["value"], o)[0].lower(), "grading": "first_of",
                "options": ["high", "low", "normal"],
            })

    for f in pick(lambda f: f["inactive_meds"], 1):
        med = key_term(random.choice(f["inactive_meds"]))
        questions.append({
            "category": "med_history",
            "question": f"Has {f['name']} ever been prescribed {med}?",
            "expected": "yes", "grading": "yes_no",
        })
    for f in pick(lambda f: any(m not in f["all_med_text"] for m in NOT_PRESCRIBED_MEDS), 1):
        med = next(m for m in NOT_PRESCRIBED_MEDS if m not in f["all_med_text"])
        questions.append({
            "category": "med_history",
            "question": f"Has {f['name']} ever been prescribed {med}?",
            "expected": "no", "grading": "yes_no",
        })

    for f in pick(lambda f: True, 2):
        questions.append({
            "category": "demographics",
            "question": f"What year was {f['name']} born?",
            "expected": f["birth_year"], "grading": "contains",
        })

    for i, q in enumerate(questions, 1):
        q["id"] = i
    OUT.write_text(json.dumps(questions, indent=2))
    print(f"Wrote {len(questions)} questions to {OUT}")


if __name__ == "__main__":
    main()
