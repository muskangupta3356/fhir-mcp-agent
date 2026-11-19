# Evaluation report (2026-11-19 15:49)

Model: `qwen2.5-7b-16k` · 20 questions

| Metric | Value |
|---|---|
| Accuracy | **19/20 (95%)** |
| Mean response time | 15.11 s |
| Median (p50) response time | 13.82 s |
| p95 response time | 29.75 s |
| Mean time in tools per question | 210 ms |
| Mean tool calls per question | 1.5 |
| Tokens (in / out) | 43,194 / 2,359 |
| Cost of full run | $0 (local model) |

## Accuracy by category

| Category | Correct |
|---|---|
| active_medications | 4/4 |
| condition_yes_no | 4/5 |
| lab_value | 4/4 |
| lab_flag | 3/3 |
| med_history | 2/2 |
| demographics | 2/2 |

## Failures

**Q6. Does Tennille443 Denna536 Ortiz186 currently have anemia?**
- Expected: `yes`
- Got: NORMAL

The patient Tennille443 Denna536 Ortiz186 has a current condition of anemia (disorder) with an onset date of 1989-01-19.

