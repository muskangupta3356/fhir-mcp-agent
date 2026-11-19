"""LLM agent that answers clinical questions by calling the FHIR MCP server's tools.

The MCP server runs as a subprocess over stdio. The model (GPT-4o-mini by default)
decides which tools to call. Every run is logged to logs/agent_runs.jsonl with each
tool call, its arguments, latency, and token usage.
"""
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
LOG = ROOT / "logs" / "agent_runs.jsonl"
LOG.parent.mkdir(exist_ok=True)

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
MAX_STEPS = 10

SYSTEM_PROMPT = """You are a clinical data assistant with read access to a FHIR server of \
synthetic patient records. Use the tools to look up facts; never guess or invent data.

- Always call find_patient first to resolve a name to a patient_id. If several patients \
match, say so and list them.
- Answer from tool results only. Quote values with units and dates.
- For yes/no questions, begin your answer with "Yes" or "No".
- When asked whether a lab value is high, low or normal, begin with HIGH, LOW or NORMAL \
(as given by the tool's flag).
- "Currently taking" means active medications; "ever prescribed" means include inactive ones.
- A condition counts as current only if its clinical_status is active.
- Be concise."""


class FhirAgent:
    def __init__(self, model: str = MODEL):
        self.model = model
        self.llm = AsyncOpenAI()
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: list[dict] = []

    async def __aenter__(self):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "fhir_mcp.server"],
            cwd=str(ROOT),
            env={**os.environ},
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.input_schema if hasattr(t, "input_schema") else t.inputSchema,
                },
            }
            for t in listed.tools
        ]
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def _call_tool(self, name: str, args: dict) -> str:
        result = await self.session.call_tool(name, args)
        parts = [c.text for c in getattr(result, "content", []) if getattr(c, "text", None)]
        return "\n".join(parts) or json.dumps(getattr(result, "structured_content", None) or {})

    async def ask(self, question: str) -> dict:
        """Answer one question. Returns the answer plus a trace of tool calls and timings."""
        run_start = time.perf_counter()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        trace: list[dict] = []
        llm_ms = 0.0
        tokens = {"prompt": 0, "completion": 0}
        answer = None

        for _ in range(MAX_STEPS):
            t0 = time.perf_counter()
            resp = await self.llm.chat.completions.create(
                model=self.model, messages=messages, tools=self.tools, temperature=0
            )
            llm_ms += (time.perf_counter() - t0) * 1000
            if resp.usage:
                tokens["prompt"] += resp.usage.prompt_tokens
                tokens["completion"] += resp.usage.completion_tokens

            msg = resp.choices[0].message
            if not msg.tool_calls:
                answer = msg.content
                break

            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                t0 = time.perf_counter()
                try:
                    output = await self._call_tool(call.function.name, args)
                    error = None
                except Exception as e:
                    output = json.dumps({"error": str(e)})
                    error = str(e)
                trace.append({
                    "tool": call.function.name,
                    "args": args,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "result_chars": len(output),
                    "error": error,
                })
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        else:
            answer = "Stopped: too many tool-call steps without a final answer."

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": self.model,
            "question": question,
            "answer": answer,
            "tool_calls": trace,
            "num_tool_calls": len(trace),
            "tool_ms": round(sum(t["latency_ms"] for t in trace), 1),
            "llm_ms": round(llm_ms, 1),
            "total_ms": round((time.perf_counter() - run_start) * 1000, 1),
            "tokens": tokens,
        }
        with LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
        return record
