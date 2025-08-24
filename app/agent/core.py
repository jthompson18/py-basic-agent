from __future__ import annotations

import json
import re
from typing import Callable

from .schemas import Message, StepResult, ToolCall
from .config import settings
from . import llm, tools
from .memory import get_memory

EmitFn = Callable[[str, dict | str], None]

# --- JSON parsing helpers ----------------------------------------------------

# Match fenced JSON blocks like:
# ```json
# { ... }
# ```
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _json_candidates(text: str) -> list[str]:
    """
    Return possible JSON payloads from the model output.
    1) All fenced ```json ... ``` blocks.
    2) If none found, try the whole text as JSON (unfenced).
    """
    blocks = [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    if blocks:
        return blocks
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        return [t]
    return []


async def _build_messages_async(history: list, memory) -> list:
    try:
        recent = await memory.adump(20)
    except Exception:
        recent = ""
    # NEW: trim recent notes if very long
    if isinstance(recent, str) and len(recent) > 4000:
        recent = recent[:4000] + " …(truncated)"
    sys = llm.SYSTEM_PROMPT + \
        (f"\n\nRecent notes:\n{recent}" if recent else "")
    return [Message(role="system", content=sys), *history]


def _try_parse_step(text: str) -> StepResult:
    """
    Parse model reply into a tool call or final:
      - Accepts both fenced and unfenced JSON.
      - Prefers 'tool' calls; uses 'final' only if explicitly present.
      - Falls back to treating the raw text as final if no JSON matches.
    """
    for js in _json_candidates(text):
        try:
            data = json.loads(js)
        except Exception:
            continue

        if isinstance(data, dict) and "tool" in data:
            tool = str(data.get("tool", "")).strip()
            tool_input = data.get("input") or {}
            if not isinstance(tool_input, dict):
                tool_input = {}
            return StepResult(
                type="tool_call",
                tool_call=ToolCall(tool=tool, input=tool_input),
                raw=text,
            )

        if isinstance(data, dict) and "final" in data:
            return StepResult(
                type="final",
                final_answer=str(data.get("final", "")),
                raw=text,
            )

    # No usable JSON -> treat as final raw text (so you can see what happened)
    return StepResult(type="final", final_answer=text, raw=text)


async def run_agent(task: str, emit: EmitFn | None = None, verbose: bool = True) -> str:
    """
    Simple looped agent:
      1) Ask the LLM what to do (tool call or final).
      2) Run the tool (search/fetch/memory[/etl]).
      3) Record obs in memory, optionally summarize, feed back to the model.
      4) Repeat up to settings.max_steps or until final.
    """
    memory = get_memory()
    history: list[Message] = [Message(role="user", content=task)]

    for step in range(settings.max_steps):
        if emit:
            emit("step", {"n": step + 1, "max": settings.max_steps})

        # 1) Ask the model
        msgs = await _build_messages_async(history, memory)
        content = await llm.chat(msgs)

        if emit:
            emit("model", content)

        parsed = _try_parse_step(content)

        # Final?
        if parsed.type == "final":
            final = parsed.final_answer or "(no answer)"
            if emit:
                emit("final", final)
            return final

        # 2) Tool call?
        if parsed.type == "tool_call":
            tool = parsed.tool_call
            if emit:
                emit("tool_call", {"tool": tool.tool, "input": tool.input})

            try:
                # ---- search (Serper) ----
                if tool.tool == "search":
                    query = (tool.input or {}).get("query", "").strip()
                    results = await tools.serper_search(query)

                    # Log & emit preview
                    obs = "SEARCH RESULTS:\n" + "\n".join(
                        f"- {r.get('title', '?')} — {r.get('url', '?')}" for r in results
                    )
                    await memory.aadd(obs, source="search", uri=f"serper:{query}")
                    if emit:
                        emit("tool_result", {"tool": "search", "preview": obs})

                    # Optional LLM summary of search results
                    try:
                        summary = await llm.summarize_search(results)
                        if emit:
                            emit("summary", {
                                 "type": "search", "text": summary})
                        history.append(
                            Message(role="assistant",
                                    content=f"{obs}\n\nSummary:\n{summary}")
                        )
                    except Exception:
                        history.append(Message(role="assistant", content=obs))

                    continue

                # ---- fetch (page fetch + readable text) ----
                if tool.tool == "fetch":
                    url = (tool.input or {}).get("url", "").strip()
                    # expected keys: title, url, text
                    page = await tools.fetch_url(url)
                    title = page.get("title") or ""
                    text = page.get("text") or ""
                    preview = (text[:1000] + ("…" if len(text) > 1000 else ""))

                    obs = f"FETCHED PAGE:\nTitle: {title}\nURL: {url}\n\n{preview}"
                    await memory.aadd(
                        f"Fetched {url} — {title}", source="fetch", uri=url, meta={"title": title}
                    )
                    if emit:
                        emit("tool_result", {
                             "tool": "fetch", "preview": f"{title} ({len(text)} chars)"})

                    history.append(Message(role="assistant", content=obs))
                    continue

                # ---- memory (optional: allow LLM-driven memory ops) ----
                if tool.tool == "memory":
                    op = (tool.input or {}).get("op")
                    if op == "remember":
                        docs = (tool.input or {}).get("docs", [])
                        count = await memory.aupsert(docs)
                        obs = f"MEMORY: stored {count} document(s)."
                    elif op == "recall":
                        q = (tool.input or {}).get("query", "")
                        k = int((tool.input or {}).get("k", 3))
                        hits = await memory.aquery(q, k=k)
                        lines = [
                            f"- {h.get('source')}:{h.get('uri')} — {(h.get('content') or '')[:160]}"
                            for h in hits
                        ]
                        obs = "MEMORY RECALL:\n" + \
                            ("\n".join(lines) if lines else "(no hits)")
                    else:
                        obs = "MEMORY: unknown op."

                    if emit:
                        emit("tool_result", {"tool": "memory", "preview": obs})
                    history.append(Message(role="assistant", content=obs))
                    continue

                # ---- etl (if your prompts call it directly) ----
                if tool.tool == "etl":
                    # Expect the LLM to pass through the kwargs your tools.etl_tool("transform", ...) expects.
                    spec = tool.input or {}
                    tr = await tools.etl_tool("transform", **spec)
                    obs = f"ETL DONE: {str(tr)[:400]}"
                    if emit:
                        emit("tool_result", {"tool": "etl", "preview": obs})
                    # Optional ETL summary
                    try:
                        summary = await llm.summarize_etl(tr)
                        if emit:
                            emit("summary", {"type": "etl", "text": summary})
                        history.append(
                            Message(role="assistant",
                                    content=f"{obs}\n\nSummary:\n{summary}")
                        )
                    except Exception:
                        history.append(Message(role="assistant", content=obs))
                    continue

                # Unknown tool
                err = f"Unknown tool '{tool.tool}'"
                await memory.aadd(err, source="error")
                if emit:
                    emit("error", err)
                history.append(Message(role="assistant", content=err))
                continue

            except Exception as e:
                # 3) Tool error handling: persist error & inform loop
                err = f"TOOL ERROR for {tool.tool}: {type(e).__name__}: {e}"
                await memory.aadd(err, source="error")
                if emit:
                    emit("error", err)
                history.append(Message(role="assistant", content=err))
                continue

    # 4) Out of steps
    fallback = "I couldn't complete within the step limit."
    if emit:
        try:
            snapshot = await memory.adump(20)
            emit("final", snapshot or fallback)
        except Exception:
            emit("final", fallback)
    return fallback
