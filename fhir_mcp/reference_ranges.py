"""Adult reference ranges keyed by LOINC code, used to flag abnormal lab values.

Synthea rarely fills in Observation.referenceRange or .interpretation, so we fall
back to this table. Ranges are typical adult values, for demo purposes only.
"""

# loinc: (low, high, unit)
RANGES: dict[str, tuple[float, float, str]] = {
    "4548-4": (4.0, 5.6, "%"),            # Hemoglobin A1c
    "2339-0": (70, 99, "mg/dL"),          # Glucose
    "2345-7": (70, 99, "mg/dL"),          # Glucose, serum
    "2093-3": (0, 199, "mg/dL"),          # Total cholesterol
    "18262-6": (0, 99, "mg/dL"),          # LDL (direct)
    "2089-1": (0, 99, "mg/dL"),           # LDL
    "2085-9": (40, 1000, "mg/dL"),        # HDL (low is the concern)
    "2571-8": (0, 149, "mg/dL"),          # Triglycerides
    "38483-4": (0.6, 1.3, "mg/dL"),       # Creatinine
    "2160-0": (0.6, 1.3, "mg/dL"),        # Creatinine, serum
    "6299-2": (7, 20, "mg/dL"),           # Urea nitrogen
    "3094-0": (7, 20, "mg/dL"),           # BUN
    "2947-0": (136, 145, "mmol/L"),       # Sodium
    "2951-2": (136, 145, "mmol/L"),       # Sodium, serum
    "6298-4": (3.5, 5.1, "mmol/L"),       # Potassium
    "2823-3": (3.5, 5.1, "mmol/L"),       # Potassium, serum
    "2069-3": (98, 107, "mmol/L"),        # Chloride
    "2075-0": (98, 107, "mmol/L"),        # Chloride, serum
    "20565-8": (22, 29, "mmol/L"),        # CO2
    "2028-9": (22, 29, "mmol/L"),         # CO2, serum
    "49765-1": (8.5, 10.2, "mg/dL"),      # Calcium
    "17861-6": (8.5, 10.2, "mg/dL"),      # Calcium, serum
    "33914-3": (60, 1000, "mL/min/1.73m2"),  # eGFR
    "14959-1": (0, 29, "mg/g"),           # Microalbumin/creatinine ratio
    "718-7": (12.0, 17.5, "g/dL"),        # Hemoglobin
    "4544-3": (36, 50, "%"),              # Hematocrit
    "6690-2": (4.5, 11.0, "10*3/uL"),     # WBC
    "789-8": (4.2, 5.9, "10*6/uL"),       # RBC
    "777-3": (150, 450, "10*3/uL"),       # Platelets
    "787-2": (80, 100, "fL"),             # MCV
}


def flag(loinc: str, value: float | None, observation: dict | None = None) -> tuple[str, str | None]:
    """Return (flag, range_text). flag is HIGH, LOW, NORMAL or UNKNOWN."""
    if value is None:
        return "UNKNOWN", None

    # Prefer what the record itself says.
    if observation:
        for interp in observation.get("interpretation", []):
            code = (interp.get("coding") or [{}])[0].get("code", "")
            if code in ("H", "HH", "HU"):
                return "HIGH", None
            if code in ("L", "LL", "LU"):
                return "LOW", None
            if code == "N":
                return "NORMAL", None
        rr = (observation.get("referenceRange") or [None])[0]
        if rr and ("low" in rr or "high" in rr):
            lo = rr.get("low", {}).get("value")
            hi = rr.get("high", {}).get("value")
            text = f"{lo if lo is not None else ''}-{hi if hi is not None else ''}"
            if lo is not None and value < lo:
                return "LOW", text
            if hi is not None and value > hi:
                return "HIGH", text
            return "NORMAL", text

    if loinc not in RANGES:
        return "UNKNOWN", None
    lo, hi, unit = RANGES[loinc]
    text = f"{lo}-{hi} {unit}" if hi < 1000 else f">= {lo} {unit}"
    if value < lo:
        return "LOW", text
    if value > hi:
        return "HIGH", text
    return "NORMAL", text
