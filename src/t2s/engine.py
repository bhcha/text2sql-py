"""엔진 표식(ENGINE.json) 읽기와 대조. 표준 라이브러리만 쓰고 다른 t2s 패키지를 import하지 않는다.

플러그인 설치본에서는 실행 스크립트가 환경변수 T2S_ENGINE_HOME에 엔진 폴더를 넣는다. 그 폴더의 표식이
있으면 기록의 커밋·미커밋 판정을 표식에서 읽는다(사용자 프로젝트의 git을 보지 않는다).
미커밋 판정은 엔진 자리(플러그인 폴더)와 깔린 패키지를 둘 다 표식과 견준다(engine_check, verify도 같은 함수).
저장소 클론에서는 표식이 없고, 부르는 쪽이 지금처럼 루트의 git을 본다.
표식 없는 엔진 자리가 src/t2s를 가진 git 작업 트리면(개발용 갈래) 그 자리의 git을 본다.
eval과 runner가 함께 쓰므로 최상위 모듈에 둔다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ENV_HOME = "T2S_ENGINE_HOME"
STAMP_FILE = "ENGINE.json"
# 표식의 files 칸에서 깔린 패키지에 해당하는 몫(엔진 폴더 기준 상대 경로의 앞머리)
PACKAGE_PREFIX = "src/t2s/"
_SKIP_DIRS = {"__pycache__"}


def engine_home() -> Path | None:
    value = os.environ.get(ENV_HOME)
    return Path(value) if value else None


def _read_stamp(home: Path | None = None) -> dict | None:
    home = home if home is not None else engine_home()
    if home is None:
        return None
    path = home / STAMP_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def engine_stamp() -> dict | None:
    """표식의 commit, src_tree, dirty, version, checksum. 환경변수나 파일이 없으면 None."""
    stamp = _read_stamp()
    if stamp is None:
        return None
    return {key: stamp.get(key) for key in ("commit", "src_tree", "dirty", "version", "checksum")}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def folder_files(folder: Path, *, skip_stamp: bool = False) -> dict[str, str]:
    """폴더 안 파일의 상대 경로 → sha256(이름순). 캐시(__pycache__, .pyc)는 뺀다. skip_stamp면 맨 위의 표식 파일도 뺀다."""
    out: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part in _SKIP_DIRS for part in rel.parts) or path.suffix == ".pyc":
            continue
        if skip_stamp and rel.as_posix() == STAMP_FILE:
            continue
        out[rel.as_posix()] = _sha256(path)
    return dict(sorted(out.items()))


def files_checksum(files: dict[str, str]) -> str:
    """표식의 checksum: files를 이름순 한 줄 JSON으로 적은 글의 sha256. 만든 시각 같은 칸은 들어가지 않는다."""
    text = json.dumps(dict(sorted(files.items())), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compare(expected: dict[str, str], now: dict[str, str], prefix: str = "") -> list[str]:
    """두 파일 목록(상대 경로 → sha256)의 차이를 한 줄씩. 같으면 빈 목록."""
    out: list[str] = []
    for rel in sorted(set(expected) | set(now)):
        if rel not in now:
            out.append(f"없음 {prefix}{rel}")
        elif rel not in expected:
            out.append(f"더 있음 {prefix}{rel}")
        elif now[rel] != expected[rel]:
            out.append(f"바뀜 {prefix}{rel}")
    return out


def installed_differences(package_dir: Path | None = None) -> list[str] | None:
    """깔린 t2s 패키지 파일들을 표식의 src/t2s 몫과 대조한다. 표식이 없으면 None, 같으면 빈 목록.

    package_dir를 주지 않으면 지금 돌고 있는 t2s 패키지 폴더를 본다. 줄 꼴은 engine_differences와 같고
    앞에 "깔린 패키지 "가 붙는다(경로는 표식의 경로).
    """
    stamp = _read_stamp()
    if stamp is None:
        return None
    files = stamp.get("files")
    if not isinstance(files, dict):
        return ["깔린 패키지: 표식에 파일 목록 없음"]
    expected = {k[len(PACKAGE_PREFIX) :]: v for k, v in files.items() if k.startswith(PACKAGE_PREFIX)}
    if not expected:
        return ["깔린 패키지: 표식에 패키지 파일 없음"]
    if package_dir is None:
        package_dir = Path(__file__).resolve().parent
    return [f"깔린 패키지 {line}" for line in _compare(expected, folder_files(package_dir), PACKAGE_PREFIX)]


def stamp_matches(package_dir: Path | None = None) -> bool | None:
    """깔린 t2s 패키지 파일들의 sha256이 표식의 files와 같은지. 표식이 없으면 None.

    표식에 files가 없으면 같다고 말할 수 없으므로 False. 엔진 자리(플러그인 폴더)는 보지 않는다(engine_check를 본다).
    """
    diffs = installed_differences(package_dir)
    return None if diffs is None else not diffs


def engine_check(package_dir: Path | None = None) -> dict | None:
    """두 사본을 표식과 대조한다. 기록(run.json·ask.json 등)과 verify가 모두 이 함수를 쓴다.

    home: 엔진 자리(플러그인 폴더) 파일의 차이(engine_differences), installed: 깔린 t2s 패키지의 차이
    (installed_differences), matches: 둘 다 빈 목록인지. 표식이 없거나 읽을 수 없으면 None.
    """
    home = engine_home()
    if home is None or _read_stamp(home) is None:
        return None
    home_diffs = engine_differences(home) or []
    installed = installed_differences(package_dir) or []
    return {"home": home_diffs, "installed": installed, "matches": not home_diffs and not installed}


def _home_git(home: Path) -> dict | None:
    """엔진 자리 자체가 git 작업 트리의 맨 위면 그 git의 head·src_tree·dirty(src, pyproject.toml, uv.lock). 아니면 None."""
    import subprocess

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(home), *args], capture_output=True, text=True, check=False)
        except OSError:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    head = git("rev-parse", "HEAD")
    if top is None or head is None or Path(top).resolve() != home.resolve():
        return None
    status = git("status", "--porcelain", "--", "src", "pyproject.toml", "uv.lock")
    return {"head": head, "src_tree": git("rev-parse", "HEAD:src"), "dirty": None if status is None else bool(status)}


def git_from_stamp(package_dir: Path | None = None) -> dict | None:
    """기록의 git 칸(head, src_tree, dirty)을 표식에서 만든다. 표식이 없으면 None(부르는 쪽이 지금 동작대로 간다).

    표식 파일은 있는데 읽을 수 없으면 셋 다 None(작업 루트의 git을 엔진 커밋으로 적지 않는다).
    """
    stamp = engine_stamp()
    if stamp is None:
        home = engine_home()
        if home is not None and (home / STAMP_FILE).exists():
            return {"head": None, "src_tree": None, "dirty": None}
        if home is not None and (home / "src" / "t2s").is_dir():
            return _home_git(home)
        return None
    check = engine_check(package_dir)
    return {
        "head": stamp["commit"],
        "src_tree": stamp["src_tree"],
        "dirty": bool(stamp["dirty"]) or check is None or check["matches"] is not True,
    }


def engine_record(package_dir: Path | None = None) -> dict | None:
    """run.json·ask.json의 engine 칸. 표식이 없으면 None(칸을 넣지 않는다).

    matches는 엔진 자리(플러그인 폴더)와 깔린 패키지가 둘 다 표식과 같을 때만 참이다. home_matches·installed_matches는
    어느 사본이 다른지 보이는 칸이다.
    """
    stamp = engine_stamp()
    check = engine_check(package_dir)
    if stamp is None or check is None:
        return None
    return {
        "version": stamp["version"],
        "checksum": stamp["checksum"],
        "matches": check["matches"],
        "home_matches": not check["home"],
        "installed_matches": not check["installed"],
    }


def engine_differences(home: Path | None = None) -> list[str] | None:
    """엔진 자리의 파일들을 표식과 대조한다. 표식이 없거나 읽을 수 없으면 None, 같으면 빈 목록.

    다르면 한 줄씩: "바뀜 <경로>", "없음 <경로>"(표식에 있는데 없음), "더 있음 <경로>", 표식 자체가 맞지 않으면
    "표식 체크섬 다름".
    """
    home = home if home is not None else engine_home()
    if home is None:
        return None
    stamp = _read_stamp(home)
    if stamp is None:
        return None
    files = stamp.get("files")
    if not isinstance(files, dict):
        return ["표식에 파일 목록 없음"]
    out: list[str] = []
    if files_checksum(files) != stamp.get("checksum"):
        out.append("표식 체크섬 다름")
    return out + _compare(files, folder_files(home, skip_stamp=True))
