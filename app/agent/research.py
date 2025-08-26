from __future__ import annotations
import re
from typing import List, Dict, Tuple

from .memory import get_memory
from . import llm, tools
from .schemas import Message

MIN_TOKEN_LEN = 4
# >=20% of meaningful query tokens must overlap to consider memory "relevant"
MIN_OVERLAP_RATIO = 0.2
MAX_CONTEXT_DOCS = 5
SEARCH_RESULTS = 5
FETCH_TOP = 3

RESEARCH_SYSTEM = (
    "You are a precise research assistant. Answer the QUESTION using only the CONTEXT. "
    "Include bracketed citation numbers like [1], [2] that map to the provided context blocks. "
    "If the answer is not supported by CONTEXT, reply exactly: \"I don't know.\""
)


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= MIN_TOKEN_LEN]


def _overlap_ratio(query_text: str, hay: str) -> float:
    qt = set(_tokens(query_text))
    if not qt:
        return 0.0
    ht = set(_tokens(hay))
    if not ht:
        return 0.0
    return len(qt & ht) / max(1, len(qt))


def _is_memory_relevant(hits: List[Dict], query: str) -> Tuple[bool, List[Dict]]:
    """Return (relevant?, filtered_hits) based on simple token overlap against source/uri/content."""
    filtered = []
    for h in hits or []:
        src = (h.get("source") or "") + " " + (h.get("uri") or "")
        txt = (h.get("content") or h.get("text") or "")[:1500]
        ratio = max(_overlap_ratio(query, src), _overlap_ratio(query, txt))
        if ratio >= MIN_OVERLAP_RATIO:
            filtered.append(h)
    return (len(filtered) > 0), filtered[:MAX_CONTEXT_DOCS]


async def _search_and_fetch(query: str, n_search: int = SEARCH_RESULTS, n_fetch: int = FETCH_TOP) -> List[Dict]:
    """
    Use existing Serper and fetch tools.
    Expected shapes:
      - tools.serper_search(query=..., num=...) -> list of {title, url|link}
      - tools.fetch_url(url=...) -> {title, url, text|content}
    """
    results = await tools.serper_search(query=query, num=n_search)
    docs: List[Dict] = []
    seen_domains = set()
    for r in results:
        url = (r.get("url") or r.get("link") or "").strip()
        if not url:
            continue
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        if domain in seen_domains:
            continue
        seen_domains.add(domain)

        page = await tools.fetch_url(url=url)
        text = page.get("text") or page.get("content") or ""
        if text:
            docs.append({
                "source": r.get("title") or page.get("title") or domain,
                "uri": url,
                "content": text
            })
        if len(docs) >= n_fetch:
            break
    return docs


async def answer_research(question: str) -> Dict:
    """
    Deterministic research pipeline:
      1) Try memory recall (vector search)
      2) Gate by token overlap with the query
      3) If not clearly relevant -> Serper search + fetch (web)
      4) Compile compact CONTEXT blocks with [n] citations
      5) Ask LLM with strict research system guidance (via llm.chat system_extra)
    """
    mem = get_memory()
    mem_hits = await mem.aquery(question, k=MAX_CONTEXT_DOCS)
    relevant, mem_docs = _is_memory_relevant(mem_hits, question)

    context_docs: List[Dict]
    origin: str

    if not relevant:
        context_docs = await _search_and_fetch(question)
        origin = "web"
    else:
        # Start with memory docs; optionally blend a few fresh web docs for WH-questions
        context_docs = [
            {"source": h.get("source"), "uri": h.get("uri"),
             "content": h.get("content") or h.get("text")}
            for h in mem_docs
        ]
        if re.match(r"^(who|what|when|where|which|how|is|are|did|does)\\b", question.lower()):
            web_docs = await _search_and_fetch(question, n_search=4, n_fetch=2)
            context_docs = (context_docs + web_docs)[:MAX_CONTEXT_DOCS]
            origin = "mixed"
        else:
            origin = "memory"

    # Build CONTEXT with numbered blocks
    context_chunks = []
    for i, d in enumerate(context_docs[:MAX_CONTEXT_DOCS], start=1):
        source = d.get("source") or ""
        uri = d.get("uri") or ""
        text = (d.get("content") or "")[:2000]
        block = f"[{i}] SOURCE: {source} | URI: {uri}\n{text}"
        context_chunks.append(block)

    user = (
        f"QUESTION:\n{question}\n\n"
        "CONTEXT (each block has a bracket number):\n\n" +
        "\n\n---\n\n".join(context_chunks)
    )

    # Use llm.chat with system_extra so we don't have to modify global system
    answer = await llm.chat([Message(role="user", content=user)], temperature=0.1, system_extra=RESEARCH_SYSTEM)

    return {
        "origin": origin,
        "answer": answer,
        "used_docs": context_docs,
        "citations": [f"[{i+1}] {d.get('source') or d.get('uri')}" for i, d in enumerate(context_docs[:MAX_CONTEXT_DOCS])],
    }
