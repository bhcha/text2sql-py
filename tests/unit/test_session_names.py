"""세션 이름 규칙(YYYY-MM-DD-<데이터셋>-<종류>-<16진 6자>)과 옛 이름 읽기, 색인 덧붙이기."""

from __future__ import annotations

import json
from datetime import date

import pytest

from t2s.session import index as I
from t2s.session import names as N

DAY = date(2026, 11, 23)


def test_new_name_shape():
    name = N.new_name("bird-minidev", "set", today=DAY, token="665ae5")
    assert name == "2026-11-23-bird-minidev-set-665ae5"
    made = N.new_name("olist", "ask")
    p = N.parse(made)
    assert p is not None and p.kind == "ask" and p.dataset == "olist" and len(p.hex) == 6 and not p.legacy
    assert p.date == date.today().isoformat()


def test_new_name_refuses_bad_kind_or_dataset():
    with pytest.raises(ValueError):
        N.new_name("bird-minidev", "run")
    for bad in ("", "../x", "a/b", "-x", "a b"):
        with pytest.raises(ValueError):
            N.new_name(bad, "set")
    with pytest.raises(ValueError):
        N.new_name("shop", "set", token="xyz")


def test_pair_names_and_parse_roles():
    base = N.new_name("bird-minidev", "set", today=DAY, token="00ab12")
    pair = N.pair_names(base)
    assert pair == {"harness": f"{base}-harness", "single": f"{base}-single"}
    for role, name in pair.items():
        p = N.parse(name)
        assert p.role == role and p.base == base and p.dataset == "bird-minidev" and p.kind == "set"


def test_parse_every_kind_and_hyphenated_dataset():
    for kind in N.KINDS:
        p = N.parse(f"2026-11-23-my-set-db-{kind}-abcdef")
        assert p is not None and p.kind == kind and p.dataset == "my-set-db", kind


def test_parse_old_names():
    p = N.parse("20261121-bird-minidev-665ae5-harness")
    assert p.legacy and p.kind is None and p.role == "harness" and p.date == "2026-11-21"
    assert p.dataset == "bird-minidev" and p.base == "20261121-bird-minidev-665ae5"
    p = N.parse("20261116-olist-2f796e")
    assert p.legacy and p.role is None and p.dataset == "olist"


def test_parse_rejects_other_names():
    for name in ("r5", "ask", "2026-11-23-shop-run-abcdef", "2026-11-23-shop-set-ABCDEF", "../2026-11-23-x-set-abcdef"):
        assert N.parse(name) is None, name


def test_index_append_and_read(tmp_path):
    sessions = tmp_path / "sessions"
    assert I.read(sessions) == []
    env = {"CLAUDE_CODE_SESSION_ID": "abc-123"}
    I.append(sessions, I.entry("2026-11-23-shop-ask-aaaaaa", "ask", "shop", "2026-11-23T01:02:03Z", env))
    I.append(sessions, I.entry("2026-11-23-shop-set-bbbbbb", "set", "shop", "2026-11-23T01:05:00Z", {}))
    rows = I.read(sessions)
    assert [r["name"] for r in rows] == ["2026-11-23-shop-ask-aaaaaa", "2026-11-23-shop-set-bbbbbb"]
    assert rows[0]["conversation"] == {"client": "claude", "id": "abc-123"} and rows[1]["conversation"] is None
    assert rows[0]["format"] == I.INDEX_FORMAT and rows[0]["kind"] == "ask" and rows[0]["dataset"] == "shop"
    # 한 줄에 하나, 있는 줄은 건드리지 않는다
    text = I.index_path(sessions).read_text(encoding="utf-8")
    assert len(text.splitlines()) == 2 and json.loads(text.splitlines()[0])["name"] == rows[0]["name"]


def test_index_read_skips_bad_lines(tmp_path):
    path = I.index_path(tmp_path)
    path.write_text('{"name": "a"}\nnot json\n[1]\n\n{"kind": "x"}\n{"name": "b"}\n', encoding="utf-8")
    assert [r["name"] for r in I.read(tmp_path)] == ["a", "b"]


def test_conversation_ids():
    assert I.conversation({}) is None
    assert I.conversation({"CODEX_THREAD_ID": "t1"}) == {"client": "codex", "id": "t1"}
    assert I.conversation({"CLAUDE_CODE_SESSION_ID": "c1", "CODEX_THREAD_ID": "t1"})["client"] == "claude"
