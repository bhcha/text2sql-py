"""명령 run·status·stop·resume. 첫머리에서 읽기 감시를 건다. 채점을 import하지 않는다.

`run`은 앞에서 확인을 마친 뒤 `caffeinate -i`로 분리된 프로세스(`run --worker`)를 띄우고 바로 돌아온다.
분리된 프로세스는 생성이 끝나면 `score`와 `report`를 하위 프로세스로 이어 부른다(--no-score면 부르지 않는다).
TS 하네스(text2sql-harness) harness/allbird.ts(분리 실행, 제어 파일, status 줄, 멈춤)를 옮겼다.
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from t2s.cli.data_cmds import err, out
from t2s.dataset.config import Config, ConfigError, find_root, load_config
from t2s.dataset.questions import (
    PublicQuestion,
    QuestionsError,
    load_public_questions,
    load_text_questions,
    to_public_json,
)
from t2s.dataset.state import StateError, current_dataset, load_state
from t2s.datasource.base import DataSourceError
from t2s.datasource.factory import open_source
from t2s.llm import select
from t2s.pipeline import refine
from t2s.pipeline.run import PipelineSettings
from t2s.pipeline.value_search import CachedEmbedder
from t2s.prep import assets, embed
from t2s.runner import batch
from t2s.runner import records as R
from t2s.runner.guard import install_guard

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NEEDS_PREP = 0, 1, 2, 5
OUTGOING = "질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글"
PHASE_NAMES = {"generate": "생성", "score": "채점", "report": "보고"}
HALT_NAMES = {"usage_limit": "사용량 한도", "consecutive_failures": "연속 실패", "stop": "stop"}
STOP_WAIT_SECONDS = 10


def _rel(root: Path, p: Path) -> str:
    return os.path.relpath(p, root)


_popen = subprocess.Popen  # 테스트가 바꿔치기한다


def _spawn(cmd: list[str], *, cwd: Path, env: dict, log: Path) -> int:
    """분리된 프로세스를 띄우고 pid를 돌려준다."""
    with open(log, "a", encoding="utf-8") as f:
        proc = _popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def _setup():
    try:
        root = find_root()
    except ConfigError as e:
        err(f"오류: {e}")
        return None
    install_guard(root)
    try:
        state = load_state(root)
        config = load_config(root, state)
    except (ConfigError, StateError) as e:
        err(f"오류: {e}")
        return None
    return root, state, config


def run(args) -> int:
    setup = _setup()
    if setup is None:
        return EXIT_FAIL
    root, state, config = setup
    handler = {"run": cmd_run, "status": cmd_status, "stop": cmd_stop, "resume": cmd_resume}[args.command]
    try:
        return handler(args, root, state, config)
    except (DataSourceError, QuestionsError, ConfigError, StateError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL


# ── run ──────────────────────────────────────────────────


def _running_run(config: Config) -> str | None:
    for name in R.list_runs(config.runs_dir):
        c = R.read_json_if(R.run_dir(config.runs_dir, name) / R.CONTROL_FILE) or {}
        if c.get("state") == "running" and R.pid_alive(c.get("pid")):
            return name
    return None


def _questions(args, spec, root: Path) -> tuple[list[PublicQuestion], Path | None]:
    """(문항, --questions로 받은 원본 경로 또는 None)."""
    if args.questions:
        path = Path(args.questions).expanduser().resolve()
        if not path.is_file():
            raise QuestionsError(f"문항 파일이 없습니다: {path}")
        if path.suffix == ".json":
            return load_public_questions(path), path
        if len(spec.sources) != 1:
            raise QuestionsError("텍스트 문항 파일은 소스가 하나인 데이터셋에서만 쓴다(공개 문항 꼴 JSON을 준다)")
        return load_text_questions(path, next(iter(spec.sources))), path
    if spec.questions is None:
        return [], None
    return load_public_questions(spec.questions), None


def cmd_run(args, root: Path, state, config: Config) -> int:
    if args.worker:
        return work(root, config, args.run_id)
    name, _ = current_dataset(config, state)
    if not name or name not in config.datasets:
        err("데이터셋을 정하지 않았습니다(/connect)")
        return EXIT_USAGE
    spec = config.datasets[name]
    if spec.missing:
        hint = "먼저 /getbird" if spec.kind == "bird-minidev" else "등록한 경로를 확인"
        err(f"{name}: {spec.missing} ({hint})")
        return EXIT_FAIL

    questions, given = _questions(args, spec, root)
    if not questions:
        err("이 데이터셋에는 문항 파일이 없습니다. --questions <파일>")
        return EXIT_USAGE
    unknown = sorted({q.db_id for q in questions} - set(spec.sources))
    if unknown:
        err(f"문항의 db_id가 데이터셋 {name}의 소스가 아닙니다: {', '.join(unknown[:5])}")
        return EXIT_USAGE
    total = len(questions)
    count = min(args.limit, total) if args.limit else total
    chosen = questions[:count]

    try:
        llm = select.select_from_args(
            args, config.profiles, config.default_profile, env=os.environ, cwd=root, home=Path.home()
        )
    except select.ModelUnknown as e:
        err(str(e))
        return EXIT_USAGE
    out(select.first_line(llm))
    if not select.is_reference(llm):
        out("원본 재현 비교 아님(모델이 다름)")

    db_ids = sorted({q.db_id for q in chosen})
    try:
        model_hashes = embed.check_model_files(config.embedding_model_dir)
    except embed.EmbeddingModelMissing as e:
        err(str(e))
        err(f"준비 필요: t2s prepare {name}")
        return EXIT_NEEDS_PREP
    for db in db_ids:
        s = spec.sources[db]
        st = assets.asset_state(
            open_source(s.kind, path=str(s.path)), assets.asset_dir(config.data_dir, name, db), model_hashes
        )
        if st.state != "ok":
            err(f"자산이 {st.state}입니다({db}: {', '.join(st.reasons)})")
            err(f"준비 필요: t2s prepare {name}")
            return EXIT_NEEDS_PREP

    bin = select.backend_bin(llm.backend)
    if bin is not None and shutil.which(bin) is None:
        err(f"모델 실행 파일을 찾지 못했습니다: {bin}")
        return EXIT_FAIL
    busy = _running_run(config)
    if busy:
        err(f"이미 도는 run이 있습니다: {busy}")
        return EXIT_FAIL
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d')}-{name}-{secrets.token_hex(3)}"
    run = R.run_dir(config.runs_dir, run_id)
    if run.exists() or run_id == R.ASK_DIR:
        err(f"이미 있는 run 이름입니다: {run_id}")
        return EXIT_USAGE

    if llm.backend != "fake" and not args.no_probe:
        outcome = select.backend_for(llm).call("Reply with OK.", None, name="generate_sql", attempt=0)
        if outcome.failure is not None:
            err(f"모델 호출 확인 실패: {outcome.failure.type}: {outcome.failure.message[:300]}")
            return EXIT_FAIL

    if llm.backend == "fake":
        out("밖으로 나가는 것 없음(가짜 백엔드)")
    else:
        out(f"밖으로 나감({select.provider_name(llm.backend)}): {OUTGOING}")

    run.mkdir(parents=True)
    if given is not None:
        qpath = run / R.QUESTIONS_FILE
        R.write_json_atomic(qpath, to_public_json(questions))
    else:
        qpath = Path(spec.questions)
    settings = PipelineSettings.from_mapping(config.pipeline)
    git = batch.git_info(root)
    run_meta = {
        "format": R.RUN_FORMAT,
        "run_id": run_id,
        "kind": "generate",
        "dataset": name,
        "dataset_kind": spec.kind,
        "archive_sha256": spec.archive_sha256,
        "questions": {
            "path": _rel(root, qpath),
            "sha256": R.sha256_file(qpath),
            "total": total,
            "given": None if given is None else str(given),
        },
        "count": count,
        "limit": args.limit,
        "sample": count != total,
        "workers": args.workers,
        "llm": llm.to_json(),
        "pipeline": {**asdict(settings), "refine_template_sha256": refine.template_sha256()},
        "git": git,
        "versions_start": batch.versions(),
        "databases": batch.fingerprints(spec, db_ids),
        "score_after": not args.no_score,
        "started_at": batch.now(),
        "command": list(sys.argv),
    }
    R.write_json_atomic(run / R.RUN_FILE, run_meta)
    R.write_json_atomic(
        run / R.CONTROL_FILE,
        {"run_id": run_id, "pid": None, "started_at": run_meta["started_at"], "state": "starting", "sessions": []},
    )
    return _launch(root, config, run, run_id, args.foreground, f"{name} {count}문항 · 동시 {args.workers}")


def _launch(root: Path, config: Config, run: Path, run_id: str, foreground: bool, what: str) -> int:
    if foreground:
        R.update_control(run, pid=os.getpid(), state="running", phase="generate")
        out(f"시작: {run_id} (pid {os.getpid()}, 앞에서 돎) · {what}")
        return work(root, config, run_id)
    cmd = [sys.executable, "-m", "t2s.cli.main", "run", "--worker", "--run-id", run_id]
    caffeinate = shutil.which("caffeinate")
    if caffeinate:
        cmd = [caffeinate, "-i", *cmd]
    env = {**os.environ, "T2S_ROOT": str(root)}
    pid = _spawn(cmd, cwd=root, env=env, log=run / R.LOG_FILE)
    R.update_control(run, pid=pid, state="running", phase="generate", command=cmd)
    if not caffeinate:
        out("caffeinate가 없어 잠자기 막기 없이 띄웠습니다")
    out(f"시작: {run_id} (pid {pid}) · {what}")
    out(f"진행: t2s status · 로그: {_rel(root, run / R.LOG_FILE)}")
    return EXIT_OK


# ── 본체(분리된 프로세스) ────────────────────────────────


def work(root: Path, config: Config, run_id: str) -> int:
    run = R.run_dir(config.runs_dir, run_id)
    meta = R.read_json(run / R.RUN_FILE)
    spec = config.datasets.get(meta["dataset"])
    if spec is None:
        err(f"run의 데이터셋이 설정에 없습니다: {meta['dataset']}")
        R.update_control(run, state="failed")
        return EXIT_FAIL
    questions = load_public_questions(root / meta["questions"]["path"])[: meta["count"]]
    llm = select.ResolvedLlm.from_json(meta["llm"])
    settings = PipelineSettings(**{k: v for k, v in meta["pipeline"].items() if k != "refine_template_sha256"})
    R.update_control(run, worker_pid=os.getpid(), state="running", phase="generate")
    started = time.monotonic()
    try:
        model_hashes = embed.check_model_files(config.embedding_model_dir)
        embedder = CachedEmbedder(batch.LockedEmbedder(embed.load_embedder(config.embedding_model_dir)))
    except (embed.EmbeddingModelMissing, embed.EmbeddingFailed) as e:
        err(f"임베딩 모델을 싣지 못했습니다: {e}")
        R.update_control(run, state="failed")
        return EXIT_FAIL
    ctx = batch.RunContext(
        config=config,
        spec=spec,
        run=run,
        llm=llm,
        settings=settings,
        embedder=embedder,
        model_hashes=model_hashes,
        backend_factory=BACKEND_FACTORY,
    )
    done = R.read_item_records(run)
    attempts = {q.position: _next_attempt(run, q.position) for q in questions}
    todo = [q for q in questions if q.position not in done]
    previous = None
    if threading_main():
        previous = signal.signal(signal.SIGTERM, lambda signum, frame: batch.request_stop(ctx))
    try:
        batch.run_pool(todo, ctx, int(meta.get("workers") or 4), attempts)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    manifest = batch.finalize(ctx, questions, meta, started_monotonic=started)
    out(
        f"생성 끝: {run_id} · "
        + ", ".join(f"{k} {v}" for k, v in manifest["status_counts"].items())
        + f" · DB 불변 {manifest['database_unchanged']}"
    )
    code = EXIT_OK if manifest["database_unchanged"] else EXIT_FAIL
    if manifest["halted"] is not None:
        R.update_control(run, state="stopped", phase="generate")
        out(f"멈춤: {HALT_NAMES.get(manifest['halted']['reason'], manifest['halted']['reason'])} · 채점하지 않음")
        return code
    if meta.get("score_after"):
        code = max(code, _score_and_report(root, run, run_id))
    R.update_control(run, state="finished", phase="done")
    return code


BACKEND_FACTORY = None  # 테스트가 바꿔치기한다(문항마다 부를 백엔드를 만드는 함수)


def threading_main() -> bool:
    import threading

    return threading.current_thread() is threading.main_thread()


def _score_and_report(root: Path, run: Path, run_id: str) -> int:
    """채점·보고를 하위 프로세스로 부른다. 이 프로세스는 t2s.eval을 import하지 않는다."""
    env = {**os.environ, "T2S_ROOT": str(root)}
    code = EXIT_OK
    with open(run / R.LOG_FILE, "a", encoding="utf-8") as log:
        for phase, extra in (("score", []), ("report", [])):
            R.update_control(run, phase=phase)
            cmd = [sys.executable, "-m", "t2s.cli.main", phase, "--run", run_id, *extra]
            log.flush()
            r = subprocess.run(cmd, cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            if r.returncode not in (0, 4):
                code = EXIT_FAIL
    return code


def _next_attempt(run: Path, position: int) -> int:
    base = R.item_dir(run, position)
    n = len(list(base.parent.glob(base.name + ".attempt-*"))) if base.parent.is_dir() else 0
    return n + 1


# ── status ───────────────────────────────────────────────


def _pick_run(args, config: Config) -> str | None:
    rid = getattr(args, "run_id", None) or getattr(args, "run", None)
    return rid or R.latest_run(config.runs_dir)


def state_word(run: Path) -> str:
    control = R.read_json_if(run / R.CONTROL_FILE) or {}
    manifest = R.read_json_if(run / R.MANIFEST_FILE)
    alive = R.pid_alive(control.get("pid")) or R.pid_alive(control.get("worker_pid"))
    if control.get("state") in ("running", "starting") and alive:
        phase = PHASE_NAMES.get(control.get("phase", "generate"), control.get("phase"))
        return f"진행 중({phase}, pid {control.get('pid')})"
    if manifest and manifest.get("halted"):
        return f"멈춤({HALT_NAMES.get(manifest['halted']['reason'], manifest['halted']['reason'])})"
    if control.get("state") == "stopped":
        return "멈춤(stop)"
    if manifest is None:
        return "멈춤(프로세스 없음)"
    if control.get("state") == "failed":
        return "멈춤(실패)"
    return "끝남"


def cmd_status(args, root: Path, state, config: Config) -> int:
    rid = _pick_run(args, config)
    if rid is None:
        out("시작한 run이 없습니다")
        return EXIT_OK
    run = R.run_dir(config.runs_dir, rid)
    meta = R.read_json_if(run / R.RUN_FILE)
    if meta is None:
        err(f"run이 없습니다: {rid}")
        return EXIT_FAIL
    control = R.read_json_if(run / R.CONTROL_FILE) or {}
    items = R.read_item_records(run)
    total = int(meta.get("count", 0))
    word = state_word(run)
    item_limit = float(meta.get("llm", {}).get("item_timeout_seconds") or 0) * 1000
    slow = sum(1 for r in items.values() if item_limit and r.get("elapsed_ms", 0) > item_limit)
    out(f"{rid} · {meta.get('dataset')} · {word}" + (f" · 오래 걸린 문항 {slow}" if slow else ""))
    if meta.get("kind") == "rescore":
        out("재채점 run(생성 없음)")
    else:
        out(select.first_line(select.ResolvedLlm.from_json(meta["llm"])))
    started = control.get("started_at") or meta.get("started_at")
    finished = (R.read_json_if(run / R.MANIFEST_FILE) or {}).get("finished_at")
    try:
        t0 = datetime.fromisoformat(str(started)).timestamp()
        t1 = datetime.fromisoformat(str(finished)).timestamp() if finished else time.time()
        elapsed = max(0, t1 - t0)
    except ValueError:
        elapsed = 0
    n = len(items)
    line = f"진행 {n}/{total} · 경과 {round(elapsed)}초"
    if word.startswith("진행 중(생성") and 0 < n < total:
        line += f" · 남은 시간 약 {round(elapsed / n * (total - n))}초"
    out(line)
    counts = {s: sum(1 for r in items.values() if r.get("status") == s) for s in ("generated", "no_sql", "exception")}
    out("상태 " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    ex = {s: sum(1 for r in items.values() if (r.get("final_exec") or {}).get("status") == s) for s in R.EXEC_STATUSES}
    ex_none = sum(1 for r in items.values() if not r.get("final_exec"))
    out("실행 " + " · ".join(f"{k} {v}" for k, v in ex.items()) + f" · 없음 {ex_none}")
    last = max(
        (r for r in items.values() if r.get("exception")), key=lambda r: r.get("finished_at") or "", default=None
    )
    if last is not None:
        e = last["exception"]
        out(f"마지막 예외: 위치 {last['position']} {e.get('type')}: {str(e.get('message', ''))[:80]}")

    def yn(name: str) -> str:
        return "예" if (run / name).is_file() else "아니오"

    out(f"생성 끝 {yn(R.MANIFEST_FILE)} · 채점 끝 {yn(R.SCORE_FILE)} · 보고 끝 {yn(R.REPORT_FILE)}")
    log = run / R.LOG_FILE
    if log.is_file():
        tail = [x for x in log.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()][-2:]
        for x in tail:
            out(f"로그: {x[:200]}")
    return EXIT_OK


# ── stop ─────────────────────────────────────────────────


def cmd_stop(args, root: Path, state, config: Config) -> int:
    rid = _pick_run(args, config)
    if rid is None:
        out("시작한 run이 없습니다")
        return EXIT_OK
    run = R.run_dir(config.runs_dir, rid)
    control = R.read_json_if(run / R.CONTROL_FILE) or {}
    pid = control.get("pid")
    if not R.pid_alive(pid):
        out(f"이미 멈춘 run입니다: {rid}")
        return EXIT_OK
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline and R.pid_alive(pid):
        time.sleep(0.2)
    if R.pid_alive(pid):
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    R.update_control(run, state="stopped", stopped_at=batch.now())
    out(f"{rid} 멈춤")
    return EXIT_OK


# ── resume ───────────────────────────────────────────────


def cmd_resume(args, root: Path, state, config: Config) -> int:
    rid = _pick_run(args, config)
    if rid is None:
        err("시작한 run이 없습니다")
        return EXIT_FAIL
    run = R.run_dir(config.runs_dir, rid)
    meta = R.read_json_if(run / R.RUN_FILE)
    if meta is None:
        err(f"run이 없습니다: {rid}")
        return EXIT_FAIL
    control = R.read_json_if(run / R.CONTROL_FILE) or {}

    def refuse(why: str) -> int:
        err(f"이어 돌 수 없습니다: {why}")
        return EXIT_FAIL

    if control.get("state") in ("running", "starting") and (
        R.pid_alive(control.get("pid")) or R.pid_alive(control.get("worker_pid"))
    ):
        return refuse("아직 돌고 있습니다")
    if (run / R.SCORE_FILE).exists():
        return refuse("채점한 run입니다")
    if meta.get("kind") == "rescore":
        return refuse("재채점 run입니다")
    now_git = batch.git_info(root)
    then = meta.get("git") or {}
    if now_git.get("dirty") or then.get("dirty"):
        return refuse("미커밋 코드 상태입니다(같은 코드라고 말할 수 없음)")
    if (now_git.get("head"), now_git.get("src_tree")) != (then.get("head"), then.get("src_tree")):
        return refuse("코드 판이 다릅니다")
    spec = config.datasets.get(meta["dataset"])
    if spec is None:
        return refuse("데이터셋이 설정에 없습니다")
    qpath = root / meta["questions"]["path"]
    if not qpath.is_file() or R.sha256_file(qpath) != meta["questions"]["sha256"]:
        return refuse("문항 파일이 다릅니다")
    if batch.fingerprints(spec, list(meta.get("databases", {}))) != meta.get("databases"):
        return refuse("DB 지문이 다릅니다")
    busy = _running_run(config)
    if busy:
        return refuse(f"이미 도는 run이 있습니다: {busy}")

    items = R.read_item_records(run)
    retry = []
    for pos in range(int(meta["count"])):
        rec = items.get(pos)
        t = ((rec or {}).get("exception") or {}).get("type")
        if rec is None or t in select_retryable():
            retry.append(pos)
    for pos in retry:
        folder = R.item_dir(run, pos)
        if folder.exists():
            folder.rename(folder.with_name(f"{folder.name}.attempt-{_next_attempt(run, pos)}"))
    for name in (R.RECORDS_FILE, R.PREDICTIONS_FILE, R.MANIFEST_FILE):
        (run / name).unlink(missing_ok=True)
    resumes = list(control.get("resumes", []))
    resumes.append({"at": batch.now(), "rerun": len(retry), "positions": retry})
    sessions = list(control.get("sessions", []))
    sessions.append({"pid": control.get("pid"), "started_at": control.get("started_at")})
    R.update_control(run, resumes=resumes, sessions=sessions, started_at=batch.now(), state="starting")
    out(select.first_line(select.ResolvedLlm.from_json(meta["llm"])))
    return _launch(root, config, run, rid, args.foreground, f"이어 돌기 {len(retry)}문항")


def select_retryable() -> tuple[str, ...]:
    from t2s.llm.base import RETRYABLE_FAILURES

    return RETRYABLE_FAILURES
