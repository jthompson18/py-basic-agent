# app/agent/tools.py  (only the ETL-related parts shown; keep your existing search/memory)
from __future__ import annotations
import os
import re
import urllib.parse
import httpx
import pandas as pd
from typing import Any, Dict, List
from bs4 import BeautifulSoup
from readability import Document

from .config import settings
from .etl import (
    load_csv, load_json, transform, profile,
    save_csv, save_parquet, save_sqlite, save_json  # NEW
)


def _is_url(p: str) -> bool:
    try:
        u = urllib.parse.urlparse(p)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def _resolve_local_path(p: str) -> str:
    # URLs pass through
    if _is_url(p):
        return p

    p_norm = os.path.normpath(p)

    # Absolute path: return as-is
    if os.path.isabs(p_norm):
        return p_norm

    # If the user typed ./data/... or data/..., map to /app/data/<tail>
    rel = p_norm[2:] if p_norm.startswith("./") else p_norm
    if rel.startswith("data/"):
        tail = rel[len("data/"):]
        return os.path.normpath(os.path.join("/app/data", tail))

    # Otherwise, treat as relative to project root (/app)
    return os.path.normpath(os.path.join("/app", p_norm))


async def etl_tool(op: str, **kwargs) -> Dict[str, Any]:
    if op == "load_csv":
        path = _resolve_local_path(kwargs["path"])
        df = load_csv(path, **{k: v for k, v in kwargs.items() if k != "path"})
        return {"profile": profile(df), "path": path}

    if op == "load_json":
        path = _resolve_local_path(kwargs["path"])
        if _is_url(path):
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                r = await client.get(path, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                data = r.json()
            df = pd.json_normalize(data)
        else:
            df = load_json(path)
        return {"profile": profile(df), "path": path}

    if op == "transform":
        # choose loader based on extension
        if "path" in kwargs:
            path = _resolve_local_path(kwargs["path"])
            ext = os.path.splitext(urllib.parse.urlparse(path).path.lower())[1]
            if ext == ".csv":
                df = load_csv(path)
            elif ext == ".json":
                if _is_url(path):
                    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                        r = await client.get(path, headers={"User-Agent": "Mozilla/5.0"})
                        r.raise_for_status()
                        data = r.json()
                    df = pd.json_normalize(data)
                else:
                    df = load_json(path)
            else:
                return {"error": f"Unsupported transform source extension: {ext}"}
        else:
            return {"error": "transform requires 'path'."}

        df2 = transform(df, kwargs.get("spec", {}))
        out: Dict[str, Any] = {"profile": profile(df2)}

        if "save" in kwargs:
            save = kwargs["save"]
            fmt = (save.get("format") or "").lower()
            if fmt == "csv":
                out["saved_as"] = save_csv(
                    df2, _resolve_local_path(save["path"]))
            elif fmt == "parquet":
                out["saved_as"] = save_parquet(
                    df2, _resolve_local_path(save["path"]))
            elif fmt == "sqlite":
                out["saved_as"] = save_sqlite(
                    df2, _resolve_local_path(save["sqlite_path"]), save["table"])
            elif fmt == "json":
                out["saved_as"] = save_json(
                    df2, _resolve_local_path(save["path"]))
            else:
                out["saved_as"] = None
        return out

    # keep other tool code above unchanged
    return {"error": f"unknown op {op}"}
