"""스터디 평가셋 채점: 예측 SQL을 읽기 전용으로 실행해 정답 파일의 기대 결과와 비교한다(스터디 채점 스크립트 규칙).

출처(복사 후 수정):
- 저장소: jenn25ng/text-to-sql-study (비공개), 커밋 290ff9bee2d8ac70e01932857911f025a377c1ba
- 원본 파일: scripts/run_baseline.py
- 가져온 줄: `extract_sql` 118-121행 가운데 120-121행(끝 ';' 떼기), `ALLOWED` 126행, `connect` 129-140행,
  `execute` 143-158행, `_same` 163-166행, `_rows_equal` 169-180행, `compare` 183-200행,
  `run` 391행(실행 오류면 비교하지 않고 실패). `run` 333행(SQL 문항만 채점)의 동작은 문항을 읽는
  dataset/questions.py가 question_type이 "sql"이 아니면 멈추는 것으로 대신한다.
- 라이선스: 이 저장소 커밋에는 LICENSE 파일이 없다. 사용자 판단으로 복사 후 수정했다(docs/provenance.md 2절).
- 바꾼 것:
  - `connect`: 경로를 저장소 루트 기준이 아니라 받은 DB 경로로, `mode=ro`에 `immutable=1`을 더함(AGENTS 규칙 10).
  - `execute`: 결과 셋째 칸(오류 글)에 더해 이유 코드(넷째 칸)를 돌려줌. 시간 초과(interrupted)·거절(not authorized)을
    코드로 나눔. 판정은 그대로(오류면 0점). sqlite3.Error 밖의 ValueError·OverflowError도 실행 오류로 받음.
  - `compare`: 돌려주는 것에 이유 코드를 더함. 판정 규칙(열 이름 무시, 열 6개 이하면 열 순서 순열, row_order가
    any·ignored·not_required·unordered일 때만 행 순서 무시, 수 허용 오차, 1000행 초과 실패)은 그대로.
  - 끝 ';' 떼기만 가져오고 코드 블록 꺼내기는 가져오지 않음(예측은 이미 꺼낸 SQL이다).
  - 정답 대조를 더함: 정답 SQL을 같은 execute·compare로 돌려 기대 결과와 같은지 본다.
  - 문항 조건(task_conditions)은 이 하네스의 evidence 자리다. 기본은 모델에 주지 않는다(evidence not_used).
    evidence used run에서만 각 줄의 앞뒤 공백을 떼고 "; "로 이은 한 줄을 준다. 채점에는 쓰지 않고, used run이면
    run의 hints.json이 정답 파일의 model_input.task_conditions를 같은 규칙으로 이은 글과 같은지만 대조한다.

정답 글자(정답 SQL, 기대 결과, 이유 글)는 gold/_work/<run_id>/에만 쓴다. run 폴더에는 문항별 0·1, 실행 이유 코드, 요약만.
datasource를 import할 수 없으므로 DB는 sqlite3로 직접 연다.
"""

from __future__ import annotations

import itertools
import platform
import re
import sqlite3
import time
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.dataset.hints import join_conditions
from t2s.dataset.questions import STUDY_FORMAT, QuestionsError, load_public_questions
from t2s.eval import gold as G
from t2s.eval.bird import (
    SIDE_SUFFIXES,
    ScoreError,
    _generating,
    check_run_evidence,
    db_state,
    now,
    official_percent,
    sha256_file,
)

