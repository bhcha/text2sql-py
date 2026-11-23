"""엔진 스킬 빌드(scripts/build_engine_skills.py)와 verify. 임시 저장소 사본에서 만든다(검사 셋은 건너뛴다)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from t2s import engine
from t2s.cli.main import main

REPO = Path(__file__).resolve().parents[2]
COPY = (
    "src",
    "plugin",
    "scripts",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "LICENSE",
    "LICENSES",
    "harness.toml",
    "baselines",
    "AGENTS.md",
    "docs/provenance.md",
    ".gitignore",
)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    for name in COPY:
        src = REPO / name
        dest = r / name
        if src.is_dir():
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    (r / "plugin/skills/p3-engine/scripts/p3").chmod(0o755)
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return r


def build(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts/build_engine_skills.py"), "--skip-checks", *extra],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _stamp(repo: Path) -> dict:
    return json.loads((repo / "dist/engine-skills/skills/p3-engine/ENGINE.json").read_text(encoding="utf-8"))


def test_two_builds_same_checksum(repo):
    r1 = build(repo)
    assert r1.returncode == 0, r1.stderr
    s1 = _stamp(repo)
    r2 = build(repo)
    assert r2.returncode == 0 and _stamp(repo) == s1
    assert r1.stdout.strip() == f"만듦: dist/engine-skills/skills · 체크섬 {s1['checksum']}"
    assert s1["commit"] == _git(repo, "rev-parse", "HEAD") and s1["src_tree"] == _git(repo, "rev-parse", "HEAD:src")
    assert s1["dirty"] is False and s1["engine"] == "p3" and s1["source_repo"] == "text2sql-py"
    assert s1["checksum"] == engine.files_checksum(s1["files"]) and list(s1["files"]) == sorted(s1["files"])
    eng = repo / "dist/engine-skills/skills/p3-engine"
    for rel in ("src/t2s/cli/main.py", "assets/harness.toml", "assets/baselines/ts-pure-fix.json", "LICENSES/NOTICE"):
        assert rel in s1["files"], rel
    assert (
        (eng / "references/rules.md")
        .read_text(encoding="utf-8")
        .startswith("text2sql-py의 AGENTS.md에서 만든 사본. 여기서 고치지 않는다.\n\n# text2sql-py 운영 규칙")
    )
    assert (eng / "scripts/p3").stat().st_mode & 0o111
    skills = sorted(p.name for p in (repo / "dist/engine-skills/skills").iterdir())
    assert skills == ["connect", "getbird", "getolist", "newmodel", "p3-engine", "runset", "sessions", "text2sql"]
    assert not any(p.name == "__pycache__" for p in eng.rglob("*"))
    assert _git(repo, "status", "--porcelain") == ""  # dist/는 git 밖


def test_dirty_tree_stops_unless_allowed(repo):
    (repo / "src/t2s/cli/main.py").write_text(
        (repo / "src/t2s/cli/main.py").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    r = build(repo)
    assert r.returncode == 1 and "작업 트리가 깨끗하지 않습니다" in r.stderr
    assert not (repo / "dist").exists()
    r = build(repo, "--allow-dirty")
    assert r.returncode == 0 and _stamp(repo)["dirty"] is True


@pytest.mark.parametrize(
    ("rel", "content", "why"),
    [
        ("plugin/skills/connect/gold/x.txt", "x", "금지 폴더"),
        ("plugin/skills/connect/data/x.txt", "x", "금지 폴더"),
        ("plugin/skills/sessions/sessions/x.txt", "x", "금지 폴더"),
        ("plugin/skills/p3-engine/sessions/x.txt", "x", "금지 폴더"),
        ("plugin/skills/connect/x.sqlite", "x", "금지 파일"),
        ("plugin/skills/connect/x.zip", "x", "금지 파일"),
        ("plugin/skills/connect/hints.public.json", "{}", "금지 파일"),
        ("plugin/skills/connect/.env", "K=V", "금지 파일"),
        ("plugin/skills/connect/note.md", "자리: /Users/someone/x", "로컬 절대 경로"),
    ],
)
def test_forbidden_files_stop(repo, rel, content, why):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-f", rel)
    r = build(repo, "--allow-dirty")
    assert r.returncode == 1 and why in r.stderr, r.stderr
    assert not (repo / "dist/engine-skills").exists()


def test_skill_text_checks(repo):
    path = repo / "plugin/skills/runset/SKILL.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("준비: 이 스킬은", "준비: 이 스킬만은", 1), encoding="utf-8")
    r = build(repo, "--allow-dirty")
    assert r.returncode == 1 and "'준비' 단락이 일곱에 같지 않음" in r.stderr
    path.write_text(text.replace("\n## 지킬 것\n\n", "\n## 지킬 것\n\n- 더한 줄\n", 1), encoding="utf-8")
    r = build(repo, "--allow-dirty")
    assert r.returncode == 1 and "'지킬 것' 묶음이 일곱에 같지 않음" in r.stderr
    path.write_text(text + "\nuv run t2s datasets\n", encoding="utf-8")
    r = build(repo, "--allow-dirty")
    assert r.returncode == 1 and "runset: 'uv run' 1건" in r.stderr
    path.write_text(text.replace("name: runset", "name: run-set", 1), encoding="utf-8")
    r = build(repo, "--allow-dirty")
    assert r.returncode == 1 and "runset: 머리말 name이 폴더 이름과 다름" in r.stderr


def test_verify(repo, monkeypatch, capsys):
    assert build(repo).returncode == 0
    eng = repo / "dist/engine-skills/skills/p3-engine"
    monkeypatch.setenv(engine.ENV_HOME, str(eng))
    assert main(["verify"]) == 0
    assert capsys.readouterr().out.startswith("같음: 엔진 표식과 파일이 같습니다(판 0.1.0, 커밋 ")
    (eng / "references/egress.md").write_text("바꿈\n", encoding="utf-8")
    (eng / "extra.txt").write_text("", encoding="utf-8")
    (eng / "LICENSE").unlink()
    assert main(["verify"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["다름: 3곳", "  없음 LICENSE", "  더 있음 extra.txt", "  바뀜 references/egress.md"]


def test_verify_without_stamp(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(engine.ENV_HOME, raising=False)
    assert main(["verify"]) == 1
    assert "엔진 표식이 없습니다" in capsys.readouterr().out
    monkeypatch.setenv(engine.ENV_HOME, str(tmp_path))
    assert main(["verify"]) == 1


def test_installed_package_matches_built_stamp(repo, monkeypatch):
    """빌드한 표식의 src/t2s 몫이 지금 저장소의 패키지 파일과 같다(깔린 코드 대조가 같은 규칙을 쓴다)."""
    assert build(repo).returncode == 0
    eng = repo / "dist/engine-skills/skills/p3-engine"
    monkeypatch.setenv(engine.ENV_HOME, str(eng))
    assert engine.stamp_matches(eng / "src/t2s") is True


def test_ignored_files_are_not_copied(repo):
    """git이 추적하는 파일만 싣는다. .git/info/exclude로 가린 파일은 깨끗한 트리에서도 실리지 않는다."""
    with open(repo / ".git/info/exclude", "a", encoding="utf-8") as f:
        f.write("local-note.md\n")
    (repo / "plugin/skills/connect/local-note.md").write_text("local only\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain") == ""
    r = build(repo)
    assert r.returncode == 0, r.stderr
    out = repo / "dist/engine-skills/skills/connect"
    assert (out / "SKILL.md").is_file() and not (out / "local-note.md").exists()
