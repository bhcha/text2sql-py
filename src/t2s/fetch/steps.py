"""받기에서 네트워크·파일 없이 판단하는 부분(TS harness/lib/fetch-steps.ts와 같은 일). zip 항목 이름은 믿지 않는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CONTROL_OR_GLOB = re.compile(r"[\x00-\x1f\x7f\\*?\[\]]")
MAX_SUMMARY_LINES = 10


class FetchStopped(Exception):
    """받기를 멈춘다. 출력은 `중단 <단계>: <글>`과 조치 한 줄."""

    def __init__(self, step: str, message: str, action: str | None = None) -> None:
        super().__init__(f"중단 {step}: {message}")
        self.step = step
        self.message = message
        self.action = action

    def lines(self) -> list[str]:
        out = [f"중단 {self.step}: {self.message}"]
        if self.action:
            out.append(self.action)
        return out


@dataclass
class Selection:
    files: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def is_safe_member(name: str) -> bool:
    """절대 경로, "..", ".", 제어 글자, 역슬래시, * ? [ ] 가 있으면 False."""
    if not name or name.startswith("/") or _CONTROL_OR_GLOB.search(name):
        return False
    return not any(part in ("..", ".") for part in name.split("/"))


def select_members(names: list[str], expected: list[str]) -> Selection:
    """zip 항목 목록에서 기대 목록에 든 것만 고른다. 기대 항목이 모두 있고 안전해야 한다."""
    unsafe = [n for n in names if not is_safe_member(n)]
    present = {n for n in names if is_safe_member(n) and not n.endswith("/")}
    return Selection(
        files=[e for e in expected if e in present],
        unsafe=unsafe,
        missing=[e for e in expected if e not in present],
    )


def curl_args(url: str, part: str) -> list[str]:
    return [
        "-fL",
        "-sS",
        "-C",
        "-",
        "--retry",
        "3",
        "--retry-delay",
        "5",
        "--speed-limit",
        "10240",
        "--speed-time",
        "120",
        "-o",
        part,
        url,
    ]


def short_hash(h: str) -> str:
    return h[:8]


def hash_mismatch(label: str, expected: str, actual: str) -> str:
    return f"{label} sha256 불일치 (기대 {short_hash(expected)} / 실제 {short_hash(actual) or '없음'})"


def megabytes(n: int) -> str:
    return f"{n / 1048576:.1f}"


def format_summary(header: str, steps: list[str], license_note: str) -> list[str]:
    lines = [header, *(f"{i + 1} {s}" for i, s in enumerate(steps)), license_note]
    if len(lines) > MAX_SUMMARY_LINES:
        raise ValueError(f"요약이 {MAX_SUMMARY_LINES}줄을 넘는다: {len(lines)}")
    return lines
