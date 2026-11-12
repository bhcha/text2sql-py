"""일괄 실행: 한 문항 실행, 스레드 풀, 한도·연속 실패 멈춤, 마무리(records.jsonl·predictions.json·manifest.json).

TS benchmarks/benchkit/run.ts(HALT_AFTER = 5, 다시 돌 수 있는 실패, 문항마다 바로 기록)를 옮긴 자체 코드다.
TS와 다른 점: 팩 프로세스 대신 한 프로세스의 스레드로 돌고, 모델 호출 실패가 종류와 상관없이 연달아 20번이면 멈춘다.
정답을 모른다(eval을 import하지 않는다).
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from t2s.dataset.config import Config, DatasetSpec
from t2s.dataset.questions import PublicQuestion
from t2s.datasource.base import DataSource
from t2s.datasource.factory import open_source
from t2s.llm import _proc
from t2s.llm.base import LlmBackend, is_usage_limit, usage_totals
from t2s.llm.select import ResolvedLlm, backend_for
from t2s.pipeline.run import PipelineSettings, answer
from t2s.pipeline.trace import Trace
from t2s.pipeline.value_search import CachedEmbedder
from t2s.prep import assets
from t2s.runner import records as R

HALT_AFTER = 5  # 사용량 한도 연속(TS HALT_AFTER)
HALT_AFTER_FAILURES = 20  # 종류와 상관없는 모델 호출 실패 연속
CALL_FAILURE_SUFFIXES = ("Exit", "Timeout", "UsageLimit")
VERSION_PACKAGES = ("numpy", "onnxruntime", "tokenizers")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── 판·코드 기록 ─────────────────────────────────────────


def git_info(root: Path) -> dict:
    """head, src_tree, dirty. git 저장소가 아니면 셋 다 None."""

    def git(*args: str) -> subprocess.CompletedProcess | None:
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
        "src_tree": tree.stdout.strip() if tree is not None and tree.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status is not None else None,
    }


def _cli_version_fresh(bin: str) -> str | None:
    try:
        r = subprocess.run([bin, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = r.stdout.strip()
    return text.splitlines()[0] if r.returncode == 0 and text else None


def versions() -> dict:
    out: dict = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version}
    for pkg in VERSION_PACKAGES:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            out[pkg] = None
    out["codex"] = _cli_version_fresh(os.environ.get("T2S_CODEX_BIN") or "codex")
    out["claude"] = _cli_version_fresh(os.environ.get("T2S_CLAUDE_BIN") or "claude")
    return out


def fingerprints(spec: DatasetSpec, names: list[str] | None = None) -> dict:
    out = {}
    for sname in names or list(spec.sources):
        s = spec.sources[sname]
        fp = open_source(s.kind, path=str(s.path)).fingerprint()
        out[sname] = {"sha256": fp.sha256, "size": fp.size}
    return out


# ── 문맥 ─────────────────────────────────────────────────


class LockedEmbedder:
    """임베딩 계산을 한 번에 하나씩 한다(onnxruntime·tokenizers를 여러 스레드에서 함께 부르는 것은 확인하지 않았다)."""

    def __init__(self, embedder) -> None:
        self.embedder = embedder
        self.name = embedder.name
        self._lock = threading.Lock()

    def embed(self, text: str):
        with self._lock:
            return self.embedder.embed(text)

    def token_count(self, text: str) -> int:
        with self._lock:
            return self.embedder.token_count(text)


@dataclass
class RunContext:
    config: Config
    spec: DatasetSpec
    run: Path
    llm: ResolvedLlm
    settings: PipelineSettings
    embedder: CachedEmbedder
    model_hashes: dict[str, str]
    backend_factory: Callable[[], LlmBackend] | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    halt_reason: str | None = None
    _sources: dict[str, DataSource] = field(default_factory=dict)
    _assets: dict[str, assets.LoadedAssets] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _shared_backend: LlmBackend | None = None

    def source(self, name: str) -> DataSource:
        with self._lock:
            if name not in self._sources:
                s = self.spec.sources[name]
                self._sources[name] = open_source(s.kind, path=str(s.path))
            return self._sources[name]

    def assets_for(self, name: str) -> assets.LoadedAssets:
        src = self.source(name)
        with self._lock:
            if name not in self._assets:
                out_dir = assets.asset_dir(self.config.data_dir, self.spec.name, name)
                self._assets[name] = assets.load_assets(src, out_dir, self.model_hashes)
            return self._assets[name]

    def backend(self) -> LlmBackend:
        if self.backend_factory is not None:
            return self.backend_factory()
        if self.llm.backend == "fake":
            return backend_for(self.llm)  # 가짜 백엔드는 문항마다 새로(호출 기록을 갖고 있다)
        with self._lock:
            if self._shared_backend is None:
                self._shared_backend = backend_for(self.llm)
            return self._shared_backend


# ── 한 문항 ──────────────────────────────────────────────


def _final_exec(trace: Trace, source: DataSource, settings: PipelineSettings) -> dict | None:
    if trace.sql is None:
        return None
    refine = trace.stages.get("refine")
    last = trace.rounds[-1] if trace.rounds else None
    use_round = (
        last is not None
        and last.executed
        and last.old_sql == trace.sql
        and last.status in R.EXEC_STATUSES
        and (refine is None or refine.get("final_executed"))
    )
    if use_round:
        return {
            "status": last.status,
            "row_count": last.row_count,
            "column_count": last.column_count,
            "error": last.sqlite_error,
            "source": "round",
        }
    r = source.execute(trace.sql, settings.exec_timeout_seconds, 0)
    return {
        "status": r.status,
        "row_count": r.row_count,
        "column_count": r.column_count,
        "error": r.error or None,
        "source": "extra",
    }


def _status(trace: Trace) -> tuple[str, str | None]:
    if trace.sql is not None:
        return "generated", None
    extract = trace.stages.get("extract")
    if extract is not None and extract.get("sql") is None:
        return "no_sql", "응답에서 SQL을 꺼내지 못함"
    ex = trace.exception or {}
    return "exception", f"{ex.get('type')}: {str(ex.get('message', ''))[:200]}"


def _llm_summary(trace: Trace) -> dict:
    totals = [usage_totals(c.usage) for c in trace.calls]
    known = bool(totals) and all(t is not None for t in totals)
    return {
        "call_count": len(trace.calls),
        "tool_calls": sum(c.tool_calls for c in trace.calls),
        "elapsed_ms": sum(c.elapsed_ms for c in trace.calls),
        "input_tokens": sum(t[0] for t in totals) if known else None,
        "output_tokens": sum(t[1] for t in totals) if known else None,
        "failures": [c.failure for c in trace.calls if c.failure],
    }


def run_item(q: PublicQuestion, ctx: RunContext, attempt: int) -> dict:
    """문항 하나를 돌리고 item 폴더에 trace.json과 record.json을 쓴다. record를 돌려준다."""
    started = time.monotonic()
    folder = R.item_dir(ctx.run, q.position)
    folder.mkdir(parents=True, exist_ok=True)
    trace: Trace | None = None
    exception = None
    final = None
    try:
        source = ctx.source(q.db_id)
        loaded = ctx.assets_for(q.db_id)
        trace = answer(
            q.question,
            source,
            loaded,
            ctx.backend(),
            embedder=ctx.embedder,
            settings=ctx.settings,
            calls_dir=folder / "calls",
        )
        final = _final_exec(trace, source, ctx.settings)
    except Exception as e:  # noqa: BLE001 — 한 문항의 실패가 다른 문항을 막지 않는다
        exception = {"type": type(e).__name__, "message": str(e)[:2000]}
    if trace is not None:
        R.write_json_atomic(folder / R.TRACE_FILE, trace.to_json())
        status, reason = _status(trace)
        exception = trace.exception
        refine = trace.stages.get("refine") or {}
        execute = trace.stages.get("execute") or {}
        llm = _llm_summary(trace)
        sql = trace.sql
    else:
        status, reason = "exception", f"{exception['type']}: {exception['message'][:200]}"
        refine, execute, sql = {}, {}, None
        llm = {
            "call_count": 0,
            "tool_calls": 0,
            "elapsed_ms": 0,
            "input_tokens": None,
            "output_tokens": None,
            "failures": [],
        }
    record = {
        "position": q.position,
        "question_id": q.question_id,
        "db_id": q.db_id,
        "difficulty": q.difficulty,
        "question": q.question,
        "status": status,
        "status_reason": reason,
        "sql": sql,
        "exception": None
        if exception is None
        else {"type": exception.get("type"), "message": exception.get("message")},
        "final_exec": final,
        "refine_calls": refine.get("calls", 0),
        "stop": execute.get("stop"),
        "llm": llm,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "attempt": attempt,
        "item_dir": os.path.relpath(folder, ctx.run),
        "finished_at": now(),
    }
    R.write_json_atomic(folder / R.RECORD_FILE, record)
    return record


def is_call_failure(record: dict) -> bool:
    t = (record.get("exception") or {}).get("type") or ""
    return record.get("status") == "exception" and t.endswith(CALL_FAILURE_SUFFIXES)


# ── 풀 ───────────────────────────────────────────────────


def run_pool(questions: list[PublicQuestion], ctx: RunContext, workers: int, attempts: dict[int, int]) -> list[dict]:
    """앞에서부터 위치를 하나씩 가져다 돈다. 멈춤 깃발이 서면 새 문항을 시작하지 않는다."""
    queue = list(questions)
    lock = threading.Lock()
    done: list[dict] = []
    counters = {"limit": 0, "fail": 0}

    def take() -> PublicQuestion | None:
        with lock:
            if ctx.stop.is_set() or not queue:
                return None
            return queue.pop(0)

    def finish(record: dict) -> None:
        with lock:
            done.append(record)
            t = (record.get("exception") or {}).get("type")
            counters["limit"] = counters["limit"] + 1 if is_usage_limit(t) else 0
            counters["fail"] = counters["fail"] + 1 if is_call_failure(record) else 0
            if counters["limit"] >= HALT_AFTER and not ctx.stop.is_set():
                ctx.halt_reason = "usage_limit"
                ctx.stop.set()
            elif counters["fail"] >= HALT_AFTER_FAILURES and not ctx.stop.is_set():
                ctx.halt_reason = "consecutive_failures"
                ctx.stop.set()

    def worker() -> None:
        while True:
            q = take()
            if q is None:
                return
            finish(run_item(q, ctx, attempts.get(q.position, 1)))

    threads = [threading.Thread(target=worker, name=f"t2s-run-{i}") for i in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return done


def request_stop(ctx: RunContext) -> None:
    """멈춤 신호: 새 문항을 시작하지 않고, 돌고 있는 모델 CLI를 끝낸다(그 문항은 …Exit로 기록된다)."""
    if ctx.halt_reason is None:
        ctx.halt_reason = "stop"
    ctx.stop.set()
    _proc.kill_active()


# ── 마무리 ───────────────────────────────────────────────


def finalize(ctx: RunContext, questions: list[PublicQuestion], run_meta: dict, *, started_monotonic: float) -> dict:
    """records.jsonl·predictions.json·manifest.json을 쓰고 manifest 내용을 돌려준다."""
    items = R.read_item_records(ctx.run)
    halted_note = {
        "usage_limit": f"사용량 한도 {HALT_AFTER}회 연속",
        "consecutive_failures": f"모델 호출 실패 {HALT_AFTER_FAILURES}회 연속",
        "stop": "멈춤 신호",
    }.get(ctx.halt_reason or "", "")
    rows = []
    for q in questions:
        rec = items.get(q.position)
        if rec is None:
            rec = {
                "position": q.position,
                "question_id": q.question_id,
                "db_id": q.db_id,
                "difficulty": q.difficulty,
                "question": q.question,
                "status": "not_started",
                "status_reason": f"시작하지 않음: {halted_note or '기록 없음'}",
                "sql": None,
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
        rows.append(rec)
    R.write_jsonl(ctx.run / R.RECORDS_FILE, rows)
    pred_sha = R.write_predictions(ctx.run, rows)
    after = fingerprints(ctx.spec, list(run_meta.get("databases", {})))
    status_counts = {s: sum(1 for r in rows if r["status"] == s) for s in R.STATUSES}
    exec_counts = {s: sum(1 for r in rows if (r.get("final_exec") or {}).get("status") == s) for s in R.EXEC_STATUSES}
    exec_counts["none"] = sum(1 for r in rows if not r.get("final_exec"))
    inputs = [r["llm"].get("input_tokens") for r in rows if r["llm"].get("call_count")]
    outputs = [r["llm"].get("output_tokens") for r in rows if r["llm"].get("call_count")]
    item_limit = float(run_meta.get("llm", {}).get("item_timeout_seconds") or 0) * 1000
    control = R.read_json_if(ctx.run / R.CONTROL_FILE) or {}
    manifest = {
        "format": R.RUN_FORMAT,
        "finished_at": now(),
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 1),
        "status_counts": status_counts,
        "exec_counts": exec_counts,
        "records": {"sha256": R.sha256_file(ctx.run / R.RECORDS_FILE), "count": len(rows)},
        "predictions": {"sha256": pred_sha},
        "databases_after": after,
        "database_unchanged": after == run_meta.get("databases"),
        "versions_end": versions(),
        "halted": None
        if ctx.halt_reason is None
        else {
            "reason": ctx.halt_reason,
            "after": HALT_AFTER if ctx.halt_reason == "usage_limit" else HALT_AFTER_FAILURES,
        },
        "resumes": control.get("resumes", []),
        "llm_totals": {
            "call_count": sum(r["llm"]["call_count"] for r in rows),
            "tool_calls": sum(r["llm"]["tool_calls"] for r in rows),
            "positions_with_tool_calls": [r["position"] for r in rows if r["llm"]["tool_calls"]],
            "input_tokens": None if any(v is None for v in inputs) else sum(inputs),
            "output_tokens": None if any(v is None for v in outputs) else sum(outputs),
        },
        "over_item_timeout": [r["position"] for r in rows if item_limit and r.get("elapsed_ms", 0) > item_limit],
    }
    R.write_json_atomic(ctx.run / R.MANIFEST_FILE, manifest)
    return manifest
