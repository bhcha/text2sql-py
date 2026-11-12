"""누설 검사: run의 모든 프롬프트에 정답 SQL·evidence가 들어 있는지 사후에 센다. 값은 출력하지 않고 수만 낸다.

옮긴 TS 코드(자체): harness/leak-audit.ts(세 가지 셈, 모델이 쓴 SQL이 정답과 같은 경우 따로 세기, 프롬프트 누락 세기).
TS와 다른 점: 이어 돌 때 옮겨 둔 `<위치>.attempt-<n>` 폴더의 프롬프트도 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.eval import gold as G

OTHER_GOLD_MIN = 40
EVIDENCE_MIN = 25
RESCORE_NOTE = "프롬프트 없음(재채점 run)"
HIT_KEYS = ("gold_sql_hits", "evidence_hits", "other_gold_hits", "prompts_missing")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """공백류를 한 칸으로, 앞뒤 공백 제거(TS와 같다)."""
    return _WS.sub(" ", text or "").strip()


def _item_dirs(run: Path) -> list[tuple[int, Path, bool]]:
    """(위치, 폴더, 지금 시도인가)."""
    base = run / R.ITEMS_DIR
    out = []
    if not base.is_dir():
        return out
    for p in sorted(base.iterdir()):
        if not p.is_dir() or not p.name[:4].isdigit():
            continue
        current = len(p.name) == 4
        if not current and not p.name[4:].startswith(".attempt-"):
            continue
        out.append((int(p.name[:4]), p, current))
    return out


def _prompts(folder: Path) -> list[Path]:
    calls = folder / "calls"
    if not calls.is_dir():
        return []
    return [c / "prompt.txt" for c in sorted(calls.iterdir()) if c.name.isdigit() and (c / "prompt.txt").is_file()]


def audit(config: Config, run_id: str) -> dict:
    run = R.run_dir(config.runs_dir, run_id)
    meta = R.read_json(run / R.RUN_FILE)
    result: dict = {
        "run": run_id,
        "positions": int(meta.get("count", 0)),
        "prompts": 0,
        "gold_sql_hits": [],
        "evidence_hits": [],
        "other_gold_hits": [],
        "model_made": [],
        "prompts_missing": [],
        "note": None,
    }
    if meta.get("kind") == "rescore":
        result["note"] = RESCORE_NOTE
        R.write_json_atomic(run / R.LEAK_FILE, result)
        return result
    gold = G.load_gold(config.root, meta["dataset"])
    items = G.gold_items(gold)
    gold_sql = [normalize(str(it.get("SQL", ""))) for it in items]
    evidence = [normalize(str(it.get("evidence", ""))) for it in items]
    others = [(i, s) for i, s in enumerate(gold_sql) if len(s) >= OTHER_GOLD_MIN]
    records = (
        {int(r["position"]): r for r in R.read_jsonl(run / R.RECORDS_FILE)} if (run / R.RECORDS_FILE).is_file() else {}
    )
    if not records:
        records = R.read_item_records(run)
    for pos, folder, current in _item_dirs(run):
        if pos >= len(items):
            continue
        rec = records.get(pos) if current else R.read_json_if(folder / R.RECORD_FILE)
        if rec is not None and rec.get("db_id") != items[pos].get("db_id"):
            raise G.GoldError(f"run 기록과 정답 파일의 db_id가 어긋납니다(위치 {pos})")
        files = _prompts(folder)
        if current and rec is not None and len(files) != int((rec.get("llm") or {}).get("call_count", 0)):
            result["prompts_missing"].append(pos)
        result["prompts"] += len(files)
        trace = R.read_json_if(folder / R.TRACE_FILE) or {}
        rounds = {normalize(r.get("old_sql") or "") for r in trace.get("rounds") or []}
        own = gold_sql[pos]
        for f in files:
            text = normalize(f.read_text(encoding="utf-8"))
            if own and own in text:
                (result["model_made"] if own in rounds else result["gold_sql_hits"]).append(pos)
            if len(evidence[pos]) >= EVIDENCE_MIN and evidence[pos] in text:
                result["evidence_hits"].append(pos)
            if any(i != pos and s != own and s in text for i, s in others):
                result["other_gold_hits"].append(pos)
    R.write_json_atomic(run / R.LEAK_FILE, result)
    return result


def leaked(result: dict) -> bool:
    return sum(len(result[k]) for k in HIT_KEYS) > 0


def leak_summary(result: dict) -> str:
    if result.get("note"):
        return f"누설 검사: {result['note']}"
    return (
        f"누설 검사: 프롬프트 {result['prompts']}개, 정답 SQL {len(result['gold_sql_hits'])}, "
        f"evidence {len(result['evidence_hits'])}, 다른 문항 정답 {len(result['other_gold_hits'])}, "
        f"프롬프트 누락 {len(result['prompts_missing'])} (모델이 쓴 SQL이 정답과 같음 {len(result['model_made'])})"
    )


def short(result: dict) -> dict:
    """score.json에 넣는 요약(수만)."""
    return {
        "prompts": result["prompts"],
        "gold_sql": len(result["gold_sql_hits"]),
        "evidence": len(result["evidence_hits"]),
        "other_gold": len(result["other_gold_hits"]),
        "prompts_missing": len(result["prompts_missing"]),
        "model_made": len(result["model_made"]),
        "total": sum(len(result[k]) for k in HIT_KEYS),
        "note": result.get("note"),
        "summary": leak_summary(result),
    }
