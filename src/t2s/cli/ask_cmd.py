"""명령 ask: 지금 바라보는 데이터셋에 질문 하나 → SQL → 읽기 전용 실행 → 표시. 첫머리에서 읽기 감시를 건다.

TS 하네스(text2sql-harness) harness/ask.ts의 출력 줄을 옮겼다. 데이터셋을 고르는 인자는 두지 않는다(t2s use).
종료 코드: 0 성공, 1 실패(예외, DB 지문 다름), 2 쓰임새(소스·모델을 정해야 함), 5 준비 필요.
경로는 run과 같은 규칙(pipeline/route.py)을 따르고 ask.json에 `route` 칸을 남긴다. single이면 단일 호출 하나만 부른다.
--semantic used(사용자가 말했을 때만)면 읽기 감시를 allow_semantic=True로 걸고, 고정 기록대로 받아들인 지표 정의를
한 줄 글로 만들어 hint 인자로 넘긴다. 확인은 모두 모델을 부르기 전에 한다(BIRD·정의 없음 2, 파일·DB 바뀜 1).
not_used(기본)면 hint를 넘기지 않고 화면 글과 프롬프트가 그 전과 같다. ask.json에는 semantic 칸 하나만 더해진다.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from t2s import engine
from t2s.cli.data_cmds import _semantic_spec, err, out, sandbox_hint
from t2s.dataset import semantic as SEM
from t2s.dataset.config import ConfigError, find_root, load_config
from t2s.dataset.state import StateError, current_dataset, load_state
from t2s.datasource.base import DataSourceError
from t2s.datasource.factory import open_source
from t2s.llm import select
from t2s.llm.base import usage_totals
from t2s.pipeline import route as RT
from t2s.pipeline.run import PipelineSettings, answer
from t2s.pipeline.single import answer_single
from t2s.prep import assets, embed
from t2s.runner import batch
from t2s.runner.guard import install_guard
from t2s.session import index as session_index
from t2s.session import names as session_names

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NEEDS_PREP = 0, 1, 2, 5
OUTGOING = "질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글"
OUTGOING_SINGLE = "질문, CREATE 원문"
OUTGOING_SEMANTIC = ", 지표 정의 글"
SQL_MAX_LINES = 12
SHOW_ROWS = 10
CELL_MAX = 60
LINE_MAX = 200


def outgoing_line(backend: str, route: str = RT.HARNESS, semantic: bool = False) -> str:
    if backend == "fake":
        return "밖으로 나가는 것 없음(가짜 백엔드)"
    what = OUTGOING_SINGLE if route == RT.SINGLE else OUTGOING
    return f"밖으로 나감({select.provider_name(backend)}): {what}{OUTGOING_SEMANTIC if semantic else ''}"


def cell(value) -> str:
    text = "NULL" if value is None else str(value)
    return text if len(text) <= CELL_MAX else text[: CELL_MAX - 1] + "…"


def row_line(row) -> str:
    text = "  " + " | ".join(cell(v) for v in row)
    return text if len(text) <= LINE_MAX else text[: LINE_MAX - 1] + "…"


def exec_line(result) -> str:
    if result.status in ("rows", "empty"):
        return f"실행: {result.status} {result.row_count}행 · 열 {', '.join(result.columns)}"
    return f"실행: {result.status} {result.error[:200]}"


def git_head(root: Path) -> str | None:
    """기록할 커밋. run.json과 같은 함수(batch.git_info)를 쓴다. 엔진 표식이 있으면(플러그인 설치본) 표식의 커밋."""
    return batch.git_info(root)["head"]


def _git_fields(root: Path) -> dict:
    """ask.json의 git_head·dirty(run.json의 git.head·git.dirty와 같은 값). dirty가 참이면 미커밋 코드다."""
    info = batch.git_info(root)
    return {"git_head": info["head"], "dirty": info["dirty"]}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stage(trace, sid: str) -> dict:
    return trace.stages.get(sid) or {}


def stage_line(trace) -> str:
    kw = _stage(trace, "extract_keywords").get("keywords") or []
    values = _stage(trace, "attach_values").get("values", 0)
    tables = _stage(trace, "attach_profile").get("tables", 0)
    fixes = _stage(trace, "refine").get("calls", 0)
    stop = _stage(trace, "execute").get("stop") or "없음"
    return f"단계: 키워드 {len(kw)}개 · 붙은 값 {values}개 · 프로파일 테이블 {tables}개 · 고치기 {fixes}번 · 멈춘 까닭 {stop}"


def reported_model(trace, calls_dir: Path) -> str:
    gen = next((c for c in trace.calls if c.name in ("generate_sql", "single_sql")), None)
    if gen is None:
        return "기록 없음"
    try:
        meta = json.loads((calls_dir / str(gen.k) / "llm-meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "기록 없음"
    return meta.get("model_reported") or "기록 없음"


def calls_line(trace, calls_dir: Path) -> str:
    totals = [usage_totals(c.usage) for c in trace.calls]
    if not totals or any(t is None for t in totals):
        tokens = "토큰 기록 없음"
    else:
        tokens = f"입력 {sum(t[0] for t in totals):,} · 출력 {sum(t[1] for t in totals):,}"
    seconds = trace.timing.get("total_ms", 0) / 1000
    tools = sum(c.tool_calls for c in trace.calls)
    return (
        f"호출 {len(trace.calls)}번 · {seconds:.1f}초 · {tokens} · 도구 호출 {tools} · "
        f"응답 모델 {reported_model(trace, calls_dir)}"
    )


def run(args) -> int:
    try:
        root = find_root()
    except ConfigError as e:
        err(f"오류: {e}")
        return EXIT_FAIL
    used = args.semantic == "used"
    if used:
        install_guard(root, allow_semantic=True)
    else:
        install_guard(root)
    try:
        state = load_state(root)
        config = load_config(root, state)
    except (ConfigError, StateError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL

    name, _ = current_dataset(config, state)
    if not name or name not in config.datasets:
        err("데이터셋을 정하지 않았습니다(/connect)")
        return EXIT_USAGE
    spec = config.datasets[name]
    if used:  # BIRD·소스 여럿은 2(자료가 있든 없든). 모델을 고르거나 부르기 전에 모두 확인한다
        ok_spec, code = _semantic_spec(name, config)
        if ok_spec is None:
            return code
    if spec.missing:
        hint = "먼저 /getbird" if spec.kind == "bird-minidev" else "등록한 경로를 확인"
        err(f"{name}: {spec.missing} ({hint})")
        return EXIT_FAIL
    names = list(spec.sources)
    if args.source:
        if args.source not in spec.sources:
            err(f"{name}에 없는 소스입니다: {args.source}")
            err("소스: " + ", ".join(names))
            return EXIT_USAGE
        sname = args.source
    elif len(names) == 1:
        sname = names[0]
    else:
        err(f"소스를 정해야 합니다({name}의 소스 {len(names)}개)")
        err("소스: " + ", ".join(names))
        return EXIT_USAGE

    semantic_text = semantic_rec = None
    if used:
        semantic_rec = state.semantic.get(name)
        if semantic_rec is None:
            err("받아들인 지표 정의가 없습니다. 먼저 /getmetrics")
            return EXIT_USAGE
        s0 = spec.sources[sname]
        try:
            semantic_text = SEM.load_pinned(root, name, semantic_rec, open_source(s0.kind, path=str(s0.path)))
        except SEM.SemanticError as e:
            err(f"오류: {e}")
            return EXIT_FAIL

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
        decision = RT.resolve_route(
            profile_name=args.profile,
            profiles=config.profiles,
            base=PipelineSettings.from_mapping(config.pipeline),
            llm_backend=llm.backend,
            llm_model=llm.model,
            llm_effort=llm.reasoning_effort,
            llm_model_source=llm.model_source,
            llm_effort_source=llm.effort_source,
            model_arg=args.model,
            forced=None,
            dataset=name,
            cli_version=batch.cli_version,
        )
    except ValueError as e:
        err(f"오류: {e}")
        return EXIT_USAGE
    for line in RT.shown_route_lines(decision):
        out(line)

    s = spec.sources[sname]
    source = open_source(s.kind, path=str(s.path))
    try:
        model_hashes = embed.check_model_files(config.embedding_model_dir)
        loaded = assets.load_assets(source, assets.asset_dir(config.data_dir, name, sname), model_hashes)
    except (embed.EmbeddingModelMissing, assets.AssetsStale) as e:
        err(str(e))
        err(f"준비 필요: t2s prepare {name}")
        return EXIT_NEEDS_PREP

    out(f"데이터셋 {name} · 소스 {sname} · {source.describe()}")
    out(outgoing_line(llm.backend, decision.applied, semantic=used))
    if used:
        out(SEM.summary_line(semantic_rec))
    side_before = source.side_files()
    if side_before:
        out(
            f"주의: 부속 파일 {', '.join(sorted(side_before))}가 있습니다. 다른 프로그램이 이 DB에 쓰고 있지 않아야 합니다"
        )
    fp_before = source.fingerprint()

    session = session_names.new_name(name, session_names.ASK)  # 세션 폴더: <기록 자리>/<세션 이름>/
    run_dir = config.runs_dir / session
    calls_dir = run_dir / "calls"
    run_dir.mkdir(parents=True, exist_ok=False)
    started_at = _now()
    settings = decision.settings or PipelineSettings.from_mapping(config.pipeline)
    semantic_field: dict = {"mode": "not_used"}
    hint_kw: dict = {}  # not_used면 hint를 넘기지 않는다(그 전과 같은 호출)
    if used:
        copy_raw = SEM.copy_body(semantic_text, semantic_rec)
        (run_dir / SEM.COPY_NAME).write_bytes(copy_raw)
        copy_rel = os.path.relpath(run_dir / SEM.COPY_NAME, root)
        semantic_field = SEM.used_record(copy_rel, copy_raw, semantic_rec, semantic_text)
        hint_kw = {"hint": semantic_text}
    try:
        if decision.applied == RT.SINGLE:
            trace = answer_single(
                args.question, source, select.backend_for(llm, single=True), calls_dir=calls_dir, **hint_kw
            )
        else:
            embedder = embed.load_embedder(config.embedding_model_dir)
            trace = answer(
                args.question,
                source,
                loaded,
                select.backend_for(llm),
                embedder=embedder,
                settings=settings,
                calls_dir=calls_dir,
                **hint_kw,
            )
    except (embed.EmbeddingFailed, embed.EmbeddingModelMissing, DataSourceError, sqlite3.Error) as e:
        err(f"실행 실패: {type(e).__name__}: {e}")
        return EXIT_FAIL

    shown = None
    if trace.sql is not None:
        shown = source.execute(trace.sql, settings.exec_timeout_seconds, SHOW_ROWS)
    fp_after = source.fingerprint()
    side_after = source.side_files()
    same = fp_before == fp_after and side_before == side_after

    code = EXIT_OK
    if trace.exception is not None:
        code = EXIT_FAIL
    if not same:
        code = EXIT_FAIL
    (run_dir / "trace.json").write_text(
        json.dumps(trace.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record = {
        "format": "t2s-ask/1",
        "llm": llm.to_json(),
        "route": decision.to_json(),
        "semantic": semantic_field,
        "dataset": name,
        "source": sname,
        "database": source.describe(),
        "fingerprint_before": {"sha256": fp_before.sha256, "size": fp_before.size},
        "fingerprint_after": {"sha256": fp_after.sha256, "size": fp_after.size},
        "side_files_before": sorted(side_before),
        "side_files_after": sorted(side_after),
        "database_unchanged": same,
        "shown": None
        if shown is None
        else {"status": shown.status, "row_count": shown.row_count, "columns": shown.columns},
        "exit_code": code,
        "finished_at": _now(),
        **_git_fields(root),
        **({"engine": rec} if (rec := engine.engine_record()) is not None else {}),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    (run_dir / "ask.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    session_index.append(
        config.runs_dir, session_index.entry(session, session_names.ASK, name, started_at, os.environ)
    )  # 색인 한 줄(ask 끝)

    if trace.exception is not None:
        ex = trace.exception
        out(f"예외: {ex.get('type')}: {str(ex.get('message', ''))[:300]}")
        if hint := sandbox_hint(ex.get("message")):
            err(hint)
    out("SQL:")
    if trace.sql is None:
        out("(생성 없음)")
    else:
        lines = trace.sql.split("\n")
        for line in lines[:SQL_MAX_LINES]:
            out(line)
        if len(lines) > SQL_MAX_LINES:
            out("…(전체는 실행 폴더의 trace.json)")
    if shown is not None:
        out(exec_line(shown))
        for row in shown.head[:SHOW_ROWS]:
            out(row_line(row))
    out(stage_line(trace))
    out(calls_line(trace, calls_dir))
    out(f"DB 지문: 앞 {fp_before.sha256[:8]} · 뒤 {fp_after.sha256[:8]} · {'같음' if same else '다름'}")
    out(f"실행 폴더: {os.path.relpath(run_dir, root)}")
    if not same:
        err("경고: 실행 전후 DB 지문이 다릅니다")
    return code