EVALUATOR_ID = "study-run-baseline-290ff9b"
SOURCE = {
    "repository": "jenn25ng/text-to-sql-study",
    "commit": "290ff9bee2d8ac70e01932857911f025a377c1ba",
    "file": "scripts/run_baseline.py",
}
DEFAULT_TIMEOUT = 10.0
MAX_ROWS = 1000
DEFAULT_TOLERANCE = 1e-4
MAX_PERMUTED_COLUMNS = 6
UNORDERED = ("any", "ignored", "not_required", "unordered")
DETAIL_FILE = "detail.jsonl"
CROSS_CHECK_LABEL = "정답 대조"
RULES = {
    "column_names": "무시(열 수·행 수는 같아야 함)",
    "column_order": f"열 {MAX_PERMUTED_COLUMNS}개 이하면 모든 순열, 그 위는 그대로",
    "row_order": f"comparison.row_order가 {'·'.join(UNORDERED)}일 때만 무시(중복 보존), 그 밖은 순서 비교",
    "numbers": f"양쪽 수면 |차| <= numeric_abs_tolerance(없으면 {DEFAULT_TOLERANCE}), 아니면 같음(NULL은 NULL과만)",
    "statements": "끝 ';'를 떼고 안에 ';'가 남으면 실행하지 않음",
    "limits": f"시간 제한 기본 {DEFAULT_TIMEOUT:g}초(진행 처리기), {MAX_ROWS}행 초과는 실패",
    "authorizer": "SELECT·READ·FUNCTION·RECURSIVE만 허용, load_extension 거절, mode=ro&immutable=1",
}

# 원본 126행
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def strip_sql(sql: str) -> str:
    """원본 extract_sql 120-121행: 앞뒤 공백과 끝 ';'를 뗀다."""
    return sql.strip().rstrip(";").strip()


