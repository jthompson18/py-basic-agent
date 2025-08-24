# tests/test_core_agent.py
import asyncio
import pytest

from agent import tools, llm
from agent.core import run_agent


@pytest.mark.asyncio
async def test_run_agent_final_only(monkeypatch):
    # LLM returns final immediately
    async def fake_chat(_messages):
        return "```json\n{\"final\":\"Ready.\"}\n```"
    monkeypatch.setattr(llm, "chat", fake_chat)

    ans = await run_agent("Hello?", emit=None, verbose=False)
    assert "Ready" in ans


@pytest.mark.asyncio
async def test_run_agent_with_tool_search(monkeypatch):
    calls = {"n": 0}

    async def fake_chat(_messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "```json\n{\"tool\":\"search\",\"input\":{\"query\":\"NVIDIA founders\"}}\n```"
        return "```json\n{\"final\":\"NVIDIA was founded by Jensen Huang, Chris Malachowsky, and Curtis Priem.\"}\n```"
    monkeypatch.setattr(llm, "chat", fake_chat)

    async def fake_serper(q: str):
        return [{"title": "NVIDIA - Wikipedia", "url": "https://example/nv"}]
    monkeypatch.setattr(tools, "serper_search", fake_serper)

    # summaries not required but present in code
    async def fake_sum(_payload): return "summary"
    monkeypatch.setattr(llm, "summarize_search", fake_sum, raising=False)

    ans = await run_agent("Who founded NVIDIA?", emit=None, verbose=False)
    assert "founded" in ans.lower()
