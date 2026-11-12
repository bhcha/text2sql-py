"""명령 datasets, use, register, prepare. 첫머리에서 읽기 감시를 건다."""

from __future__ import annotations

import os
import platform
import re
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from t2s.dataset.config import Config, ConfigError, DatasetSpec, find_root, load_config
from t2s.dataset.state import State, StateError, current_dataset, load_state, save_state
from t2s.datasource.base import DataSourceError
from t2s.datasource.factory import open_source
from t2s.prep import assets, embed
from t2s.runner.guard import install_guard

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NEEDS_APPROVAL = 0, 1, 2, 3
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
NOTICE = (
    (
        "이 DB의 CREATE 원문, 예시 3행, 질문과 비슷한 값, 열 프로파일(범주형 열은 값 전부), 실행 오류 글이 "
        "모델 제공자 서버(OpenAI 또는 Anthropic)로 나갑니다."
    ),
    "다른 프로그램이 이 DB에 쓰고 있지 않아야 합니다.",
    "민감한 DB에는 쓰지 마세요.",
)


def out(line: str = "") -> None:
    print(line, flush=True)


def err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


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
    handler = {"datasets": cmd_datasets, "use": cmd_use, "register": cmd_register, "prepare": cmd_prepare}[args.command]
    try:
        return handler(args, config, state)
    except (DataSourceError, assets.PrepFailed, ConfigError, StateError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL


# ── 준비 상태 ────────────────────────────────────────────


def _model_hashes(config: Config) -> dict[str, str]:
    return embed.model_file_hashes(config.embedding_model_dir)


def prep_status(config: Config, spec: DatasetSpec, model_hashes: dict[str, str]) -> str:
    if spec.missing:
        return "자료 없음"
    states = []
    for sname, s in spec.sources.items():
        src = open_source(s.kind, path=str(s.path))
        states.append(assets.asset_state(src, assets.asset_dir(config.data_dir, spec.name, sname), model_hashes))
    n = len(states)
    ok = sum(1 for s in states if s.state == "ok")
    stale = [s for s in states if s.state == "stale"]
    if stale:
        reasons = sorted({r for s in stale for r in s.reasons})
        return f"낡음 {len(stale)}/{n}({', '.join(reasons)})"
    if ok == 0:
        return "준비 안 됨"
    return f"준비됨 {ok}/{n}"


def _model_line(config: Config, hashes: dict[str, str]) -> str:
    good = sum(1 for f in embed.MODEL_FILES if hashes[f] == embed.MODEL_SHA256[f])
    if good == len(embed.MODEL_FILES):
        return f"임베딩 모델: 있음(sha256 {good}/{len(embed.MODEL_FILES)} 일치)"
    have = sum(1 for f in embed.MODEL_FILES if hashes[f])
    if have == 0:
        return "임베딩 모델: 없음(t2s fetch model)"
    return f"임베딩 모델: 다름(sha256 {good}/{len(embed.MODEL_FILES)} 일치, t2s fetch model)"


def cmd_datasets(args, config: Config, state: State) -> int:
    name, origin = current_dataset(config, state)
    out(f"지금 바라보는 데이터셋: {name or '없음'} (출처 {origin})")
    out(f"Python {platform.python_version()} · SQLite {sqlite3.sqlite_version}")
    hashes = _model_hashes(config)
    for spec in config.datasets.values():
        mark = "*" if spec.name == name else " "
        status = prep_status(config, spec, hashes)
        out(f"{mark} {spec.name}  {spec.kind}  소스 {len(spec.sources)}  {status}  ({spec.origin})")
    if not config.datasets:
        out("  (데이터셋 없음)")
    out(_model_line(config, hashes))
    return EXIT_OK


def cmd_use(args, config: Config, state: State) -> int:
    if args.name not in config.datasets:
        err(f"없는 데이터셋: {args.name}")
        for n in config.datasets:
            err(f"  {n}")
        return EXIT_FAIL
    state.current_dataset = args.name
    save_state(config.root, state)
    spec = config.datasets[args.name]
    out(f"지금 바라보는 데이터셋: {args.name} ({prep_status(config, spec, _model_hashes(config))})")
    return EXIT_OK


# ── 등록 ─────────────────────────────────────────────────


def _toml_snippet(name: str, path: Path) -> list[str]:
    return [
        f"[datasets.{name}]",
        'kind = "single"',
        f"[datasets.{name}.sources.{name}]",
        'kind = "sqlite"',
        f'path = "{path}"',
    ]


def cmd_register(args, config: Config, state: State) -> int:
    if args.remove:
        return _remove(args.name, config, state)
    if not args.sqlite:
        err("쓰임: t2s register <이름> --sqlite <경로> [--yes]")
        return EXIT_USAGE
    name = args.name
    if not NAME_RE.match(name):
        err(f"이름은 소문자로 시작하고 소문자·숫자·밑줄·하이픈만 쓴다(최대 41자): {name}")
        return EXIT_USAGE
    if name in config.datasets:
        err(f"이미 있는 데이터셋 이름: {name} ({config.datasets[name].origin})")
        return EXIT_FAIL
    path = Path(os.path.realpath(os.path.expanduser(args.sqlite)))
    if not path.is_file():
        err(f"파일이 없습니다: {path}")
        return EXIT_FAIL
    src = open_source("sqlite", path=str(path))
    try:
        tables = src.tables()
    except (DataSourceError, sqlite3.Error) as e:
        err(f"SQLite로 읽기 전용으로 열지 못했습니다: {path} ({e})")
        return EXIT_FAIL
    if not tables:
        err(f"테이블이 없습니다: {path}")
        return EXIT_FAIL
    out(f"등록할 DB: {path} (테이블 {len(tables)}개)")
    for line in NOTICE:
        out(line)
    side = src.side_files()
    if side:
        out(f"주의: 부속 파일 {', '.join(sorted(side))}가 있습니다. 다른 프로그램이 쓰는 중일 수 있습니다.")
    if not args.yes:
        out(f"승인하면 --yes를 붙여 다시 실행: t2s register {name} --sqlite {path} --yes")
        return EXIT_NEEDS_APPROVAL
    fp = src.fingerprint()
    state.registered[name] = {
        "kind": "single",
        "sources": {name: {"kind": "sqlite", "path": str(path)}},
        "registered_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "fingerprint": {"sha256": fp.sha256, "size": fp.size},
    }
    save_state(config.root, state)
    out(f"등록함: {name} (var/state.json, sha256 {fp.sha256[:8]})")
    out("harness.toml에 영구히 두려면 아래 조각을 넣고 등록은 지운다(t2s register --remove):")
    for line in _toml_snippet(name, path):
        out("  " + line)
    out(f"다음: t2s prepare {name}")
    return EXIT_OK


def _remove(name: str, config: Config, state: State) -> int:
    if name not in state.registered:
        if name in config.datasets:
            err(f"{name}은 harness.toml의 데이터셋이라 지우지 않습니다")
        else:
            err(f"등록 기록이 없습니다: {name}")
        return EXIT_FAIL
    del state.registered[name]
    if state.current_dataset == name:
        state.current_dataset = None
    save_state(config.root, state)
    shutil.rmtree(Path(config.data_dir) / "assets" / name, ignore_errors=True)
    out(f"지움: {name} (등록 기록과 자산)")
    return EXIT_OK


# ── 준비 ─────────────────────────────────────────────────


def cmd_prepare(args, config: Config, state: State) -> int:
    name = args.dataset or current_dataset(config, state)[0]
    if not name:
        err("준비할 데이터셋을 정하지 않았습니다(t2s prepare <데이터셋> 또는 t2s use)")
        return EXIT_USAGE
    spec = config.datasets.get(name)
    if spec is None:
        err(f"없는 데이터셋: {name}")
        return EXIT_FAIL
    if spec.missing:
        hint = "먼저 /getbird" if spec.kind == "bird-minidev" else "등록한 경로를 확인"
        err(f"{name}: {spec.missing} ({hint})")
        return EXIT_FAIL
    try:
        model_hashes = embed.check_model_files(config.embedding_model_dir)
    except embed.EmbeddingModelMissing as e:
        err(f"{e}")
        err("먼저 /getbird 또는 t2s fetch model")
        return EXIT_FAIL
    chosen = args.source or list(spec.sources)
    unknown = [s for s in chosen if s not in spec.sources]
    if unknown:
        err(f"{name}에 없는 소스: {', '.join(unknown)}")
        return EXIT_FAIL
    started = time.monotonic()
    built = skipped = 0
    values = columns = 0
    for sname in chosen:
        s = spec.sources[sname]
        src = open_source(s.kind, path=str(s.path))
        out_dir = assets.asset_dir(config.data_dir, name, sname)
        st = assets.asset_state(src, out_dir, model_hashes)
        if st.state == "ok" and not args.force:
            m = assets.read_manifest(out_dir) or {}
            c = m.get("counts", {})
            values += c.get("values", 0)
            columns += c.get("columns", 0)
            skipped += 1
            out(f"{sname}: 건너뜀(준비됨)")
            continue
        t0 = time.monotonic()
        m = assets.build_assets(src, out_dir, dataset=name, source_name=sname, model_hashes=model_hashes)
        c = m["counts"]
        values += c["values"]
        columns += c["columns"]
        built += 1
        why = "" if st.state == "missing" else f", 다시 만듦: {', '.join(st.reasons) or '--force'}"
        out(
            f"{sname}: 값 {c['values']}, 값 열 {c['value_columns']}, 프로파일 열 {c['columns']}, {time.monotonic() - t0:.1f}초{why}"
        )
    out(
        f"합계: 소스 {len(chosen)}(만듦 {built}, 건너뜀 {skipped}), 값 {values}, 프로파일 열 {columns}, "
        f"{time.monotonic() - started:.1f}초"
    )
    return EXIT_OK
