"""P3 흐름: 단계를 차례로 부르는 answer().

옮긴 TS 코드: engines/pure-fix-h/pack/run.ts `main`(단계 차례, 호출 기록, 실패 처리, 단계별 기록 칸).
DB 지문 전후 비교, 읽기 감시, 최종 SQL 표시는 부르는 쪽(ask·run)의 일이다.
힌트·지식을 받는 인자는 두지 않는다(AGENTS 규칙 9). 키워드 힌트는 늘 빈 글, 주석의 지식은 늘 None이다.
"""

from __future__ import annotations

import platform
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from t2s.datasource.base import DataSource
from t2s.llm.base import LlmBackend, sha256_text
from t2s.pipeline import attach, extract, keywords, prompt, refine, schema_text, value_search
from t2s.pipeline.trace import CallRecord, Trace
from t2s.prep.assets import LoadedAssets
from t2s.prep.embed import EmbeddingFailed

if TYPE_CHECKING:
    from t2s.prep.embed import Embedder


@dataclass(frozen=True)
class PipelineSettings:
    example_rows: int = 3
    keyword_attempts: int = 3
    exec_timeout_seconds: float = 120
    refine_rounds: int = 3
    keep_rows: int = 5

    @classmethod
    def from_mapping(cls, raw: Mapping) -> PipelineSettings:
        """harness.toml [pipeline] 표. refine_template은 "builtin"만 받는다."""
        template = raw.get("refine_template", "builtin")
        if template != "builtin":
            raise ValueError(f"refine_template은 builtin만 받습니다: {template!r}")
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known - {"refine_template"}
        if unknown:
            raise ValueError(f"[pipeline]에 모르는 칸이 있습니다: {', '.join(sorted(unknown))}")
        return cls(**{k: raw[k] for k in known if k in raw})


class LlmCallFailed(Exception):
    def __init__(self, type_: str, message: str) -> None:
        super().__init__(message)
        self.type = type_
        self.message = message


class ValueSearchFailed(Exception):
    pass


def _count_values(per: list) -> int:
    return sum(len(vs) for _, cols in per for _, vs in cols)


def _plain_per_column(per: list) -> list:
    return [[t, [[c, list(vs)] for c, vs in cols]] for t, cols in per]


