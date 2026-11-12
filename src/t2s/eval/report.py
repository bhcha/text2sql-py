"""보고: 라벨, EX, 난도별, 기준과 위치별 비교(새로 맞음·새로 틀림), 변동 기준 문장, 누설 검사 한 줄.

옮긴 TS 코드(자체): harness/lib/sentence.ts(signTestP, comparisonSentences), benchmarks/benchkit/report.ts(비교 대상 읽기,
SQL 바뀜 세기), harness/allbird.ts report(보고 줄). 원 논문·리더보드 점수는 어디에도 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.eval import gold as G
from t2s.eval import leak_audit

DEFAULT_BASELINES = {"bird-minidev": "baselines/ts-pure-fix.json"}
VARIATION_LIMIT = 13
REFERENCE = ("codex-cli", "gpt-5.6-terra", "medium")
LEVELS = ("simple", "moderate", "challenging")


class ReportError(Exception):
    pass


class NotReady(Exception):
    pass


def sign_test_p(a: int, b: int) -> float:
    """양쪽 꼬리 부호 검정(TS signTestP와 같은 식, 1을 넘지 않게)."""
    total = a + b
    if total == 0:
        return 1.0
    low = min(a, b)
    term = 2.0**-total
    s = term
    for i in range(1, low + 1):
        term *= (total - i + 1) / i
        s += term
    return min(1.0, 2 * s)


def comparison_sentences(
    *, newly_correct: int, regressed: int, positions: int, sample: bool, is_reference: bool, backend_differs: bool
) -> list[str]:
    a, b = newly_correct, regressed
    lines = [f"새로 맞음 {a}, 새로 틀림 {b} (공통 {positions}위치)"]
    if sample:
        return [*lines, "소표본·점수 아님 — 비교 문장 생략"]
    diff = abs(a - b)
    p = sign_test_p(a, b)
    stat = f"|차이|={diff}, p={p:.3f}"
    if diff <= VARIATION_LIMIT or p >= 0.05:
        lines.append(f"차이 없음(변동 범위, {stat})")
    else:
        lines.append(f"변동 기준보다 크다({stat}). 1회 실행이라 낫다·못하다로 읽지 않는다")
    if not is_reference:
        lines.append("모델이 달라 재현 비교 아님")
    if backend_differs:
        lines.append("백엔드가 달라 우열을 말하지 않는다")
    return lines


def sql_digest(sql) -> str | None:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest() if isinstance(sql, str) else None


@dataclass
class Side:
    ref: str
    description: str
    rows: dict[int, dict]
    summary: dict = field(default_factory=dict)


def _resolve(config: Config, ref: str) -> Path:
    p = Path(ref).expanduser()
    return p if p.is_absolute() else config.root / p


def load_side(config: Config, ref: str) -> Side:
    """기준선 .json | run id | ex.jsonl 경로."""
    path = _resolve(config, ref)
    if path.suffix == ".json" and path.is_file():
        data = R.read_json(path)
        rows = {int(r["position"]): r for r in data["rows"]}
        ex = data.get("summary", {}).get("ex_total", {})
        desc = f"{ref} (TS {data.get('pack')} {data.get('run_id')}, EX {ex.get('correct')}/{ex.get('denominator')})"
        return Side(ref, desc, rows, data.get("summary", {}))
    if path.suffix == ".jsonl" and path.is_file():
        rows = {int(r["position"]): r for r in R.read_jsonl(path)}
        correct = sum(int(r["ex_match"]) for r in rows.values())
        return Side(ref, f"{ref} (ex.jsonl, EX {correct}/{len(rows)})", rows)
    run = R.run_dir(config.runs_dir, ref)
    score = R.read_json_if(run / R.SCORE_FILE)
    if score is None or not (run / R.EX_FILE).is_file():
        raise ReportError(f"비교 대상을 읽지 못했습니다(채점한 run·기준선 .json·ex.jsonl): {ref}")
    sqls = {int(r["position"]): r.get("sql") for r in R.read_jsonl(run / R.RECORDS_FILE)}
    rows = {}
    for r in R.read_jsonl(run / R.EX_FILE):
        pos = int(r["position"])
        rows[pos] = {**r, "sql_sha256": sql_digest(sqls.get(pos))}
    ex = score["ex_total"]
    return Side(ref, f"run {ref} (EX {ex['correct']}/{ex['denominator']})", rows, score)


def compare(a_rows: dict[int, dict], b_rows: dict[int, dict]) -> dict:
    """a가 이번 run, b가 기준. 새로 맞음 = a 1·b 0, 새로 틀림 = a 0·b 1."""
    common = sorted(set(a_rows) & set(b_rows))
    out = {"positions": len(common), "newly_correct": [], "regressed": [], "both_correct": 0, "both_wrong": 0}
    sql_changed = 0
    sql_compared = 0
    for pos in common:
        a, b = a_rows[pos], b_rows[pos]
        for key in ("question_id", "db_id"):
            if key in a and key in b and a[key] != b[key]:
                raise ReportError(f"같은 위치의 {key}가 다릅니다(위치 {pos})")
        x, y = int(a["ex_match"]), int(b["ex_match"])
        if x and not y:
            out["newly_correct"].append(pos)
        elif y and not x:
            out["regressed"].append(pos)
        elif x:
            out["both_correct"] += 1
        else:
            out["both_wrong"] += 1
        sa, sb = a.get("sql_sha256"), b.get("sql_sha256")
        if "sql_sha256" in b and "sql_sha256" in a:
            sql_compared += 1
            if sa != sb:
                sql_changed += 1
    out["sql_changed"] = sql_changed if sql_compared else None
    return out


def _model_line(llm: dict | None) -> str:
    """t2s.llm.select.first_line과 같은 꼴(eval은 llm을 import하지 않는다. test_eval_report가 대조한다)."""
    if not llm:
        return "모델 기록 없음(재채점 run)"
    if llm.get("backend") == "fake":
        source = "가짜 백엔드"
    elif llm.get("model_source") == "conversation":
        source = f"대화({llm['profile']})" if llm.get("profile") else "대화"
    else:
        source = f"세션({llm.get('model_source')}, 추론 {llm.get('effort_source')})"
    return f"모델 {llm.get('model')} · 추론 {llm.get('reasoning_effort')} · 백엔드 {llm.get('backend')} · 출처 {source}"


def _status_text(counts: dict) -> str:
    return ", ".join(f"{s} {counts.get(s, 0)}" for s in R.STATUSES)


def _versions_line(meta: dict, manifest: dict, score: dict | None, tool_calls: int) -> tuple[str, str | None]:
    py = (score or {}).get("python") or (meta.get("versions_start") or {}).get("python")
    sq = (score or {}).get("sqlite") or (meta.get("versions_start") or {}).get("sqlite")
    start = {k: (meta.get("versions_start") or {}).get(k) for k in ("codex", "claude")}
    end_raw = manifest.get("versions_end")
    warn = None
    if end_raw is None:
        cli = "CLI 판 기록 없음"
    else:
        end = {k: end_raw.get(k) for k in ("codex", "claude")}
        cli = "CLI 판 시작·끝 같음" if start == end else "CLI 판 시작·끝 다름"
        if start != end:
            warn = f"주의: CLI 판이 측정 중 바뀌었습니다 {start} → {end}"
    return f"도구 호출 {tool_calls} · Python {py} · SQLite {sq} · {cli}", warn


def _check_ready(run: Path, run_id: str) -> tuple[dict, dict]:
    meta = R.read_json_if(run / R.RUN_FILE)
    if meta is None:
        raise ReportError(f"run이 없습니다: {run_id}")
    manifest = R.read_json_if(run / R.MANIFEST_FILE)
    if manifest is None:
        raise NotReady("아직 생성 중입니다: t2s status")
    return meta, manifest


def _write(run: Path, lines: list[str], data: dict, extra_md: list[str]) -> None:
    R.write_json_atomic(run / R.REPORT_FILE, {**data, "lines": lines})
    (run / R.REPORT_MD_FILE).write_text(
        "\n".join(["# 보고", "", *[f"- {x}" for x in lines], "", *extra_md]) + "\n", encoding="utf-8"
    )


def no_gold_summary(config: Config, run_id: str) -> list[str]:
    run = R.run_dir(config.runs_dir, run_id)
    meta, manifest = _check_ready(run, run_id)
    rows = R.read_jsonl(run / R.RECORDS_FILE)
    status = {s: sum(1 for r in rows if r["status"] == s) for s in R.STATUSES}
    ex = {s: sum(1 for r in rows if (r.get("final_exec") or {}).get("status") == s) for s in R.EXEC_STATUSES}
    ex_none = sum(1 for r in rows if not r.get("final_exec"))
    fixed = sum(1 for r in rows if r.get("refine_calls"))
    calls = sum((r.get("llm") or {}).get("call_count", 0) for r in rows)
    tools = sum((r.get("llm") or {}).get("tool_calls", 0) for r in rows)
    lines = [
        f"{meta['dataset']} · 정답 없음: 채점 생략",
        _model_line(meta.get("llm")),
        f"문항 {len(rows)} · 상태 {_status_text(status)}",
        "실행 " + " · ".join(f"{k} {v}" for k, v in ex.items()) + f" · 없음 {ex_none}",
        f"고치기 한 문항 {fixed} · 호출 {calls}번 · 도구 호출 {tools} · DB 불변 {manifest.get('database_unchanged')}",
        f"SQL과 실행 상태: {os.path.relpath(run / R.RECORDS_FILE, config.root)}",
    ]
    _write(run, lines, {"kind": "no_gold", "status_counts": status, "exec_counts": {**ex, "none": ex_none}}, [])
    return lines


def report(config: Config, run_id: str, against: str | None) -> tuple[list[str], int]:
    """(출력 줄, 종료 코드). report.json·report.md도 쓴다. 정답이 없으면 G.NoGold. 준비 전이면 NotReady."""
    run = R.run_dir(config.runs_dir, run_id)
    meta, manifest = _check_ready(run, run_id)
    G.load_gold(config.root, meta["dataset"])  # 정답이 없으면 NoGold
    score = R.read_json_if(run / R.SCORE_FILE)
    if score is None:
        raise NotReady(f"아직 채점 전입니다: t2s score --run {run_id}")
    records = {int(r["position"]): r for r in R.read_jsonl(run / R.RECORDS_FILE)}
    mine = {}
    for r in R.read_jsonl(run / R.EX_FILE):
        pos = int(r["position"])
        mine[pos] = {**r, "sql_sha256": sql_digest(records.get(pos, {}).get("sql"))}
    ex = score["ex_total"]
    bd = score.get("by_difficulty", {})
    pct = f"{ex['correct'] / ex['denominator'] * 100:.2f}" if ex["denominator"] else "None"
    lines = [score["label"]]
    lines.append(
        f"EX {ex['correct']}/{ex['denominator']} ({pct}%)"
        + "".join(f" · {lvl} {bd[lvl]['correct']}/{bd[lvl]['total']}" for lvl in LEVELS if lvl in bd)
    )
    lines.append(
        f"채점기 대조 {score['cross_check']} · DB 불변 {score['data_unchanged']} · 상태 {_status_text(score['status_counts'])}"
    )
    rescore = meta.get("kind") == "rescore"
    ref = against or DEFAULT_BASELINES.get(meta["dataset"])
    comparison = None
    side = None
    if ref and (against or _resolve(config, ref).is_file()):
        side = load_side(config, ref)
        comparison = compare(mine, side.rows)
        changed = "" if comparison["sql_changed"] is None else f" · SQL 바뀜 {comparison['sql_changed']}"
        lines.append(f"기준 {side.description}{changed}")
        llm = meta.get("llm") or {}
        is_ref = rescore or (llm.get("backend"), llm.get("model"), llm.get("reasoning_effort")) == REFERENCE
        lines += comparison_sentences(
            newly_correct=len(comparison["newly_correct"]),
            regressed=len(comparison["regressed"]),
            positions=comparison["positions"],
            sample=bool(score.get("sample")),
            is_reference=is_ref,
            backend_differs=(not rescore) and llm.get("backend") != "codex-cli",
        )
        if not rescore:
            fixed = {p for p, r in records.items() if r.get("refine_calls")}
            nc = sum(1 for p in comparison["newly_correct"] if p in fixed)
            rg = sum(1 for p in comparison["regressed"] if p in fixed)
            lines.append(f"고친 문항 {len(fixed)}개 가운데 새로 맞음 {nc}, 새로 틀림 {rg}")
    else:
        lines.append("비교 대상 없음")
    leak = R.read_json_if(run / R.LEAK_FILE)
    if leak is None:
        leak = leak_audit.audit(config, run_id)
    lines.append(leak_audit.leak_summary(leak))
    tool_calls = (manifest.get("llm_totals") or {}).get("tool_calls", 0)
    vline, warn = _versions_line(meta, manifest, score, tool_calls)
    lines.append(vline)
    if warn:
        lines.append(warn)
    lines.append(os.path.relpath(run, config.root))

    md: list[str] = ["## 난도별", "", "| 난도 | 맞음 | 문항 |", "|---|---|---|"]
    md += [f"| {lvl} | {b['correct']} | {b['total']} |" for lvl, b in bd.items()]
    md += ["", "## DB별", "", "| DB | 맞음 | 문항 |", "|---|---|---|"]
    md += [f"| {db} | {b['correct']} | {b['total']} |" for db, b in sorted(score.get("by_db", {}).items())]
    if comparison is not None:
        for title, key in (("새로 맞은 위치", "newly_correct"), ("새로 틀린 위치", "regressed")):
            md += ["", f"## {title}", "", "| 위치 | DB | 난도 |", "|---|---|---|"]
            md += [f"| {p} | {mine[p].get('db_id')} | {mine[p].get('difficulty')} |" for p in comparison[key]]
    data = {
        "kind": "scored",
        "run_id": run_id,
        "label": score["label"],
        "ex_total": ex,
        "by_difficulty": bd,
        "against": None if side is None else side.ref,
        "comparison": comparison,
        "leak": leak_audit.short(leak),
    }
    _write(run, lines, data, md)
    return lines, (1 if leak_audit.leaked(leak) else 0)
