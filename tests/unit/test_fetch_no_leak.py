"""가짜 정답·문항에 표식 글자를 넣고 정상·실패 경로를 돌려, 잡은 출력과 예외 글 어디에도 표식이 없는지 본다.

모든 경로는 원격 받기(가짜 curl)로 돈다.
"""

import hashlib
import io
import json
import zipfile

import pytest

from fetch_helpers import MARK, install_fake_curl, make_fake_source
from t2s.cli.main import main
from t2s.fetch import bird


@pytest.fixture
def env(tmp_path, monkeypatch):
    src, manifest = make_fake_source(tmp_path)
    install_fake_curl(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "harness.toml").write_text("[defaults]\n", encoding="utf-8")
    monkeypatch.setenv("T2S_ROOT", str(root))
    monkeypatch.setattr(bird, "load_manifest", lambda: manifest)
    return src, manifest, root


def _run(capsys, argv):
    rc = main(argv)
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def test_success_paths_do_not_leak(env, capsys):
    rc, text = _run(capsys, ["fetch", "bird"])
    assert rc == 0 and "getbird 완료" in text
    assert MARK not in text
    rc, text = _run(capsys, ["fetch", "bird"])
    assert rc == 0 and MARK not in text


def test_failure_paths_do_not_leak(env, capsys):
    _src, manifest, _root = env
    # 정답 파일의 해시가 표와 다름 → 3단계에서 멈춤
    manifest["answers"]["questions"]["sha256"] = "0" * 64
    rc, text = _run(capsys, ["fetch", "bird"])
    assert rc == 1 and "중단 3단계" in text and MARK not in text


def test_bad_official_keys_do_not_leak(env, capsys):
    src, manifest, _root = env
    # 키 순서가 다른 공식 파일을 zip에 넣고 해시 표도 그것에 맞춘다
    zpath = src / manifest["archive"]["path"]
    with zipfile.ZipFile(zpath) as zf:
        items = json.loads(zf.read("minidev/MINIDEV/mini_dev_sqlite.json"))
    items[1] = {k: items[1][k] for k in ("db_id", "question_id", "question", "evidence", "SQL", "difficulty")}
    data = json.dumps(items).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(zpath) as old, zipfile.ZipFile(buf, "w") as new:
        for n in old.namelist():
            new.writestr(n, data if n.endswith("mini_dev_sqlite.json") else old.read(n))
    zpath.write_bytes(buf.getvalue())
    manifest["archive"].update(sha256=hashlib.sha256(buf.getvalue()).hexdigest(), size=len(buf.getvalue()))
    manifest["answers"]["questions"].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    rc, text = _run(capsys, ["fetch", "bird"])
    assert rc == 1
    assert "중단 5단계: 공식 문제 1번 항목의 키가 예상과 다름" in text
    assert MARK not in text


def test_exception_text_has_no_marker(env):
    _src, manifest, root = env
    manifest["answers"]["sql"]["sha256"] = "0" * 64
    with pytest.raises(bird.FetchStopped) as e:
        bird.fetch_bird(root, manifest=manifest)
    assert MARK not in str(e.value) and MARK not in (e.value.action or "")
