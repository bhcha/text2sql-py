"""세션 색인 `<세션 폴더들의 자리>/index.jsonl`: 세션을 시작할 때 한 줄씩 덧붙인다. 고치거나 지우지 않는다.

한 줄의 칸: 세션 이름, 종류, 데이터셋, 시작 시각, 대화 세션 ID(환경에 있으면, 없으면 null).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

INDEX_FILE = "index.jsonl"
INDEX_FORMAT = "t2s-session-index/1"
# 대화 세션 ID를 읽는 환경변수(있는 것만). Claude Code는 CLAUDE_CODE_SESSION_ID, Codex는 CODEX_THREAD_ID(미확인)
CONVERSATION_ENV = (("claude", "CLAUDE_CODE_SESSION_ID"), ("codex", "CODEX_THREAD_ID"))


def index_path(sessions_dir: Path) -> Path:
    return Path(sessions_dir) / INDEX_FILE


def conversation(env: Mapping[str, str]) -> dict | None:
    """대화 세션 ID. 환경에 없으면 None."""
    for client, key in CONVERSATION_ENV:
        value = env.get(key)
        if value:
            return {"client": client, "id": value}
    return None


def entry(name: str, kind: str, dataset: str | None, started_at: str, env: Mapping[str, str]) -> dict:
    return {
        "format": INDEX_FORMAT,
        "name": name,
        "kind": kind,
        "dataset": dataset,
        "started_at": started_at,
        "conversation": conversation(env),
    }


def append(sessions_dir: Path, row: dict) -> None:
    """한 줄을 덧붙인다(한 번의 write로. 있는 줄은 건드리지 않는다)."""
    path = index_path(sessions_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read(sessions_dir: Path) -> list[dict]:
    """색인의 줄들(덧붙인 차례). 읽지 못하는 줄은 건너뛴다. 파일이 없으면 빈 목록."""
    path = index_path(sessions_dir)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            rows.append(row)
    return rows
