"""Load Synthea FHIR transaction bundles into the HAPI FHIR server.

Organizations and practitioners go first, because patient bundles point at them.
Usage: python scripts/load_patients.py [synthea_output/fhir]
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")


def drop_null_identifier_refs(node) -> int:
    """Synthea sometimes writes conditional references like
    'Location?identifier=...|null' that can never resolve, so HAPI rejects the whole
    bundle. Remove those references and keep their display text."""
    removed = 0
    if isinstance(node, dict):
        ref = node.get("reference")
        if isinstance(ref, str) and "?identifier=" in ref and ref.endswith("|null"):
            del node["reference"]
            removed += 1
        for v in node.values():
            removed += drop_null_identifier_refs(v)
    elif isinstance(node, list):
        for v in node:
            removed += drop_null_identifier_refs(v)
    return removed


def load(path: Path, client: httpx.Client) -> tuple[bool, str]:
    bundle = json.loads(path.read_bytes())
    drop_null_identifier_refs(bundle)
    r = client.post(
        BASE,
        content=json.dumps(bundle),
        headers={"Content-Type": "application/fhir+json"},
    )
    if r.status_code >= 300:
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    return True, ""


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "synthea_output/fhir")
    files = [target] if target.is_file() else sorted(target.glob("*.json"))
    if not files:
        sys.exit(f"No bundles in {target}. Run scripts/generate_patients.sh first.")
    shared = [f for f in files if f.name.startswith(("hospitalInformation", "practitionerInformation"))]
    patients = [f for f in files if f not in shared]

    ok = failed = 0
    start = time.time()
    with httpx.Client(timeout=600) as client:
        for i, f in enumerate(shared + patients, 1):
            success, err = load(f, client)
            if success:
                ok += 1
            else:
                failed += 1
                print(f"  FAILED {f.name}: {err}")
            print(f"[{i}/{len(shared) + len(patients)}] {f.name} {'ok' if success else 'FAILED'}", flush=True)

    print(f"\nLoaded {ok} bundles, {failed} failed, in {time.time() - start:.0f}s")
    total = httpx.get(f"{BASE}/Patient", params={"_summary": "count"}, timeout=60).json().get("total")
    print(f"Patients on server: {total}")


if __name__ == "__main__":
    main()