def connect(db: Path) -> sqlite3.Connection:
    """원본 connect 129-140행. 경로를 받은 그대로, immutable=1을 더함."""
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    if hasattr(con, "enable_load_extension"):
        con.enable_load_extension(False)
    con.execute("PRAGMA query_only=ON")

    def authorize(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in ALLOWED else sqlite3.SQLITE_DENY

    con.set_authorizer(authorize)
    return con


def _error_code(message: str) -> str:
    text = message.lower()
    if "interrupted" in text:
        return "timeout"
    if "not authorized" in text or "authorization denied" in text:
        return "denied"
    return "error"


def execute(con: sqlite3.Connection, sql: str, timeout_s: float = DEFAULT_TIMEOUT, max_rows: int = MAX_ROWS):
    """원본 execute 143-158행. 돌려주는 것: (열 이름, 행, 오류 글 또는 None, 이유 코드)."""
    if ";" in sql.strip().rstrip(";"):
        return None, None, "여러 문장은 실행하지 않음", "multi_statement"
    started = time.monotonic()
    con.set_progress_handler(lambda: int(time.monotonic() - started > timeout_s), 1000)
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
        if len(rows) > max_rows:
            return cols, rows[:max_rows], f"결과가 {max_rows}행을 넘음", "too_many_rows"
        return cols, rows, None, "ok"
    except sqlite3.Error as e:
        return None, None, f"실행 오류: {e}", _error_code(str(e))
    except (ValueError, OverflowError) as e:
        return None, None, f"실행 오류: {e}", "error"
    finally:
        con.set_progress_handler(None, 0)


def _same(a, b, tol: float) -> bool:
    """원본 _same 163-166행."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= tol
    return a == b


def _rows_equal(exp: list, got: list, ordered: bool, tol: float) -> bool:
    """원본 _rows_equal 169-180행."""
    if len(exp) != len(got):
        return False
    if ordered:
        return all(all(_same(x, y, tol) for x, y in zip(e, g, strict=False)) for e, g in zip(exp, got, strict=False))
    remaining = list(got)
    for e in exp:
        hit = next(
            (i for i, g in enumerate(remaining) if all(_same(x, y, tol) for x, y in zip(e, g, strict=False))), None
        )
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def compare(item: dict, got_cols, got_rows) -> tuple[bool, str, str]:
    """원본 compare 183-200행. 돌려주는 것: (같은가, 이유 글, 이유 코드). 열 이름은 보지 않는다."""
    exp_rows = item["expected_rows"]
    rule = item.get("comparison", {})
    ordered = str(rule.get("row_order", "required")).strip().lower() not in UNORDERED
    tol = float(rule.get("numeric_abs_tolerance", DEFAULT_TOLERANCE))
    n = len(item.get("expected_columns") or (exp_rows[0] if exp_rows else []))
    if got_cols is None:
        return False, "실행 실패", "exec_failed"
    if len(got_cols) != n:
        return False, f"열 수가 다름 (정답 {n}개, 답안 {len(got_cols)}개)", "column_count"
    if len(got_rows) != len(exp_rows):
        return False, f"행 수가 다름 (정답 {len(exp_rows)}행, 답안 {len(got_rows)}행)", "row_count"
    for perm in itertools.permutations(range(n)) if n <= MAX_PERMUTED_COLUMNS else [tuple(range(n))]:
        if _rows_equal(exp_rows, [[r[i] for i in perm] for r in got_rows], ordered, tol):
            if perm == tuple(range(n)):
                return True, "일치", "match"
            return True, "일치 (열 순서만 다름)", "match_column_order"
    return False, "값이 다름", "value_diff"


def judge(con: sqlite3.Connection, item: dict, sql: str | None, timeout_s: float) -> dict:
    """한 문항 판정. 원본 run 391행처럼 실행 오류면 비교하지 않고 실패."""
    if sql is None:
        return {"ex_match": 0, "exec": "no_sql", "reason": "SQL 없음", "code": "no_sql"}
    cols, rows, err, code = execute(con, strip_sql(sql), timeout_s)
    if err:
        return {"ex_match": 0, "exec": code, "reason": err, "code": code}
    ok, reason, rcode = compare(item, cols, rows)
    return {"ex_match": int(ok), "exec": code, "reason": reason, "code": rcode}


# ── 라벨·정답 글 ─────────────────────────────────────────


def set_name(gold: G.Gold) -> str:
    m = re.search(r"v(\d+(?:\.\d+)*)", gold.questions_path.name)
    return f"Olist 스터디 평가셋 v{m.group(1)}" if m else f"{gold.dataset} 스터디 평가셋"


def label(run: dict, count: int, sample: bool, name: str, answers_sha256: str) -> str:
    git = run.get("git") or {}
    commit = (git.get("head") or "")[:7] or "커밋 없음"
    llm = run.get("llm") or {}
    text = (
        f"{name} / SQLite / {answers_sha256[:8]} / {count}문제 / EX(스터디 결과 비교)"
        f" / {G.evidence_word(G.evidence_mode(run))} / 로컬 측정"
        f" / {llm.get('model')}·추론 {llm.get('reasoning_effort')}·{llm.get('backend')} / 1회 실행"
        f" / 구현 Python {commit}"
    )
    if git.get("dirty"):
        text += " / (미커밋 코드)"
    if sample:
        text += " / 소표본·점수 아님"
    if llm.get("backend") == "fake":
        text += " / 가짜 백엔드·점수 아님"
    return text


def leak_texts(gold: G.Gold) -> list[dict]:
    """누설 검사에 쓰는 정답 글: 위치마다 정답 SQL, 문항 조건 각 줄(conditions), 채점 메모(notes), 둘을 합친 hints,
    evidence used run에 준 것과 같은 규칙으로 이은 조건 글(evidence). 정규화·길이 거르기는 부르는 쪽."""
    out = []
    for it in G.gold_items(gold):
        raw = (it.get("model_input") or {}).get("task_conditions") or []
        conditions = [str(c) for c in raw if isinstance(c, str)]
        notes = [it["note"]] if isinstance(it.get("note"), str) else []
        out.append(
            {
                "sql": str(it.get("gold_sql") or ""),
                "conditions": conditions,
                "notes": notes,
                "hints": conditions + notes,
                "evidence": join_conditions(conditions),
            }
        )
    return out


def task_condition_texts(items: list[dict]) -> list[str]:
    """정답 파일의 model_input.task_conditions를 evidence used run과 같은 규칙(dataset/hints.py)으로 이은 글."""
    out = []
    for i, it in enumerate(items):
        try:
            out.append(join_conditions((it.get("model_input") or {}).get("task_conditions") or []))
        except QuestionsError:
            raise ScoreError(f"정답 파일의 문항 조건이 글 목록이 아닙니다(위치 {i})") from None
    return out


# ── 채점 ─────────────────────────────────────────────────


def _side_files(spec) -> dict:
    side = {}
    for s in spec.sources.values():
        for suffix in SIDE_SUFFIXES:
            f = Path(str(s.path) + suffix)
            if f.exists():
                side[str(f)] = f.stat().st_size
    return side


def score(config: Config, run_id: str, *, meta_time_out: float | None = None, leak=None) -> dict:
    """score.json 내용을 돌려준다. 정답이 없으면 G.NoGold. leak는 누설 검사 함수(run_id → 요약 dict) 또는 None."""
    timeout_s = DEFAULT_TIMEOUT if meta_time_out is None else float(meta_time_out)
    run = R.run_dir(config.runs_dir, run_id)
    meta = R.read_json_if(run / R.RUN_FILE)
    if meta is None:
        raise ScoreError(f"run이 없습니다: {run_id}")
    manifest = R.read_json_if(run / R.MANIFEST_FILE)
    if manifest is None or not (run / R.RECORDS_FILE).is_file() or not (run / R.PREDICTIONS_FILE).is_file():
        raise ScoreError("아직 생성이 끝나지 않았습니다(manifest·records·predictions가 없음)")
    if sha256_file(run / R.RECORDS_FILE) != manifest["records"]["sha256"]:
        raise ScoreError("records.jsonl이 manifest의 해시와 다릅니다")
    if sha256_file(run / R.PREDICTIONS_FILE) != manifest["predictions"]["sha256"]:
        raise ScoreError("predictions.json이 manifest의 해시와 다릅니다")
    if meta.get("kind") == "rescore":
        raise ScoreError("스터디 채점기는 재채점 run을 받지 않습니다")
    dataset = meta["dataset"]
    spec = config.datasets.get(dataset)
    if spec is None:
        raise ScoreError(f"run의 데이터셋이 설정에 없습니다: {dataset}")
    if len(spec.sources) != 1:
        raise ScoreError("스터디 채점기는 소스가 하나인 데이터셋만 받습니다")
    busy = _generating(config, dataset, run_id)
    if busy:
        raise ScoreError(f"같은 데이터셋을 생성 중인 run이 있습니다: {busy}")

    gold = G.load_gold(config.root, dataset)
    if gold.evaluator != EVALUATOR_ID:
        raise ScoreError(f"정답 설정의 채점기({gold.evaluator})가 이 저장소의 스터디 채점기({EVALUATOR_ID})와 다릅니다")
    records = R.read_jsonl(run / R.RECORDS_FILE)
    count = int(meta["count"])
    sample = bool(meta.get("sample"))
    if len(records) != count:
        raise ScoreError(f"records.jsonl {len(records)}행이 분모 {count}와 다릅니다")
    qmeta = meta.get("questions") or {}
    ids = qmeta.get("ids")
    if qmeta.get("input_format") != STUDY_FORMAT or not isinstance(ids, list):
        raise ScoreError("스터디 모델 입력 파일(--questions)로 돌린 run이 아닙니다(문항 id 기록이 없음)")
    public = load_public_questions(config.root / qmeta["path"])
    items = G.gold_items(gold)
    if len(items) < count or len(public) < count or len(ids) < count:
        raise ScoreError("정답 파일·문항 파일의 문항 수가 분모보다 적습니다")
    source_name, source = next(iter(spec.sources.items()))
    for i in range(count):
        it, rec = items[i], records[i]
        same = (
            ids[i] == it.get("id")
            and public[i].question == (it.get("model_input") or {}).get("question")
            and rec["position"] == i
            and rec["db_id"] == source_name
        )
        if not same:
            raise ScoreError(f"run의 문항과 정답 파일이 어긋납니다(위치 {i})")
    mode = G.evidence_mode(meta)
    expected = task_condition_texts(items[:count]) if mode == G.EVIDENCE_USED else []
    evidence = check_run_evidence(run, meta, expected)

    side_before = _side_files(spec)
    hashes_before, _ = db_state(spec)
    t0 = time.monotonic()
    con = connect(Path(source.path))
    try:
        gold_checks = []
        for it in items[:count]:
            g = judge(con, it, str(it.get("gold_sql") or ""), timeout_s)
            gold_checks.append(g)
        judged = [judge(con, items[i], records[i].get("sql"), timeout_s) for i in range(count)]
    finally:
        con.close()
    elapsed = round(time.monotonic() - t0, 2)
    side_after = _side_files(spec)
    removed = {}
    for f, size in side_after.items():
        if f not in side_before:
            Path(f).unlink()
            removed[f] = size
    hashes_after, _ = db_state(spec)
    unchanged = hashes_after == hashes_before

    work = G.work_dir(config.root, run_id)
    work.mkdir(parents=True, exist_ok=True)
    R.write_jsonl(
        work / DETAIL_FILE,
        [
            {
                "position": i,
                "id": ids[i],
                "ex_match": judged[i]["ex_match"],
                "pred_exec": judged[i]["exec"],
                "pred_code": judged[i]["code"],
                "pred_reason": judged[i]["reason"],
                "gold_match": bool(gold_checks[i]["ex_match"]),
                "gold_code": gold_checks[i]["code"],
                "gold_reason": gold_checks[i]["reason"],
            }
            for i in range(count)
        ],
    )
    rows = [
        {
            "position": rec["position"],
            "question_id": rec["question_id"],
            "id": ids[i],
            "db_id": rec["db_id"],
            "difficulty": "",
            "status": rec["status"],
            "ex_match": judged[i]["ex_match"],
            "pred_exec": judged[i]["exec"],
        }
        for i, rec in enumerate(records)
    ]
    R.write_jsonl(run / R.EX_FILE, rows)

    mismatched = [i for i, g in enumerate(gold_checks) if not g["ex_match"]]
    total_correct = sum(r["ex_match"] for r in rows)
    pct = official_percent(total_correct, count)
    by_db = {source_name: {"correct": total_correct, "total": count, "percent": None if pct is None else float(pct)}}
    exec_names = ("ok", "no_sql", "multi_statement", "error", "timeout", "denied", "too_many_rows")
    leak_summary = leak(run_id) if leak is not None else None
    summary = {
        "label": label(meta, count, sample, set_name(gold), sha256_file(gold.questions_path)),
        "sample": sample,
        "denominator": count,
        "ex_total": {"correct": total_correct, "denominator": count, "percent": None if pct is None else float(pct)},
        "by_difficulty": {},
        "by_db": by_db,
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in R.STATUSES},
        "correct_by_status": {s: sum(r["ex_match"] for r in rows if r["status"] == s) for s in R.STATUSES},
        "exec_counts": {e: sum(1 for r in rows if r["pred_exec"] == e) for e in exec_names},
        "cross_check": not mismatched,
        "cross_check_label": CROSS_CHECK_LABEL,
        "cross_check_detail": {"checked": count, "equal": count - len(mismatched), "mismatch_positions": mismatched},
        "cross_check_note": "정답 SQL을 같은 실행·비교로 돌려 정답 파일의 기대 결과와 같은지 본다",
        "data_unchanged": unchanged,
        "side_files": {
            "before": side_before,
            "created_and_removed": removed,
            "left_untouched": {f: s for f, s in side_after.items() if f in side_before},
        },
        "evaluator": {"id": EVALUATOR_ID, "source": SOURCE, "rules": RULES, "timeout_seconds": timeout_s},
        "conditions": mode,
        "evidence": evidence,
        "per_question": {"file": R.EX_FILE, "method": "eval/study.py: execute·compare", "elapsed_seconds": elapsed},
        "integrity": {
            "records_sha256": sha256_file(run / R.RECORDS_FILE),
            "ex_sha256": sha256_file(run / R.EX_FILE),
            "run_sha256": sha256_file(run / R.RUN_FILE),
            "predictions_sha256": sha256_file(run / R.PREDICTIONS_FILE),
        },
        "leak": leak_summary,
        "scored_at": now(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    R.write_json_atomic(run / R.SCORE_FILE, summary)
    return summary
