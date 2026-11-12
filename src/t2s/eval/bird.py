"""BIRD Mini-Dev 채점: 예측 준비, 공식 채점기 실행, 문항별 판정, 교차 대조, DB 불변 확인, 라벨.

옮긴 TS 코드(자체): benchmarks/benchkit/submit.ts(예측 값, 소표본 정답, 난도 JSON Lines), score.ts(채점기 인자, 표 읽기,
교차 대조, 부속 파일 지우기, DB 해시 확인). TS와 다른 점: 정답 글자가 든 중간 파일(난도 파일, 소표본 정답, 문항별 원시
결과)은 run 폴더가 아니라 gold/_work/<run_id>/에 쓴다. 소표본에 없는 난도가 있으면 공식 채점기는 0으로 나누기 오류가 나므로
그때는 공식 채점기를 건너뛰고 문항별 판정만 낸다.

공식 채점기와 ex_worker.py는 같은 venv의 Python 하위 프로세스로 돈다. 이 프로세스는 func_timeout·psycopg2·pymysql을
import하지 않는다. datasource를 import할 수 없으므로 DB 파일 해시와 부속 파일은 hashlib·os로 직접 본다.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.dataset.questions import load_public_questions
from t2s.eval import gold as G

VENDOR_ID = "bird-minidev-abd11b6"
VENDOR = Path(__file__).resolve().parent / "vendor" / VENDOR_ID
EX_WORKER = Path(__file__).resolve().parent / "ex_worker.py"
LEVELS = ("simple", "moderate", "challenging", "total")
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
DIFF_FILE = "diff.jsonl"
SUBSET_GOLD_FILE = "gold.subset.sql"
RAW_FILE = "ex.raw.jsonl"
EVALUATOR_STDOUT = "evaluator-stdout.txt"
EVALUATOR_LOG = "evaluator-log.txt"
SKIP_NOTE = "소표본에 없는 난도가 있어 공식 채점기 표를 내지 않았다"


class ScoreError(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def git_info(root: Path) -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
        except OSError:
            return None

    head = git("rev-parse", "HEAD")
    if head is None or head.returncode != 0:
        return {"head": None, "src_tree": None, "dirty": None}
    tree = git("rev-parse", "HEAD:src")
    status = git("status", "--porcelain", "--", "src", "pyproject.toml", "uv.lock", "harness.toml")
    return {
        "head": head.stdout.strip(),
        "src_tree": tree.stdout.strip() if tree and tree.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status else None,
    }


# ── 표·백분율·라벨 ───────────────────────────────────────


def parse_table(text: str) -> dict | None:
    """print_data 표에서 count 줄과 EX 줄을 읽는다. 없으면 None."""
    count = re.search(r"^count\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", text, re.MULTILINE)
    ex = re.search(r"^EX\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", text, re.MULTILINE)
    if not count or not ex:
        return None
    return {
        "count": {lvl: int(count.group(i + 1)) for i, lvl in enumerate(LEVELS)},
        "EX": {lvl: ex.group(i + 1) for i, lvl in enumerate(LEVELS)},
        "count_row": count.group(0),
        "ex_row": ex.group(0),
    }


def official_percent(correct: int, total: int) -> str | None:
    """공식 채점기와 같은 식·같은 반올림: (맞은 수 / 전체) * 100 을 '.2f'로."""
    if not total:
        return None
    return f"{correct / total * 100:.2f}"


def _bucket(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        b = out.setdefault(r[key], {"correct": 0, "total": 0})
        b["total"] += 1
        b["correct"] += int(r["ex_match"])
    for b in out.values():
        p = official_percent(b["correct"], b["total"])
        b["percent"] = None if p is None else float(p)
    return out


def cross_check(ours: dict, table: dict | None) -> tuple[bool, dict]:
    """문항별 합계와 채점기 표가 같은지. (일치, 항목별 비교)."""
    if table is None:
        return False, {"error": "채점기 출력에서 count/EX 표를 찾지 못했습니다"}
    ok = True
    detail = {}
    for lvl in LEVELS:
        mine = ours["total"] if lvl == "total" else ours["by_difficulty"].get(lvl, {"correct": 0, "total": 0})
        text = official_percent(mine["correct"], mine["total"])
        derived = round(float(table["EX"][lvl]) * table["count"][lvl] / 100)
        equal = table["count"][lvl] == mine["total"] and text == table["EX"][lvl] and derived == mine["correct"]
        detail[lvl] = {
            "ours_correct": mine["correct"],
            "ours_total": mine["total"],
            "ours_percent": text,
            "evaluator_total": table["count"][lvl],
            "evaluator_percent": table["EX"][lvl],
            "evaluator_correct_derived": derived,
            "equal": equal,
        }
        ok = ok and equal
    return ok, detail


def label(run: dict, count: int, sample: bool) -> str:
    git = run.get("git") or {}
    commit = (git.get("head") or "")[:7] or "커밋 없음"
    if run.get("dataset_kind") == "bird-minidev":
        head = f"BIRD Mini-Dev / SQLite / {str(run.get('archive_sha256') or '')[:8]} / {count}문제"
    else:
        head = f"{run.get('dataset')} / SQLite / {count}문제"
    text = f"{head} / EX / evidence not_used / 로컬 측정"
    if run.get("kind") == "rescore":
        text += f" / 재채점(예측 파일 {str((run.get('source') or {}).get('sha256', ''))[:8]}) / 채점 Python {commit}"
    else:
        llm = run.get("llm") or {}
        text += f" / {llm.get('model')}·추론 {llm.get('reasoning_effort')}·{llm.get('backend')} / 1회 실행"
        text += f" / 구현 Python {commit}"
    if git.get("dirty"):
        text += " / (미커밋 코드)"
    if sample:
        text += " / 소표본·점수 아님"
    if (run.get("llm") or {}).get("backend") == "fake":
        text += " / 가짜 백엔드·점수 아님"
    return text


# ── 채점기 사본·DB ───────────────────────────────────────


def check_vendor(evaluator: str) -> dict:
    if evaluator != VENDOR_ID:
        raise ScoreError(f"정답 설정의 채점기({evaluator})가 이 저장소의 채점기 사본({VENDOR_ID})과 다릅니다")
    hashes = json.loads((VENDOR / "HASHES.json").read_text(encoding="utf-8"))
    for name, digest in hashes["sha256"].items():
        if sha256_file(VENDOR / name) != digest:
            raise ScoreError(f"채점기 파일 {name}의 해시가 고정값과 다릅니다")
    return hashes


def db_root(spec) -> str:
    """소스 경로의 두 단계 위 폴더 + '/'. 모든 소스가 <뿌리><이름>/<이름>.sqlite여야 한다(공식 package_sqls의 꼴)."""
    roots = set()
    for name, s in spec.sources.items():
        p = Path(s.path).resolve()
        if p.name != f"{name}.sqlite" or p.parent.name != name:
            raise ScoreError(f"소스 {name}의 경로가 <뿌리>/{name}/{name}.sqlite 꼴이 아닙니다")
        roots.add(str(p.parent.parent))
    if len(roots) != 1:
        raise ScoreError("소스들의 뿌리 폴더가 하나가 아닙니다")
    return roots.pop() + "/"


def db_state(spec) -> tuple[dict, dict]:
    """(DB 파일 sha256, 부속 파일 크기)."""
    hashes, side = {}, {}
    for name, s in spec.sources.items():
        p = Path(s.path)
        hashes[name] = sha256_file(p)
        for suffix in SIDE_SUFFIXES:
            f = Path(str(p) + suffix)
            if f.exists():
                side[str(f)] = f.stat().st_size
    return hashes, side


def _universal(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _generating(config: Config, dataset: str, run_id: str) -> str | None:
    for name in R.list_runs(config.runs_dir):
        if name == run_id:
            continue
        folder = R.run_dir(config.runs_dir, name)
        c = R.read_json_if(folder / R.CONTROL_FILE) or {}
        meta = R.read_json_if(folder / R.RUN_FILE) or {}
        if (
            meta.get("dataset") == dataset
            and c.get("state") == "running"
            and c.get("phase", "generate") == "generate"
            and (R.pid_alive(c.get("pid")) or R.pid_alive(c.get("worker_pid")))
        ):
            return name
    return None


# ── 채점 ─────────────────────────────────────────────────


def score(config: Config, run_id: str, *, num_cpus: int = 4, meta_time_out: float = 30.0, leak=None) -> dict:
    """score.json 내용을 돌려준다. 정답이 없으면 G.NoGold. leak는 누설 검사 함수(run_id → 요약 dict) 또는 None."""
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
    dataset = meta["dataset"]
    spec = config.datasets.get(dataset)
    if spec is None:
        raise ScoreError(f"run의 데이터셋이 설정에 없습니다: {dataset}")
    busy = _generating(config, dataset, run_id)
    if busy:
        raise ScoreError(f"같은 데이터셋을 생성 중인 run이 있습니다: {busy}(채점기는 DB를 일반 연결로 연다)")

    gold = G.load_gold(config.root, dataset)
    hashes = check_vendor(gold.evaluator)
    records = R.read_jsonl(run / R.RECORDS_FILE)
    count = int(meta["count"])
    sample = bool(meta.get("sample"))
    if len(records) != count:
        raise ScoreError(f"records.jsonl {len(records)}행이 분모 {count}와 다릅니다")
    public = load_public_questions(config.root / meta["questions"]["path"])
    items = G.gold_items(gold)
    lines = G.gold_lines(gold)
    if len(items) < count or len(lines) < count or len(public) < count:
        raise ScoreError("정답 파일의 문항 수가 분모보다 적습니다")
    for i in range(count):
        q, it, rec = public[i], items[i], records[i]
        same = (
            (q.question_id, q.db_id, q.question, q.difficulty)
            == (it.get("question_id"), it.get("db_id"), it.get("question"), it.get("difficulty"))
            and lines[i].rsplit("\t", 1)[-1] == q.db_id
            and rec["position"] == i
            and rec["db_id"] == q.db_id
        )
        if not same:
            raise ScoreError(f"공개 문항과 정답 파일이 어긋납니다(위치 {i})")

    work = G.work_dir(config.root, run_id)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    diff = work / DIFF_FILE
    diff.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items[:count]), encoding="utf-8")
    if sample:
        gold_path = work / SUBSET_GOLD_FILE
        gold_path.write_text("\n".join(lines[:count]) + "\n", encoding="utf-8")
    else:
        gold_path = gold.gold_sql_path
    root_dir = db_root(spec)
    hashes_before, side_before = db_state(spec)
    predictions = run / R.PREDICTIONS_FILE
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    mto = str(float(meta_time_out))

    log_path = run / EVALUATOR_LOG
    if log_path.exists():
        log_path.unlink()
    levels_present = {it.get("difficulty") for it in items[:count]}
    run_official = all(lvl in levels_present for lvl in LEVELS[:3])
    command = [
        sys.executable,
        "evaluation_ex.py",
        "--predicted_sql_path",
        str(predictions),
        "--ground_truth_path",
        str(gold_path),
        "--db_root_path",
        root_dir,
        "--num_cpus",
        str(num_cpus),
        "--meta_time_out",
        mto,
        "--diff_json_path",
        str(diff),
        "--sql_dialect",
        "SQLite",
        "--output_log_path",
        str(log_path),
    ]
    evaluator: dict = {
        "command": command,
        "cwd": str(VENDOR),
        "commit": hashes["commit"],
        "files_sha256": hashes["sha256"],
    }
    table = None
    if run_official:
        t0 = time.monotonic()
        proc = subprocess.run(command, cwd=VENDOR, env=env, capture_output=True, text=True, check=False)
        stdout, stderr = _universal(proc.stdout), _universal(proc.stderr)
        (run / EVALUATOR_STDOUT).write_text(
            stdout + ("\n--- stderr ---\n" + stderr if stderr else ""), encoding="utf-8", newline=""
        )
        table = parse_table(stdout)
        evaluator.update(
            {
                "returncode": proc.returncode,
                "elapsed_seconds": round(time.monotonic() - t0, 2),
                "table": table,
                "finished": "Finished EX evaluation" in stdout,
                "skipped": False,
            }
        )
    else:
        evaluator.update(
            {"returncode": None, "elapsed_seconds": None, "table": None, "finished": False, "skipped": True}
        )

    raw = work / RAW_FILE
    worker_cmd = [
        sys.executable,
        "-B",
        str(EX_WORKER),
        "--vendor",
        str(VENDOR),
        "--predictions",
        str(predictions),
        "--gold",
        str(gold_path),
        "--db-root",
        root_dir,
        "--num-cpus",
        str(num_cpus),
        "--meta-time-out",
        mto,
        "--sql-dialect",
        "SQLite",
        "--out",
        str(raw),
    ]
    t0 = time.monotonic()
    worker = subprocess.run(worker_cmd, cwd=VENDOR, env=env, capture_output=True, text=True, check=False)
    worker_seconds = round(time.monotonic() - t0, 2)

    side_after = {}
    for s in spec.sources.values():
        for suffix in SIDE_SUFFIXES:
            f = Path(str(s.path) + suffix)
            if f.exists():
                side_after[str(f)] = f.stat().st_size
    removed = {}
    for f, size in side_after.items():
        if f not in side_before:
            Path(f).unlink()
            removed[f] = size
    hashes_after, _ = db_state(spec)
    unchanged = hashes_after == hashes_before

    if worker.returncode != 0 or not raw.is_file():
        raise ScoreError(f"문항별 판정 실패(종료 코드 {worker.returncode}): {_universal(worker.stderr)[-500:]}")
    judged = {row["position"]: int(row["ex_match"]) for row in R.read_jsonl(raw)}
    rows = []
    for rec in records:
        if rec["position"] not in judged:
            raise ScoreError(f"문항별 판정에 위치 {rec['position']}이 없습니다")
        rows.append(
            {
                "position": rec["position"],
                "question_id": rec["question_id"],
                "db_id": rec["db_id"],
                "difficulty": rec["difficulty"],
                "status": rec["status"],
                "ex_match": judged[rec["position"]],
            }
        )
    R.write_jsonl(run / R.EX_FILE, rows)

    total_correct = sum(r["ex_match"] for r in rows)
    by_diff = _bucket(rows, "difficulty")
    ours = {"total": {"correct": total_correct, "total": len(rows)}, "by_difficulty": by_diff}
    if run_official:
        agreed, detail = cross_check(ours, table)
        note = None
    else:
        agreed, detail, note = None, None, SKIP_NOTE
    leak_summary = leak(run_id) if leak is not None else None
    pct = official_percent(total_correct, count)
    summary = {
        "label": label(meta, count, sample),
        "sample": sample,
        "denominator": count,
        "ex_total": {"correct": total_correct, "denominator": count, "percent": None if pct is None else float(pct)},
        "by_difficulty": by_diff,
        "by_db": _bucket(rows, "db_id"),
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in R.STATUSES},
        "correct_by_status": {s: sum(r["ex_match"] for r in rows if r["status"] == s) for s in R.STATUSES},
        "cross_check": agreed,
        "cross_check_detail": detail,
        "cross_check_note": note,
        "data_unchanged": unchanged,
        "side_files": {
            "before": side_before,
            "created_and_removed": removed,
            "left_untouched": {f: s for f, s in side_after.items() if f in side_before},
        },
        "evaluator": evaluator,
        "per_question": {
            "file": R.EX_FILE,
            "method": "ex_worker.py: 채점기 package_sqls·execute_model 직접 호출",
            "elapsed_seconds": worker_seconds,
        },
        "integrity": {
            "records_sha256": sha256_file(run / R.RECORDS_FILE),
            "ex_sha256": sha256_file(run / R.EX_FILE),
            "run_sha256": sha256_file(run / R.RUN_FILE),
            "predictions_sha256": sha256_file(predictions),
        },
        "leak": leak_summary,
        "scored_at": now(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    R.write_json_atomic(run / R.SCORE_FILE, summary)
    return summary


def score_line(summary: dict) -> str:
    ex = summary["ex_total"]
    pct = official_percent(ex["correct"], ex["denominator"])
    leak = summary.get("leak")
    if leak is None:
        leak_text = "누설 검사 없음"
    elif leak.get("note"):
        leak_text = f"누설 검사 {leak['note']}"
    else:
        leak_text = f"누설 {leak.get('total', 0)}"
    return (
        f"score: {summary['label']} → EX {ex['correct']}/{ex['denominator']} ({pct}%) · "
        f"채점기 대조 {summary['cross_check']} · DB 불변 {summary['data_unchanged']} · {leak_text}"
    )


def score_failed(summary: dict) -> bool:
    leak = summary.get("leak") or {}
    return summary["cross_check"] is False or not summary["data_unchanged"] or bool(leak.get("total"))


# ── 예측 파일 받아 채점(rescore run) ─────────────────────


def import_predictions(config: Config, dataset: str, path: Path, run_id: str) -> Path:
    """예측 파일을 받아 rescore run 폴더를 만든다. .json(BIRD 예측 꼴) 또는 .jsonl(줄마다 position·db_id·sql)."""
    spec = config.datasets.get(dataset)
    if spec is None or spec.questions is None or not Path(spec.questions).is_file():
        raise ScoreError(f"데이터셋 {dataset}에 공개 문항 파일이 없습니다")
    run = R.run_dir(config.runs_dir, run_id)
    if run.exists():
        raise ScoreError(f"이미 있는 run 이름입니다: {run_id}")
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ScoreError(f"예측 파일이 없습니다: {path}")
    public = load_public_questions(spec.questions)
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        values = [data[str(i)] for i in range(len(data))]
        pairs = []
        for i, v in enumerate(values):
            sql, sep, db = str(v).rpartition(R.SEPARATOR)
            if not sep:
                raise ScoreError(f"예측 값 꼴이 아닙니다(위치 {i})")
            pairs.append((sql, db))
    elif path.suffix == ".jsonl":
        rows = R.read_jsonl(path)
        rows.sort(key=lambda r: r["position"])
        if [r["position"] for r in rows] != list(range(len(rows))):
            raise ScoreError("예측 파일의 위치가 0부터 이어지지 않습니다")
        pairs = [(r["sql"] if r.get("sql") is not None else R.FAILURE_SQL, r["db_id"]) for r in rows]
    else:
        raise ScoreError("예측 파일은 .json 또는 .jsonl이어야 합니다")
    count = len(pairs)
    if count == 0 or count > len(public):
        raise ScoreError(f"예측 {count}개가 공개 문항 {len(public)}개와 맞지 않습니다")
    records = []
    for i, (sql, db) in enumerate(pairs):
        q = public[i]
        if db != q.db_id:
            raise ScoreError(f"예측의 db_id가 공개 문항과 다릅니다(위치 {i})")
        failed = sql.startswith(R.FAILURE_SQL)
        records.append(
            {
                "position": i,
                "question_id": q.question_id,
                "db_id": q.db_id,
                "difficulty": q.difficulty,
                "question": q.question,
                "status": "no_sql" if failed else "generated",
                "status_reason": None,
                "sql": None if failed else sql,
                "exception": None,
                "final_exec": None,
                "refine_calls": 0,
                "stop": None,
                "llm": {
                    "call_count": 0,
                    "tool_calls": 0,
                    "elapsed_ms": 0,
                    "input_tokens": None,
                    "output_tokens": None,
                    "failures": [],
                },
                "elapsed_ms": 0,
                "attempt": 0,
                "item_dir": None,
                "finished_at": None,
            }
        )
    run.mkdir(parents=True)
    if path.suffix == ".json":
        shutil.copyfile(path, run / R.PREDICTIONS_FILE)
        pred_sha = sha256_file(run / R.PREDICTIONS_FILE)
    else:
        pred_sha = R.write_predictions(run, records)
    R.write_jsonl(run / R.RECORDS_FILE, records)
    started = now()
    meta = {
        "format": R.RUN_FORMAT,
        "run_id": run_id,
        "kind": "rescore",
        "dataset": dataset,
        "dataset_kind": spec.kind,
        "archive_sha256": spec.archive_sha256,
        "source": {"path": str(path), "sha256": sha256_file(path)},
        "questions": {
            "path": os.path.relpath(spec.questions, config.root),
            "sha256": sha256_file(Path(spec.questions)),
            "total": len(public),
        },
        "count": count,
        "limit": None,
        "sample": count != len(public),
        "workers": None,
        "llm": None,
        "git": git_info(config.root),
        "versions_start": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
        "databases": {},
        "score_after": False,
        "started_at": started,
        "command": list(sys.argv),
    }
    R.write_json_atomic(run / R.RUN_FILE, meta)
    R.write_json_atomic(
        run / R.MANIFEST_FILE,
        {
            "format": R.RUN_FORMAT,
            "finished_at": started,
            "status_counts": {s: sum(1 for r in records if r["status"] == s) for s in R.STATUSES},
            "records": {"sha256": sha256_file(run / R.RECORDS_FILE), "count": count},
            "predictions": {"sha256": pred_sha},
            "halted": None,
            "resumes": [],
        },
    )
    R.write_json_atomic(
        run / R.CONTROL_FILE,
        {"run_id": run_id, "pid": None, "started_at": started, "state": "finished", "phase": "done", "sessions": []},
    )
    return run
