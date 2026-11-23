"""명령 sessions와 session open·note·close. 첫머리에서 읽기 감시를 건다.

sessions: 색인(index.jsonl)과 세션 폴더의 작은 파일(run.json·control.json·report.json, ask.json, session.json)에서 이름·
종류·상태·라벨 요약 한 줄씩을 뽑아 최근 것부터 20개를 낸다. records.jsonl·items/ 같은 큰 파일은 읽지 않는다.
세션을 지우거나 이름을 바꾸는 명령은 없다.

session open·note·close: 지표 길(text2sql-plugin의 스킬)이 쓰는 세션 폴더.

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
from t2s.cli.run_cmds import evidence_word
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
    if args.command == "sessions":
        return cmd_sessions(args, root, state, config)
    if args.command == "session":
        handler = {"open": cmd_open, "note": cmd_note, "close": cmd_close}[args.session_command]
        return handler(args, root, state, config)
    raise AssertionError(args.command)


# ── sessions(목록) ──────────────────────────────────────

LIST_MAX = 20
QUESTION_SHOW = 40
ASK_FILE = "ask.json"


def _alive(control: dict) -> bool:
    return R.pid_alive(control.get("pid")) or R.pid_alive(control.get("worker_pid"))


def _set_line(folder: Path) -> tuple[str, str]:
    """(상태, 라벨 요약). run.json·control.json·report.json만 읽는다(score.json·manifest.json은 있는지만 본다)."""
    meta = R.read_json_if(folder / R.RUN_FILE) or {}
    control = R.read_json_if(folder / R.CONTROL_FILE) or {}
    if control.get("state") in ("running", "starting") and _alive(control):
        state = f"진행 중({control.get('phase', 'generate')})"
    elif (folder / R.REPORT_FILE).is_file():
        state = "끝남(보고까지)"
    elif (folder / R.SCORE_FILE).is_file():
        state = "끝남(채점까지)"
    elif control.get("state") == "failed":
        state = "멈춤(실패)"
    elif control.get("state") == "stopped":
        state = "멈춤"
    elif (folder / R.MANIFEST_FILE).is_file():
        state = "생성 끝"
    else:
        state = "멈춤(프로세스 없음)"
    report = R.read_json_if(folder / R.REPORT_FILE) or {}
    if report.get("kind") == "scored" and report.get("label"):
        ex = report.get("ex_total") or {}
        return state, f"EX {ex.get('correct')}/{ex.get('denominator')} · {report['label']}"  # 점수에는 라벨 전체
    if meta.get("kind") == "rescore":
        return state, "재채점 run(생성 없음)"
    llm = meta.get("llm") or {}
    summary = (
        f"{llm.get('model')}·추론 {llm.get('reasoning_effort') or 'none'}·{llm.get('backend')} · "
        f"{evidence_word(meta)} · {meta.get('count')}문항"
    )
    if report.get("kind") == "no_gold":
        summary += " · 정답 없음"
    return state, summary


def _ask_line(folder: Path) -> tuple[str, str]:
    rec = R.read_json_if(folder / ASK_FILE)
    if not isinstance(rec, dict):
        return "기록 없음(끝나지 않음)", ""
    llm = rec.get("llm") or {}
    return (
        f"끝남(종료 코드 {rec.get('exit_code')})",
        f"{llm.get('model')}·추론 {llm.get('reasoning_effort') or 'none'}·{llm.get('backend')} · 소스 {rec.get('source')}",
    )


def _metric_line(folder: Path) -> tuple[str, str]:
    rec = R.read_json_if(folder / SESSION_FILE)
    if not isinstance(rec, dict):
        return "기록 없음", ""
    q = rec.get("question") or ""
    shown = q if len(q) <= QUESTION_SHOW else q[: QUESTION_SHOW - 1] + "…"
    return ("닫힘" if rec.get("closed_at") else "열림"), (f'질문 "{shown}"' if q else "질문 없음")


def _entries(config) -> list[dict]:
    """색인의 줄과, 색인에 없지만 세션 폴더 자리에 있는 run(옛 이름의 run 포함). 이름이 같으면 색인의 첫 줄."""
    seen: dict[str, dict] = {}
    for row in session_index.read(config.runs_dir):
        seen.setdefault(row["name"], row)
    for name in R.list_runs(config.runs_dir):
        if name not in seen:
            meta = R.read_json_if(R.run_dir(config.runs_dir, name) / R.RUN_FILE) or {}
            seen[name] = {
                "name": name,
                "kind": session_names.SET,
                "dataset": meta.get("dataset"),
                "started_at": meta.get("started_at"),
            }
    return sorted(seen.values(), key=lambda r: (str(r.get("started_at") or ""), r["name"]), reverse=True)


def session_line(config, row: dict) -> str:
    name, kind = row["name"], row.get("kind")
    folder = R.run_dir(config.runs_dir, name)
    if "/" in name or name in (".", "..") or not folder.is_dir():
        state, summary = "폴더 없음", ""
    elif kind == session_names.ASK:
        state, summary = _ask_line(folder)
    elif kind in session_names.METRIC_KINDS:
        state, summary = _metric_line(folder)
    else:
        state, summary = _set_line(folder)
    parts = [name, str(kind), str(row.get("dataset") or "none"), state] + ([summary] if summary else [])
    return " · ".join(parts)


def cmd_sessions(args, root: Path, state, config) -> int:
    rows = _entries(config)
    out(f"세션 폴더: {config.runs_dir}")
    if not rows:
        out("세션이 없습니다")
        return EXIT_OK
    for row in rows[:LIST_MAX]:
        out(session_line(config, row))
    if len(rows) > LIST_MAX:
        out(f"… 그 밖 {len(rows) - LIST_MAX}개(더 오래된 것)")
    return EXIT_OK


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
