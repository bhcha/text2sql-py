"""누설 검사: run의 모든 프롬프트에 정답 SQL·evidence가 들어 있는지 사후에 센다. 값은 출력하지 않고 수만 낸다.

옮긴 TS 코드(자체): harness/leak-audit.ts(세 가지 셈, 모델이 쓴 SQL이 정답과 같은 경우 따로 세기, 프롬프트 누락 세기).
TS와 다른 점: 이어 돌 때 옮겨 둔 `<위치>.attempt-<n>` 폴더의 프롬프트도 본다.
스터디 채점기 정답이면 정답 SQL은 gold_sql, evidence 자리는 문항 조건 각 줄과 채점 메모(study.leak_texts)로 세고,
db_id 대조는 건너뛴다(스터디 정답 파일에는 db_id가 없다).

evidence used run(run.json evidence.mode가 used)이면 그 문항의 evidence 글(BIRD 공식 evidence, Olist 문항 조건)은
누설로 세지 않는다. 정답 SQL·다른 문항 정답·프롬프트 누락·Olist 채점 메모는 그대로 센다. 대신 evidence_missing을 센다:
빈 글이 아닌 evidence가 그 문항(지금 시도)의 어느 프롬프트에도 없으면 그 위치를 넣는다(0이 아니면 실패).
not_used run의 셈과 요약 글은 이 갈래를 두기 전과 같다.
"""

from __future__ import annotations

import re
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import Config
from t2s.eval import gold as G
from t2s.eval import study

OTHER_GOLD_MIN = 40
EVIDENCE_MIN = 25
RESCORE_NOTE = "프롬프트 없음(재채점 run)"
EVIDENCE_LABEL = "evidence"
STUDY_EVIDENCE_LABEL = "문항 조건·채점 메모"
STUDY_NOTES_LABEL = "채점 메모"  # evidence used run에서 Olist evidence_hits가 세는 것
HIT_KEYS = ("gold_sql_hits", "evidence_hits", "other_gold_hits", "prompts_missing", "evidence_missing")
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
        "evidence_label": EVIDENCE_LABEL,
        "note": None,
    }
    if meta.get("kind") == "rescore":
        result["note"] = RESCORE_NOTE
        R.write_json_atomic(run / R.LEAK_FILE, result)
        return result
    gold = G.load_gold(config.root, meta["dataset"])
    items = G.gold_items(gold)
    is_study = G.is_study(gold.evaluator)
    used = G.evidence_mode(meta) == G.EVIDENCE_USED
    given: list[str] = []  # used run: 위치별로 준 evidence 글(정규화). 누설로 세지 않고 빠짐만 센다
    if is_study:
        texts = study.leak_texts(gold)
        gold_sql = [normalize(t["sql"]) for t in texts]
        if used:
            hints = [[normalize(h) for h in t["notes"]] for t in texts]
            given = [normalize(t["evidence"]) for t in texts]
            result["evidence_label"] = STUDY_NOTES_LABEL
        else:
            hints = [[normalize(h) for h in t["hints"]] for t in texts]
            result["evidence_label"] = STUDY_EVIDENCE_LABEL
    else:
        gold_sql = [normalize(str(it.get("SQL", ""))) for it in items]
        if used:
            hints = [[] for _ in items]
            given = [normalize(str(it.get("evidence", ""))) for it in items]
        else:
            hints = [[normalize(str(it.get("evidence", "")))] for it in items]
    if used:
        result["evidence_mode"] = G.EVIDENCE_USED
        result["evidence_missing"] = []
    hints = [[h for h in hs if len(h) >= EVIDENCE_MIN] for hs in hints]
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
        if not is_study and rec is not None and rec.get("db_id") != items[pos].get("db_id"):
            raise G.GoldError(f"run 기록과 정답 파일의 db_id가 어긋납니다(위치 {pos})")
        files = _prompts(folder)
        if current and rec is not None and len(files) != int((rec.get("llm") or {}).get("call_count", 0)):
            result["prompts_missing"].append(pos)
        result["prompts"] += len(files)
        trace = R.read_json_if(folder / R.TRACE_FILE) or {}
        rounds = {normalize(r.get("old_sql") or "") for r in trace.get("rounds") or []}
        own = gold_sql[pos]
        seen_given = False
        for f in files:
            text = normalize(f.read_text(encoding="utf-8"))
            if own and own in text:
                (result["model_made"] if own in rounds else result["gold_sql_hits"]).append(pos)
            if any(h in text for h in hints[pos]):
                result["evidence_hits"].append(pos)
            if any(i != pos and s != own and s in text for i, s in others):
                result["other_gold_hits"].append(pos)
            if used and given[pos] and given[pos] in text:
                seen_given = True
        if used and current and files and given[pos] and not seen_given:
            result["evidence_missing"].append(pos)
    R.write_json_atomic(run / R.LEAK_FILE, result)
    return result


def _total(result: dict) -> int:
    return sum(len(result.get(k) or []) for k in HIT_KEYS)


def leaked(result: dict) -> bool:
    return _total(result) > 0


def leak_summary(result: dict) -> str:
    if result.get("note"):
        return f"누설 검사: {result['note']}"
    if result.get("evidence_mode") == G.EVIDENCE_USED:
        middle = f"evidence 제공(used)·빠짐 {len(result.get('evidence_missing') or [])}, "
        if result.get("evidence_label") == STUDY_NOTES_LABEL:
            middle += f"{STUDY_NOTES_LABEL} {len(result['evidence_hits'])}, "
    else:
        middle = f"{result.get('evidence_label', EVIDENCE_LABEL)} {len(result['evidence_hits'])}, "
    return (
        f"누설 검사: 프롬프트 {result['prompts']}개, 정답 SQL {len(result['gold_sql_hits'])}, "
        f"{middle}다른 문항 정답 {len(result['other_gold_hits'])}, "
        f"프롬프트 누락 {len(result['prompts_missing'])} (모델이 쓴 SQL이 정답과 같음 {len(result['model_made'])})"
    )


def short(result: dict) -> dict:
    """score.json에 넣는 요약(수만)."""
    out = {
        "prompts": result["prompts"],
        "gold_sql": len(result["gold_sql_hits"]),
        "evidence": len(result["evidence_hits"]),
        "other_gold": len(result["other_gold_hits"]),
        "prompts_missing": len(result["prompts_missing"]),
        "model_made": len(result["model_made"]),
        "total": _total(result),
        "note": result.get("note"),
        "summary": leak_summary(result),
    }
    if result.get("evidence_mode") == G.EVIDENCE_USED:
        out["evidence_mode"] = G.EVIDENCE_USED
        out["evidence_missing"] = len(result.get("evidence_missing") or [])
    return out
