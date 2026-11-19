#!/usr/bin/env bash
# Generate synthetic patients with Synthea (needs Java 11+).
# Usage: scripts/generate_patients.sh [num_patients] [seed]
set -euo pipefail
cd "$(dirname "$0")/.."

N="${1:-75}"
SEED="${2:-42}"
JAR="tools/synthea-with-dependencies.jar"
OUT="synthea_output"

mkdir -p tools
if [ ! -f "$JAR" ]; then
  echo "Downloading Synthea..."
  curl -fL -o "$JAR" \
    https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
fi

rm -rf "$OUT"
java -jar "$JAR" -p "$N" -s "$SEED" -cs "$SEED" \
  --exporter.baseDirectory "./$OUT" \
  --exporter.years_of_history 10 \
  --exporter.fhir.export true \
  --exporter.fhir.transaction_bundle true \
  --exporter.csv.export false \
  --exporter.ccda.export false \
  --exporter.hospital.fhir.export true \
  --exporter.practitioner.fhir.export true \
  Massachusetts

echo "Done. Bundles in $OUT/fhir/ ($(ls "$OUT/fhir" | wc -l | tr -d ' ') files)"
