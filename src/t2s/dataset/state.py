"""var/state.json: 지금 바라보는 데이터셋과 등록 기록(프로그램이 쓴다)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from t2s.dataset.config import Config

STATE_FORMAT = "t2s-state/1"
STATE_FILE = Path("var") / "state.json"


class StateError(Exception):
    pass


@dataclass
class State:
    current_dataset: str | None = None
    # 이름 → {"kind":"single","sources":{이름:{"kind":"sqlite","path":절대경로}},"registered_at":…,"fingerprint":{sha256,size}}
    registered: dict[str, dict] = field(default_factory=dict)


def state_path(root: Path) -> Path:
    return root / STATE_FILE


def load_state(root: Path) -> State:
    """파일이 없으면 빈 상태."""
    p = state_path(root)
    if not p.is_file():
        return State()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StateError(f"{p}를 읽지 못했습니다: {e}") from e
    if raw.get("format") != STATE_FORMAT:
        raise StateError(f"{p}의 format이 {STATE_FORMAT}이 아닙니다")
    return State(current_dataset=raw.get("current_dataset"), registered=dict(raw.get("registered", {})))


def save_state(root: Path, state: State) -> None:
    """var/state.json.tmp에 쓰고 os.replace로 바꾼다(원자적)."""
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    body = {"format": STATE_FORMAT, "current_dataset": state.current_dataset, "registered": state.registered}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def current_dataset(config: Config, state: State) -> tuple[str | None, str]:
    """(이름, 출처). 출처는 "state" | "harness.toml" | "없음". state가 [defaults]보다 앞선다."""
    if state.current_dataset:
        return state.current_dataset, "state"
    if config.default_dataset:
        return config.default_dataset, "harness.toml"
    return None, "없음"
