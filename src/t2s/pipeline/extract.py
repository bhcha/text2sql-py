"""⑨ 모델 응답에서 SQL 꺼내기와 stop 자르기.

자체 코드다. TS engines/pure-fix-h/pack/extract.ts(규칙 1.2.0)를 Python으로 옮겼고 규칙은 바꾸지 않았다.
착안 출처: DAMO-ConvAI `bird/llm/src/gpt_request.py` 184행(stop 목록 `['--', '\\n\\n', ';', '#']`)·189-192행(`'SELECT' + 생성문`),
`bird/llm/src/post_process_cot.py` `fetch_sql` 13-14행(`': SELECT'`로 가르기). 코드는 복사하지 않았다.

차례:
1. 울타리 줄(```…)을 지운다. 지운 뒤 공백뿐이면 EmptyResponse(갈래 empty).
2. CoT 가르기: 콜론 + 공백 하나 이상 + 대문자 SELECT 또는 대문자 WITH CTE 머리가 있으면 첫 번째 것부터(cot_split).
3. 앞뒤 공백을 뺀 글이 머리로 시작하면(대소문자 무시) 그대로(starts_sql).
4. 줄 맨 앞 머리 가운데 쓸 수 있는 첫 번째 것부터(line_start_sql). 앞 글에 닫히지 않은 `(`가 있거나 앞 글이
   UNION [ALL]·INTERSECT·EXCEPT로 끝나면 건너뛰고 센다.
5. 아니면 'SELECT ' + 앞뒤 공백을 뺀 글(continuation).
6. stop 네 글자 가운데 가장 먼저 나오는 곳에서 자르고 앞뒤 공백을 뺀다.

정규식은 JavaScript의 `\\s`·`\\w`·`\\b`·대소문자 무시·`trim`과 같은 뜻이 되도록 풀어 썼다.
남은 차이: JavaScript의 m 깃발 `^`·`$`는 `\\r`·U+2028·U+2029도 줄 끝으로 보고, Python re.M은 `\\n`만 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EXTRACT_RULES_VERSION = "1.2.0"
STOP = ("--", "\n\n", ";", "#")

W = r"[A-Za-z0-9_]"  # JavaScript \w
# JavaScript `\s`이자 String.prototype.trim이 떼는 글자들
JS_WS = "".join(
    map(
        chr,
        (
            0x09,
            0x0A,
            0x0B,
            0x0C,
            0x0D,
            0x20,
            0xA0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
            0xFEFF,
        ),
    )
)
S = "[" + "".join(f"\\u{ord(c):04x}" for c in JS_WS) + "]"  # JavaScript \s
NAME = r'(?:"[^"\n]+"|`[^`\n]+`|\[[^\]\n]+\]|' + W + "+)"
HEAD = rf"(?:SELECT(?!{W})|WITH{S}+(?:RECURSIVE{S}+)?{NAME}{S}*(?:\([^()]*\){S}*)?AS{S}*\()"
FENCE_LINE = re.compile(r"^[ \t]*```[^\n]*$", re.MULTILINE)
COT_SPLIT = re.compile(rf":{S}+(?={HEAD})")  # 깃발 없음: 대문자 머리만
STARTS_SQL = re.compile("^" + HEAD, re.IGNORECASE | re.ASCII)
LINE_START_SQL = re.compile(rf"^([ \t]*){HEAD}", re.IGNORECASE | re.ASCII | re.MULTILINE)
SET_OP_TAIL = re.compile(rf"(?<!{W})(?:UNION(?:{S}+ALL)?|INTERSECT|EXCEPT){S}*\Z", re.IGNORECASE | re.ASCII)
# JavaScript m 깃발은 줄 끝으로 보고 Python re.M은 보지 않는 글자
JS_ONLY_LINE_ENDS = (chr(0x0D), chr(0x2028), chr(0x2029))
EMPTY_MESSAGE = "모델 응답이 비어 있습니다"


@dataclass(frozen=True)
class ExtractCheck:
    branch: str  # cot_split | starts_sql | line_start_sql | continuation | empty
    stop_hit: str | None
    fenced: bool
    cot_split_count: int
    line_start_skipped: int

    def to_json(self) -> dict:
        return {
            "branch": self.branch,
            "stop_hit": self.stop_hit,
            "fenced": self.fenced,
            "cot_split_count": self.cot_split_count,
            "line_start_skipped": self.line_start_skipped,
        }


@dataclass(frozen=True)
class Extracted:
    sql: str | None
    exception: dict | None  # {"type": "EmptyResponse", "message": …}
    check: ExtractCheck


def js_trim(text: str) -> str:
    return text.strip(JS_WS)


def open_parens(text: str) -> int:
    """닫히지 않은 '(' 수. ')'가 더 많으면 0에서 멈춘다."""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
    return depth


def line_start_sql(text: str) -> tuple[int, int]:
    """(쓸 수 있는 첫 줄 맨 앞 머리의 위치 또는 -1, 건너뛴 머리 수)."""
    skipped = 0
    for m in LINE_START_SQL.finditer(text):
        at = m.start() + len(m.group(1))
        before = text[:at]
        if open_parens(before) == 0 and not SET_OP_TAIL.search(before):
            return at, skipped
        skipped += 1
    return -1, skipped


def cut_at_stop(text: str) -> tuple[str, str | None]:
    at = -1
    hit: str | None = None
    for stop in STOP:
        i = text.find(stop)
        if i >= 0 and (at < 0 or i < at):
            at, hit = i, stop
    return (text, None) if at < 0 else (text[:at], hit)


def has_js_only_line_end(text: str) -> bool:
    return any(c in text for c in JS_ONLY_LINE_ENDS)


def extract_sql(response: str) -> Extracted:
    fenced = FENCE_LINE.search(response) is not None
    text = FENCE_LINE.sub("", response)
    splits = list(COT_SPLIT.finditer(text))
    line_at, skipped = line_start_sql(text)

    def check(branch: str, stop_hit: str | None = None) -> ExtractCheck:
        return ExtractCheck(branch, stop_hit, fenced, len(splits), skipped)

    trimmed = js_trim(text)
    if not trimmed:
        return Extracted(None, {"type": "EmptyResponse", "message": EMPTY_MESSAGE}, check("empty"))
    if splits:
        branch, raw = "cot_split", text[splits[0].end() :]
    elif STARTS_SQL.match(trimmed):
        branch, raw = "starts_sql", trimmed
    elif line_at >= 0:
        branch, raw = "line_start_sql", text[line_at:]
    else:
        branch, raw = "continuation", "SELECT " + trimmed
    cut, stop_hit = cut_at_stop(raw)
    return Extracted(js_trim(cut), None, check(branch, stop_hit))
