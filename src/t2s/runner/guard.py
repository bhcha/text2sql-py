"""읽기 감시: 생성 쪽 프로세스가 정답 폴더를 열지 못하게 한다.

`sys.addaudithook`으로 `open`과 `sqlite3.connect` 이벤트를 보고, 경로가 보호 폴더 아래면 GoldAccessBlocked를 낸다.
실수를 잡는 장치이지 악의적 코드를 막는 경계가 아니다. 잡지 못하는 것:
- C 확장이 직접 여는 파일(예: onnxruntime의 모델 읽기)
- 하위 프로세스(claude, codex 등)
- 폴더 목록 보기(`os.listdir`), 파일 정보(`os.stat`)
훅은 한 번 걸면 뗄 수 없다.

이 파일은 eval·fetch 밖에서 정답 폴더 이름 글자를 가질 수 있는 유일한 곳이다(PROTECTED_DIRS 한 줄).
"""

from __future__ import annotations

import fnmatch
import os
import sys
import urllib.parse
from pathlib import Path

PROTECTED_DIRS = ("gold",)
PROTECTED_GLOBS = ("data/*/download",)  # 받은 zip에도 정답이 들어 있다

_installed_root: str | None = None


class GoldAccessBlocked(RuntimeError):
    pass


def _protected_prefixes(root: str) -> list[str]:
    return [os.path.join(root, d) for d in PROTECTED_DIRS]


def _as_path(target: str | bytes | os.PathLike) -> str | None:
    if isinstance(target, int):
        return None
    try:
        p = os.fsdecode(os.fspath(target))
    except TypeError:
        return None
    return p


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base + os.sep)


def _check(root: str, path: str) -> bool:
    real = os.path.realpath(path)
    for prefix in _protected_prefixes(root):
        if _inside(real, prefix):
            return True
    if _inside(real, root):
        rel = os.path.relpath(real, root).split(os.sep)
        for pattern in PROTECTED_GLOBS:
            parts = pattern.split("/")
            if len(rel) >= len(parts) and all(fnmatch.fnmatchcase(r, p) for r, p in zip(rel, parts)):
                return True
    return False


def is_protected(root: Path, target: str | bytes | os.PathLike) -> bool:
    p = _as_path(target)
    if p is None:
        return False
    return _check(os.path.realpath(root), p)


def _sqlite_target(arg: object) -> str | None:
    p = _as_path(arg)  # type: ignore[arg-type]
    if p is None:
        return None
    if p.startswith("file:"):
        p = urllib.parse.unquote(p[len("file:") :].split("?", 1)[0])
    if p in ("", ":memory:"):
        return None
    return p


def install_guard(root: Path) -> None:
    """한 프로세스에서 한 번. 두 번째 호출은 아무것도 하지 않는다."""
    global _installed_root
    if _installed_root is not None:
        return
    real_root = os.path.realpath(root)
    _installed_root = real_root

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            target = args[0] if args else None
            p = _as_path(target) if target is not None else None
            if p is not None and _check(real_root, p):
                raise GoldAccessBlocked(f"보호된 경로를 열 수 없습니다: {p}")
        elif event == "sqlite3.connect":
            target = args[0] if args else None
            p = _sqlite_target(target) if target is not None else None
            if p is not None and _check(real_root, p):
                raise GoldAccessBlocked(f"보호된 경로를 열 수 없습니다: {p}")

    sys.addaudithook(hook)
