"""명령 session open·note·close: 지표 길(text2sql-plugin의 스킬)이 쓰는 세션 폴더. 첫머리에서 읽기 감시를 건다.

이 명령들은 DB를 열지 않고 결과 행을 받지 않는다. 세션 폴더에는 `session.json`(질문, 데이터셋, 적은 칸, 시작·끝 시각)만
쓴다. 지표 길의 스킬이 그 폴더에 `t2s-log.jsonl`(text2sql-plugin의 T2S_LOG 기록)과 `report.md`를 둔다.
지표 길의 세션은 프롬프트 파일이 없어 누설 검사 대상이 아니며 session.json에 그렇게 적는다(AGENTS 규칙 3).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from t2s.cli.data_cmds import err, out
from t2s.dataset.config import ConfigError, find_root, load_config
from t2s.dataset.state import StateError, current_dataset, load_state
from t2s.runner import records as R
from t2s.runner.guard import install_guard
from t2s.session import index as session_index
from t2s.session import names as session_names

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
SESSION_FILE = "session.json"
SESSION_FORMAT = "t2s-session/1"
NO_DATASET = "none"  # 지금 바라보는 데이터셋이 없을 때 세션 이름에 넣는 말
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
VALUE_MAX = 500  # 칸 하나의 글 길이 한도(결과 행을 담는 자리가 아니다)
RESERVED = ("format", "name", "kind", "dataset", "question", "started_at", "closed_at", "conversation", "leak_audit")
LEAK_AUDIT_NOTE = "대상 아님(지표 길 세션: 프롬프트 파일이 없다)"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(args) -> int:
    try:
        root = find_root()
    except ConfigError as e:
        err(f"오류: {e}")
        return EXIT_FAIL
    install_guard(root)
    try:
        state = load_state(root)
        config = load_config(root, state)
    except (ConfigError, StateError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL
    if args.command == "session":
        handler = {"open": cmd_open, "note": cmd_note, "close": cmd_close}[args.session_command]
        return handler(args, root, state, config)
    raise AssertionError(args.command)


# ── session open·note·close ─────────────────────────────


def cmd_open(args, root: Path, state, config) -> int:
    dataset, _ = current_dataset(config, state)
    in_name = dataset if dataset and session_names.DATASET_RE.match(dataset) else NO_DATASET
    name = session_names.new_name(in_name, args.kind)
    folder = config.runs_dir / name
    folder.mkdir(parents=True, exist_ok=False)
    started = _now()
    record = {
        "format": SESSION_FORMAT,
        "name": name,
        "kind": args.kind,
        "dataset": dataset,
        "question": args.question,
        "started_at": started,
        "closed_at": None,
        "conversation": session_index.conversation(os.environ),
        "leak_audit": LEAK_AUDIT_NOTE,
    }
    R.write_json_atomic(folder / SESSION_FILE, record)
    session_index.append(config.runs_dir, session_index.entry(name, args.kind, dataset, started, os.environ))
    out(str(folder))
    return EXIT_OK


def _open_session(config, name: str) -> tuple[Path, dict] | int:
    """지표 길 세션 하나(이름 → 폴더, session.json). 아니면 종료 코드. 세션 폴더 자리만 본다(옛 run 폴더는 보지 않는다)."""
    parsed = session_names.parse(name)
    if parsed is None or parsed.legacy or parsed.kind not in session_names.METRIC_KINDS or parsed.role:
        err(f"지표 길 세션 이름이 아닙니다: {name}")
        return EXIT_USAGE
    folder = config.runs_dir / name
    record = R.read_json_if(folder / SESSION_FILE)
    if not isinstance(record, dict) or record.get("format") != SESSION_FORMAT or record.get("name") != name:
        err(f"세션이 없습니다: {name}")
        return EXIT_FAIL
    return folder, record


def cmd_note(args, root: Path, state, config) -> int:
    found = _open_session(config, args.name)
    if isinstance(found, int):
        return found
    folder, record = found
    key, value = args.key, args.value
    if not KEY_RE.match(key) or key in RESERVED:
        err(
            f"쓸 수 없는 칸 이름입니다: {key} (소문자로 시작하는 소문자·숫자·밑줄, 정해진 칸 {', '.join(RESERVED)} 제외)"
        )
        return EXIT_USAGE
    if len(value) > VALUE_MAX:
        err(f"글이 깁니다({len(value)}자, 한도 {VALUE_MAX}자): 결과 행은 적지 않는다")
        return EXIT_USAGE
    if record.get("closed_at"):
        err(f"닫힌 세션입니다: {args.name}")
        return EXIT_FAIL
    if key in record:
        if record[key] == value:
            out(f"이미 적혀 있습니다: {args.name} · {key}")
            return EXIT_OK
        err(f"이미 있는 칸입니다(덮어쓰지 않음): {args.name} · {key}")
        return EXIT_FAIL
    R.write_json_atomic(folder / SESSION_FILE, {**record, key: value})
    out(f"적음: {args.name} · {key}")
    return EXIT_OK


def cmd_close(args, root: Path, state, config) -> int:
    found = _open_session(config, args.name)
    if isinstance(found, int):
        return found
    folder, record = found
    if record.get("closed_at"):
        out(f"이미 닫힌 세션입니다: {args.name} ({record['closed_at']})")
        return EXIT_OK
    closed = _now()
    R.write_json_atomic(folder / SESSION_FILE, {**record, "closed_at": closed})
    out(f"닫음: {args.name} ({closed})")
    return EXIT_OK
