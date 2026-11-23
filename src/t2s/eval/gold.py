"""정답 설정(gold/<데이터셋>/gold.toml)과 정답 파일 읽기, 해시 대조.

오류 글에는 경로와 해시 앞 8자만 넣고 파일 내용을 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

GOLD_DIR = "gold"
GOLD_TOML = "gold.toml"
WORK_DIR = "_work"
GOLD_MARK = "<gold>"  # 기록에 남길 때 정답 폴더 경로 대신 쓰는 자리 표시


class NoGold(Exception):
    """정답 설정이 없다(종료 코드 4)."""


class GoldError(Exception):
    """정답 설정이 잘못되었거나 해시가 다르다(종료 코드 1)."""


STUDY_PREFIX = "study-"  # 스터디 채점기(eval/study.py) 이름의 앞머리
# 채점기별로 gold.toml에 있어야 하는 파일 칸. 모르는 채점기는 BIRD 칸을 요구한다.
BIRD_FILE_KEYS = ("questions_with_answers", "gold_sql")
STUDY_FILE_KEYS = ("answers",)


@dataclass(frozen=True)
class Gold:
    dataset: str
    folder: Path
    evaluator: str
    questions_path: Path  # 정답 포함 문제 파일(해시 대조를 마친 경로). 스터디는 정답 파일(answers)
    gold_sql_path: Path | None  # 정답 SQL 파일(BIRD만)
    files: dict[str, Path] = field(default_factory=dict)  # 칸 이름 → 해시 대조를 마친 경로


def is_study(evaluator: str) -> bool:
    return str(evaluator).startswith(STUDY_PREFIX)


EVIDENCE_USED, EVIDENCE_NOT_USED = "used", "not_used"


def evidence_mode(run_meta: dict) -> str | None:
    """run.json의 evidence 방식. 칸이 없는 옛 run은 not_used, 재채점 run(예측 파일)은 None(미확인)."""
    if run_meta.get("kind") == "rescore":
        return None
    return (run_meta.get("evidence") or {}).get("mode", EVIDENCE_NOT_USED)


def evidence_word(mode: str | None) -> str:
    """라벨의 evidence 칸."""
    return "evidence 미확인" if mode is None else f"evidence {mode}"


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
        evaluator = str(raw["evaluator"])
        keys = STUDY_FILE_KEYS if is_study(evaluator) else BIRD_FILE_KEYS
        names = {key: raw[key] for key in keys}
        want = raw["sha256"]
    except KeyError as e:
        raise GoldError(f"정답 설정에 칸이 없습니다: {e.args[0]} ({cfg})") from None
    out = {}
    for key, name in names.items():
        p = folder / str(name)
        if not p.is_file():
            raise GoldError(f"정답 파일이 없습니다: {p}")
        got = _sha256(p)
        if got != want.get(key):
            raise GoldError(f"정답 파일 해시가 다릅니다: {p.name} (지금 {got[:8]}, 기대 {str(want.get(key))[:8]})")
        out[key] = p
    if is_study(evaluator):
        return Gold(dataset, folder, evaluator, out["answers"], None, out)
    return Gold(dataset, folder, evaluator, out["questions_with_answers"], out["gold_sql"], out)


def gold_items(gold: Gold) -> list[dict]:
    """정답 포함 문제 파일의 항목들(차례 그대로)."""
    items = json.loads(gold.questions_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise GoldError(f"정답 포함 문제 파일이 목록이 아닙니다: {gold.questions_path.name}")
    return items


def gold_lines(gold: Gold) -> list[str]:
    """정답 SQL 파일의 줄들(줄 끝 제거). 줄마다 "SQL\\tdb_id"."""
    if gold.gold_sql_path is None:
        raise GoldError(f"정답 설정에 정답 SQL 파일이 없습니다(채점기 {gold.evaluator})")
    return [line.rstrip("\r\n") for line in gold.gold_sql_path.read_text(encoding="utf-8").splitlines()]


def mask(text: str, root: Path) -> str:
    """글 안의 정답 폴더 경로(<root>/gold)를 GOLD_MARK로 바꾼다. run·세션 폴더에 남는 글에 쓴다(AGENTS 규칙 3)."""
    for base in dict.fromkeys((Path(root) / GOLD_DIR, Path(root).resolve() / GOLD_DIR)):
        b = str(base)
        text = text.replace(b + "/", GOLD_MARK + "/").replace(b + "\\", GOLD_MARK + "/")
        if text == b:
            text = GOLD_MARK
    return text


def work_dir(root: Path, run_id: str) -> Path:
    """채점 중간 파일(정답 글자가 든 것) 자리: <root>/gold/_work/<run_id>/."""
    return Path(root) / GOLD_DIR / WORK_DIR / run_id