def answer(
    question: str,
    source: DataSource,
    assets: LoadedAssets,
    llm: LlmBackend,
    *,
    embedder: Embedder | value_search.CachedEmbedder,
    settings: PipelineSettings | None = None,
    calls_dir: Path | None = None,
) -> Trace:
    settings = settings or PipelineSettings()
    started = time.monotonic()
    trace = Trace(question=question)
    trace.versions = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "extract_rules": extract.EXTRACT_RULES_VERSION,
        "keyword_rules": keywords.KEYWORD_RULES_VERSION,
        "refine_template_sha256": refine.template_sha256(),
    }
    cached = embedder if isinstance(embedder, value_search.CachedEmbedder) else value_search.CachedEmbedder(embedder)

    def ms(t0: float) -> int:
        return round((time.monotonic() - t0) * 1000)

    def ask(name: str, text: str, attempt: int, parse) -> tuple[str, int]:
        k = len(trace.calls) + 1
        outcome = llm.call(
            text, Path(calls_dir) / str(k) if calls_dir is not None else None, name=name, attempt=attempt
        )
        meta = outcome.meta
        parsed = None if outcome.response is None else parse(outcome.response)
        trace.calls.append(
            CallRecord(
                k=k,
                name=name,
                attempt=attempt,
                prompt_sha256=meta.get("prompt_sha256", ""),
                prompt_chars=meta.get("prompt_chars", len(text)),
                response_sha256=meta.get("response_sha256"),
                response_chars=None if outcome.response is None else len(outcome.response),
                parse_ok=parsed,
                elapsed_ms=int(meta.get("elapsed_ms", 0)),
                tool_calls=int(meta.get("tool_calls", 0)),
                usage=meta.get("usage"),
                failure=None if outcome.failure is None else outcome.failure.type,
            )
        )
        if outcome.failure is not None or outcome.response is None:
            f = outcome.failure
            raise LlmCallFailed(f.type if f else "NoResponse", f"호출 {k}({name}): {f.message if f else '응답 없음'}")
        return outcome.response, k

    try:
        # ① 스키마
        schema = schema_text.build_schema(source, settings.example_rows)
        trace.set_stage(
            "schema",
            {
                "tables": len(schema.tables),
                "names": list(schema.tables),
                "chars": len(schema.text),
                "num_rows": settings.example_rows,
                "rows": dict(schema.rows),
            },
        )

        # ② 키워드(읽지 못하면 같은 프롬프트로 다시, 모두 keyword_attempts번)
        t0 = time.monotonic()
        kw_prompt = keywords.keywords_prompt(question, "")
        items: list | None = None
        why: str | None = None
        attempts = 0
        kw_calls: list[int] = []
        try:
            for attempt in range(1, settings.keyword_attempts + 1):
                attempts = attempt
                box: list[keywords.KeywordParse] = []

                def parse_kw(text: str, box: list = box) -> bool:
                    box.append(keywords.parse_keywords(text))
                    return box[-1].ok

                _, k = ask("extract_keywords", kw_prompt, attempt, parse_kw)
                kw_calls.append(k)
                if box and box[-1].ok:
                    items = box[-1].items
                    break
                why = box[-1].why if box else "no response"
        finally:
            kws = items if items is not None else []
            non_string = sum(1 for x in kws if not isinstance(x, str))
            trace.set_stage(
                "extract_keywords",
                {
                    "calls": kw_calls,
                    "keywords": None if items is None else [repr(x) for x in kws],
                    "ok": items is not None,
                    "attempts": attempts,
                    "why": None if items is not None else why,
                    "non_string": non_string,
                    "prompt_chars": len(kw_prompt),
                },
            )
            trace.timing["keywords_ms"] = ms(t0)

        # ③ 값 검색(글이 아닌 키워드가 하나라도 있으면 건너뛴다. 원본은 .strip()에서 예외가 나 값이 붙지 않는다)
        t0 = time.monotonic()
        if non_string == 0:
            try:
                vs = value_search.similar_entities(assets.index, [str(x) for x in kws], cached)
            except EmbeddingFailed:
                raise
            except Exception as e:
                raise ValueSearchFailed(f"값 검색 실패: {type(e).__name__}: {e}") from e
            per_column = vs.per_column
            trace.set_stage(
                "retrieve_entity",
                {
                    "search_strings": len(vs.packets),
                    "jaccard_hits": vs.jaccard_hits,
                    "after_edit": vs.after_edit,
                    "after_embedding": vs.after_embedding,
                    "columns": sum(len(cols) for _, cols in per_column),
                    "values": _count_values(per_column),
                    "per_column": _plain_per_column(per_column),
                    "error": None,
                    "min_margin": vs.min_margin,
                    "near": vs.near,
                    "comparisons": vs.comparisons,
                    "margin_counts": vs.margin_counts,
                },
            )
        else:
            per_column = []
            trace.set_stage(
                "retrieve_entity",
                {
                    "search_strings": 0,
                    "jaccard_hits": 0,
                    "after_edit": 0,
                    "after_embedding": 0,
                    "columns": 0,
                    "values": 0,
                    "per_column": [],
                    "error": f"글이 아닌 키워드 {non_string}개(원본은 .strip()에서 예외가 나 값 검색을 하지 않는다)",
                    "min_margin": None,
                    "near": [],
                    "comparisons": 0,
                    "margin_counts": {},
                },
            )
        trace.timing["values_ms"] = ms(t0)

        # ④ 값 줄
        attached = attach.attach_values(schema.tables, schema.blocks, per_column, assets.values)
        trace.set_stage(
            "attach_values",
            {
                "tables": len(attached.attached),
                "columns": sum(len(cols) for _, cols in attached.attached),
                "values": _count_values(attached.attached),
                "attached": _plain_per_column(attached.attached),
                "chars_added": attached.chars_added,
                "schema_chars": len(attached.text),
                "unmatched_tables": attached.unmatched_tables,
            },
        )

        # ⑤ 프로파일 줄
        profiled = attach.attach_profile(schema.tables, attached.blocks, assets.profile)
        trace.set_stage(
            "attach_profile",
            {
                "tables": sum(1 for n in profiled.line_chars.values() if n > 0),
                "line_chars": dict(profiled.line_chars),
                "chars_added": profiled.chars_added,
                "schema_chars": len(profiled.text),
                "profile_sha256": assets.manifest.get("files", {}).get("profile.json", {}).get("sha256"),
            },
        )

        # ⑥ ⑦ 주석·결합
        trace.set_stage("comment", {"knowledge": "None"})
        gen_prompt = prompt.combined_prompt(profiled.text, question, None, source.dialect.name)
        trace.set_stage(
            "prompt",
            {"chars": len(gen_prompt), "sha256": sha256_text(gen_prompt), "cot": prompt.COT in gen_prompt},
        )

        # ⑧ 모델 호출
        try:
            response, _ = ask("generate_sql", gen_prompt, 1, lambda _text: None)
        finally:
            gen = next((c for c in trace.calls if c.name == "generate_sql"), None)
            if gen is not None:
                trace.set_stage(
                    "model",
                    {
                        "k": gen.k,
                        "model": llm.settings.model,
                        "reasoning_effort": llm.settings.reasoning_effort,
                        "elapsed_ms": gen.elapsed_ms,
                        "tool_calls": gen.tool_calls,
                        "prompt_chars": gen.prompt_chars,
                        "usage": gen.usage,
                        "failure": gen.failure,
                    },
                )

        # ⑨ SQL 꺼내기
        extracted = extract.extract_sql(response)
        trace.set_stage(
            "extract",
            {
                "sql": extracted.sql,
                **extracted.check.to_json(),
                "exception": None if extracted.exception is None else extracted.exception["type"],
                "js_only_line_end": extract.has_js_only_line_end(response),
            },
        )
        if extracted.sql is None:
            trace.exception = extracted.exception
            trace.sql = None
        else:
            # ⑩ ⑪ 실행·판정·고치기
            t0 = time.monotonic()
            fix = refine.FixState(old_sql=extracted.sql, sql=extracted.sql)
            try:
                refine.refine_loop(
                    extracted.sql,
                    question=question,
                    schema_text=profiled.text,
                    tables=schema.tables,
                    source=source,
                    ask=ask,
                    rounds=settings.refine_rounds,
                    exec_timeout_seconds=settings.exec_timeout_seconds,
                    keep_rows=settings.keep_rows,
                    state=fix,
                )
            finally:
                _fix_stages(trace, fix)
                trace.timing["fix_ms"] = ms(t0)
            trace.sql = fix.sql
    except (LlmCallFailed, EmbeddingFailed, ValueSearchFailed, attach.ProfileMissing) as e:
        trace.exception = {"type": getattr(e, "type", None) or type(e).__name__, "message": str(e)}
        trace.sql = None
    trace.timing["total_ms"] = ms(started)
    return trace


