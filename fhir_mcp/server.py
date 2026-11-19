"""MCP server exposing patient-record tools backed by a FHIR server.

Run standalone:  python -m fhir_mcp.server
Every tool call is appended to logs/mcp_tool_calls.jsonl with its latency.
"""
import functools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import fhir_client as fc
from .reference_ranges import flag

LOG = Path(__file__).resolve().parent.parent / "logs" / "mcp_tool_calls.jsonl"
LOG.parent.mkdir(exist_ok=True)

mcp = MCPServer(
    "fhir-records",
    instructions=(
        "Tools for reading synthetic patient records from a FHIR server. "
        "Call find_patient first to get a patient_id, then use the other tools."
    ),
)


@functools.lru_cache(maxsize=1024)
def _patient_exists(patient_id: str) -> bool:
    try:
        fc.read("Patient", patient_id)
        return True
    except Exception:
        return False


def logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        error = None
        try:
            # Searching by an unknown id silently returns nothing, which a model would
            # read as "patient has no medications". Small models also often pass the
            # patient's name here. Accept a name if it matches exactly one patient;
            # otherwise fail loudly.
            pid = kwargs.get("patient_id")
            if pid is not None and not _patient_exists(str(pid)):
                found = find_patient.__wrapped__(name=str(pid))["matches"]
                if len(found) == 1:
                    kwargs["patient_id"] = found[0]["patient_id"]
                elif found:
                    raise ValueError(
                        f"'{pid}' matches {len(found)} patients: "
                        + "; ".join(f"{m['name']} (id {m['patient_id']}, born {m['birth_date']})" for m in found)
                        + ". Ask which one, or pass the exact patient_id."
                    )
                else:
                    raise ValueError(
                        f"No patient with id or name '{pid}'. Call find_patient(name) to search."
                    )
            result = fn(*args, **kwargs)
            return result
        except Exception as e:  # report errors to the model instead of crashing
            error = f"{type(e).__name__}: {e}"
            return {"error": error}
        finally:
            with LOG.open("a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "tool": fn.__name__,
                    "args": kwargs or list(args),
                    "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                    "error": error,
                }) + "\n")
    return wrapper


@mcp.tool()
@logged
def find_patient(name: str) -> dict:
    """Find patients by name (first, last, or full name; partial names work).
    Returns matching patients with their patient_id, gender, birth date and age."""
    tokens = [t for t in name.replace(",", " ").split() if t]
    if not tokens:
        return {"matches": []}
    # Search on the first word, then keep patients whose name contains every word.
    candidates = fc.search("Patient", {"name": tokens[0]}, max_results=200)
    matches = []
    for p in candidates:
        full = fc.patient_name(p).lower()
        if all(t.lower() in full for t in tokens):
            matches.append({
                "patient_id": p["id"],
                "name": fc.patient_name(p),
                "gender": p.get("gender"),
                "birth_date": p.get("birthDate"),
                "age": fc.age(p.get("birthDate"), p.get("deceasedDateTime")),
                "deceased": bool(p.get("deceasedDateTime") or p.get("deceasedBoolean")),
            })
    return {"query": name, "count": len(matches), "matches": matches[:20]}


@mcp.tool()
@logged
def get_medications(patient_id: str, include_inactive: bool = False) -> dict:
    """List a patient's medications. By default only active prescriptions;
    set include_inactive=True to also see stopped/completed ones (medication history)."""
    reqs = fc.search("MedicationRequest", {"patient": patient_id, "_sort": "-authoredon"})
    seen: dict[str, dict] = {}
    for m in reqs:
        name = fc.medication_name(m)
        status = m.get("status")
        # Keep the most recent request per drug (results are sorted newest first).
        if name in seen:
            continue
        seen[name] = {
            "medication": name,
            "status": status,
            "authored_on": (m.get("authoredOn") or "")[:10],
            "reason": fc.concept_text((m.get("reasonCode") or [None])[0]) or None,
        }
    meds = list(seen.values())
    active = [m for m in meds if m["status"] == "active"]
    if include_inactive:
        return {"patient_id": patient_id, "active_count": len(active), "medications": meds}
    return {"patient_id": patient_id, "active_count": len(active), "active_medications": active}


@mcp.tool()
@logged
def get_conditions(patient_id: str, include_resolved: bool = False) -> dict:
    """List a patient's diagnoses/conditions. By default only active ones; set
    include_resolved=True for full history. '(disorder)' entries are diagnoses,
    '(finding)'/'(situation)' entries are social or administrative notes."""
    conds = fc.search("Condition", {"patient": patient_id, "_sort": "-onset-date"})
    out = []
    for c in conds:
        status = fc.concept_code(c.get("clinicalStatus")) or "unknown"
        if not include_resolved and status != "active":
            continue
        out.append({
            "condition": fc.concept_text(c.get("code")),
            "snomed": fc.concept_code(c.get("code"), "snomed"),
            "clinical_status": status,
            "onset": (c.get("onsetDateTime") or "")[:10],
            "resolved": (c.get("abatementDateTime") or "")[:10] or None,
        })
    return {"patient_id": patient_id, "count": len(out), "conditions": out}


@mcp.tool()
@logged
def get_lab_results(patient_id: str, test_name: str = "", latest_only: bool = True) -> dict:
    """Get a patient's lab results, each flagged HIGH / LOW / NORMAL against a reference
    range (UNKNOWN when no range is known). Optionally filter by test_name
    (e.g. 'glucose', 'A1c', 'cholesterol'). latest_only=True keeps only the most recent
    value per test; set it to False to see the trend over time."""
    obs = fc.search(
        "Observation",
        {"patient": patient_id, "category": "laboratory", "_sort": "-date"},
        max_results=1000,
    )
    needle = test_name.lower().strip()
    seen: set[str] = set()
    results = []
    for o in obs:
        name = fc.concept_text(o.get("code"))
        loinc = fc.concept_code(o.get("code"), "loinc")
        if needle and needle not in name.lower() and needle != loinc:
            continue
        vq = o.get("valueQuantity")
        if not vq:
            continue  # skip coded/text results (e.g. urinalysis colour)
        key = loinc or name
        if latest_only and key in seen:
            continue
        seen.add(key)
        value = vq.get("value")
        status, ref = flag(loinc, value, o)
        results.append({
            "test": name,
            "loinc": loinc,
            "value": round(value, 2) if isinstance(value, float) else value,
            "unit": vq.get("unit") or vq.get("code"),
            "date": (o.get("effectiveDateTime") or "")[:10],
            "flag": status,
            "reference_range": ref,
        })
    abnormal = [r for r in results if r["flag"] in ("HIGH", "LOW")]
    return {
        "patient_id": patient_id,
        "count": len(results),
        "abnormal_count": len(abnormal),
        "results": results[:150],
    }


@mcp.tool()
@logged
def get_patient_details(patient_id: str) -> dict:
    """Demographics for one patient: name, gender, birth date, age, address, deceased status."""
    p = fc.read("Patient", patient_id)
    addr = (p.get("address") or [{}])[0]
    return {
        "patient_id": p["id"],
        "name": fc.patient_name(p),
        "gender": p.get("gender"),
        "birth_date": p.get("birthDate"),
        "age": fc.age(p.get("birthDate"), p.get("deceasedDateTime")),
        "deceased_date": (p.get("deceasedDateTime") or "")[:10] or None,
        "city": addr.get("city"),
        "state": addr.get("state"),
        "marital_status": fc.concept_text(p.get("maritalStatus")) or None,
    }


if __name__ == "__main__":
    mcp.run()
