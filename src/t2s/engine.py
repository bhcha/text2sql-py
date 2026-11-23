"""엔진 표식(ENGINE.json) 읽기와 대조. 표준 라이브러리만 쓰고 다른 t2s 패키지를 import하지 않는다.

플러그인 설치본에서는 실행 스크립트가 환경변수 T2S_ENGINE_HOME에 엔진 폴더를 넣는다. 그 폴더의 표식이
있으면 기록의 커밋·미커밋 판정을 표식에서 읽는다(사용자 프로젝트의 git을 보지 않는다).
저장소 클론에서는 표식이 없고, 부르는 쪽이 지금처럼 루트의 git을 본다.
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


def _installed_files(package_dir: Path) -> dict[str, str]:
    return folder_files(package_dir)


def stamp_matches(package_dir: Path | None = None) -> bool | None:
    """깔린 t2s 패키지 파일들의 sha256이 표식의 files와 같은지. 표식이 없으면 None.

    package_dir를 주지 않으면 지금 돌고 있는 t2s 패키지 폴더를 본다. 표식에 files가 없으면 같다고 말할 수 없으므로 False.
    """
    stamp = _read_stamp()
    if stamp is None:
        return None
    files = stamp.get("files")
    if not isinstance(files, dict):
        return False
    expected = {k[len(PACKAGE_PREFIX) :]: v for k, v in files.items() if k.startswith(PACKAGE_PREFIX)}
    if not expected:
        return False
    if package_dir is None:
        package_dir = Path(__file__).resolve().parent
    return _installed_files(package_dir) == expected


def git_from_stamp(package_dir: Path | None = None) -> dict | None:
    """기록의 git 칸(head, src_tree, dirty)을 표식에서 만든다. 표식이 없으면 None(부르는 쪽이 지금 동작대로 간다).

    표식 파일은 있는데 읽을 수 없으면 셋 다 None(작업 루트의 git을 엔진 커밋으로 적지 않는다).
    """
    stamp = engine_stamp()
    if stamp is None:
        home = engine_home()
        if home is not None and (home / STAMP_FILE).exists():
            return {"head": None, "src_tree": None, "dirty": None}
        return None
    matches = stamp_matches(package_dir)
    return {
        "head": stamp["commit"],
        "src_tree": stamp["src_tree"],
        "dirty": bool(stamp["dirty"]) or matches is not True,
    }


def engine_record(package_dir: Path | None = None) -> dict | None:
    """run.json·ask.json의 engine 칸(표식의 판·체크섬·깔린 코드가 맞는지). 표식이 없으면 None(칸을 넣지 않는다)."""
    stamp = engine_stamp()
    if stamp is None:
        return None
    return {"version": stamp["version"], "checksum": stamp["checksum"], "matches": stamp_matches(package_dir)}


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
    now = folder_files(home, skip_stamp=True)
    for rel in sorted(set(files) | set(now)):
        if rel not in now:
            out.append(f"없음 {rel}")
        elif rel not in files:
            out.append(f"더 있음 {rel}")
        elif now[rel] != files[rel]:
            out.append(f"바뀜 {rel}")
    return out
