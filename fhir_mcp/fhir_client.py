"""Thin FHIR REST client plus helpers for turning resources into plain dicts."""
import functools
import logging
import os
from datetime import date

import httpx

BASE = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")
logging.getLogger("httpx").setLevel(logging.WARNING)
_client = httpx.Client(base_url=BASE, timeout=60, headers={"Accept": "application/fhir+json", "Cache-Control": "no-cache"})


def search(resource: str, params: dict | None = None, max_results: int = 500) -> list[dict]:
    """Run a FHIR search and follow paging links. Returns the matched resources."""
    out: list[dict] = []
    r = _client.get(f"/{resource}", params={"_count": 200, **(params or {})})
    while True:
        r.raise_for_status()
        bundle = r.json()
        for e in bundle.get("entry", []):
            if e.get("search", {}).get("mode", "match") == "match":
                out.append(e["resource"])
        nxt = next((l["url"] for l in bundle.get("link", []) if l["relation"] == "next"), None)
        if not nxt or len(out) >= max_results:
            return out[:max_results]
        r = _client.get(nxt)


def read(resource: str, rid: str) -> dict:
    r = _client.get(f"/{resource}/{rid}")
    r.raise_for_status()
    return r.json()


def concept_text(cc: dict | None) -> str:
    if not cc:
        return ""
    if cc.get("text"):
        return cc["text"]
    codings = cc.get("coding") or []
    return codings[0].get("display", codings[0].get("code", "")) if codings else ""


def concept_code(cc: dict | None, system_hint: str = "") -> str:
    for c in (cc or {}).get("coding", []):
        if system_hint in c.get("system", ""):
            return c.get("code", "")
    return ""


def patient_name(p: dict) -> str:
    names = p.get("name") or []
    n = next((x for x in names if x.get("use") == "official"), names[0] if names else {})
    return " ".join(n.get("given", []) + [n.get("family", "")]).strip()


def age(birth: str | None, death: str | None = None) -> int | None:
    if not birth:
        return None
    b = date.fromisoformat(birth[:10])
    end = date.fromisoformat(death[:10]) if death else date.today()
    return end.year - b.year - ((end.month, end.day) < (b.month, b.day))


@functools.lru_cache(maxsize=2048)
def _medication_by_ref(ref: str) -> str:
    resource, rid = ref.split("/", 1)
    return concept_text(read(resource, rid).get("code"))


def medication_name(req: dict) -> str:
    """Drug name from a MedicationRequest, whether it is coded inline or points at a
    separate Medication resource (Synthea does both)."""
    if req.get("medicationCodeableConcept"):
        return concept_text(req["medicationCodeableConcept"])
    ref = (req.get("medicationReference") or {}).get("reference", "")
    if "/" in ref:
        try:
            return _medication_by_ref(ref)
        except httpx.HTTPError:
            pass
    return (req.get("medicationReference") or {}).get("display", "") or "unknown medication"
