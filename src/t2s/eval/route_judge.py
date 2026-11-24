"""경로 판정(route-judge/1): 하네스(전체 P3) run과 후보(single 또는 partial) run을 같은 위치끼리 견주어 경로를 정한다.

자체 코드다. 읽는 것은 두 run의 run.json·manifest.json·score.json·ex.jsonl·records.jsonl뿐이고 정답 폴더와 채점 중간 파일은
열지 않는다(스터디 데이터셋의 라벨 값만 gold.load_gold로 정답 설정의 sha256을 읽는다). 판정 파일에는 위치별 0·1에서 센
수만 들어가고 SQL·정답 글은 없다.

판정 규칙(h = 하네스만 맞은 위치 수, s = 후보만 맞은 위치 수, d = h − s, p = 양쪽 꼬리 부호 검정):
- BIRD 채점기 run(AGENTS 규칙 6): d > 13 이고 p < 0.05 → harness_better(경로 harness). d < −13 이고 p < 0.05 →
  other_better(경로 후보). 그 밖(|d| ≤ 13 이거나 p ≥ 0.05) → no_difference(경로 후보, 더 싸므로). 다만 후보의 토큰 합과
  문항 시간 합이 둘 다 하네스보다 크면 "더 싸므로"가 성립하지 않아 harness로 둔다. 토큰 기록이 없으면 후보로 두되
  "토큰 미확인"을 적는다.
- 스터디 채점기 run(Olist): 13 기준을 쓰지 않고 p만 본다. p < 0.05면 d의 부호로 정하고, p ≥ 0.05면 inconclusive(판정 보류,
  harness.toml에 쓰지 않음, 전체 P3 유지. 사용자 결정).
- evidence 조건이 다른 두 run은 견주지 않는다(새로 맞음·새로 틀림도 내지 않는다).
토큰·시간은 판정 조건이 아니라 같은 표에 함께 보이고 근거에 적는다. 시간은 두 run을 함께 돌려 잰 값이다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.eval import gold as G
from t2s.eval import report as REP

RULE = "route-judge/1"
FORMAT = "t2s-route-judgment/1"
P_LIMIT = 0.05
CLI_KEYS = {"codex-cli": "codex", "claude-code": "claude"}
VERDICT_TEXT = {
    "harness_better": "하네스가 변동 기준보다 낫다",
    "other_better": "후보가 변동 기준보다 낫다",
    "no_difference": "차이 없음(변동 범위)",
    "inconclusive": "판정 보류(49문항 등 작은 평가셋에서 p ≥ 0.05)",
}


class JudgeError(Exception):
    pass


def pick_runs(
    config: Config, pair: str | None, harness: str | None, candidate: str | None
) -> tuple[str, str, str | None]:
    """(하네스 run, 후보 run, 짝 이름 또는 None)."""
    if harness or candidate:
        if not (harness and candidate):
            raise JudgeError("--harness와 --candidate는 함께 준다")
        return harness, candidate, None
    try:
        return R.pick_pair_runs(config.runs_dir, pair)
    except ValueError as e:
        raise JudgeError(str(e)) from e


NO_GOLD_LINE = "이 데이터셋은 정답이 없어 경로를 판정할 수 없습니다"


@dataclass
class RunSide:
    run_id: str
    meta: dict
    manifest: dict
    score: dict
    rows: dict[int, dict]
    records: list[dict]
    cost: dict = field(default_factory=dict)
    legacy: bool = False  # 옛 run 폴더(legacy_runs)에서 읽은 run. 그 폴더에는 쓰지 않는다


def _load(config: Config, run_id: str) -> RunSide:
    found = R.find_run(config.runs_dir, run_id, config.legacy_runs_dirs)  # 세션 폴더에 없으면 옛 run 폴더에서(읽기만)
    run, legacy = found if found else (R.run_dir(config.runs_dir, run_id), False)
    meta = R.read_json_if(run / R.RUN_FILE)
    if meta is None:
        raise JudgeError(f"run이 없습니다: {run_id}")
    if not (G.gold_folder(config.root, meta["dataset"]) / G.GOLD_TOML).is_file():
        raise G.NoGold(NO_GOLD_LINE)  # 채점해도 점수가 생기지 않으므로 "아직 채점 전"이라 말하지 않는다
    manifest = R.read_json_if(run / R.MANIFEST_FILE)
    if manifest is None:
        raise REP.NotReady(f"아직 생성 중입니다: {run_id} (t2s route-status)")
    if manifest.get("halted") is not None:
        raise REP.NotReady(f"멈춘 run입니다: {run_id} (한도가 풀린 뒤 이어 돌리기: t2s resume --run-id {run_id})")
    control = R.read_json_if(run / R.CONTROL_FILE) or {}
    alive = R.pid_alive(control.get("pid")) or R.pid_alive(control.get("worker_pid"))
    if control.get("state") in ("running", "starting") and alive:
        raise REP.NotReady(f"아직 도는 중입니다: {run_id} (t2s route-status)")
    score = R.read_json_if(run / R.SCORE_FILE)
    if score is None or not (run / R.EX_FILE).is_file():
        raise REP.NotReady(f"아직 채점 전입니다: t2s score --run {run_id}")
    rows = {int(r["position"]): r for r in R.read_jsonl(run / R.EX_FILE)}
    records = R.read_jsonl(run / R.RECORDS_FILE)
    return RunSide(run_id, meta, manifest, score, rows, records, _cost(manifest, records), legacy)


def _cost(manifest: dict, records: list[dict]) -> dict:
    totals = manifest.get("llm_totals") or {}
    return {
        "calls": totals.get("call_count"),
        "input_tokens": totals.get("input_tokens"),
        "output_tokens": totals.get("output_tokens"),
        "item_seconds": round(sum(int(r.get("elapsed_ms") or 0) for r in records) / 1000, 1),
        "elapsed_seconds": manifest.get("elapsed_seconds"),
    }


def _tokens(cost: dict) -> int | None:
    a, b = cost.get("input_tokens"), cost.get("output_tokens")
    return None if a is None or b is None else int(a) + int(b)


def _cli(meta: dict, manifest: dict) -> tuple[str | None, str | None]:
    key = CLI_KEYS.get((meta.get("llm") or {}).get("backend"))
    if key is None:
        return None, None
    return (meta.get("versions_start") or {}).get(key), (manifest.get("versions_end") or {}).get(key)


def _evidence(side: RunSide) -> str:
    ev = (side.score or {}).get("evidence")
    if isinstance(ev, dict) and "mode" in ev:
        return ev["mode"]
    return G.evidence_mode(side.meta) or G.EVIDENCE_NOT_USED


def usability(h: RunSide, c: RunSide) -> list[str]:
    """판정을 기록에 쓸 수 없는 까닭(없으면 빈 목록)."""
    reasons: list[str] = []
    hm, cm = h.meta, c.meta
    if hm.get("dataset") != cm.get("dataset"):
        reasons.append("데이터셋이 다름")
    hq, cq = hm.get("questions") or {}, cm.get("questions") or {}
    if hq.get("sha256") != cq.get("sha256") or hm.get("count") != cm.get("count"):
        reasons.append("문항이 다름(파일 sha256 또는 문항 수)")
    if hm.get("sample") or cm.get("sample") or h.score.get("sample") or c.score.get("sample"):
        reasons.append("소표본")
    hl, cl = hm.get("llm") or {}, cm.get("llm") or {}
    if "fake" in (hl.get("backend"), cl.get("backend")):
        reasons.append("가짜 백엔드")
    if any(hl.get(k) != cl.get(k) for k in ("backend", "model", "reasoning_effort")):
        reasons.append("백엔드·모델·추론 단계가 다름")
    hg, cg = hm.get("git") or {}, cm.get("git") or {}
    if hg.get("dirty") or cg.get("dirty") or hg.get("dirty") is None or cg.get("dirty") is None:
        reasons.append("미커밋 코드 또는 커밋 기록 없음")
    if (hg.get("head"), hg.get("src_tree")) != (cg.get("head"), cg.get("src_tree")):
        reasons.append("커밋이 다름")
    for side, name in ((h, "하네스"), (c, "후보")):
        if side.manifest.get("halted") is not None:
            reasons.append(f"{name} run이 멈췄음")
        if (side.score.get("status_counts") or {}).get("not_started", 0):
            reasons.append(f"{name} run에 시작하지 않은 문항이 있음")
        leak = side.score.get("leak")
        if not isinstance(leak, dict) or leak.get("note") or leak.get("total", 1) != 0:
            reasons.append(f"{name} run 누설 검사가 0이 아님(또는 없음)")
        if side.score.get("cross_check") is not True:
            reasons.append(f"{name} run 채점기 대조가 맞지 않음")
        if not side.score.get("data_unchanged") or not side.manifest.get("database_unchanged"):
            reasons.append(f"{name} run DB가 바뀜")
        if (side.manifest.get("llm_totals") or {}).get("tool_calls", 0):
            reasons.append(f"{name} run에 도구 호출이 있음")
        start, end = _cli(side.meta, side.manifest)
        if start != end:
            reasons.append(f"{name} run CLI 판이 측정 중 바뀜")
    if _cli(hm, h.manifest)[0] != _cli(cm, c.manifest)[0]:
        reasons.append("두 run의 CLI 판이 다름")
    if R.route_of(hm) != R.HARNESS_ROUTE or (hm.get("route") or {}).get("overrides"):
        reasons.append("하네스 쪽 run이 전체 P3(공통 pipeline 그대로)가 아님")
    if R.route_of(cm) not in ("single", "partial"):
        reasons.append("후보 run의 경로가 single·partial이 아님")
    return reasons


def verdict(h: int, s: int, *, study: bool) -> tuple[str, float]:
    p = REP.sign_test_p(h, s)
    d = h - s
    if study:
        if p < P_LIMIT:
            return ("harness_better" if d > 0 else "other_better"), p
        return "inconclusive", p
    if d > REP.VARIATION_LIMIT and p < P_LIMIT:
        return "harness_better", p
    if d < -REP.VARIATION_LIMIT and p < P_LIMIT:
        return "other_better", p
    return "no_difference", p


def decide_route(v: str, candidate_route: str, h_cost: dict, c_cost: dict) -> tuple[str | None, str]:
    """(경로 또는 None(보류), 까닭)."""
    if v == "harness_better":
        return R.HARNESS_ROUTE, VERDICT_TEXT[v]
    if v == "other_better":
        return candidate_route, VERDICT_TEXT[v]
    if v == "inconclusive":
        return None, VERDICT_TEXT[v] + ". 기록하지 않고 전체 P3를 유지한다"
    ht, ct = _tokens(h_cost), _tokens(c_cost)
    if ht is None or ct is None:
        return candidate_route, "차이 없음(변동 범위). 토큰 미확인"
    if ct > ht and c_cost["item_seconds"] > h_cost["item_seconds"]:
        return R.HARNESS_ROUTE, "차이 없음(변동 범위)이지만 후보의 토큰·시간이 둘 다 더 커서 더 싸지 않다"
    return candidate_route, "차이 없음(변동 범위). 더 싸므로 후보"


def _num(v) -> str:
    return "기록 없음" if v is None else f"{v:,}"


def _cost_line(name: str, cost: dict, correct: int) -> str:
    tok = _tokens(cost)
    per = "기록 없음" if tok is None or not correct else f"{round(tok / correct):,}"
    return (
        f"비용 {name}: 호출 {_num(cost.get('calls'))} · 입력 {_num(cost.get('input_tokens'))} · "
        f"출력 {_num(cost.get('output_tokens'))} 토큰 · 문항 시간 합 {cost['item_seconds']}초 · "
        f"경과 {cost.get('elapsed_seconds')}초 · 정답 1개당 {per} 토큰"
    )


def _ratio(a, b) -> str:
    return "기록 없음" if a is None or b in (None, 0) else f"{a / b:.2f}배"


def _dataset_id(config: Config, meta: dict, study: bool) -> str | None:
    if not study:
        return str(meta.get("archive_sha256") or "")[:8] or None
    try:
        gold = G.load_gold(config.root, meta["dataset"])
    except (G.GoldError, G.NoGold):
        return None
    return hashlib.sha256(Path(gold.questions_path).read_bytes()).hexdigest()[:8]


def judge(config: Config, pair: str | None, harness: str | None, candidate: str | None) -> tuple[list[str], dict, int]:
    """(화면 줄, 판정 내용, 종료 코드). 판정 파일을 쓴다(이미 있으면 같은 내용일 때만 그대로 둔다)."""
    h_id, c_id, pair_name = pick_runs(config, pair, harness, candidate)
    h, c = _load(config, h_id), _load(config, c_id)
    cand_route = R.route_of(c.meta)
    head = f"경로 판정 {RULE}" + (f" · 짝 {pair_name}" if pair_name else "")
    lines = [head, f"하네스: {h.score['label']}", f"후보({cand_route}): {c.score['label']}"]
    he, ce = _evidence(h), _evidence(c)
    if he != ce:
        lines.append(f"evidence 조건이 달라 견주지 않는다(하네스 {he}, 후보 {ce})")
        return lines, {}, 1
    hs = G.semantic_mode(h.meta) or G.SEMANTIC_NOT_USED
    cs = G.semantic_mode(c.meta) or G.SEMANTIC_NOT_USED
    if G.SEMANTIC_USED in (hs, cs):  # 방식이 다르거나 어느 쪽이든 used면 판정하지 않는다(AGENTS 규칙 6)
        lines.append(f"semantic used run으로는 경로 판정을 하지 않는다(하네스 {hs}, 후보 {cs})")
        return lines, {}, 1
    h_llm, c_llm = h.meta.get("llm") or {}, c.meta.get("llm") or {}
    differs = [k for k in ("backend", "model", "reasoning_effort") if h_llm.get(k) != c_llm.get(k)]
    if (h.meta.get("git") or {}).get("head") != (c.meta.get("git") or {}).get("head"):
        differs.append("git head")
    if differs:
        lines.append(
            f"두 run의 {', '.join(differs)}이(가) 달라 견주지 않는다(같은 백엔드·모델·추론 단계·커밋만 견준다)"
        )
        return lines, {}, 1
    study = str((h.score.get("evaluator") or {}).get("id") or "").startswith(G.STUDY_PREFIX)
    cmp = REP.compare(h.rows, c.rows)
    hn, sn = len(cmp["newly_correct"]), len(cmp["regressed"])
    hx, cx = h.score["ex_total"], c.score["ex_total"]
    lines.append(f"EX 하네스 {hx['correct']}/{hx['denominator']} · 후보 {cx['correct']}/{cx['denominator']}")
    sentences = REP.study_sentences if study else REP.comparison_sentences
    lines += sentences(
        newly_correct=hn,
        regressed=sn,
        positions=cmp["positions"],
        sample=False,
        is_reference=True,
        backend_differs=False,
    )
    v, p = verdict(hn, sn, study=study)
    route, why = decide_route(v, cand_route, h.cost, c.cost)
    lines.append(_cost_line("하네스", h.cost, hx["correct"]))
    lines.append(_cost_line("후보", c.cost, cx["correct"]))
    lines.append(
        f"배수(하네스÷후보): 토큰 {_ratio(_tokens(h.cost), _tokens(c.cost))} · "
        f"문항 시간 {_ratio(h.cost['item_seconds'], c.cost['item_seconds'])} (두 run을 함께 돌려 잰 값)"
    )
    shown = "보류(전체 P3 유지)" if route is None else route
    lines.append(f"경로 판정: {shown}({why}). 1회 실행 측정으로 정한 설정값이며 다시 재면 바뀔 수 있다")
    reasons = usability(h, c)
    if route is None:
        reasons.append("판정 보류")
    if reasons:
        lines.append("기록에 쓸 수 없음: " + ", ".join(reasons))
    path = R.judgment_path(config.runs_dir, h_id, c_id)
    hl = h.meta.get("llm") or {}
    finished = max(str(h.manifest.get("finished_at") or ""), str(c.manifest.get("finished_at") or ""))
    over = (c.meta.get("route") or {}).get("overrides")
    over_text = json.dumps(over, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    basis = {
        "route": route,
        "verdict": v,
        "rule": RULE,
        "backend": hl.get("backend"),
        "model": hl.get("model"),
        "reasoning_effort": hl.get("reasoning_effort"),
        "dataset": h.meta.get("dataset"),
        "dataset_id": _dataset_id(config, h.meta, study),
        "evidence": he,
        "questions": h.meta.get("count"),
        "harness_run": h_id,
        f"{cand_route}_run": c_id,
        "harness_correct": hx["correct"],
        f"{cand_route}_correct": cx["correct"],
        "new_right": hn,
        "new_wrong": sn,
        "p": round(p, 6),
        "harness_input_tokens": h.cost.get("input_tokens"),
        "harness_output_tokens": h.cost.get("output_tokens"),
        f"{cand_route}_input_tokens": c.cost.get("input_tokens"),
        f"{cand_route}_output_tokens": c.cost.get("output_tokens"),
        "harness_seconds": h.cost["item_seconds"],
        f"{cand_route}_seconds": c.cost["item_seconds"],
        "measured": finished[:10] or None,
        "cli": _cli(h.meta, h.manifest)[0],
        "commit": ((h.meta.get("git") or {}).get("head") or "")[:7] or None,
        "overrides": over_text if cand_route == "partial" else None,
    }
    data = {
        "format": FORMAT,
        "rule": RULE,
        "pair": pair_name,
        "harness_run": h_id,
        "candidate_run": c_id,
        "candidate_route": cand_route,
        "study": study,
        "verdict": v,
        "route": route,
        "reason": why,
        "usable": not reasons,
        "reasons": reasons,
        "basis": {k: val for k, val in basis.items() if val is not None},
        "lines": list(lines),
    }
    code = 0
    if h.legacy:
        lines.append("판정 파일을 쓰지 않음: 하네스 run이 옛 run 폴더(legacy_runs)에 있어 읽기만 합니다")
        return lines, data, code
    existing = R.read_json_if(path)
    if existing is not None:
        if {k: val for k, val in existing.items() if k != "judged_at"} != data:
            lines.append(f"판정 파일이 이미 있고 내용이 다릅니다(덮어쓰지 않음): {path.name}")
            return lines, data, 1
        lines.append(f"판정 파일(이미 있음, 같은 내용): {_rel(config, path)}")
        return lines, existing, code
    R.write_json_atomic(path, {**data, "judged_at": _now()})
    lines.append(f"판정 파일: {_rel(config, path)}")
    return lines, data, code


def _rel(config: Config, path: Path) -> str:
    return os.path.relpath(path, config.root)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
