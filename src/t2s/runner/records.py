"""run 폴더의 꼴·파일 이름·기록 읽고 쓰기·예측 값. 표준 라이브러리만 쓴다.

채점 쪽(t2s.eval)이 이 파일을 불러오므로 여기서 pipeline·llm·prep을 import하지 않는다(채점 프로세스에 생성 쪽 코드가
딸려 올라오지 않게). 예측 값의 꼴은 TS benchmarks/benchkit/submit.ts와 같다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

RUN_FORMAT = "t2s-run/1"
SEPARATOR = "\t----- bird -----\t"
FAILURE_SQL = "SELECT 1 FROM __no_sql__"  # 없는 테이블이라 늘 오류 → 0점. 빈 SQL은 정답이 0행인 문항에서 1점을 받는다
STATUSES = ("generated", "no_sql", "exception", "not_started")
EXEC_STATUSES = ("rows", "empty", "error", "timeout")

RUN_FILE = "run.json"
CONTROL_FILE = "control.json"
LOG_FILE = "run.log"
QUESTIONS_FILE = "questions.json"
MANIFEST_FILE = "manifest.json"
RECORDS_FILE = "records.jsonl"
PREDICTIONS_FILE = "predictions.json"
SCORE_FILE = "score.json"
EX_FILE = "ex.jsonl"
LEAK_FILE = "leak-audit.json"
REPORT_FILE = "report.json"
REPORT_MD_FILE = "report.md"
ITEMS_DIR = "items"
RECORD_FILE = "record.json"
TRACE_FILE = "trace.json"
ASK_DIR = "ask"


def run_dir(runs_dir: Path, run_id: str) -> Path:
    return Path(runs_dir) / run_id


def item_dir(run: Path, position: int) -> Path:
    return Path(run) / ITEMS_DIR / f"{position:04d}"


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_json_if(path: Path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return read_json(p)
    except (json.JSONDecodeError, OSError):
        return None


def write_json_atomic(path: Path, data) -> None:
    """<이름>.tmp에 쓰고 os.replace로 바꿔 넣는다(반쯤 쓴 파일이 남지 않게)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    os.replace(tmp, p)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def is_run(path: Path) -> bool:
    return (Path(path) / RUN_FILE).is_file()


def list_runs(runs_dir: Path) -> list[str]:
    """run.json이 있는 폴더 이름들(ask 폴더 제외)."""
    base = Path(runs_dir)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name != ASK_DIR and is_run(p))


def latest_run(runs_dir: Path) -> str | None:
    """run.json의 started_at이 가장 늦은 run."""
    best: tuple[str, str] | None = None
    for name in list_runs(runs_dir):
        meta = read_json_if(Path(runs_dir) / name / RUN_FILE) or {}
        key = (str(meta.get("started_at", "")), name)
        if best is None or key > best:
            best = key
    return None if best is None else best[1]


def read_item_records(run: Path) -> dict[int, dict]:
    """items/<4자리>/record.json만 읽는다(이어 돌 때 옮겨 둔 <위치>.attempt-<n> 폴더는 뺀다)."""
    out: dict[int, dict] = {}
    base = Path(run) / ITEMS_DIR
    if not base.is_dir():
        return out
    for p in base.iterdir():
        if not (p.is_dir() and len(p.name) == 4 and p.name.isdigit()):
            continue
        rec = read_json_if(p / RECORD_FILE)
        if rec is not None:
            out[int(p.name)] = rec
    return out


def prediction_value(record: dict) -> str:
    """generated면 SQL, 아니면 FAILURE_SQL. 뒤에 SEPARATOR와 db_id."""
    sql = record.get("sql") if record.get("status") == "generated" and record.get("sql") else FAILURE_SQL
    return f"{sql}{SEPARATOR}{record['db_id']}"


def write_predictions(run: Path, records: list[dict]) -> str:
    """키 "0".."N-1"(위치 순서). sha256을 돌려준다."""
    data = {str(r["position"]): prediction_value(r) for r in sorted(records, key=lambda r: r["position"])}
    path = Path(run) / PREDICTIONS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return sha256_file(path)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # 거두지 않은 좀비도 살아 있는 것으로 보이므로 ps로 한 번 더 본다
    try:
        import subprocess

        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
        return bool(r.stdout.strip()) and not r.stdout.strip().startswith("Z")
    except OSError:
        return True


def update_control(run: Path, **fields) -> dict:
    """control.json을 다시 읽고 칸을 고쳐 쓴다."""
    p = Path(run) / CONTROL_FILE
    data = read_json_if(p) or {}
    data.update(fields)
    write_json_atomic(p, data)
    return data
