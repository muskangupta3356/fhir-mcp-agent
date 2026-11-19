"""Run the agent on eval/testset.json and report accuracy and response times.

Outputs results/eval_results.jsonl (one line per question) and results/report.md.
"""
import asyncio
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from agent.agent import FhirAgent

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "eval" / "testset.json"
RESULTS = ROOT / "results"

# gpt-4o-mini list price, USD per 1M tokens
PRICE_IN, PRICE_OUT = 0.15, 0.60


def grade(q: dict, answer: str | None) -> bool:
    a = (answer or "").lower()
    kind, exp = q["grading"], q["expected"]
    if kind == "yes_no":
        first = re.findall(r"\b(yes|no)\b", a)
        return bool(first) and first[0] == exp
    if kind == "all_terms":
        return all(t in a for t in exp)
    if kind == "number":
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", a.replace(",", ""))]
        return any(abs(n - exp) <= max(0.01 * abs(exp), 0.05) for n in nums)
    if kind == "first_of":
        hits = re.findall(r"\b(" + "|".join(q["options"]) + r")\b", a)
        return bool(hits) and hits[0] == exp
    if kind == "contains":
        return str(exp).lower() in a
    raise ValueError(kind)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def main() -> None:
    questions = json.loads(TESTSET.read_text())
    RESULTS.mkdir(exist_ok=True)
    rows = []
    async with FhirAgent() as agent:
        for q in questions:
            r = await agent.ask(q["question"])
            ok = grade(q, r["answer"])
            rows.append({**q, "answer": r["answer"], "correct": ok,
                         "tool_calls": [t["tool"] for t in r["tool_calls"]],
                         "total_ms": r["total_ms"], "tool_ms": r["tool_ms"], "llm_ms": r["llm_ms"],
                         "tokens": r["tokens"], "model": r["model"]})
            print(f"[{q['id']:>2}] {'PASS' if ok else 'FAIL'}  {r['total_ms'] / 1000:5.1f}s  {q['question']}")

    with (RESULTS / "eval_results.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    lat = [r["total_ms"] / 1000 for r in rows]
    tool_lat = [r["tool_ms"] for r in rows if r["tool_calls"]]
    calls = [len(r["tool_calls"]) for r in rows]
    tin = sum(r["tokens"]["prompt"] for r in rows)
    tout = sum(r["tokens"]["completion"] for r in rows)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r["correct"])

    lines = [
        f"# Evaluation report ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"Model: `{rows[0]['model']}` · {n} questions",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Accuracy | **{correct}/{n} ({100 * correct / n:.0f}%)** |",
        f"| Mean response time | {statistics.mean(lat):.2f} s |",
        f"| Median (p50) response time | {pct(lat, 50):.2f} s |",
        f"| p95 response time | {pct(lat, 95):.2f} s |",
        f"| Mean time in tools per question | {statistics.mean(tool_lat):.0f} ms |" if tool_lat else "",
        f"| Mean tool calls per question | {statistics.mean(calls):.1f} |",
        f"| Tokens (in / out) | {tin:,} / {tout:,} |",
        f"| Cost of full run | ${(tin * PRICE_IN + tout * PRICE_OUT) / 1e6:.4f} |"
        if rows[0]["model"].startswith("gpt-4o-mini") else "| Cost of full run | $0 (local model) |",
        "",
        "## Accuracy by category", "",
        "| Category | Correct |", "|---|---|",
        *[f"| {c} | {sum(v)}/{len(v)} |" for c, v in by_cat.items()],
        "",
        "## Failures", "",
    ]
    fails = [r for r in rows if not r["correct"]]
    for r in fails:
        lines += [f"**Q{r['id']}. {r['question']}**", f"- Expected: `{r['expected']}`",
                  f"- Got: {(r['answer'] or '').strip()}", ""]
    if not fails:
        lines.append("None.")
    (RESULTS / "report.md").write_text("\n".join(l for l in lines if l is not None) + "\n")
    print(f"\nAccuracy {correct}/{n} ({100 * correct / n:.0f}%), "
          f"mean {statistics.mean(lat):.2f}s, p95 {pct(lat, 95):.2f}s")
    print(f"Report: {RESULTS / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
