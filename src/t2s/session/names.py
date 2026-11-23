"""세션 이름 만들기·읽기.

새 이름: `YYYY-MM-DD-<데이터셋>-<종류>-<16진 6자>`. 경로 짝(route-start)의 두 run은 끝에 `-harness`·`-single`이 붙는다.
옛 이름: `YYYYMMDD-<데이터셋>-<16진 6자>`(경로 짝이면 같은 꼬리). 옛 이름은 읽기만 하고 새로 만들지 않는다.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import date

SET = "set"  # run·route-start
ASK = "ask"  # ask 한 번
METRIC_KINDS = ("metric", "explore", "check", "diagnose", "define")  # 지표 길의 세션(session open)
KINDS = (SET, ASK, *METRIC_KINDS)
ROLES = ("harness", "single")  # 경로 짝 꼬리
HEX_LEN = 6

_DATASET = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_KIND = "|".join(KINDS)
_ROLE = "|".join(ROLES)
NEW_RE = re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}})-({_DATASET})-({_KIND})-([0-9a-f]{{{HEX_LEN}}})(?:-({_ROLE}))?$")
OLD_RE = re.compile(rf"^(\d{{8}})-({_DATASET})-([0-9a-f]{{{HEX_LEN}}})(?:-({_ROLE}))?$")
DATASET_RE = re.compile(rf"^{_DATASET}$")


@dataclass(frozen=True)
class SessionName:
    name: str
    date: str  # YYYY-MM-DD
    dataset: str
    kind: str | None  # 옛 이름은 None(종류를 이름에 적지 않았다)
    hex: str
    role: str | None  # 경로 짝이면 harness·single
    legacy: bool

    @property
    def base(self) -> str:
        """경로 짝 꼬리를 뺀 이름(짝 이름)."""
        return self.name.removesuffix(f"-{self.role}") if self.role else self.name


def new_name(dataset: str, kind: str, *, today: date | None = None, token: str | None = None) -> str:
    """새 세션 이름. today·token은 시험용(없으면 오늘 날짜와 임의의 16진 6자)."""
    if kind not in KINDS:
        raise ValueError(f"세션 종류가 아닙니다: {kind} ({', '.join(KINDS)})")
    if not DATASET_RE.match(dataset):
        raise ValueError(f"세션 이름에 쓸 수 없는 데이터셋 이름입니다: {dataset}")
    day = (today or date.today()).isoformat()
    hexpart = token if token is not None else secrets.token_hex(HEX_LEN // 2)
    name = f"{day}-{dataset}-{kind}-{hexpart}"
    if NEW_RE.match(name) is None:
        raise ValueError(f"세션 이름 꼴이 아닙니다: {name}")
    return name


def pair_names(base: str) -> dict[str, str]:
    """짝 이름 → {역할: run 이름}(harness, single 차례)."""
    return {role: f"{base}-{role}" for role in ROLES}


def parse(name: str) -> SessionName | None:
    """새 이름 또는 옛 이름을 읽는다. 어느 꼴도 아니면 None."""
    m = NEW_RE.match(name)
    if m:
        day, dataset, kind, hexpart, role = m.groups()
        return SessionName(name, day, dataset, kind, hexpart, role, legacy=False)
    m = OLD_RE.match(name)
    if m:
        day, dataset, hexpart, role = m.groups()
        return SessionName(name, f"{day[:4]}-{day[4:6]}-{day[6:]}", dataset, None, hexpart, role, legacy=True)
    return None
