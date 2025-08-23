from typing import Any, Dict
import pandas as pd


def load_csv(path_or_url: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path_or_url, **kwargs)


def load_json(path_or_url: str, **kwargs) -> pd.DataFrame:
    return pd.read_json(path_or_url, **kwargs)


def transform(df: pd.DataFrame, spec: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if cols := spec.get("select"):
        out = out[cols]
    if ren := spec.get("rename"):
        out = out.rename(columns=ren)
    if filt := spec.get("filter"):
        # Very small DSL: {"col": "value"} equals filter, or {"expr":"colA > 10"}
        if "expr" in filt:
            out = out.query(filt["expr"])
        else:
            for k, v in filt.items():
                out = out[out[k] == v]
    if derives := spec.get("derive"):
        # {"new_col":"colA * 1.1"} (pandas eval)
        for k, expr in derives.items():
            out[k] = out.eval(expr)
    return out


def save_csv(df: pd.DataFrame, path: str, **kwargs) -> str:
    df.to_csv(path, index=False, **kwargs)
    return path


def save_parquet(df: pd.DataFrame, path: str, **kwargs) -> str:
    df.to_parquet(path, index=False, **kwargs)
    return path


def save_sqlite(df: pd.DataFrame, sqlite_path: str, table: str, if_exists="replace") -> str:
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{sqlite_path}")
    df.to_sql(table, eng, if_exists=if_exists, index=False)
    return f"sqlite:///{sqlite_path}#{table}"


def profile(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "null_counts": {c: int(df[c].isna().sum()) for c in df.columns},
        "sample": df.head(5).to_dict(orient="records"),
    }