def _fix_stages(trace: Trace, fix: refine.FixState) -> None:
    trace.rounds = list(fix.rounds)
    trace.set_stage(
        "execute",
        {
            "executions": sum(1 for r in fix.rounds if r.executed),
            "stop": fix.stop,
            "rounds": [
                {
                    "round": r.round,
                    "executed": r.executed,
                    "status": r.status,
                    "row_count": r.row_count,
                    "column_count": r.column_count,
                    "has_null": r.has_null,
                    "sqlite_error": r.sqlite_error,
                    "exception_class": r.exception_class,
                    "denied": r.denied,
                    "elapsed_ms": r.elapsed_ms,
                    "reason": r.reason,
                    "refine": r.refine,
                }
                for r in fix.rounds
            ],
        },
    )
    if fix.refine_calls == 0:
        trace.set_stage("refine", None)
        return
    trace.set_stage(
        "refine",
        {
            "calls": fix.refine_calls,
            "stop": fix.stop,
            "final": fix.final,
            "final_executed": fix.final_executed,
            "fk": None if fix.fk is None else fix.fk.to_json(),
            "rounds": [
                {
                    "round": r.round,
                    "k": r.call_k,
                    "prompt_chars": r.prompt_chars,
                    "sql_arg_prefixed": r.sql_arg_prefixed,
                    "parse": r.parse,
                    "blocks": r.blocks,
                }
                for r in fix.rounds
                if r.prompt_chars is not None
            ],
        },
    )
