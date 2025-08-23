from __future__ import annotations

import json
import re
from typing import List, Callable, Optional
from loguru import logger
from .schemas import Message, StepResult, ToolCall
from .config import settings
from . import llm, tools

try:
    from .memory import SimpleMemory
except Exception:
    try:
        from .memory.scratchpad import SimpleMemory
    except Exception:
        from .memory.simple_memory import SimpleMemory

EmitFn = Callable[[str, dict | str], None]


def _build_messages(history: List[Message], memory: SimpleMemory) -> List[dict]:
    sys = Message(
        role="system",
        content=llm.SYSTEM_PROMPT + f"\n\nRecent notes:\n{memory.dump()}",
    )
    return [sys.model_dump()] + [m.model_dump() for m in history]


def _try_parse_step(text: str) -> StepResult:
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
        return StepResult(type="tool_call", tool_call=ToolCall(tool=data["tool"], input=data["input"]), raw=text)
    return StepResult(type="final", final_answer=text, raw=text)


async def run_agent(task: str, emit: Optional[EmitFn] = None, verbose: bool = False) -> str:
    if emit is None:
        emit = lambda *_args, **_kwargs: None

    memory = SimpleMemory()
    history: List[Message] = [Message(role="user", content=task)]

    for step in range(settings.max_steps):
        emit("step", {"n": step + 1, "max": settings.max_steps})
        content = await llm.chat(_build_messages(history, memory))
        if verbose:
            emit("model", content)
        parsed = _try_parse_step(content)

        if parsed.type == "final":
            emit("final", parsed.final_answer or "(no answer)")
            return parsed.final_answer or "(no answer)"

        tool = parsed.tool_call
        assert tool is not None

        emit("tool_call", {"tool": tool.tool, "input": tool.input})
        try:
            if tool.tool == "search":
                query = tool.input.get("query", "")
                results = await tools.serper_search(query)
                obs = "SEARCH RESULTS:\n" + \
                    "\n".join(f"- {r['title']} — {r['url']}" for r in results)
                memory.add(obs)
                history.append(Message(role="tool", content=obs))

                # Summarize search results
                summary = await llm.summarize_search(results)
                memory.add("SEARCH SUMMARY:\n" + summary)
                history.append(
                    Message(role="tool", content="SEARCH SUMMARY:\n" + summary))
                emit("summary", {"type": "search", "text": summary})
                emit("tool_result", {"tool": "search", "preview": obs})

            elif tool.tool == "fetch":
                url = tool.input.get("url", "")
                page = await tools.fetch_url(url)
                obs = f"FETCHED: {page['title']} — {page['url']}\n\n{page['text'][:1500]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))
                emit("tool_result", {"tool": "fetch", "preview": obs})

            elif tool.tool == "memory":
                op = tool.input.get("op")
                args = {k: v for k, v in tool.input.items() if k != "op"}
                res = await tools.memory_tool(op, **args)
                obs = f"MEMORY {op.upper()} RESULT:\n{json.dumps(res)[:1200]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))
                emit("tool_result", {"tool": f"memory:{op}", "preview": obs})

            elif tool.tool == "etl":
                op = tool.input.get("op")
                args = {k: v for k, v in tool.input.items() if k != "op"}
                res = await tools.etl_tool(op, **args)
                obs = f"ETL {op.upper()} RESULT:\n{json.dumps(res)[:1200]}"
                memory.add(obs)
                history.append(Message(role="tool", content=obs))
                emit("tool_result", {"tool": f"etl:{op}", "preview": obs})

                # LLM ETL summary
                etl_summary = await llm.summarize_etl(res)
                memory.add("ETL SUMMARY:\n" + etl_summary)
                history.append(
                    Message(role="tool", content="ETL SUMMARY:\n" + etl_summary))
                emit("summary", {"type": "etl", "text": etl_summary})

            else:
                msg = f"(unknown tool {tool.tool})"
                memory.add(msg)
                history.append(Message(role="tool", content=msg))
                emit("error", msg)

        except Exception as e:
            err = f"TOOL ERROR for {tool.tool}: {type(e).__name__}: {e}"
            memory.add(err)
            history.append(Message(role="tool", content=err))
            emit("error", err)

    out = "Reached max steps without final answer."
    emit("final", out)
    return out
