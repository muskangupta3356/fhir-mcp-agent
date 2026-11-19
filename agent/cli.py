"""Ask the agent questions from the terminal.

    python -m agent.cli "What medications is Abe604 Smith43 taking?"
    python -m agent.cli            # interactive mode
"""
import asyncio
import sys

from .agent import FhirAgent


def show(r: dict) -> None:
    for t in r["tool_calls"]:
        print(f"  -> {t['tool']}({t['args']})  {t['latency_ms']:.0f} ms")
    print(f"\n{r['answer']}\n")
    print(f"[{r['total_ms'] / 1000:.1f}s total | {r['num_tool_calls']} tool calls | "
          f"{r['tokens']['prompt'] + r['tokens']['completion']} tokens]\n")


async def main() -> None:
    async with FhirAgent() as agent:
        if len(sys.argv) > 1:
            show(await agent.ask(" ".join(sys.argv[1:])))
            return
        print("Ask about a patient (blank line to quit).")
        while q := input("> ").strip():
            show(await agent.ask(q))


if __name__ == "__main__":
    asyncio.run(main())
