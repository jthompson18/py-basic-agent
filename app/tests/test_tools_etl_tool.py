# tests/test_tools_etl_tool.py
from __future__ import annotations
import json
import os
import pytest
from agent.tools import etl_tool

pytestmark = pytest.mark.asyncio


async def test_etl_tool_transform_csv(tmp_path, data_dir):
    src = str(data_dir / "sales_orders.csv")
    out = tmp_path / "out.csv"

    load_res = await etl_tool("load_csv", path=src)
    assert "profile" in load_res and load_res["profile"]["rows"] >= 5

    spec = {
        "select": ["date", "region", "product", "units", "unit_price"],
        "rename": {"unit_price": "price"},
        "limit": 3,
    }
    tr = await etl_tool(
        "transform",
        path=src,
        spec=spec,
        save={"format": "csv", "path": str(out)},
    )
    assert tr["profile_after"]["rows"] == 3
    expected_prefix = ["date", "region", "product", "units", "price"]
    assert tr["profile_after"]["columns"][:len(
        expected_prefix)] == expected_prefix
    assert os.path.exists(out)

    text = out.read_text().splitlines()
    assert text[0].split(",")[:len(expected_prefix)] == expected_prefix
    assert len(text) == 1 + 3  # header + 3 rows


async def test_etl_tool_transform_json(tmp_path, data_dir):
    src = str(data_dir / "customers.json")
    out = tmp_path / "out.json"

    load_res = await etl_tool("load_json", path=src)
    assert load_res["profile"]["rows"] >= 1

    spec = {"select": ["customer_id", "name"],
            "rename": {"customer_id": "cid"}, "limit": 2}
    tr = await etl_tool(
        "transform",
        path=src,
        spec=spec,
        save={"format": "json", "path": str(out)},
    )
    assert tr["profile_after"]["rows"] == 2
    assert tr["profile_after"]["columns"][:2] == ["cid", "name"]
    assert os.path.exists(out)

    loaded = json.loads(out.read_text())
    assert isinstance(loaded, list) and len(loaded) == 2
    assert set(loaded[0]).issuperset({"cid", "name"})
