"""파일 하나 받기(curl, .part 이어받기)와 크기·sha256 대조."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

from t2s.fetch.steps import FetchStopped, curl_args, hash_mismatch, megabytes, short_hash

CURL = "curl"  # 테스트는 가짜 실행 파일로 바꾼다
PROGRESS_SECONDS = 15.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def check_file(path: Path, sha256: str, size: int | None, label: str, step: str) -> None:
    if not path.is_file():
        raise FetchStopped(step, f"{label} 없음")
    if size is not None and path.stat().st_size != size:
        raise FetchStopped(step, f"{label} 크기 불일치 (기대 {size} / 실제 {path.stat().st_size})")
    actual = sha256_file(path)
    if actual != sha256:
        raise FetchStopped(step, hash_mismatch(label, sha256, actual))


def _run_curl(url: str, part: Path, label: str, size: int | None) -> int:
    part.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def progress() -> None:
        while not stop.wait(PROGRESS_SECONDS):
            if part.exists():
                total = f"/{megabytes(size)}" if size else ""
                print(f"{label}: {megabytes(part.stat().st_size)}{total} MB", file=sys.stderr, flush=True)

    t = threading.Thread(target=progress, daemon=True)
    t.start()
    try:
        r = subprocess.run(
            [CURL, *curl_args(url, str(part))], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=False
        )
        return r.returncode
    except FileNotFoundError:
        return 127
    finally:
        stop.set()
        t.join()


def fetch_file(
    *,
    dest: Path,
    url: str | None,
    sha256: str,
    size: int | None,
    label: str,
    step: str,
) -> str:
    """돌려주는 것: "건너뜀" | "받음". 문제가 있으면 FetchStopped."""
    dest = Path(dest)
    if dest.exists():
        if (size is not None and dest.stat().st_size != size) or sha256_file(dest) != sha256:
            actual = sha256_file(dest)
            raise FetchStopped(
                step, hash_mismatch(f"이미 있는 {label}", sha256, actual), "해당 파일을 확인하고 지운 뒤 다시 /getbird"
            )
        return "건너뜀"
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if url is None:
        raise FetchStopped(step, f"{label}의 주소가 없음")
    have = part.stat().st_size if part.exists() else -1
    if size is not None and have > size:
        part.unlink()
        have = -1
    code = 0 if (size is not None and have == size) else _run_curl(url, part, label, size)
    finished = sha256_file(part) if part.exists() else ""
    if code != 0 and finished != sha256:
        left = megabytes(part.stat().st_size) if part.exists() else "0"
        raise FetchStopped(
            step, f"{label} 네트워크 오류(curl {code}), .part {left} MB 남김", "다시 /getbird를 부르면 이어받는다"
        )
    if finished != sha256 or (size is not None and part.stat().st_size != size):
        rejected = dest.with_name(f"{dest.name}.rejected-{short_hash(finished)}")
        os.replace(part, rejected)
        raise FetchStopped(
            step,
            hash_mismatch(label, sha256, finished),
            f"받은 파일은 {rejected.name}로 남겼다. 원격 자료가 바뀌었을 수 있으니 받아들이지 않는다",
        )
    os.replace(part, dest)
    return "받음"
