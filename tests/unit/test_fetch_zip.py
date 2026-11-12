import zipfile

import pytest

from fetch_helpers import make_fake_source
from t2s.fetch.bird import extract
from t2s.fetch.steps import FetchStopped


def _root_with_zip(tmp_path, ts, manifest):
    root = tmp_path / "repo"
    z = root / manifest["archive"]["path"]
    z.parent.mkdir(parents=True)
    z.write_bytes((ts / manifest["archive"]["path"]).read_bytes())
    return root


def test_extracts_only_expected_members(tmp_path):
    ts, manifest = make_fake_source(tmp_path)
    root = _root_with_zip(tmp_path, ts, manifest)
    line = extract(root, manifest)
    assert line == "풀기: DB 2 + 정답 2, 해시 4 일치"
    files = sorted(p.relative_to(root).as_posix() for p in (root / "data").rglob("*") if p.is_file())
    assert files == [
        "data/bird-minidev/download/minidev.zip",
        "data/bird-minidev/extracted/minidev/MINIDEV/dev_databases/alpha/alpha.sqlite",
        "data/bird-minidev/extracted/minidev/MINIDEV/dev_databases/beta/beta.sqlite",
    ]
    assert sorted(p.name for p in (root / "gold/bird-minidev").iterdir()) == [
        "mini_dev_sqlite.json",
        "mini_dev_sqlite_gold.sql",
    ]


def test_unsafe_member_stops(tmp_path):
    ts, manifest = make_fake_source(tmp_path)
    root = _root_with_zip(tmp_path, ts, manifest)
    with zipfile.ZipFile(root / manifest["archive"]["path"], "a") as zf:
        zf.writestr("../evil.txt", b"x")
    with pytest.raises(FetchStopped, match="안전하지 않은 항목 1개"):
        extract(root, manifest)
    assert not (root / "data/bird-minidev/extracted").exists()
    assert not (tmp_path / "evil.txt").exists() and not (root / "data/evil.txt").exists()


def test_missing_member_stops(tmp_path):
    ts, manifest = make_fake_source(tmp_path)
    root = _root_with_zip(tmp_path, ts, manifest)
    manifest["databases"].append(
        {"db_id": "gamma", "member": "minidev/MINIDEV/dev_databases/gamma/gamma.sqlite", "sha256": "0" * 64, "size": 1}
    )
    with pytest.raises(FetchStopped, match="빠진 항목 1개"):
        extract(root, manifest)


def test_corrupt_zip_stops(tmp_path):
    ts, manifest = make_fake_source(tmp_path)
    root = _root_with_zip(tmp_path, ts, manifest)
    (root / manifest["archive"]["path"]).write_bytes(b"not a zip")
    with pytest.raises(FetchStopped, match="zip을 읽지 못했다"):
        extract(root, manifest)
