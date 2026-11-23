"""명령 run·status·stop·resume. 첫머리에서 읽기 감시를 건다. 채점을 import하지 않는다.

`run`은 앞에서 확인을 마친 뒤 `caffeinate -i`로 분리된 프로세스(`run --worker`)를 띄우고 바로 돌아온다.
분리된 프로세스는 생성이 끝나면 `score`와 `report`를 하위 프로세스로 이어 부른다(--no-score면 부르지 않는다).
TS 하네스(text2sql-harness) harness/allbird.ts(분리 실행, 제어 파일, status 줄, 멈춤)를 옮겼다.

evidence(`--evidence used|not_used`, 기본 not_used): used면 run을 시작하는 이 프로세스만 읽기 감시를 allow_hints=True로
걸어 공개 evidence 파일(BIRD) 또는 스터디 모델 입력 파일의 문항 조건(Olist)을 읽고, 위치별 글을 run 폴더의 hints.json에
쓴다. 분리된 프로세스와 resume은 hints.json만 읽는다(run.json의 sha256과 대조).

경로(run.json `route`): `pipeline/route.py`가 정한다. `--route`(내부 인자)는 측정용 강제다.
`route-start`는 같은 확인을 한 번 하고 하네스·단일 호출 run 둘을 짝(`route.pair`)으로 만들어 함께 띄운다. 짝 run은 서로를
"이미 도는 run"으로 막지 않는다. 채점은 차례를 지킨다(단일 호출 run은 하네스 run의 생성이 끝난 뒤, 하네스 run은 단일 호출
run이 끝난 뒤). 채점기가 DB를 일반 연결로 열기 때문에 같은 데이터셋의 생성·채점이 겹치지 않게 한다.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from t2s import engine
from t2s.cli.data_cmds import err, out, sandbox_hint
from t2s.dataset import hints as H
from t2s.dataset.config import Config, ConfigError, DatasetSpec, find_root, load_config
from t2s.dataset.questions import (
    PublicQuestion,
    QuestionsError,
    load_public_questions,
    load_questions_json,
    load_text_questions,
    to_public_json,
)
from t2s.dataset.state import StateError, current_dataset, load_state
from t2s.datasource.base import DataSourceError
from t2s.datasource.factory import open_source
from t2s.llm import select
from t2s.pipeline import refine
from t2s.pipeline import route as RT
from t2s.pipeline.run import PipelineSettings
from t2s.pipeline.value_search import CachedEmbedder
from t2s.prep import assets, embed
from t2s.runner import batch
from t2s.runner import records as R
from t2s.runner.guard import install_guard
from t2s.session import index as session_index
from t2s.session import names

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NEEDS_PREP = 0, 1, 2, 5
OUTGOING = "질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글"
OUTGOING_EVIDENCE = "문항 evidence 글"
OUTGOING_SINGLE = "질문, CREATE 원문"  # 단일 호출 경로(지금 목록의 부분집합)
EVIDENCE_USED, EVIDENCE_NOT_USED = "used", "not_used"
NEEDS_GETBIRD = "먼저 /getbird(다시 부르면 evidence 파일을 만든다)"
PHASE_NAMES = {"generate": "생성", "waiting": "채점 차례 기다림", "score": "채점", "report": "보고"}
HALT_NAMES = {"usage_limit": "사용량 한도", "consecutive_failures": "연속 실패", "stop": "stop"}
STOP_WAIT_SECONDS = 10
PAIR_POLL_SECONDS = 5.0  # 짝 run의 채점 차례를 기다릴 때 control.json을 다시 보는 간격(테스트가 줄인다)


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


def _setup(allow_hints: bool = False):
    try:
        root = find_root()
    except ConfigError as e:
        err(f"오류: {e}")
        return None
    if allow_hints:
        install_guard(root, allow_hints=True)  # evidence used run을 시작하는 프로세스 한 곳뿐
    else:
        install_guard(root)
    try:
        state = load_state(root)
        config = load_config(root, state)
    except (ConfigError, StateError) as e:
        err(f"오류: {e}")
        return None
    return root, state, config


def run(args) -> int:
    allow_hints = (
        args.command in ("run", "route-start")
        and not getattr(args, "worker", False)
        and getattr(args, "evidence", EVIDENCE_NOT_USED) == EVIDENCE_USED
    )
    setup = _setup(allow_hints)
    if setup is None:
        return EXIT_FAIL
    root, state, config = setup
    handler = {
        "run": cmd_run,
        "status": cmd_status,
        "stop": cmd_stop,
        "resume": cmd_resume,
        "route-start": cmd_route_start,
        "route-status": cmd_route_status,
    }[args.command]
    try:
        return handler(args, root, state, config)
    except (DataSourceError, QuestionsError, ConfigError, StateError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL


# ── run ──────────────────────────────────────────────────


def _running_run(config: Config, skip: tuple[str, ...] = ()) -> str | None:
    """돌고 있는 run 이름. skip은 건너뛸 이름(짝 run의 resume에서 다른 짝 run)."""
    for name in R.list_runs(config.runs_dir):
        if name in skip:
            continue
        c = R.read_json_if(R.run_dir(config.runs_dir, name) / R.CONTROL_FILE) or {}
        if c.get("state") == "running" and R.pid_alive(c.get("pid")):
            return name
    return None


def _questions(
    args, spec, root: Path, keep_conditions: bool = False
) -> tuple[list[PublicQuestion], Path | None, dict | None, list[list[str]] | None]:
    """(문항, --questions로 받은 원본 경로 또는 None, run.json questions에 더할 칸 또는 None,
    문항별 조건 목록 또는 None). 조건 목록은 keep_conditions이고 스터디 모델 입력 꼴일 때만 있다."""
    single = next(iter(spec.sources)) if len(spec.sources) == 1 else None
    if args.questions:
        path = Path(args.questions).expanduser().resolve()
        if not path.is_file():
            raise QuestionsError(f"문항 파일이 없습니다: {path}")
        if path.suffix == ".json":
            if keep_conditions:
                questions, extra, conditions = load_questions_json(path, single, keep_conditions=True)
                return questions, path, extra, conditions
            questions, extra = load_questions_json(path, single)
            return questions, path, extra, None
        if single is None:
            raise QuestionsError("텍스트 문항 파일은 소스가 하나인 데이터셋에서만 쓴다(공개 문항 꼴 JSON을 준다)")
        return load_text_questions(path, single), path, None, None
    if spec.questions is None:
        return [], None, None, None
    return load_public_questions(spec.questions), None, None, None


class _Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _evidence_texts(
    root: Path, spec, questions: list[PublicQuestion], given: Path | None, extra: dict | None, conditions
) -> tuple[list[str], dict]:
    """evidence used run의 위치별 글(문항 전체)과 run.json evidence에 넣을 출처 칸. 쓸 수 없으면 _Refused."""
    if spec.kind == "bird-minidev" and given is None:
        if spec.hints is None or not Path(spec.hints).is_file():
            raise _Refused(EXIT_FAIL, f"{spec.name}: 공개 evidence 파일이 없습니다({NEEDS_GETBIRD})")
        texts = H.load_public_hints(Path(spec.hints), questions)
        return texts, {
            "source": "official",
            "source_path": _rel(root, Path(spec.hints)),
            "source_sha256": R.sha256_file(Path(spec.hints)),
        }
    if conditions is not None and given is not None and extra is not None:
        texts = [H.join_conditions(c) for c in conditions]
        return texts, {
            "source": "task_conditions",
            "source_path": _rel(root, given),
            "source_sha256": extra["source_sha256"],
            "join": H.JOIN,
        }
    raise _Refused(
        EXIT_USAGE,
        "이 문항에는 evidence가 없습니다(evidence used는 BIRD Mini-Dev 공개 문항과 스터디 모델 입력 파일에서만 쓴다)",
    )


@dataclass
class _Prepared:
    """run을 만들기 전에 마친 확인의 결과(cmd_run과 cmd_route_start가 함께 쓴다)."""

    name: str
    spec: DatasetSpec
    questions: list[PublicQuestion]
    given: Path | None
    extra: dict | None
    total: int
    count: int
    used: bool
    hint_texts: list[str] | None
    evidence_source: dict
    llm: select.ResolvedLlm
    db_ids: list[str]
    decisions: list[RT.RouteDecision]


def _decide(args, config: Config, name: str, llm: select.ResolvedLlm, forced: str | None, used: bool):
    """경로를 정하고(pipeline/route.py), 근거의 evidence 방식이 이번과 다르면 경고를 더한다(이 비교만 여기서 한다)."""
    base = PipelineSettings.from_mapping(config.pipeline)
    decision = RT.resolve_route(
        profile_name=getattr(args, "profile", None),
        profiles=config.profiles,
        base=base,
        llm_backend=llm.backend,
        llm_model=llm.model,
        llm_effort=llm.reasoning_effort,
        llm_model_source=llm.model_source,
        llm_effort_source=llm.effort_source,
        model_arg=getattr(args, "model", None),
        forced=forced,
        dataset=name,
        cli_version=batch.cli_version,
    )
    mode = EVIDENCE_USED if used else EVIDENCE_NOT_USED
    then = (decision.basis or {}).get("evidence")
    if decision.source in ("profile", RT.MATCH_SOURCE) and then != mode:
        decision.warnings.append(f"근거 evidence 조건이 다릅니다({then or '기록 없음'} → {mode}). 잠정 경로입니다")
    return decision


def _prepare_run(args, root: Path, state, config: Config, forced: tuple[str | None, ...]) -> _Prepared | int:
    """데이터셋·문항·evidence·모델·경로·자산·실행 파일·도는 run을 확인한다. 막히면 종료 코드를 돌려준다."""
    name, _ = current_dataset(config, state)
    if not name or name not in config.datasets:
        err("데이터셋을 정하지 않았습니다(/connect)")
        return EXIT_USAGE
    spec = config.datasets[name]
    if spec.missing:
        hint = "먼저 /getbird" if spec.kind == "bird-minidev" else "등록한 경로를 확인"
        err(f"{name}: {spec.missing} ({hint})")
        return EXIT_FAIL

    used = getattr(args, "evidence", EVIDENCE_NOT_USED) == EVIDENCE_USED
    if used and spec.kind == "bird-minidev" and args.questions:
        err("evidence used는 BIRD 공개 문항 전체(--questions 없이)에서만 쓴다")
        return EXIT_USAGE
    questions, given, extra, conditions = _questions(args, spec, root, keep_conditions=used)
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
    hint_texts: list[str] | None = None
    evidence_source: dict = {}
    if used:
        try:
            all_texts, evidence_source = _evidence_texts(root, spec, questions, given, extra, conditions)
        except _Refused as e:
            err(e.message)
            return e.code
        hint_texts = all_texts[:count]

    try:
        llm = select.select_from_args(
            args,
            config.profiles,
            config.default_profile,
            env=os.environ,
            cwd=Path(os.environ.get("T2S_CALLER_CWD") or root),  # 설치본: 세션 기록은 처음 불린 폴더 이름
            home=Path.home(),
        )
    except select.ModelUnknown as e:
        err(str(e))
        return EXIT_USAGE
    out(select.first_line(llm))
    try:
        decisions = [_decide(args, config, name, llm, f, used) for f in forced]
    except ValueError as e:
        err(f"오류: {e}")
        return EXIT_USAGE
    for d in decisions:
        for line in RT.shown_route_lines(d):
            out(line)
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
    return _Prepared(
        name, spec, questions, given, extra, total, count, used, hint_texts, evidence_source, llm, db_ids, decisions
    )


def _probe(prep: _Prepared, args, *, single: bool) -> int:
    if prep.llm.backend != "fake" and not args.no_probe:
        outcome = select.backend_for(prep.llm, single=single).call(
            "Reply with OK.", None, name="generate_sql", attempt=0
        )
        if outcome.failure is not None:
            err(f"모델 호출 확인 실패: {outcome.failure.type}: {outcome.failure.message[:300]}")
            if hint := sandbox_hint(outcome.failure.message):
                err(hint)
            return EXIT_FAIL
    return EXIT_OK


def _outgoing(prep: _Prepared, routes: list[str]) -> None:
    if prep.llm.backend == "fake":
        out("밖으로 나가는 것 없음(가짜 백엔드)")
        return
    base = OUTGOING_SINGLE if all(r == RT.SINGLE for r in routes) else OUTGOING
    outgoing = f"{base}, {OUTGOING_EVIDENCE}" if prep.used else base
    out(f"밖으로 나감({select.provider_name(prep.llm.backend)}): {outgoing}")


def _new_run_dir(config: Config, run_id: str) -> Path | None:
    run = R.run_dir(config.runs_dir, run_id)
    if run.exists() or run_id == R.ASK_DIR:
        err(f"이미 있는 run 이름입니다: {run_id}")
        return None
    return run


def _create_run(
    root: Path, config: Config, args, prep: _Prepared, run: Path, decision: RT.RouteDecision, pair: dict | None
) -> str:
    """run 폴더·run.json·control.json을 만든다. evidence 방식을 돌려준다."""
    run_id = run.name
    run.mkdir(parents=True)
    if prep.given is not None:
        qpath = run / R.QUESTIONS_FILE
        R.write_json_atomic(qpath, to_public_json(prep.questions))
    else:
        qpath = Path(prep.spec.questions)
    evidence: dict = {"mode": EVIDENCE_NOT_USED}
    if prep.hint_texts is not None:
        hpath = run / R.HINTS_FILE
        R.write_json_atomic(hpath, prep.hint_texts)
        src = prep.evidence_source
        evidence = {
            "mode": EVIDENCE_USED,
            "source": src["source"],
            "path": _rel(root, hpath),
            "sha256": R.sha256_file(hpath),
            "source_path": src["source_path"],
            "source_sha256": src["source_sha256"],
            "count": len(prep.hint_texts),
            "rules": H.HINT_RULES_VERSION,
            **({"join": src["join"]} if "join" in src else {}),
        }
    settings = decision.settings or PipelineSettings.from_mapping(config.pipeline)
    route = {**decision.to_json(), "pair": pair}
    git = batch.git_info(root)
    run_meta = {
        "format": R.RUN_FORMAT,
        "run_id": run_id,
        "kind": "generate",
        "dataset": prep.name,
        "dataset_kind": prep.spec.kind,
        "archive_sha256": prep.spec.archive_sha256,
        "questions": {
            "path": _rel(root, qpath),
            "sha256": R.sha256_file(qpath),
            "total": prep.total,
            "given": None if prep.given is None else str(prep.given),
            **(prep.extra or {}),
        },
        "count": prep.count,
        "limit": args.limit,
        "sample": prep.count != prep.total,
        "evidence": evidence,
        "workers": args.workers,
        "llm": prep.llm.to_json(),
        "route": route,
        "pipeline": {**asdict(settings), "refine_template_sha256": refine.template_sha256()},
        "git": git,
        **({"engine": rec} if (rec := engine.engine_record()) is not None else {}),
        "versions_start": batch.versions(),
        "databases": batch.fingerprints(prep.spec, prep.db_ids),
        "score_after": not args.no_score,
        "started_at": batch.now(),
        "command": list(sys.argv),
    }
    R.write_json_atomic(run / R.RUN_FILE, run_meta)
    R.write_json_atomic(
        run / R.CONTROL_FILE,
        {"run_id": run_id, "pid": None, "started_at": run_meta["started_at"], "state": "starting", "sessions": []},
    )
    # 색인 한 줄(run을 시작하는 곳. resume·분리 프로세스는 더하지 않는다)
    session_index.append(
        config.runs_dir, session_index.entry(run_id, names.SET, prep.name, run_meta["started_at"], os.environ)
    )
    return evidence["mode"]


def cmd_run(args, root: Path, state, config: Config) -> int:
    if args.worker:
        return work(root, config, args.run_id)
    prep = _prepare_run(args, root, state, config, (getattr(args, "route", None),))
    if isinstance(prep, int):
        return prep
    [decision] = prep.decisions
    run_id = args.run_id or names.new_name(prep.name, names.SET)
    run = _new_run_dir(config, run_id)
    if run is None:
        return EXIT_USAGE
    code = _probe(prep, args, single=decision.applied == RT.SINGLE)
    if code != EXIT_OK:
        return code
    _outgoing(prep, [decision.applied])
    mode = _create_run(root, config, args, prep, run, decision, None)
    what = f"{prep.name} {prep.count}문항 · 동시 {args.workers} · evidence {mode}"
    if decision.applied != RT.HARNESS:
        what += f" · 경로 {decision.applied}"
    return _launch(root, config, run, run_id, args.foreground, what)


# ── route-start(하네스·단일 호출 두 run을 함께) ──────────


def _dirty_full_measurement(backend: str, count: int, total: int, git: dict) -> bool:
    """가짜 백엔드가 아닌 전체 측정을 미커밋 코드(또는 커밋 기록 없음)에서 시작하려 하는가(AGENTS 규칙 5)."""
    return backend != "fake" and count == total and git.get("dirty") is not False


def cmd_route_start(args, root: Path, state, config: Config) -> int:
    """같은 확인을 한 번 하고 run 둘(하네스, 단일 호출)을 만들어 분리 프로세스 둘로 함께 띄운다."""
    roles = (RT.HARNESS, RT.SINGLE)
    prep = _prepare_run(args, root, state, config, roles)
    if isinstance(prep, int):
        return prep
    pair_id = names.new_name(prep.name, names.SET)
    ids = {role: names.pair_names(pair_id)[role] for role in roles}
    runs = {role: _new_run_dir(config, rid) for role, rid in ids.items()}
    if any(r is None for r in runs.values()):
        return EXIT_USAGE
    if _dirty_full_measurement(prep.llm.backend, prep.count, prep.total, batch.git_info(root)):
        err("미커밋 코드 상태에서는 전체 측정을 시작하지 않습니다: 커밋한 뒤 돌리거나 --limit N으로 소표본을 씁니다")
        return EXIT_FAIL
    code = _probe(prep, args, single=False)
    if code != EXIT_OK:
        return code
    _outgoing(prep, list(roles))
    for role, decision in zip(roles, prep.decisions, strict=True):
        other = ids[roles[1] if role == roles[0] else roles[0]]
        _create_run(root, config, args, prep, runs[role], decision, {"id": pair_id, "role": role, "other": other})
    mode = EVIDENCE_USED if prep.used else EVIDENCE_NOT_USED
    code = EXIT_OK
    for role in roles:
        what = f"{prep.name} {prep.count}문항 · 동시 {args.workers} · evidence {mode} · 경로 {role}"
        code = max(code, _launch(root, config, runs[role], ids[role], False, what, quiet=True))
    out(f"짝 {pair_id} · 진행: t2s route-status · 판정: t2s route-judge (두 run을 함께 돌리므로 시간은 함께 잰 값)")
    return code


def _launch(
    root: Path, config: Config, run: Path, run_id: str, foreground: bool, what: str, quiet: bool = False
) -> int:
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
    if not quiet:
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
    try:
        hint_texts = _run_hints(run, meta)
    except _Refused as e:
        err(e.message)
        R.update_control(run, state="failed")
        return EXIT_FAIL
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
        hints=hint_texts,
        route=R.route_of(meta),
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
        _wait_for_partner(config, run, meta)
        code = max(code, _score_and_report(root, run, run_id))
    R.update_control(run, state="finished", phase="done")
    return code


BACKEND_FACTORY = None  # 테스트가 바꿔치기한다(문항마다 부를 백엔드를 만드는 함수)


def _partner_busy(control: dict) -> bool:
    alive = R.pid_alive(control.get("pid")) or R.pid_alive(control.get("worker_pid"))
    return control.get("state") in ("running", "starting") and alive


def _wait_for_partner(config: Config, run: Path, meta: dict) -> None:
    """짝 run의 채점 차례. 하네스 run은 짝 run이 끝날 때까지, 다른 쪽은 하네스 run의 생성이 끝날 때까지 기다린다."""
    pair = R.pair_of(meta)
    if pair is None:
        return
    other = R.run_dir(config.runs_dir, pair["other"])
    R.update_control(run, phase="waiting")
    while True:
        c = R.read_json_if(other / R.CONTROL_FILE) or {}
        if not _partner_busy(c):
            return
        phase = c.get("phase", "generate")
        if pair["role"] == RT.HARNESS and phase == "done":
            return
        if pair["role"] != RT.HARNESS and phase != "generate":
            return
        time.sleep(PAIR_POLL_SECONDS)


def evidence_mode(meta: dict) -> str | None:
    """run.json의 evidence 방식. 칸이 없는 옛 run은 not_used, 재채점 run은 None(미확인)."""
    if meta.get("kind") == "rescore":
        return None
    return (meta.get("evidence") or {}).get("mode", EVIDENCE_NOT_USED)


def evidence_word(meta: dict) -> str:
    """status·sessions 줄의 evidence 칸: "evidence used|not_used", 재채점 run은 "evidence 미확인"."""
    mode = evidence_mode(meta)
    return "evidence 미확인" if mode is None else f"evidence {mode}"


def _run_hints(run: Path, meta: dict) -> list[str] | None:
    """evidence used run이면 run 폴더의 hints.json을 run.json의 sha256과 대조해 읽는다. not_used면 None."""
    if evidence_mode(meta) != EVIDENCE_USED:
        return None
    ev = meta["evidence"]
    hpath = run / R.HINTS_FILE
    if not hpath.is_file() or R.sha256_file(hpath) != ev.get("sha256"):
        raise _Refused(EXIT_FAIL, "run의 evidence 파일(hints.json)이 run.json의 sha256과 다릅니다")
    texts = R.read_json(hpath)
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts) or len(texts) != meta["count"]:
        raise _Refused(EXIT_FAIL, "run의 evidence 파일(hints.json)의 꼴이나 수가 run.json과 다릅니다")
    return texts


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
    found = R.find_run(config.runs_dir, rid, config.legacy_runs_dirs)  # 세션 폴더에 없으면 옛 run 폴더에서(읽기만)
    run, legacy = found if found else (R.run_dir(config.runs_dir, rid), False)
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
    ev_word = evidence_word(meta)
    out(
        f"{rid} · {meta.get('dataset')} · {ev_word} · {word}"
        + (f" · 오래 걸린 문항 {slow}" if slow else "")
        + (" · 옛 run(읽기만)" if legacy else "")
    )
    if meta.get("kind") == "rescore":
        out("재채점 run(생성 없음)")
    else:
        out(select.first_line(select.ResolvedLlm.from_json(meta["llm"])))
    route_line = route_status_line(meta)
    if route_line:
        out(route_line)
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
        if hint := sandbox_hint(e.get("message")):
            out(hint)

    def yn(name: str) -> str:
        return "예" if (run / name).is_file() else "아니오"

    out(f"생성 끝 {yn(R.MANIFEST_FILE)} · 채점 끝 {yn(R.SCORE_FILE)} · 보고 끝 {yn(R.REPORT_FILE)}")
    log = run / R.LOG_FILE
    if log.is_file():
        keep = 1 if route_line else 2  # 10줄 안
        tail = [x for x in log.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()][-keep:]
        for x in tail:
            out(f"로그: {x[:200]}")
    return EXIT_OK


def route_status_line(meta: dict) -> str | None:
    """경로가 harness가 아니거나 경고가 있을 때만 한 줄."""
    applied = R.route_of(meta)
    warnings = R.route_warnings(meta)
    if applied == RT.HARNESS and not warnings:
        return None
    line = f"경로 {RT.route_name(applied, (meta.get('route') or {}).get('overrides'))}"
    if warnings:
        line += f" · 주의 {len(warnings)}건: {warnings[0]}"
    return line


# ── route-status ─────────────────────────────────────────


def cmd_route_status(args, root: Path, state, config: Config) -> int:
    pairs = R.list_pairs(config.runs_dir)
    pair = args.pair or R.latest_pair(config.runs_dir)
    if pair is None:
        out("시작한 짝(route-start)이 없습니다")
        return EXIT_OK
    if pair not in pairs:
        err(f"짝이 없습니다: {pair}")
        return EXIT_FAIL
    runs = pairs[pair]["runs"]
    metas = {role: R.read_json(R.run_dir(config.runs_dir, rid) / R.RUN_FILE) for role, rid in runs.items()}
    first = next(iter(metas.values()))
    out(f"짝 {pair} · {first.get('dataset')} · evidence {evidence_mode(first)} · {first.get('count')}문항")
    out(select.first_line(select.ResolvedLlm.from_json(first["llm"])))
    scored = 0
    for role in sorted(runs, key=lambda r: (r != RT.HARNESS, r)):
        rid = runs[role]
        run = R.run_dir(config.runs_dir, rid)
        items = R.read_item_records(run)
        counts = {
            s: sum(1 for r in items.values() if r.get("status") == s) for s in ("generated", "no_sql", "exception")
        }
        done = (run / R.SCORE_FILE).is_file()
        scored += done
        out(f"{role} {rid} · {state_word(run)} · 진행 {len(items)}/{metas[role].get('count')}")
        out(
            "  상태 "
            + " · ".join(f"{k} {v}" for k, v in counts.items())
            + f" · 생성 끝 {'예' if (run / R.MANIFEST_FILE).is_file() else '아니오'}"
            + f" · 채점 끝 {'예' if done else '아니오'}"
        )
    if len(runs) != 2:
        out(f"짝 run이 {len(runs)}개입니다(2개여야 판정한다)")
    elif scored == 2:
        out("두 run 모두 채점 끝: t2s route-judge")
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

    def refuse(why: str) -> int:
        err(f"이어 돌 수 없습니다: {why}")
        return EXIT_FAIL

    if meta is None:
        if (R.find_run(config.runs_dir, rid, config.legacy_runs_dirs) or (None, False))[1]:
            return refuse("옛 run 폴더(legacy_runs)의 run은 읽기만 합니다")
        err(f"run이 없습니다: {rid}")
        return EXIT_FAIL
    control = R.read_json_if(run / R.CONTROL_FILE) or {}

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
    try:
        _run_hints(run, meta)  # evidence 방식은 run.json 그대로 이어 간다
    except _Refused:
        return refuse("evidence 파일(hints.json)이 run.json과 다릅니다")
    if batch.fingerprints(spec, list(meta.get("databases", {}))) != meta.get("databases"):
        return refuse("DB 지문이 다릅니다")
    pair = R.pair_of(meta)
    busy = _running_run(config, skip=(pair["other"],) if pair else ())
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
    what = f"이어 돌기 {len(retry)}문항 · evidence {evidence_mode(meta)}"
    return _launch(root, config, run, rid, args.foreground, what)


def select_retryable() -> tuple[str, ...]:
    from t2s.llm.base import RETRYABLE_FAILURES

    return RETRYABLE_FAILURES
