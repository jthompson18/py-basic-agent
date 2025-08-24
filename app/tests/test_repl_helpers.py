# tests/test_repl_helpers.py
from agent.repl import (
    _parse_flag_line,
    _build_transform_spec,
    _detect_source_type,
    _default_outpath,
    _parse_mcp_add_http_flags,
    _parse_mcp_add_stdio_flags,
)


def test_parse_flag_line():
    s = '-p ./data/sales.csv -t "reorder:date,region; rename:unit_price->price" -l ./out.csv'
    flags = _parse_flag_line(s)
    assert flags["p"] == "./data/sales.csv"
    assert "reorder:date,region" in flags["t"]
    assert "rename:unit_price->price" in flags["t"]
    assert flags["l"] == "./out.csv"


def test_build_transform_spec():
    spec = _build_transform_spec(
        "reorder:date,region,product; rename:unit_price->price; limit:5")
    assert spec["select"] == ["date", "region", "product"]
    assert spec["rename"] == {"unit_price": "price"}
    assert spec["limit"] == 5


def test_detect_source_type():
    assert _detect_source_type("file.csv") == "csv"
    assert _detect_source_type("http://x/y.json") == "json"
    assert _detect_source_type("x.txt") is None


def test_default_outpath():
    assert _default_outpath("./data/abc.csv").endswith("transformed_abc.csv")
    assert _default_outpath("http://x/y.json").endswith("transformed_y.json")


def test_mcp_flag_parsers():
    http = _parse_mcp_add_http_flags('-n fs -u http://host:8765')
    assert http["n"] == "fs" and http["u"].startswith("http://")
    stdio = _parse_mcp_add_stdio_flags(
        '-n alpha -c "node server.js" --env A=1,B=2')
    assert stdio["n"] == "alpha" and "node" in stdio["c"] and stdio["env"] == "A=1,B=2"
