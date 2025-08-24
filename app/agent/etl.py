# app/agent/etl.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


# ---------- LOAD ----------

def load_csv(path: str, **read_csv_kwargs) -> pd.DataFrame:
    """Load CSV into DataFrame."""
    return pd.read_csv(path, **read_csv_kwargs)


def load_json(path: str, **kwargs) -> pd.DataFrame:
    """Load JSON (array of objects or single object) into DataFrame."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return pd.json_normalize(data)
    elif isinstance(data, dict):
        # single object -> one-row table
        return pd.json_normalize([data])
    else:
        raise ValueError(
            "Unsupported JSON structure; expected object or list of objects")


# ---------- TRANSFORM ----------

def _reorder_columns(df: pd.DataFrame, desired: List[str]) -> pd.DataFrame:
    cols = list(df.columns)
    in_order = [c for c in desired if c in cols]
    rest = [c for c in cols if c not in in_order]
    return df[in_order + rest]


def _rename_columns(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    return df.rename(columns=mapping)


def transform(df: pd.DataFrame, spec: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply transform spec:
      • select: list[str]     (reorder; unspecified cols appended)
      • rename: dict[str,str]
      • limit: int
    Order of operations:
      1) reorder (select)
      2) rename
      3) limit (head)
    """
    out = df.copy()

    sel = spec.get("select")
    if sel:
        out = _reorder_columns(out, sel)

    ren = spec.get("rename")
    if ren:
        out = _rename_columns(out, ren)

    lim = spec.get("limit")
    if isinstance(lim, int) and lim >= 0:
        out = out.head(lim)

    return out


# ---------- SAVE ----------

def save_csv(df: pd.DataFrame, path: str, index: bool = False, **to_csv_kwargs) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=index, encoding="utf-8", **to_csv_kwargs)
    return path


def save_json(df: pd.DataFrame, path: str, **kwargs) -> str:
    """
    Save DataFrame as a list-of-objects JSON.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    records = df.to_dict(orient="records")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return path


# ---------- PROFILE (for summaries / debugging) ----------

def profile(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "dtypes": {k: str(v) for k, v in df.dtypes.items()},
        "preview": df.head(3).to_dict(orient="records"),
    }
