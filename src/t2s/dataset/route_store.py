"""경로 판정을 harness.toml 프로필에 더하는 글을 만든다. 표준 라이브러리만 쓴다(TOML을 쓰는 의존을 더하지 않는다).

자체 코드다. 근거 표의 칸 이름을 글자로 갖지 않고 판정 파일의 근거 표를 그대로 돈다.
- 새 프로필이면 파일 끝에 프로필 블록과 근거 블록(`[[profiles.<이름>.route_basis]]`)을 더한다.
- 있는 프로필이면 그 표 안에 `route = "…"` 한 줄을 넣고(이미 있으면 그 한 줄만 바꾼다) 근거 블록을 파일 끝에 더한다.
  옛 경로 값은 앞선 근거 항목의 `route`에 남는다(근거는 배열에 더하기만 한다).
- 쓰기 전에 새 글을 tomllib로 읽어 "옛 내용 + 기대한 바뀜"과 같은지 대조하고, 다르면 쓰지 않는다.
- 참조 프로필(p3-ref)은 쓰지 않는다(AGENTS 규칙 8: 검증 측정 프로필은 고정).
값은 글·정수·실수·참거짓만 쓴다.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASIS_KEY = "route_basis"
REFERENCE_PROFILE = "p3-ref"
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
NEW_PROFILE_KEYS = ("backend", "model", "reasoning_effort", "timeout_seconds", "item_timeout_seconds")


class StoreError(Exception):
    pass


@dataclass
class Plan:
    profile: str
    new_profile: bool
    text: str  # 쓸 전체 글
    shown: list[str]  # 사용자에게 보일 더하는·바꾸는 줄


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise StoreError(f"유한하지 않은 수는 쓰지 않습니다: {v!r}")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise StoreError(f"쓸 수 없는 값의 형: {type(v).__name__}")


def _key(k: str) -> str:
    if not KEY_RE.match(k):
        raise StoreError(f"쓸 수 없는 칸 이름: {k!r}")
    return k


def choose_profile(profiles: Mapping[str, Mapping], llm: Mapping, given: str | None) -> tuple[str, bool]:
    """(프로필 이름, 새 프로필인가). --profile이 있으면 그것, 없으면 백엔드·모델·추론 단계가 글자 그대로 같은 프로필 가운데
    파일에서 먼저 나오는 것(참조 프로필과 backend auto 제외), 없으면 `<모델>-<추론 단계>`에서 만든 새 이름."""
    if given:
        if given == REFERENCE_PROFILE:
            raise StoreError(f"참조 프로필 {REFERENCE_PROFILE}에는 쓰지 않습니다(검증 측정 프로필은 고정)")
        if not NAME_RE.match(given):
            raise StoreError(f"프로필 이름은 영문 소문자·숫자·-만 씁니다: {given!r}")
        return given, given not in profiles
    want = (llm.get("backend"), llm.get("model"), llm.get("reasoning_effort"))
    for name, p in profiles.items():
        if name == REFERENCE_PROFILE or p.get("backend", "auto") == "auto":
            continue
        if (p.get("backend"), p.get("model"), p.get("reasoning_effort", "medium")) == want:
            if not NAME_RE.match(name):
                raise StoreError(f"맞는 프로필 이름을 이 명령이 쓸 수 없습니다(--profile로 다른 이름을 준다): {name!r}")
            return name, False
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", f"{llm.get('model')}-{llm.get('reasoning_effort')}".lower()))
    base = base.strip("-")[:50] or "model"
    name, n = base, 2
    while name in profiles or name == REFERENCE_PROFILE:
        name, n = f"{base}-{n}", n + 1
    return name, True


def _same_measurement(old: Mapping, new: Mapping) -> bool:
    runs = [k for k in new if k.endswith("_run")]
    return bool(runs) and all(old.get(k) == new.get(k) for k in runs)


def _header_index(lines: list[str], name: str) -> int:
    head = re.compile(rf"^\s*\[\s*profiles\.{re.escape(name)}\s*\]\s*(#.*)?$")
    found = [i for i, line in enumerate(lines) if head.match(line)]
    if len(found) != 1:
        raise StoreError(f"harness.toml에서 [profiles.{name}] 머리를 하나로 찾지 못했습니다")
    return found[0]


def plan(text: str, *, profile: str, new_profile: bool, llm: Mapping, route: str, basis: Mapping) -> Plan:
    """쓸 글을 만들고 tomllib로 대조한다. 같은 측정의 근거가 이미 있으면 StoreError."""
    _key(profile)
    old = tomllib.loads(text)
    profiles = old.get("profiles", {})
    if profile == REFERENCE_PROFILE:
        raise StoreError(f"참조 프로필 {REFERENCE_PROFILE}에는 쓰지 않습니다")
    existing = profiles.get(profile)
    if new_profile and existing is not None:
        raise StoreError(f"이미 있는 프로필입니다: {profile}")
    if not new_profile and existing is None:
        raise StoreError(f"없는 프로필입니다: {profile}")
    if existing is not None:
        for b in existing.get(BASIS_KEY) or []:
            if _same_measurement(b, basis):
                raise StoreError("같은 측정의 근거가 이미 있습니다(두 번 쓰지 않는다)")
    basis_lines = [f"[[profiles.{profile}.{BASIS_KEY}]]"] + [f"{_key(k)} = {toml_value(v)}" for k, v in basis.items()]
    lines = text.split("\n")
    shown: list[str] = []
    if new_profile:
        block = [f"[profiles.{profile}]"]
        block += [f"{k} = {toml_value(llm[k])}" for k in NEW_PROFILE_KEYS if llm.get(k) is not None]
        block.append(f"route = {toml_value(route)}")
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", *block, "", *basis_lines, ""]
        shown += [f"+ {x}" for x in block] + [f"+ {x}" for x in basis_lines]
    else:
        h = _header_index(lines, profile)
        end = next((i for i in range(h + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        route_line = f"route = {toml_value(route)}"
        at = next((i for i in range(h + 1, end) if re.match(r"^\s*route\s*=", lines[i])), None)
        if at is not None:
            shown.append(f"~ {lines[at].strip()} → {route_line}")
            lines[at] = route_line
        else:
            keys = [i for i in range(h + 1, end) if re.match(r"^\s*[A-Za-z0-9_-]+\s*=", lines[i])]
            pos = (keys[-1] if keys else h) + 1
            lines.insert(pos, route_line)
            shown.append(f"+ {route_line}  ([profiles.{profile}] 안)")
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", *basis_lines, ""]
        shown += [f"+ {x}" for x in basis_lines]
    new_text = "\n".join(lines)
    try:
        got = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise StoreError(f"만든 글을 TOML로 읽지 못했습니다(쓰지 않음): {e}") from e
    expected = copy.deepcopy(old)
    prof = expected.setdefault("profiles", {}).setdefault(profile, {})
    if new_profile:
        prof.update({k: llm[k] for k in NEW_PROFILE_KEYS if llm.get(k) is not None})
    prof["route"] = route
    prof.setdefault(BASIS_KEY, []).append(dict(basis))
    if got != expected:
        raise StoreError("만든 글을 읽은 내용이 기대한 바뀜과 다릅니다(쓰지 않음)")
    return Plan(profile, new_profile, new_text, shown)


def write(path: Path, text: str) -> None:
    """임시 파일에 쓰고 os.replace로 바꿔 넣는다."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
