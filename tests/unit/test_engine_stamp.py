"""엔진 표식(ENGINE.json). 표식이 없으면 지금 동작(루트의 git), 있으면 표식의 값, 깔린 코드가 다르면 미커밋."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from t2s import engine
from t2s.cli import ask_cmd
from t2s.cli.main import main
from t2s.eval import bird
from t2s.runner import batch

PACKAGE = Path(engine.__file__).resolve().parent


def _stamp(home: Path, package_dir: Path, **over) -> dict:
    """빌드와 같은 꼴의 엔진 자리: home/src/t2s에 package_dir 사본, 표식의 files·checksum은 그 자리의 파일."""
    shutil.copytree(package_dir, home / "src" / "t2s", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    files = engine.folder_files(home, skip_stamp=True)
    stamp = {
        "engine": "p3",
        "version": "0.1.0",
        "source_repo": "text2sql-py",
        "commit": "c" * 40,
        "src_tree": "t" * 40,
        "dirty": False,
        "files": files,
        "checksum": engine.files_checksum(files),
        **over,
    }
    (home / engine.STAMP_FILE).write_text(json.dumps(stamp), encoding="utf-8")
    return stamp


def _git_repo(path: Path) -> Path:
    path.mkdir()
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-q")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return path


@pytest.fixture(autouse=True)
def _no_engine_home(monkeypatch):
    monkeypatch.delenv(engine.ENV_HOME, raising=False)


def test_no_stamp_keeps_git_behaviour(tmp_path, monkeypatch):
    assert engine.engine_stamp() is None
    assert engine.stamp_matches() is None
    assert engine.git_from_stamp() is None
    assert engine.engine_record() is None
    assert batch.git_info(tmp_path) == {"head": None, "src_tree": None, "dirty": None}
    assert bird.git_info(tmp_path) == {"head": None, "src_tree": None, "dirty": None}
    assert ask_cmd.git_head(tmp_path) is None
    repo = _git_repo(tmp_path / "repo")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert (
        batch.git_info(repo)
        == bird.git_info(repo)
        == {"head": head, "src_tree": batch.git_info(repo)["src_tree"], "dirty": False}
    )
    assert ask_cmd.git_head(repo) == head
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert batch.git_info(repo)["dirty"] is True and bird.git_info(repo)["dirty"] is True
    # 환경변수가 있어도 표식 파일이 없으면 지금 동작 그대로
    monkeypatch.setenv(engine.ENV_HOME, str(tmp_path / "no-engine"))
    assert engine.engine_stamp() is None
    assert batch.git_info(repo)["head"] == head


def test_stamp_values_replace_git(tmp_path, monkeypatch):
    home = tmp_path / "engine"
    stamp = _stamp(home, PACKAGE)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    want = {"head": stamp["commit"], "src_tree": stamp["src_tree"], "dirty": False}
    # 루트가 git 저장소여도 표식을 본다(사용자 프로젝트의 git을 기록하지 않는다)
    repo = _git_repo(tmp_path / "project")
    for root in (tmp_path, repo):
        assert batch.git_info(root) == want
        assert bird.git_info(root) == want
        assert ask_cmd.git_head(root) == stamp["commit"]
    assert engine.engine_stamp() == {k: stamp[k] for k in ("commit", "src_tree", "dirty", "version", "checksum")}
    assert engine.stamp_matches() is True
    assert engine.engine_record() == {
        "version": "0.1.0",
        "checksum": stamp["checksum"],
        "matches": True,
        "home_matches": True,
        "installed_matches": True,
    }


def test_dirty_stamp_is_dirty(tmp_path, monkeypatch):
    home = tmp_path / "engine"
    _stamp(home, PACKAGE, dirty=True)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert batch.git_info(tmp_path)["dirty"] is True
    assert bird.git_info(tmp_path)["dirty"] is True


def test_changed_installed_file_is_dirty(tmp_path, monkeypatch):
    copy = tmp_path / "site" / "t2s"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    home = tmp_path / "engine"
    _stamp(home, copy)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert engine.stamp_matches(copy) is True
    assert engine.git_from_stamp(copy)["dirty"] is False
    target = copy / "engine.py"
    target.write_text(target.read_text(encoding="utf-8") + "# 바꿈\n", encoding="utf-8")
    assert engine.stamp_matches(copy) is False
    assert engine.git_from_stamp(copy)["dirty"] is True
    assert engine.engine_record(copy)["matches"] is False
    # 표식에 없는 파일이 더 깔려 있어도 다르다
    target.write_text(target.read_text(encoding="utf-8").removesuffix("# 바꿈\n"), encoding="utf-8")
    assert engine.stamp_matches(copy) is True
    (copy / "extra.py").write_text("", encoding="utf-8")
    assert engine.stamp_matches(copy) is False


def test_stamp_without_files_is_not_trusted(tmp_path, monkeypatch):
    home = tmp_path / "engine"
    _stamp(home, PACKAGE, files=None)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert engine.stamp_matches() is False
    assert engine.git_from_stamp()["dirty"] is True


def test_broken_stamp_does_not_use_root_git(tmp_path, monkeypatch):
    # 표식 파일이 있는데 읽을 수 없으면 루트(사용자 프로젝트)의 git을 엔진 커밋으로 적지 않는다
    repo = _git_repo(tmp_path / "proj")
    home = tmp_path / "engine"
    home.mkdir()
    (home / engine.STAMP_FILE).write_text("{깨짐", encoding="utf-8")
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert engine.engine_stamp() is None
    for info in (batch.git_info(repo), bird.git_info(repo)):
        assert info["head"] is None and info["dirty"] is not False
    assert ask_cmd.git_head(repo) is None


def test_unstamped_git_engine_home_uses_engine_git(tmp_path, monkeypatch):
    # 표식 없는 엔진 자리(개발용: 저장소 루트)가 git이면 작업 루트가 아니라 엔진 자리의 git을 적는다
    home = _git_repo(tmp_path / "engine")
    (home / "src" / "t2s").mkdir()
    (home / "src" / "t2s" / "b.py").write_text("y = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(home), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(home), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "y"],
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "-C", str(home), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    root = _git_repo(tmp_path / "project")
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    info = batch.git_info(root)
    assert info["head"] == head and info["dirty"] is False
    (home / "src" / "t2s" / "b.py").write_text("y = 2\n", encoding="utf-8")
    assert batch.git_info(root)["dirty"] is True


def test_changed_home_file_is_dirty_and_verify_fails(tmp_path, monkeypatch, capsys):
    # 엔진 자리(플러그인 폴더)를 고치면 깔린 패키지가 표식과 같아도 기록이 미커밋이고 verify도 1이다(같은 함수)
    home = tmp_path / "engine"
    _stamp(home, PACKAGE)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert main(["verify"]) == 0
    capsys.readouterr()
    target = home / "src/t2s/pipeline/judge.py"
    target.write_text(target.read_text(encoding="utf-8") + "# 바꿈\n", encoding="utf-8")
    assert engine.engine_check() == {"home": ["바뀜 src/t2s/pipeline/judge.py"], "installed": [], "matches": False}
    assert batch.git_info(tmp_path)["dirty"] is True and bird.git_info(tmp_path)["dirty"] is True
    rec = engine.engine_record()
    assert (rec["matches"], rec["home_matches"], rec["installed_matches"]) == (False, False, True)
    assert main(["verify"]) == 1
    assert capsys.readouterr().out.splitlines() == ["다름: 1곳", "  바뀜 src/t2s/pipeline/judge.py"]


def test_changed_installed_file_fails_verify(tmp_path, monkeypatch, capsys):
    # 깔린 패키지를 고치면 엔진 자리가 표식과 같아도 verify가 1이다(기록과 같은 판정)
    copy = tmp_path / "site" / "t2s"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    home = tmp_path / "engine"
    _stamp(home, copy)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    monkeypatch.setattr(engine, "__file__", str(copy / "engine.py"))  # 지금 돌고 있는 패키지를 사본으로 본다
    assert main(["verify"]) == 0
    capsys.readouterr()
    target = copy / "pipeline" / "judge.py"
    target.write_text(target.read_text(encoding="utf-8") + "# 바꿈\n", encoding="utf-8")
    assert engine.git_from_stamp()["dirty"] is True
    rec = engine.engine_record()
    assert (rec["matches"], rec["home_matches"], rec["installed_matches"]) == (False, True, False)
    assert main(["verify"]) == 1
    assert capsys.readouterr().out.splitlines() == ["다름: 1곳", "  깔린 패키지 바뀜 src/t2s/pipeline/judge.py"]


def test_check_unavailable_is_not_same(tmp_path, monkeypatch):
    # 대조 중 표식을 다시 읽지 못하면(None) 같음이 아니라 판정 불가이고, 기록은 미커밋이다
    home = tmp_path / "engine"
    _stamp(home, PACKAGE)
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    for name in ("engine_differences", "installed_differences"):
        with monkeypatch.context() as m:
            m.setattr(engine, name, lambda *a, **k: None)
            assert engine.engine_check() is None
            assert engine.git_from_stamp()["dirty"] is True
