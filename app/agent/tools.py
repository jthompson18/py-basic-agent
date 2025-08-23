# app/agent/tools.py
from __future__ import annotations
from .etl import (
    load_csv, load_json, transform,
    save_csv, save_parquet, save_sqlite, profile
)

import os
import re
import httpx
from bs4 import BeautifulSoup
from readability import Document
from typing import Any, Dict, List, Optional

from .config import settings

SERPER_URL = "https://google.serper.dev/search"

# ---------- Search ----------


async def serper_search(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Calls Serper (Google) and returns a small list of {title, url, snippet}.
    Requires SERPER_API_KEY in env/.env.
    """
    api_key = settings.serper_api_key
    if not api_key:
        raise RuntimeError(
            "SERPER_API_KEY is not set; cannot call serper_search.")
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(SERPER_URL, headers=headers, json={"q": query})
        r.raise_for_status()
        data = r.json()
    out: List[Dict[str, str]] = []
    for item in (data.get("organic", [])[:num]):
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return out

# ---------- Fetch & Clean ----------


def _allowlisted(url: str) -> bool:
    allow = os.getenv("TOOL_DOMAIN_ALLOWLIST", "").strip()
    if not allow:
        return True
    domains = [d.strip().lower() for d in allow.split(",") if d.strip()]
    return any(d in url.lower() for d in domains)


async def fetch_url(url: str, max_chars: int = 20000) -> Dict[str, str]:
    """
    Fetches a web page and returns {url, title, text} using readability.
    Respects optional TOOL_DOMAIN_ALLOWLIST (comma-separated host fragments).
    """
    if not _allowlisted(url):
        raise RuntimeError(f"Fetch blocked by TOOL_DOMAIN_ALLOWLIST: {url}")

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text

    doc = Document(html)
    cleaned_html = doc.summary()
    text = BeautifulSoup(cleaned_html, "html.parser").get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return {"url": url, "title": doc.short_title(), "text": text[:max_chars]}

# ---------- Memory (pgvector/sqlite) ----------

_mem = None


def _get_mem():
    global _mem
    if _mem is None:
        if settings.memory_backend.lower() == "pgvector":
            from .memory.pg_store import PgVectorMemory
            _mem = PgVectorMemory()
        else:
            from .memory.sqlite_store import SqliteVectorMemory
            _mem = SqliteVectorMemory(settings.sqlite_path)
    return _mem


async def memory_tool(op: str, **kwargs) -> Dict[str, Any]:
    """
    memory_tool("remember", docs=[{content, source, uri, meta}])
    memory_tool("recall", query="...", k=5)
    """
    mem = _get_mem()
    if op == "remember":
        docs = kwargs["docs"]
        await mem.aupsert(docs)           # <— await
        return {"ok": True, "count": len(docs)}
    if op == "recall":
        q = kwargs["query"]
        k = int(kwargs.get("k", 5))
        results = await mem.aquery(q, k=k)  # <— await
        return {"results": results}
    return {"error": f"unknown op {op}"}

# ---------- Basic ETL facade ----------


async def etl_tool(op: str, **kwargs) -> Dict[str, Any]:
    """
    etl_tool("load_csv", path="./data/in.csv")
    etl_tool("load_json", path="./data/in.json")
    etl_tool("transform", path="./data/in.csv", spec={...}, save={...})
    """
    if op == "load_csv":
        df = load_csv(
            kwargs["path"], **{k: v for k, v in kwargs.items() if k not in {"path"}})
        return {"profile": profile(df)}
    if op == "load_json":
        df = load_json(
            kwargs["path"], **{k: v for k, v in kwargs.items() if k not in {"path"}})
        return {"profile": profile(df)}
    if op == "transform":
        # load from path or url
        if "path" in kwargs:
            df = load_csv(kwargs["path"])
        elif "url" in kwargs:
            df = load_json(kwargs["url"])
        else:
            return {"error": "transform requires 'path' or 'url'."}
        df2 = transform(df, kwargs.get("spec", {}))
        out: Dict[str, Any] = {"profile": profile(df2)}
        if "save" in kwargs:
            save = kwargs["save"]
            fmt = save.get("format")
            if fmt == "csv":
                out["saved_as"] = save_csv(df2, save["path"])
            elif fmt == "parquet":
                out["saved_as"] = save_parquet(df2, save["path"])
            elif fmt == "sqlite":
                out["saved_as"] = save_sqlite(
                    df2, save["sqlite_path"], save["table"])
            else:
                out["saved_as"] = None
        return out
    return {"error": f"unknown op {op}"}

__all__ = ["serper_search", "fetch_url", "memory_tool", "etl_tool"]
