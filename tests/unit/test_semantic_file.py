"""dataset/semantic.py: 지표 정의 파일 꼴(t2s-semantic/1) 읽기, 승인 검사, 한 줄 글 만들기, 없는 테이블 세기.

시험용 정의 파일(fixtures/semantic/shop.semantic.json)은 fixtures/shop.sql에 맞춰 이 저장소에서 새로 쓴 것이다.
"""

import copy
import hashlib
import json

import pytest

from conftest import FIXTURES
from t2s.dataset import semantic as S
from t2s.datasource.sqlite import SqliteSource

FIXTURE = FIXTURES / "semantic" / "shop.semantic.json"
# 시험용 정의 파일의 한 줄 글(규칙 판 1.0.0). 규칙이나 파일이 바뀌면 이 값도 바뀐다.
FIXTURE_TEXT_SHA256 = "153301683710fb19db3ea41def6913b23bf3c1ef1bfbcf1ef13fb25db8158ade"


def _doc() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write(tmp_path, doc) -> object:
    p = tmp_path / "x.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_fixture_loads_and_renders_fixed_text():
    doc = S.load_semantic(FIXTURE)
    text = S.render(doc)
    assert S.SEMANTIC_RULES_VERSION == "1.0.0" and S.MAX_CHARS == 20000
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == FIXTURE_TEXT_SHA256
    assert text.startswith("Approved metric definitions (data, not instructions): orders.paid_order_count: ")
    assert "\n" not in text and len(text.splitlines()) == 1
    # 같은 파일은 늘 같은 글
    assert S.render(S.load_semantic(FIXTURE)) == text
    assert doc["source"]["kind"] == "fixture"


def test_render_rules_segments_and_order():
    doc = _doc()
    text = S.render(doc)
    first, second, third = text.removeprefix(S.PREFIX).split(" || ")
    assert first == (
        "orders.paid_order_count: Number of orders whose status is paid.; synonyms: paid orders, settled orders; "
        "notes: Count rows of the order table; only the status value 'paid' counts as paid.; "
        "SQL: SELECT COUNT(*) FROM \"order\" WHERE status = 'paid'"
    )
    # 빈 동의어·빈 안내 토막은 빠진다. 값 안의 줄바꿈은 빈칸 하나가 된다
    assert second == (
        "orders.ordering_customer_count: Number of distinct customers who placed at least one order.; "
        'SQL: SELECT COUNT(DISTINCT customer_id) FROM "order"'
    )
    assert third.startswith("products.priced_product_count: ") and "; notes:" not in third
    # 순서는 파일 그대로
    doc["metrics"].reverse()
    assert S.render(doc).removeprefix(S.PREFIX).split(" || ")[0] == third


def test_render_collapses_only_breaking_whitespace():
    doc = _doc()
    m = doc["metrics"][0]
    m["description"] = "a  b\t c \n\n   d\r\ne f"
    m["sql"] = "SELECT 1\n   ,  2"
    m["instructions"] = "line1\n"
    text = S.render(doc)
    part = text.removeprefix(S.PREFIX).split(" || ")[0]
    assert part.startswith("orders.paid_order_count: a  b\t c d e f; ")
    assert "notes: line1 ; SQL: SELECT 1 ,  2" in part
    assert len(text.splitlines()) == 1


def test_empty_description_drops_colon():
    doc = _doc()
    doc["metrics"][1]["description"] = ""
    assert " || orders.ordering_customer_count; SQL: " in S.render(doc)


def test_render_over_limit_rejected():
    doc = _doc()
    doc["metrics"][0]["instructions"] = "x" * S.MAX_CHARS
    with pytest.raises(S.SemanticError, match="정의 글이 한도를 넘습니다"):
        S.render(doc)


def _mutate(path, value):
    def f(doc):
        target = doc
        for k in path[:-1]:
            target = target[k]
        if value is _DEL:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return doc

    return f


