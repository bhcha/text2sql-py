"""init: 작업 폴더(.text2sql/) 만들기. 엔진 자리의 기본 설정·기준선을 복사하고, 있는 파일은 덮어쓰지 않는다."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from t2s import engine
from t2s.cli import data_cmds
from t2s.cli.main import main

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def engine_home(tmp_path, monkeypatch):
    home = tmp_path / "engine"
    (home / "assets").mkdir(parents=True)
    shutil.copyfile(REPO / "harness.toml", home / "assets/harness.toml")
    shutil.copytree(REPO / "baselines", home / "assets/baselines")
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    monkeypatch.delenv("T2S_ROOT", raising=False)
    monkeypatch.setattr(data_cmds, "install_guard", lambda *a, **kw: None)  # 감시 훅은 뗄 수 없다
    return home


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_new_folder(engine_home, tmp_path, capsys, monkeypatch):
    root = tmp_path / "proj/.text2sql"
    assert main(["init", "--root", str(root)]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith(f"작업 폴더를 만들었습니다: {root.resolve()}")
    assert (root / ".gitignore").read_text() == "*\n"
    assert (root / "format").read_text() == "1\n"
    assert (root / "harness.toml").read_bytes() == (REPO / "harness.toml").read_bytes()
    assert sorted(p.name for p in (root / "baselines").iterdir()) == sorted(
        p.name for p in (REPO / "baselines").iterdir()
    )
    rec = json.loads((root / "init.json").read_text())
    assert rec["format"] == 1 and len(rec["harness_toml_sha256"]) == 64 and rec["engine"] is None
    # 정답 폴더와 자료는 만들지 않는다
    assert not (root / "gold").exists() and not (root / "data").exists()
    # 완료 조건: init 뒤 T2S_ROOT를 주고 datasets가 0
    monkeypatch.setenv("T2S_ROOT", str(root))
    assert main(["datasets"]) == 0
    assert "bird-minidev" in capsys.readouterr().out


def test_again_does_not_overwrite(engine_home, tmp_path, capsys):
    root = tmp_path / ".text2sql"
    assert main(["init", "--root", str(root)]) == 0
    (root / "harness.toml").write_text("# 사용자가 고침\n", encoding="utf-8")
    (root / "baselines/ts-pure-fix.json").write_text("{}", encoding="utf-8")
    before = _tree(root)
    capsys.readouterr()
    assert main(["init", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == f"작업 폴더가 이미 있습니다: {root.resolve()} (아무것도 덮어쓰지 않음)"
    assert _tree(root) == before


def test_half_made_folder_keeps_existing_files(engine_home, tmp_path):
    root = tmp_path / ".text2sql"
    root.mkdir()
    (root / "harness.toml").write_text("# 먼저 있던 것\n", encoding="utf-8")
    assert main(["init", "--root", str(root)]) == 0
    assert (root / "harness.toml").read_text() == "# 먼저 있던 것\n"
    assert (root / "format").is_file() and (root / "baselines").is_dir()


def test_refresh_reports_only(engine_home, tmp_path, capsys):
    root = tmp_path / ".text2sql"
    assert main(["init", "--root", str(root)]) == 0
    capsys.readouterr()
    assert main(["init", "--root", str(root), "--refresh"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["엔진 기본 설정: 그대로 · 작업 폴더 설정: 만들 때 그대로", "엔진 기준선: 새로 0개 · 바뀜 0개"]
    # 엔진 쪽이 바뀌고 사용자도 고친 경우: 알리기만 하고 쓰지 않는다
    (engine_home / "assets/harness.toml").write_text("# 새 기본\n", encoding="utf-8")
    (engine_home / "assets/baselines/new.json").write_text("{}", encoding="utf-8")
    (root / "harness.toml").write_text("# 사용자가 고침\n", encoding="utf-8")
    before = _tree(root)
    assert main(["init", "--root", str(root), "--refresh"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "엔진 기본 설정: 바뀜 · 작업 폴더 설정: 고쳐 씀"
    assert lines[1] == "엔진 기준선: 새로 1개 · 바뀜 0개 (new.json)"
    assert lines[2].startswith("작업 폴더의 파일은 고치지 않았습니다.")
    assert _tree(root) == before


def test_refresh_without_folder(engine_home, tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path / ".text2sql"), "--refresh"]) == 2
    assert "먼저 init" in capsys.readouterr().err


def test_no_engine_home(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(engine.ENV_HOME, raising=False)
    root = tmp_path / ".text2sql"
    assert main(["init", "--root", str(root)]) == 2
    assert capsys.readouterr().out.strip() == "저장소 클론에서는 필요 없습니다(작업 루트는 저장소 루트)"
    assert not root.exists()


def test_engine_without_assets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(engine.ENV_HOME, str(tmp_path / "empty-engine"))
    assert main(["init", "--root", str(tmp_path / ".text2sql")]) == 1
    assert "엔진의 기본 설정이 없습니다" in capsys.readouterr().err
    assert not (tmp_path / ".text2sql").exists()


def test_engine_without_baselines(tmp_path, monkeypatch, capsys):
    home = tmp_path / "engine"
    (home / "assets").mkdir(parents=True)
    shutil.copyfile(REPO / "harness.toml", home / "assets/harness.toml")
    monkeypatch.setenv(engine.ENV_HOME, str(home))
    assert main(["init", "--root", str(tmp_path / ".text2sql")]) == 1
    assert "엔진의 기준선이 없습니다" in capsys.readouterr().err
    assert not (tmp_path / ".text2sql").exists()


def test_repo_layout_engine_home(tmp_path, monkeypatch):
    """개발용 갈래: 엔진 자리가 저장소 루트면(assets/ 없음) 루트의 harness.toml·baselines/를 복사한다."""
    monkeypatch.setenv(engine.ENV_HOME, str(REPO))
    monkeypatch.setattr(data_cmds, "install_guard", lambda *a, **kw: None)
    root = tmp_path / ".text2sql"
    assert main(["init", "--root", str(root)]) == 0
    assert (root / "harness.toml").read_bytes() == (REPO / "harness.toml").read_bytes()
    assert (root / "baselines/ts-pure-fix.json").read_bytes() == (REPO / "baselines/ts-pure-fix.json").read_bytes()
