# app/agent/tools.py
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional
import urllib.parse

import httpx
from readability import Document
from lxml import html as lxml_html

from .etl import load_csv, load_json, transform, save_csv, save_json, profile
from .memory import get_memory, Memory


logger = logging.getLogger(__name__)

# ---------------------------
# Memory backend (lazy)
# ---------------------------

_mem: Optional[Memory] = None


def _get_mem() -> Memory:
    global _mem
    if _mem is None:
        _mem = get_memory()
    return _mem


async def memory_tool(op: str, **kwargs) -> Any:
    mem = _get_mem()
    if op == "remember":
        docs = kwargs.get("docs")
        if not isinstance(docs, list):
            raise ValueError("`remember` requires docs: List[Dict]")
        return {"ok": True, "count": await mem.aupsert(docs)}
    if op == "recall":
        q = kwargs.get("query", "")
        k = int(kwargs.get("k", 3))
        return await mem.aquery(q, k=k)
    if op == "dump":
        limit = int(kwargs.get("limit", 50))
        return await mem.adump(limit)
    raise ValueError(f"Unknown memory op: {op}")

# ---------------------------
# Paths
# ---------------------------


def _resolve_local_path(p: str) -> str:
    """
    Resolve a repo-relative path to an in-container path.
    Your container workdir is /app, and ./data is mounted to /app/data.
    """
    if not p:
        return p
    # If it's a URL, don't touch it (handled elsewhere before calling us)
    if urllib.parse.urlparse(p).scheme in ("http", "https"):
        return p
    # Absolute path inside container — leave as-is
    if os.path.isabs(p):
        return p
    # Resolve relative to /app
    return os.path.abspath(os.path.join("/app", p))


# ---------------------------
# Web search (Serper)
# ---------------------------

async def serper_search(query: str, num: int = 5) -> List[Dict[str, Any]]:
    """
    Search via Serper.dev (Google). Requires SERPER_API_KEY env var.
    Returns a list of {title, url, snippet}.
    """
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY not set")

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": num}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post("https://google.serper.dev/search", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    results: List[Dict[str, Any]] = []
    for item in (data.get("organic") or []):
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("link") or item.get("url"),
                "snippet": item.get("snippet") or item.get("snippetHighlighted", ""),
            }
        )
    return results[:num]


# ---------------------------
# Fetch + Readability extract
# ---------------------------

async def fetch_url(url: str) -> Dict[str, Any]:
    """
    Fetch a URL and return a readable extract.
    """
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url)
    r.raise_for_status()

    html = r.text
    doc = Document(html)
    title = doc.short_title() or ""
    summary_html = doc.summary()
    text = lxml_html.fromstring(summary_html).text_content()

    return {
        "url": str(r.url),
        "status": r.status_code,
        "title": title,
        "text": text.strip(),
        "html_excerpt": summary_html,
    }


# ---------------------------
# Memory tool
# ---------------------------

async def memory_tool(op: str, **kwargs) -> Any:
    """
    Memory ops:
      - remember(docs=[{content, source, uri, meta}])
      - recall(query="...", k=5)
    """
    mem = _get_mem()

    if op == "remember":
        docs = kwargs.get("docs")
        if not isinstance(docs, list) or not docs:
            raise ValueError("`remember` requires docs: List[Dict]")
        await mem.aupsert(docs)  # type: ignore[attr-defined]
        return {"ok": True, "count": len(docs)}

    if op == "recall":
        query = kwargs.get("query") or ""
        k = int(kwargs.get("k", 5))
        results = await mem.aquery(query, k=k)  # type: ignore[attr-defined]
        return results

    raise ValueError(f"Unknown memory op: {op}")


# ---------------------------
# ETL tool (optional helper)
# ---------------------------

async def etl_tool(op: str, **kwargs) -> Dict[str, Any]:
    """
    ETL ops (now using a single transform):
      - load_csv(path)
      - load_json(path)
      - transform(path, spec, save={format:csv|json, path:<out>})

    Back-compat: 'transform_csv' and 'transform_json' are accepted and routed to 'transform'.
    """
    # ---------------- load ----------------
    if op == "load_csv":
        path = _resolve_local_path(kwargs["path"])
        df = load_csv(path)
        return {"profile": profile(df), "path": path}

    if op == "load_json":
        path = _resolve_local_path(kwargs["path"])
        df = load_json(path)
        return {"profile": profile(df), "path": path}

    # ----------- transform (single op) -----------
    if op in ("transform", "transform_csv", "transform_json"):
        path = _resolve_local_path(kwargs["path"])
        spec: Dict[str, Any] = kwargs.get("spec", {})
        # {"format":"csv|json","path":"..."}
        out_spec: Dict[str, Any] = kwargs.get("save", {})

        # Load based on extension (or fallback by op hint)
        ext = os.path.splitext(urllib.parse.urlparse(path).path.lower())[1]
        if ext == ".csv" or op == "transform_csv":
            df = load_csv(path)
            default_fmt = "csv"
        elif ext == ".json" or op == "transform_json":
            df = load_json(path)
            default_fmt = "json"
        else:
            # unknown -> assume CSV (most common)
            df = load_csv(path)
            default_fmt = "csv"

        # applies reorder/rename/limit deterministically
        df2 = transform(df, spec)

        saved_as = None
        if out_spec:
            fmt = (out_spec.get("format") or default_fmt).lower()
            out_path = _resolve_local_path(out_spec.get("path") or "")
            if fmt == "csv":
                saved_as = save_csv(df2, out_path)
            elif fmt == "json":
                saved_as = save_json(df2, out_path)
            else:
                raise ValueError(f"Unknown save format: {fmt}")

        return {
            "profile_before": profile(df),
            "profile_after": profile(df2),
            "saved_as": saved_as,
        }

    raise ValueError(f"Unknown etl_tool op: {op}")