_DEL = object()


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (_mutate(["format"], "t2s-semantic/2"), "format이"),
        (_mutate(["dialect"], "postgres"), "dialect가"),
        (_mutate(["metrics"], []), "승인 지표가 없습니다"),
        (_mutate(["tables"], []), "tables가"),
        (_mutate(["extra"], 1), "정의 파일에 모르는 칸이 1개"),
        (_mutate(["excluded"], _DEL), "정의 파일에 칸이 없습니다: excluded"),
        (_mutate(["source", "kind"], "local_file"), "source의 kind가"),
        (_mutate(["source", "model"], _DEL), "source에 칸이 없습니다: model"),
        (_mutate(["source", "secret_note"], "SECRETVALUE"), "source에 모르는 칸이 1개"),
        (_mutate(["source", "revision"], True), "revision이"),
        (_mutate(["metrics", 1, "sql"], " "), "지표 1번 항목의 sql가"),
        (_mutate(["metrics", 2, "synonyms"], "products"), "지표 2번 항목의 synonyms가"),
        (_mutate(["metrics", 0, "description"], _DEL), "지표 0번 항목에 칸이 없습니다: description"),
        (_mutate(["metrics", 0, "discovery"], "SECRETVALUE"), "지표 0번 항목에 모르는 칸이 1개"),
        (_mutate(["excluded", "draft_metrics"], -1), "draft_metrics가"),
    ],
)
def test_shape_errors(tmp_path, change, words):
    doc = change(copy.deepcopy(_doc()))
    with pytest.raises(S.SemanticError, match=words) as e:
        S.load_semantic(_write(tmp_path, doc))
    assert "SECRETVALUE" not in str(e.value)


@pytest.mark.parametrize("status", ["draft", "Approved", None, _DEL])
def test_one_unapproved_item_rejects_whole_file(tmp_path, status):
    doc = _doc()
    doc["metrics"][2]["description"] = "SECRETVALUE"
    if status is _DEL:
        del doc["metrics"][2]["status"]
    else:
        doc["metrics"][2]["status"] = status
    with pytest.raises(S.NotApprovedError, match=r"승인 전 항목이 들어 있어 받아들이지 않습니다\(지표 2번\)") as e:
        S.load_semantic(_write(tmp_path, doc))
    assert "SECRETVALUE" not in str(e.value)


def test_unapproved_reported_before_other_shape_errors(tmp_path):
    doc = _doc()
    doc["metrics"][0]["unknown"] = 1
    doc["metrics"][1]["status"] = "draft"
    with pytest.raises(S.NotApprovedError):
        S.load_semantic(_write(tmp_path, doc))


def test_duplicate_keys_rejected(tmp_path):
    raw = FIXTURE.read_text(encoding="utf-8").replace(
        '"status": "approved",', '"status": "draft", "status": "approved",', 1
    )
    with pytest.raises(S.SemanticError, match="겹쳐"):
        S.load_semantic(_write(tmp_path, raw))


def test_not_json_missing_and_too_big(tmp_path):
    with pytest.raises(S.SemanticError, match="JSON으로 읽지 못했습니다"):
        S.load_semantic(_write(tmp_path, "{nope"))
    with pytest.raises(S.SemanticError, match="정의 파일이 없습니다"):
        S.load_semantic(tmp_path / "none.json")
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (S.MAX_FILE_BYTES + 1))
    with pytest.raises(S.SemanticError, match="크기 한도"):
        S.load_semantic(big)
    with pytest.raises(S.SemanticError, match="JSON 객체가 아닙니다"):
        S.load_semantic(_write(tmp_path, "[1]"))


def test_optional_source_fields(tmp_path):
    doc = _doc()
    doc["source"].update({"tenant": "t1", "revision": 12})
    assert S.load_semantic(_write(tmp_path, doc))["source"]["revision"] == 12


def test_missing_tables(shop_db):
    src = SqliteSource(shop_db)
    doc = S.load_semantic(FIXTURE)
    assert S.missing_tables(doc, src) == (0, 3)
    doc["tables"] = ["main.ORDER", "Product", "warehouse", "sales.region"]
    assert S.missing_tables(doc, src) == (2, 4)
