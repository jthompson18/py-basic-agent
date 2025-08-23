# app/agent/etl.py
from __future__ import annotations
import os
import json
import sqlite3
import pandas as pd
from typing import Dict, Any, List


def load_csv(path: str, **read_csv_kwargs) -> pd.DataFrame:
    if not str(path).startswith(("http://", "https://")) and not os.path.exists(path):
        raise FileNotFoundError(path)
    return pd.read_csv(path, **read_csv_kwargs)


def load_json(path: str, **kwargs) -> pd.DataFrame:
    # local file path (URL handled in tools.py)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return pd.json_normalize(data)


def transform(df: pd.DataFrame, spec: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()

    # rename mapping
    if spec.get("rename"):
        out = out.rename(columns=spec["rename"])

    # select (reorder)
    if spec.get("select"):
        wanted = [c for c in spec["select"] if c in out.columns]
        rest = [c for c in out.columns if c not in wanted]
        out = out[wanted + rest]

    # limit rows
    if "limit" in spec and spec["limit"] is not None:
        try:
            n = int(spec["limit"])
            if n >= 0:
                out = out.head(n)
        except (TypeError, ValueError):
            pass

    return out


def profile(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "columns": list(df.columns),
        "dtypes": {k: str(v) for k, v in df.dtypes.to_dict().items()},
        "null_counts": {c: int(df[c].isna().sum()) for c in df.columns[:50]},
        "head": df.head(5).to_dict(orient="records"),
    }


def save_csv(df: pd.DataFrame, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return path


def save_parquet(df: pd.DataFrame, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def save_sqlite(df: pd.DataFrame, sqlite_path: str, table: str) -> str:
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
    con = sqlite3.connect(sqlite_path)
    try:
        df.to_sql(table, con, if_exists="replace", index=False)
    finally:
        con.close()
    return f"sqlite://{sqlite_path}#{table}"


def save_json(df: pd.DataFrame, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_json(path, orient="records", indent=2, force_ascii=False)
    return path
