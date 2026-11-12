import json

import pytest

from conftest import make_db
from fixtures import expected_shop as E
from t2s.datasource.base import ColumnProfile, ProfileValue
from t2s.datasource.sqlite import SqliteSource
from t2s.prep import profile


@pytest.fixture
def prof(shop_db):
    return profile.profile_source(SqliteSource(shop_db), "shop")


def _col(prof, table, column):
    t = next(t for t in prof["tables"] if t["name"] == table)
    return next(c for c in t["columns"] if c["name"] == column)


def test_tables_and_rows(prof):
    assert [(t["name"], t["rows"]) for t in prof["tables"]] == E.PROFILE_TABLES
    assert prof["db_id"] == "shop"


def test_selected_columns(prof):
    for (table, column), want in E.PROFILE_COLUMNS.items():
        got = _col(prof, table, column)
        for k, v in want.items():
            assert got[k] == v, (table, column, k)


def test_kinds_and_key_order(prof):
    for c in (c for t in prof["tables"] for c in t["columns"]):
        head = ["name", "rows", "nulls", "distinct", "chess_distinct", "categorical", "types", "kind"]
        tail = {
            "empty": [],
            "counts": [],
            "values": ["values"],
            "range": ["min", "max"],
            "length": ["min_length", "max_length"],
        }
        assert list(c) == head + tail[c["kind"]]


def test_mixed_types_and_value_order(prof):
    m = _col(prof, "mixed", "m")
    assert list(m["types"].items()) == list(E.MIXED_M_TYPES.items())
    assert m["values"] == E.MIXED_M_VALUES
    assert [v["repr"] for v in _col(prof, "customer", "name")["values"]] == E.CUSTOMER_NAME_ORDER
    assert _col(prof, "cats", "c19")["values"][0] == E.C19_FIRST


def test_counts(prof):
    assert profile.profile_counts(prof) == E.PROFILE_COUNTS


def test_to_json_drops_unused_keys():
    c = ColumnProfile("a", 1, 0, 1, 1, True, {"text": 1}, "values", values=[ProfileValue("'x'", "text", 1)])
    assert c.to_json() == {
        "name": "a",
        "rows": 1,
        "nulls": 0,
        "distinct": 1,
        "chess_distinct": 1,
        "categorical": True,
        "types": {"text": 1},
        "kind": "values",
        "values": [{"repr": "'x'", "type": "text", "count": 1}],
    }


def test_profile_bytes_small(tmp_path):
    db = make_db(tmp_path / "s.sqlite", "CREATE TABLE t (a TEXT, b INTEGER); INSERT INTO t VALUES ('x', 1), (NULL, 2);")
    p = profile.profile_source(SqliteSource(db), "s")
    info = profile.write_profile(tmp_path / "out", p)
    want = (
        '{"db_id":"s","tables":[{"name":"t","rows":2,"columns":['
        '{"name":"a","rows":2,"nulls":1,"distinct":1,"chess_distinct":2,"categorical":true,"types":{"text":1},'
        '"kind":"values","values":[{"repr":"\'x\'","type":"text","count":1}]},'
        '{"name":"b","rows":2,"nulls":0,"distinct":2,"chess_distinct":2,"categorical":true,"types":{"integer":2},'
        '"kind":"values","values":[{"repr":"1","type":"integer","count":1},{"repr":"2","type":"integer","count":1}]}'
        "]}]}\n"
    )
    assert (tmp_path / "out" / "profile.json").read_text(encoding="utf-8") == want
    assert info["size"] == len(want.encode())
    assert profile.read_profile(tmp_path / "out") == json.loads(want)
