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
# 모델 단위 경로. t2s.pipeline.route.ROUTES와 같다(채점 쪽이 pipeline을 불러오지 않게 여기 따로 둔다. 테스트가 대조한다)
ROUTES = ("single", "harness", "partial")
HARNESS_ROUTE = "harness"
EXEC_STATUSES = ("rows", "empty", "error", "timeout")

RUN_FILE = "run.json"
CONTROL_FILE = "control.json"
LOG_FILE = "run.log"
QUESTIONS_FILE = "questions.json"
SEMANTIC_FILE = "semantic.json"  # semantic used run만: 지표 정의 한 줄 글의 사본(시작한 프로세스가 쓰고 분리 프로세스·resume은 이것만 읽는다)
HINTS_FILE = "hints.json"  # 힌트를 주는 run만: 위치 순서의 힌트 글 목록(시작한 프로세스가 쓰고 분리 프로세스·resume은 이것만 읽는다)
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


def find_run(runs_dir: Path, run_id: str, legacy_dirs=()) -> tuple[Path, bool] | None:
    """run 이름 → (폴더, 옛 run인가). 세션 폴더 자리에서 먼저 찾고, 없으면 옛 run 폴더 자리(legacy_dirs)에서 차례로 찾는다.

    옛 run 폴더는 읽기만 한다. 부르는 쪽은 옛 run이면 그 폴더에 쓰지 않는다. run 이름은 폴더 이름 하나여야 한다.
    run.json이 있는 폴더만 run으로 본다. 어디에도 없으면 None.
    """
    if not run_id or run_id in (".", "..") or "/" in run_id or os.sep in run_id:
        return None
    here = run_dir(runs_dir, run_id)
    if is_run(here):
        return here, False
    for base in legacy_dirs:
        there = run_dir(base, run_id)
        if is_run(there):
            return there, True
    return None


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


# ── 경로(run.json의 route 칸) ────────────────────────────


def route_of(meta: dict | None) -> str:
    """run.json의 적용 경로. 칸이 없는 옛 run·재채점 run은 harness."""
    route = (meta or {}).get("route")
    applied = route.get("applied") if isinstance(route, dict) else None
    return applied if applied in ROUTES else HARNESS_ROUTE


def route_label(meta: dict | None) -> str:
    """라벨의 경로 꼬리표(`1회 실행` 뒤, `구현 Python` 앞). harness면 빈 글이라 라벨이 바뀌지 않는다."""
    applied = route_of(meta)
    if applied == "single":
        return " / 경로 single(단일 호출)"
    if applied == "partial":
        over = ((meta or {}).get("route") or {}).get("overrides") or {}
        text = ", ".join(f"{k}={_route_value(v)}" for k, v in over.items())
        return f" / 경로 partial({text})"
    return ""


def _route_value(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def route_warnings(meta: dict | None) -> list[str]:
    route = (meta or {}).get("route")
    return [str(w) for w in (route.get("warnings") or [])] if isinstance(route, dict) else []


def pair_of(meta: dict | None) -> dict | None:
    """route-start로 함께 만든 짝 run이면 {"id", "role", "other"}, 아니면 None."""
    route = (meta or {}).get("route")
    pair = route.get("pair") if isinstance(route, dict) else None
    if isinstance(pair, dict) and all(isinstance(pair.get(k), str) for k in ("id", "role", "other")):
        return pair
    return None


def list_pairs(runs_dir: Path) -> dict[str, dict]:
    """짝 이름 → {"runs": {역할: run 이름}, "started_at": 가장 이른 시작}."""
    out: dict[str, dict] = {}
    for name in list_runs(runs_dir):
        meta = read_json_if(Path(runs_dir) / name / RUN_FILE) or {}
        pair = pair_of(meta)
        if pair is None:
            continue
        entry = out.setdefault(pair["id"], {"runs": {}, "started_at": str(meta.get("started_at", ""))})
        entry["runs"][pair["role"]] = name
        entry["started_at"] = min(entry["started_at"], str(meta.get("started_at", "")))
    return out


def latest_pair(runs_dir: Path) -> str | None:
    pairs = list_pairs(runs_dir)
    if not pairs:
        return None
    return max(pairs, key=lambda k: (pairs[k]["started_at"], k))


JUDGMENT_PREFIX = "route-judgment-"  # 경로 판정 파일: 하네스 run 폴더의 route-judgment-<후보 run>.json


def judgment_path(runs_dir: Path, harness_run: str, candidate_run: str) -> Path:
    return run_dir(runs_dir, harness_run) / f"{JUDGMENT_PREFIX}{candidate_run}.json"


def pick_pair_runs(runs_dir: Path, pair: str | None) -> tuple[str, str, str]:
    """짝 이름(없으면 가장 최근 짝) → (하네스 run, 후보 run, 짝 이름). 없거나 꼴이 틀리면 ValueError."""
    pairs = list_pairs(runs_dir)
    name = pair or latest_pair(runs_dir)
    if name is None:
        raise ValueError("판정할 짝(route-start)이 없습니다")
    if name not in pairs:
        raise ValueError(f"짝이 없습니다: {name}")
    runs = pairs[name]["runs"]
    others = [r for role, r in runs.items() if role != HARNESS_ROUTE]
    if HARNESS_ROUTE not in runs or len(others) != 1:
        raise ValueError(f"짝 {name}의 run이 하네스 하나·후보 하나가 아닙니다")
    return runs[HARNESS_ROUTE], others[0], name
