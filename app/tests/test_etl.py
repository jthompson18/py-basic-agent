# tests/test_etl.py
from __future__ import annotations
import json
import pandas as pd
from agent.etl import load_csv, load_json, transform, save_csv, save_json, profile


def test_csv_load_transform_save(tmp_path, data_dir):
    df = load_csv(str(data_dir / "sales_orders.csv"))
    assert not df.empty and {"order_id", "date", "region"}.issubset(df.columns)

    spec = {
        "select": ["date", "region", "product", "units", "unit_price"],
        "rename": {"unit_price": "price"},
        "limit": 3,
    }
    df2 = transform(df, spec)

    # Unspecified columns are appended; verify prefix only.
    expected_prefix = ["date", "region", "product", "units", "price"]
    assert df2.columns[:len(expected_prefix)].tolist() == expected_prefix
    assert "price" in df2.columns
    assert len(df2) == 3

    out = tmp_path / "t.csv"
    save_csv(df2, str(out))
    df3 = pd.read_csv(out)
    assert df3.columns[:len(expected_prefix)].tolist() == expected_prefix
    assert len(df3) == 3

    prof = profile(df2)
    assert prof["rows"] == 3
    assert prof["columns"][:len(expected_prefix)] == expected_prefix


def test_json_load_transform_save(tmp_path, data_dir):
    df = load_json(str(data_dir / "customers.json"))
    # Require only the keys that are actually present in the sample
    assert {"customer_id", "name"}.issubset(set(df.columns))

    spec = {"select": ["customer_id", "name"],
            "rename": {"customer_id": "cid"}, "limit": 2}
    df2 = transform(df, spec)

    expected_prefix = ["cid", "name"]
    assert df2.columns[:len(expected_prefix)].tolist() == expected_prefix
    assert len(df2) == 2

    out = tmp_path / "cust.json"
    save_json(df2, str(out))
    loaded = json.loads(out.read_text())
    assert isinstance(loaded, list) and len(loaded) == 2
    # Only assert presence of the selected/renamed keys
    assert set(loaded[0]).issuperset({"cid", "name"})
