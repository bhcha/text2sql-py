"""스터디 Olist 받기를 가짜 gh(작은 가짜 파일 복사)로 시험한다. 실제 네트워크는 쓰지 않는다."""

import json

import pytest

from fetch_helpers import MARK, install_fake_gh, make_fake_olist_source
from t2s.cli.main import main
from t2s.fetch import olist
from t2s.fetch.olist import fetch_olist
from t2s.fetch.steps import FetchStopped


@pytest.fixture
def setup(tmp_path, monkeypatch):
    src, manifest = make_fake_olist_source(tmp_path)
    log = install_fake_gh(tmp_path, monkeypatch, src)
    root = tmp_path / "repo"
    root.mkdir()
    return src, manifest, root, log


def _downloads(log) -> int:
    return sum(1 for line in log.read_text().splitlines() if "-H" in json.loads(line))


def test_happy_path_then_skip(setup):
    _src, manifest, root, log = setup
    lines = fetch_olist(root, manifest=manifest)
    assert lines[0] == "getolist 완료" and len(lines) <= 10
    assert lines[1].startswith("1 사전 확인") and "gh version 9.9.9" in lines[1]
    assert lines[2].startswith("2 받기(커밋 aaaaaaaa): olist.db 받음")
    assert lines[3] == "3 해시: 5개 일치, 모델 입력 2문항(정답 키 없음)"
    assert lines[5] == "5 정답 쪽: gold/olist/study-v0.3.json (채점 단계만 읽는다)"
    assert lines[-1] == "라이선스 줄"
    for item in manifest["files"]:
        assert (root / item["path"]).is_file()
    assert not list(root.rglob("*.part"))
    assert _downloads(log) == 5
    again = fetch_olist(root, manifest=manifest)
    assert again[2].count("건너뜀") == 5
    assert _downloads(log) == 5
    assert not any(MARK in x or "someone" in x for x in lines + again)


def test_gold_only_under_gold(setup):
    _src, manifest, root, _log = setup
    fetch_olist(root, manifest=manifest)
    for p in (root / "data").rglob("*"):
        if p.is_file():
            assert MARK.encode() not in p.read_bytes()
    assert MARK.encode() in (root / "gold/olist/study-v0.3.json").read_bytes()


def test_hash_mismatch_stops_and_keeps_rejected(setup):
    _src, manifest, root, _log = setup
    manifest["files"][0]["sha256"] = "f" * 64
    with pytest.raises(FetchStopped) as e:
        fetch_olist(root, manifest=manifest)
    assert e.value.step == "2단계"
    assert "sha256 불일치 (기대 ffffffff" in e.value.message
    assert len(list((root / "data/olist").glob("olist.db.rejected-*"))) == 1
    assert not (root / "data/olist/olist.db").exists()
    assert "받아들이지 않는다" in e.value.action


def test_size_mismatch_stops(setup):
    _src, manifest, root, _log = setup
    manifest["files"][0]["size"] += 1
    with pytest.raises(FetchStopped, match="sha256 불일치"):
        fetch_olist(root, manifest=manifest)
    assert not (root / "data/olist/olist.db").exists()


def test_existing_different_file_stops_and_is_kept(setup):
    _src, manifest, root, _log = setup
    dest = root / "data/olist/olist.db"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"other")
    with pytest.raises(FetchStopped) as e:
        fetch_olist(root, manifest=manifest)
    assert "이미 있는 olist.db" in e.value.message and "지운 뒤" in e.value.action
    assert dest.read_bytes() == b"other"


def test_gh_failure_leaves_no_part(setup):
    _src, manifest, root, _log = setup
    manifest["files"][1]["remote"] = "FAIL.json"
    with pytest.raises(FetchStopped) as e:
        fetch_olist(root, manifest=manifest)
    assert "받기 실패(gh 1)" in e.value.message
    assert not list(root.rglob("*.part"))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"auth": False}, "gh 로그인 안 됨"), ({"access": False}, "접근 권한 없음")],
)
def test_preflight_auth_and_access(tmp_path, monkeypatch, kwargs, message):
    src, manifest = make_fake_olist_source(tmp_path)
    install_fake_gh(tmp_path, monkeypatch, src, **kwargs)
    with pytest.raises(FetchStopped) as e:
        fetch_olist(tmp_path, manifest=manifest)
    assert e.value.step == "1단계" and message in e.value.message


def test_preflight_no_gh(tmp_path, monkeypatch):
    _src, manifest = make_fake_olist_source(tmp_path)
    monkeypatch.setattr(olist, "GH", str(tmp_path / "no-such-gh"))
    with pytest.raises(FetchStopped) as e:
        fetch_olist(tmp_path, manifest=manifest)
    assert e.value.step == "1단계" and e.value.message == "gh 없음"


def test_model_inputs_extra_key_stops(tmp_path, monkeypatch):
    src, manifest = make_fake_olist_source(tmp_path, extra_key="gold_sql")
    install_fake_gh(tmp_path, monkeypatch, src)
    with pytest.raises(FetchStopped) as e:
        fetch_olist(tmp_path, manifest=manifest)
    assert e.value.step == "3단계" and e.value.message == "model_inputs에 허용하지 않은 키: gold_sql"


@pytest.mark.parametrize("bad", ["../escape/x.db", "/abs/x.db"])
def test_unsafe_path_stops_before_download(setup, bad):
    _src, manifest, root, log = setup
    manifest["files"][2]["path"] = bad
    with pytest.raises(FetchStopped, match="허용되지 않는 경로"):
        fetch_olist(root, manifest=manifest)
    assert not log.exists()


def _cli(capsys, manifest, root, monkeypatch):
    (root / "harness.toml").write_text("[defaults]\n", encoding="utf-8")
    monkeypatch.setenv("T2S_ROOT", str(root))
    monkeypatch.setattr(olist, "load_manifest", lambda: manifest)
    rc = main(["fetch", "olist"])
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def test_cli_paths_do_not_leak(setup, capsys, monkeypatch):
    _src, manifest, root, _log = setup
    rc, text = _cli(capsys, manifest, root, monkeypatch)
    assert rc == 0 and "getolist 완료" in text and MARK not in text
    gold = next(it for it in manifest["files"] if it["role"] == "gold")
    (root / gold["path"]).write_bytes(b"changed")
    rc, text = _cli(capsys, manifest, root, monkeypatch)
    assert rc == 1 and "중단 2단계" in text and MARK not in text
