# app/agent/core.py
from __future__ import annotations

import json
import re
from typing import List

from loguru import logger

from .schemas import Message, StepResult, ToolCall
from .config import settings
from . import llm, tools

# Flexible import in case your SimpleMemory lives in different modules
try:
    from .memory import SimpleMemory  # re-exported in memory/__init__.py
except Exception:
    try:
        from .memory.scratchpad import SimpleMemory
    except Exception:
        # fallback if you named it this way
        from .memory.simple_memory import SimpleMemory


def _build_messages(history: List[Message], memory: SimpleMemory) -> List[dict]:
    """Assemble the chat messages for the model."""
    sys = Message(
        role="system",
        content=llm.SYSTEM_PROMPT + f"\n\nRecent notes:\n{memory.dump()}",
    )
    return [sys.model_dump()] + [m.model_dump() for m in history]


def _try_parse_step(text: str) -> StepResult:
    """
    Extract the last fenced JSON block and interpret as either:
      - {"tool": "...", "input": {...}}
      - {"final": "..."}
    If no valid JSON block is found, treat the whole text as a final answer.
    """
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    raw_json = blocks[-1] if blocks else None
    if not raw_json:
        return StepResult(type="final", final_answer=text, raw=text)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return StepResult(type="final", final_answer=text, raw=text)

    if "final" in data:
        return StepResult(type="final", final_answer=data["final"], raw=text)

    if "tool" in data and "input" in data:
        # Pydantic will validate tool literal against schemas.ToolCall
        return StepResult(type="tool_call", tool_call=ToolCall(tool=data["tool"], input=data["input"]), raw=text)

    return StepResult(type="final", final_answer=text, raw=text)


async def run_agent(task: str) -> str:
    """
    Run the agent loop with a maximum number of steps.
    Supports tools: search, fetch, memory, etl.
    """
    memory = SimpleMemory()
    history: List[Message] = [Message(role="user", content=task)]

    for step in range(settings.max_steps):
        logger.info(f"Step {step+1}/{settings.max_steps}")
        content = await llm.chat(_build_messages(history, memory))
        parsed = _try_parse_step(content)

        if parsed.type == "final":
            return parsed.final_answer or "(no answer)"

        # Tool call branch
        tool = parsed.tool_call
        assert tool is not None, "Parsed step indicates a tool call but no tool_call payload was found."

        try:
            if tool.tool == "search":
                query = tool.input.get("query", "")
                results = await tools.serper_search(query)
                obs = "SEARCH RESULTS:\n" + \
                    "\n".join(f"- {r['title']} — {r['url']}" for r in results)
                memory.add(obs)
                history.append(Message(role="tool", content=obs))

            elif tool.tool == "fetch":
                url = tool.input.get("url", "")
                page = await tools.fetch_url(url)
                obs = f"FETCHED: {page['title']} — {page['url']}\n\n{page['text'][:1500]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))

            elif tool.tool == "memory":
                op = tool.input.get("op")
                args = {k: v for k, v in tool.input.items() if k != "op"}
                res = await tools.memory_tool(op, **args)
                obs = f"MEMORY {op.upper()} RESULT:\n{json.dumps(res)[:1200]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))

            elif tool.tool == "etl":
                op = tool.input.get("op")
                args = {k: v for k, v in tool.input.items() if k != "op"}
                res = await tools.etl_tool(op, **args)
                obs = f"ETL {op.upper()} RESULT:\n{json.dumps(res)[:1200]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))

            else:
                history.append(
                    Message(role="tool", content=f"(unknown tool {tool.tool})"))

        except Exception as e:
            # Surface tool errors back into the loop so the model can react
            err = f"TOOL ERROR for {tool.tool}: {type(e).__name__}: {e}"
            memory.add(err)
            history.append(Message(role="tool", content=err))

    return "Reached max steps without final answer."
