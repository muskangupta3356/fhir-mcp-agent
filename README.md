# FHIR MCP Agent

An AI agent that answers questions about patient records. It runs over a real
FHIR server filled with synthetic patients, and every request to the records goes
through an MCP server.

```
question ─► LLM agent (Qwen 2.5 7B local, or GPT-4o-mini) ─(MCP, stdio)─► fhir_mcp server ─(REST)─► HAPI FHIR (Docker)
                                                                          ▲
                                                Synthea fake patients ────┘
```

| Part | What it is | Where |
|---|---|---|
| Records server | HAPI FHIR R4 in Docker | `docker-compose.yml` |
| Fake patients | 75 Synthea patients, 10 years of history, seed 42 | `scripts/generate_patients.sh`, `scripts/load_patients.py` |
| MCP server | 5 tools over FHIR | `fhir_mcp/server.py` |
| Agent | Any OpenAI-compatible model with function calling; runs on Qwen 2.5 7B via Ollama by default | `agent/agent.py`, `agent/cli.py` |
| Evaluation | 20 questions with known answers, accuracy and latency report | `eval/` |

No real patient data is used anywhere.

## MCP tools

- `find_patient(name)`: partial or full name, returns `patient_id`, age, gender
- `get_medications(patient_id, include_inactive=False)`
- `get_lab_results(patient_id, test_name="", latest_only=True)`: each value flagged
  `HIGH` / `LOW` / `NORMAL`, using the record's own reference range if it has one, otherwise
  the adult reference ranges in `fhir_mcp/reference_ranges.py`
- `get_conditions(patient_id, include_resolved=False)`
- `get_patient_details(patient_id)`

## Setup

Needs Docker, Java 11+ (for Synthea) and Python 3.11+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env              # pick a model, see below

docker compose up -d              # FHIR server on http://localhost:8080/fhir
scripts/generate_patients.sh 75   # Synthea, about 1 minute
.venv/bin/python scripts/load_patients.py   # a few minutes
```

## Choosing the model

The agent talks to any OpenAI-compatible API, so switching models is a `.env` change.

Local and free (default here), with [Ollama](https://ollama.com):

```bash
brew install ollama && brew services start ollama
ollama pull qwen2.5:7b
ollama create qwen2.5-7b-16k -f Modelfile.qwen   # 16k context, temperature 0
```
```
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
AGENT_MODEL=qwen2.5-7b-16k
```

GPT-4o-mini: remove `OPENAI_BASE_URL`, set a real `OPENAI_API_KEY` and `AGENT_MODEL=gpt-4o-mini`.

## Use it

```bash
.venv/bin/python -m agent.cli "What medications is Adela471 Romero158 taking?"
.venv/bin/python -m agent.cli     # interactive
```

To use the MCP server from Claude Desktop or Claude Code instead, point the client at
`.venv/bin/python -m fhir_mcp.server` with this folder as the working directory:

```bash
claude mcp add fhir-records -- "$PWD/.venv/bin/python" -m fhir_mcp.server
```

## Evaluation

```bash
.venv/bin/python -m eval.build_testset   # writes eval/testset.json from the loaded data
.venv/bin/python -m eval.run_eval        # writes results/report.md
```

Because Synthea data is random, the test set is generated from whatever patients are
loaded. The builder reads the answers with raw FHIR queries rather than through the MCP
tools, so a bug in a tool shows up as a failed question. The question types are:

| Category | n | Graded by |
|---|---|---|
| Current medications | 4 | every drug named in the answer |
| Condition yes/no | 5 (3 yes, 2 no) | first yes/no word |
| Latest lab value | 4 | number within 1% |
| Lab high/low/normal | 3 | first high/low/normal word |
| Medication history | 2 | first yes/no word |
| Birth year | 2 | year present |

## Results (Qwen 2.5 7B, local)

| Version | Accuracy | p50 / p95 response time |
|---|---|---|
| v1: tools need an exact `patient_id` | 9/20 (45%) | 8.8 s / 24.1 s |
| v2: tools also accept a unique patient name | **19/20 (95%)** | 13.8 s / 29.8 s |

In v1, 10 of the 11 failures had the same cause: the 7B model skipped `find_patient` and
passed the patient's name as `patient_id`. The search found nothing, and the model
reported "no medications". v2 resolves a name when it matches exactly one patient and
returns an error listing the candidates when it is ambiguous, so it never guesses.
The remaining failure has the right facts but the wrong answer format. Full reports
are in `results/`.

## Logging

- `logs/agent_runs.jsonl`: one line per question with the answer, every tool call
  (args, latency, result size), LLM time, tool time, total time and tokens
- `logs/mcp_tool_calls.jsonl`: written by the MCP server itself for every call, from
  any client, with its latency and errors
