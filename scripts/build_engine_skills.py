"""text2sql-plugin에 실을 스킬 폴더를 만든다(엔진 스킬 p3-engine과 스킬 일곱).

결과: dist/engine-skills/skills/ (git 밖). 차례:
  1. 작업 트리가 깨끗한지 본다. 아니면 멈춘다(--allow-dirty면 표식에 dirty: true).
  2. 검사 셋(pytest, ruff check, ruff format --check)을 돌린다(--skip-checks는 시험용).
  3. 결과 폴더를 비우고 만든다.
  4. plugin/skills/를 복사한다(폴더째 복사는 git이 추적하는 파일만 싣는다. 무시·비추적 파일은 싣지 않는다).
  5. 엔진 본체를 p3-engine/에 복사한다: src/t2s/(캐시 제외), pyproject.toml, uv.lock, .python-version, LICENSE, LICENSES/.
  6. 사본: harness.toml·baselines/ → assets/, AGENTS.md → references/rules.md, docs/provenance.md → references/provenance.md.
  7. 금지 파일 검사(정답 폴더 이름, data·runs·var·sessions 폴더, .env*, .venv, 캐시, DB·zip 파일, 공개 evidence 파일 이름,
     글 파일 안의 로컬 절대 경로). 하나라도 있으면 멈춘다.
  8. 스킬 글 검사(uv run·"저장소 루트" 0건, 머리말 name = 폴더 이름, "준비"·"지킬 것" 묶음이 일곱에 같음).
  9. ENGINE.json을 쓴다(만든 시각은 넣지 않는다. 같은 커밋에서 두 번 만들면 체크섬이 같다).
 10. 결과 폴더와 체크섬을 한 줄로 낸다.
이 스크립트는 정답 폴더와 data/를 읽지 않는다(복사할 것을 이름으로 정하고, 결과 폴더에서 이름으로 걸러 낸다).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from t2s import engine

REPO = Path(__file__).resolve().parent.parent
OUT = Path("dist") / "engine-skills"
ENGINE_NAME = "p3-engine"
SKILLS = ("connect", "getbird", "getolist", "text2sql", "runset", "newmodel", "sessions")
ENGINE_FILES = ("pyproject.toml", "uv.lock", ".python-version", "LICENSE")
ENGINE_DIRS = ("LICENSES",)
COPIES = (("AGENTS.md", "references/rules.md"), ("docs/provenance.md", "references/provenance.md"))
CHECKS = (
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "ruff", "format", "--check", "."],
)
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".DS_Store"}

FORBIDDEN_DIRS = {"gold", "data", "runs", "var", "sessions", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".zip", ".pyc", ".part")
FORBIDDEN_NAMES = {"hints.public.json", "gold.toml"}
LOCAL_PATH = re.compile(r"(/Users/[^/\s]+|/home/[^/\s]+|/private/(?:tmp|var)/|[A-Za-z]:\\\\Users\\\\)")
PREP_LINE = re.compile(r"^준비: .*$", re.MULTILINE)


class BuildError(Exception):
    pass


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise BuildError(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout.strip()


def _skip(rel: Path) -> bool:
    return any(part in SKIP_PARTS for part in rel.parts) or rel.suffix == ".pyc"


def copy_file(src: Path, dest: Path, *, header: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if header is None:
        shutil.copyfile(src, dest)
    else:
        dest.write_text(header + "\n\n" + src.read_text(encoding="utf-8"), encoding="utf-8")
    dest.chmod(0o755 if src.stat().st_mode & 0o111 else 0o644)


def tracked_files(src: Path) -> list[Path]:
    """src 아래에서 git이 추적하는 파일(인덱스 기준). 내용은 작업 트리에서 읽는다. 디스크에 없는 파일은 건너뛴다."""
    listed = git("ls-files", "-z", "--", src.relative_to(REPO).as_posix())
    paths = [REPO / name for name in listed.split("\0") if name]
    return sorted(path for path in paths if path.is_file())


def copy_tree(src: Path, dest: Path) -> None:
    """git이 추적하는 파일만 하나씩 복사한다(캐시·빈 폴더·무시된 파일은 옮기지 않는다)."""
    for path in tracked_files(src):
        rel = path.relative_to(src)
        if not _skip(rel):
            copy_file(path, dest / rel)


def forbidden(out: Path) -> list[str]:
    bad: list[str] = []
    for path in sorted(out.rglob("*")):
        rel = path.relative_to(out).as_posix()
        name = path.name
        if path.is_dir():
            # 세션 보기 스킬의 폴더 skills/sessions 하나만 뺀다(그 아래의 같은 이름과 다른 금지 이름은 막는다)
            if name in FORBIDDEN_DIRS and rel != "skills/sessions":
                bad.append(f"금지 폴더 {rel}")
            continue
        if name.startswith(".env") or name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
            bad.append(f"금지 파일 {rel}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        m = LOCAL_PATH.search(text)
        if m:
            bad.append(f"로컬 절대 경로 {rel}: {m.group(0)}")
    return bad


def _front_name(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    head = text.split("\n---\n", 1)[0]
    m = re.search(r"^name: *(\S+) *$", head, re.MULTILINE)
    return m.group(1) if m else None


def keep_block(text: str) -> str | None:
    m = re.search(r"^## 지킬 것\n.*?(?=^## )", text, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else None


def skill_problems(skills: Path) -> list[str]:
    bad: list[str] = []
    preps, keeps = set(), set()
    for name in (ENGINE_NAME, *SKILLS):
        path = skills / name / "SKILL.md"
        if not path.is_file():
            bad.append(f"스킬 글 없음 {name}")
            continue
        text = path.read_text(encoding="utf-8")
        for word in ("uv run", "저장소 루트"):
            if word in text:
                bad.append(f"{name}: '{word}' {text.count(word)}건")
        if _front_name(text) != name:
            bad.append(f"{name}: 머리말 name이 폴더 이름과 다름")
        if name == ENGINE_NAME:
            continue
        preps.add(tuple(PREP_LINE.findall(text)))
        keeps.add(keep_block(text))
    if len(preps) != 1 or () in preps:
        bad.append("'준비' 단락이 일곱에 같지 않음")
    if len(keeps) != 1 or None in keeps:
        bad.append("'지킬 것' 묶음이 일곱에 같지 않음")
    return bad


def build(*, allow_dirty: bool, skip_checks: bool) -> tuple[Path, str]:
    status = git("status", "--porcelain")
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise BuildError("작업 트리가 깨끗하지 않습니다(커밋한 뒤 만듭니다. 시험용이면 --allow-dirty)")
    if not skip_checks:
        for cmd in CHECKS:
            if subprocess.run(cmd, cwd=REPO, check=False).returncode != 0:
                raise BuildError(f"검사 실패: {' '.join(cmd)}")
    out = REPO / OUT
    skills = out / "skills"
    if out.exists():
        shutil.rmtree(out)
    skills.mkdir(parents=True)
    copy_tree(REPO / "plugin" / "skills", skills)
    eng = skills / ENGINE_NAME
    copy_tree(REPO / "src" / "t2s", eng / "src" / "t2s")
    for name in ENGINE_FILES:
        copy_file(REPO / name, eng / name)
    for name in ENGINE_DIRS:
        copy_tree(REPO / name, eng / name)
    copy_file(REPO / "harness.toml", eng / "assets" / "harness.toml")
    copy_tree(REPO / "baselines", eng / "assets" / "baselines")
    for src, dest in COPIES:
        copy_file(REPO / src, eng / dest, header=f"text2sql-py의 {src}에서 만든 사본. 여기서 고치지 않는다.")
    bad = forbidden(out) + skill_problems(skills)
    if bad:
        shutil.rmtree(out)
        raise BuildError("만든 폴더에 문제가 있습니다: " + "; ".join(bad[:20]))
    files = engine.folder_files(eng, skip_stamp=True)
    version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    stamp = {
        "engine": "p3",
        "version": version,
        "source_repo": "text2sql-py",
        "commit": git("rev-parse", "HEAD"),
        "src_tree": git("rev-parse", "HEAD:src"),
        "dirty": dirty,
        "files": files,
        "checksum": engine.files_checksum(files),
    }
    (eng / engine.STAMP_FILE).write_text(json.dumps(stamp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (eng / engine.STAMP_FILE).chmod(0o644)
    return skills, stamp["checksum"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="text2sql-plugin에 실을 엔진 스킬 폴더를 만든다")
    parser.add_argument(
        "--allow-dirty", action="store_true", help="시험용: 미커밋 상태에서도 만든다(표식에 dirty: true)"
    )
    parser.add_argument("--skip-checks", action="store_true", help="시험용: 검사 셋을 건너뛴다")
    args = parser.parse_args(argv)
    try:
        skills, checksum = build(allow_dirty=args.allow_dirty, skip_checks=args.skip_checks)
    except BuildError as e:
        print(f"만들지 않음: {e}", file=sys.stderr)
        return 1
    print(f"만듦: {skills.relative_to(REPO)} · 체크섬 {checksum}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
