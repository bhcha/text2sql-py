"""원격 받기 길을 가짜 curl(작은 가짜 파일 복사)로 시험한다. 실제 네트워크는 쓰지 않는다."""

import pytest

from fetch_helpers import MARK, install_fake_curl, make_fake_source
from t2s.fetch import download
from t2s.fetch.bird import fetch_bird
from t2s.fetch.steps import FetchStopped


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ts, manifest = make_fake_source(tmp_path)
    install_fake_curl(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    return ts, manifest, root


def test_remote_happy_path_then_skip(setup):
    _ts, manifest, root = setup
    lines = fetch_bird(root, manifest=manifest)
    assert lines[1].startswith("1 사전 확인") and "curl 있음" in lines[1]
    assert lines[2].startswith("2 zip: 받음")
    assert lines[3] == "3 풀기: DB 2 + 정답 2, 해시 4 일치"
    assert "모델: 5파일 받음" in lines[6]
    assert "해시 11개 일치" in lines[7]
    assert not list(root.rglob("*.part"))
    again = fetch_bird(root, manifest=manifest)
    assert "zip: 건너뜀" in again[2]
    assert not any(MARK in x for x in lines + again)


def test_hash_mismatch_stops_and_keeps_rejected(setup, tmp_path):
    _ts, manifest, root = setup
    manifest["archive"]["sha256"] = "f" * 64
    with pytest.raises(FetchStopped) as e:
        fetch_bird(root, manifest=manifest)
    assert e.value.step == "2단계"
    assert "sha256 불일치 (기대 ffffffff" in e.value.message
    rejected = list((root / "data/bird-minidev/download").glob("minidev.zip.rejected-*"))
    assert len(rejected) == 1
    assert not (root / "data/bird-minidev/download/minidev.zip").exists()
    assert "받아들이지 않는다" in e.value.action


def test_size_mismatch_stops(setup):
    _ts, manifest, root = setup
    manifest["archive"]["size"] += 1
    with pytest.raises(FetchStopped, match="sha256 불일치|네트워크 오류"):
        fetch_bird(root, manifest=manifest)
    assert not (root / "data/bird-minidev/download/minidev.zip").exists()


def test_network_error_keeps_part(setup):
    _ts, manifest, root = setup
    manifest["archive"]["url"] = "fake://FAIL"
    with pytest.raises(FetchStopped) as e:
        fetch_bird(root, manifest=manifest)
    assert "네트워크 오류(curl 7)" in e.value.message
    assert (root / "data/bird-minidev/download/minidev.zip.part").exists()
    assert e.value.action == "다시 /getbird를 부르면 이어받는다"


def test_complete_part_is_accepted_without_curl(setup, monkeypatch):
    ts, manifest, root = setup
    part = root / "data/bird-minidev/download/minidev.zip.part"
    part.parent.mkdir(parents=True)
    part.write_bytes((ts / manifest["archive"]["path"]).read_bytes())
    monkeypatch.setattr(download, "CURL", "/nonexistent/curl")
    r = download.fetch_file(
        dest=root / manifest["archive"]["path"],
        url="fake://x",
        sha256=manifest["archive"]["sha256"],
        size=manifest["archive"]["size"],
        label="minidev.zip",
        step="2단계",
    )
    assert r == "받음"


def test_missing_curl_stops_in_preflight(setup, monkeypatch):
    _ts, manifest, root = setup
    monkeypatch.setattr(download, "CURL", "/nonexistent/curl")
    with pytest.raises(FetchStopped) as e:
        fetch_bird(root, manifest=manifest)
    assert e.value.step == "1단계" and "curl 없음" in e.value.message
