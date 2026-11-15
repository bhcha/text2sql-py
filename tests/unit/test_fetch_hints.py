"""공개 evidence 파일(hints.public.json): 두 칸만, 정답 SQL 없음, 해시가 다르면 멈춤, 출력에 표식 없음."""

import json

import pytest

from fetch_helpers import MARK, install_fake_curl, make_fake_source, official_items
from t2s.cli.main import main
from t2s.fetch import bird
from t2s.fetch import public_questions as pq
from t2s.fetch.steps import FetchStopped

HINTS = "data/bird-minidev/hints.public.json"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    _src, manifest = make_fake_source(tmp_path)
    install_fake_curl(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    return manifest, root


def test_build_hints_two_keys_in_order():
    official = official_items()
    items = pq.build_public_hints(official)
    assert [tuple(it) for it in items] == [("question_id", "hint")] * 3
    assert [it["hint"] for it in items] == [o["evidence"] for o in official]
    assert pq.check_hints_against_official(items, official) == []
    bad = [dict(items[0]), {**items[1], "hint": "other"}, {"question_id": 2, "hint": "x", "SQL": "y"}]
    assert pq.check_hints_against_official(bad, official) == [
        "공개 evidence 1번 항목의 hint가 공식 파일의 evidence와 다름",
        "공개 evidence 2번 항목의 키가 다름",
    ]
    with pytest.raises(pq.PublicQuestionsError, match="1번 항목의 evidence가 글이 아님"):
        pq.build_public_hints([official[0], {**official[1], "evidence": None}])


def test_fetch_writes_hints_without_gold(setup):
    manifest, root = setup
    lines = bird.fetch_bird(root, manifest=manifest)
    path = root / HINTS
    items = json.loads(path.read_text(encoding="utf-8"))
    assert [set(it) for it in items] == [{"question_id", "hint"}] * 3
    text = path.read_text(encoding="utf-8")
    for o in official_items():
        assert o["SQL"] not in text and o["question"] not in text
    assert not any(MARK in x for x in lines)
    assert not list(root.rglob("*.part"))
    # 이미 있으면 대조만
    again = bird.fetch_bird(root, manifest=manifest)
    assert any("공개 evidence: 유지(3문항" in x for x in again)


def test_existing_user_gets_hints_on_refetch(setup):
    manifest, root = setup
    bird.fetch_bird(root, manifest=manifest)
    (root / HINTS).unlink()
    lines = bird.fetch_bird(root, manifest=manifest)
    assert any("공개 evidence: 만듦(3문항" in x for x in lines) and (root / HINTS).is_file()


def test_hash_mismatch_stops(setup):
    manifest, root = setup
    manifest["hints_public"]["sha256"] = "0" * 64
    with pytest.raises(FetchStopped) as e:
        bird.fetch_bird(root, manifest=manifest)
    assert e.value.step == "5단계" and "hints.public.json sha256 불일치" in e.value.message
    assert MARK not in str(e.value) and not (root / HINTS).exists()


def test_tampered_existing_file_stops(setup, tmp_path, monkeypatch, capsys):
    manifest, root = setup
    bird.fetch_bird(root, manifest=manifest)
    (root / HINTS).write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("T2S_ROOT", str(root))
    (root / "harness.toml").write_text("[defaults]\n", encoding="utf-8")
    monkeypatch.setattr(bird, "load_manifest", lambda: manifest)
    rc = main(["fetch", "bird"])
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert rc == 1 and "이미 있는 hints.public.json sha256 불일치" in text and MARK not in text
