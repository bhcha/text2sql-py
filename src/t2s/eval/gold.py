"""정답 설정(gold/<데이터셋>/gold.toml)과 정답 파일 읽기, 해시 대조.

오류 글에는 경로와 해시 앞 8자만 넣고 파일 내용을 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

GOLD_DIR = "gold"
GOLD_TOML = "gold.toml"
WORK_DIR = "_work"


class NoGold(Exception):
    """정답 설정이 없다(종료 코드 4)."""


class GoldError(Exception):
    """정답 설정이 잘못되었거나 해시가 다르다(종료 코드 1)."""


@dataclass(frozen=True)
class Gold:
    dataset: str
    folder: Path
    evaluator: str
    questions_path: Path  # 정답 포함 문제 파일(해시 대조를 마친 경로)
    gold_sql_path: Path  # 정답 SQL 파일


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def gold_folder(root: Path, dataset: str) -> Path:
    return Path(root) / GOLD_DIR / dataset


def load_gold(root: Path, dataset: str) -> Gold:
    folder = gold_folder(root, dataset)
    cfg = folder / GOLD_TOML
    if not cfg.is_file():
        raise NoGold(f"정답 설정이 없습니다: {cfg.relative_to(root) if cfg.is_relative_to(root) else cfg}")
    try:
        raw = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise GoldError(f"정답 설정을 읽지 못했습니다: {cfg} ({e.lineno}행)") from None
    try:
        qname, sname, evaluator = raw["questions_with_answers"], raw["gold_sql"], raw["evaluator"]
        want = raw["sha256"]
    except KeyError as e:
        raise GoldError(f"정답 설정에 칸이 없습니다: {e.args[0]} ({cfg})") from None
    out = {}
    for key, name in (("questions_with_answers", qname), ("gold_sql", sname)):
        p = folder / str(name)
        if not p.is_file():
            raise GoldError(f"정답 파일이 없습니다: {p}")
        got = _sha256(p)
        if got != want.get(key):
            raise GoldError(f"정답 파일 해시가 다릅니다: {p.name} (지금 {got[:8]}, 기대 {str(want.get(key))[:8]})")
        out[key] = p
    return Gold(dataset, folder, str(evaluator), out["questions_with_answers"], out["gold_sql"])


def gold_items(gold: Gold) -> list[dict]:
    """정답 포함 문제 파일의 항목들(차례 그대로)."""
    items = json.loads(gold.questions_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise GoldError(f"정답 포함 문제 파일이 목록이 아닙니다: {gold.questions_path.name}")
    return items


def gold_lines(gold: Gold) -> list[str]:
    """정답 SQL 파일의 줄들(줄 끝 제거). 줄마다 "SQL\\tdb_id"."""
    return [line.rstrip("\r\n") for line in gold.gold_sql_path.read_text(encoding="utf-8").splitlines()]


def work_dir(root: Path, run_id: str) -> Path:
    """채점 중간 파일(정답 글자가 든 것) 자리: <root>/gold/_work/<run_id>/."""
    return Path(root) / GOLD_DIR / WORK_DIR / run_id
