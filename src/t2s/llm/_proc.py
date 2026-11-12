"""모델 CLI 하위 프로세스 실행: 표준 입력으로 프롬프트, 시간 초과면 프로세스 그룹 전체를 끝낸다.

TS engines/packkit/llm/codex.ts·claude.ts의 spawn 부분(detached, 제한 시간에 그룹 SIGKILL)을 옮긴 자체 코드다.
여러 스레드가 함께 부른다. 모듈 수준 상태는 살아 있는 자식 pid 집합과 CLI 판 캐시뿐이다.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_active: set[int] = set()
_active_lock = threading.Lock()
_versions: dict[str, str | None] = {}
_versions_lock = threading.Lock()


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None  # 띄우지 못했으면 None
    stdout: str  # UTF-8, 못 읽는 바이트는 바꿔 읽는다
    stderr: str
    timed_out: bool
    spawn_error: str | None
    elapsed_ms: int


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_cli(
    argv: list[str], prompt: str, *, cwd: Path, env: Mapping[str, str] | None, timeout_seconds: float
) -> ProcResult:
    started = time.monotonic()

    def ms() -> int:
        return round((time.monotonic() - started) * 1000)

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=None if env is None else dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # 자식이 새 프로세스 그룹의 우두머리가 된다
        )
    except OSError as e:
        return ProcResult(None, "", "", False, f"{type(e).__name__}: {e}", ms())
    with _active_lock:
        _active.add(proc.pid)
    timed_out = False
    try:
        try:
            out, errb = proc.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            out, errb = proc.communicate()
    finally:
        with _active_lock:
            _active.discard(proc.pid)
    # 손자 프로세스가 남지 않게 그룹을 한 번 더 정리한다(이미 끝났으면 아무 일도 없다)
    if timed_out:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return ProcResult(
        exit_code=proc.returncode,
        stdout=(out or b"").decode("utf-8", errors="replace"),
        stderr=(errb or b"").decode("utf-8", errors="replace"),
        timed_out=timed_out,
        spawn_error=None,
        elapsed_ms=ms(),
    )


def kill_active() -> int:
    """살아 있는 자식 그룹을 모두 SIGKILL하고 그 수를 돌려준다(멈춤 신호 처리기가 부른다)."""
    with _active_lock:
        pids = list(_active)
    n = 0
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
            n += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return n


def active_count() -> int:
    with _active_lock:
        return len(_active)


def cli_version(bin: str) -> str | None:
    """`<bin> --version` 첫 줄. 10초 제한, 프로세스 안에서 bin마다 한 번만 부른다."""
    with _versions_lock:
        if bin in _versions:
            return _versions[bin]
    try:
        r = subprocess.run([bin, "--version"], capture_output=True, timeout=10, check=False)
        text = r.stdout.decode("utf-8", errors="replace").strip()
        value = text.splitlines()[0] if r.returncode == 0 and text else None
    except (OSError, subprocess.TimeoutExpired):
        value = None
    with _versions_lock:
        _versions[bin] = value
    return value
