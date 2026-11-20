"""쓸 run 표와 run 읽기. 순수 단일 호출 run(기준선 프로젝트 꼴)과 P3 run(이 하네스 꼴)을 같은 내부 꼴로 바꾼다.

경로는 저장소 루트 기준 상대 경로로만 적는다. 읽을 때 위치별 question_id·db_id, evidence 방식, 추론 단계, 백엔드,
문항 수, 토큰 합을 대조하고 어긋나면 멈춘다. 오류 글에 SQL 원문을 넣지 않는다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.questions import load_public_questions

BASE = "../text2sql-model-baseline/runs/"
WT = "../text2sql-py-wt/"

# 조합 8개(이름, 모델, 하네스, 경로). 기준은 sol62-pure.
RUNS: list[dict] = [
    {
        "name": "luna-pure",
        "model": "gpt-5.6-luna",
        "kind": "pure",
        "path": BASE + "baseline-gaps-20261119T072033Z-3-luna-bird",
    },
    {
        "name": "terra-pure",
        "model": "gpt-5.6-terra",
        "kind": "pure",
        "path": BASE + "baseline-20261117T130630Z-terra-bird",
    },
    {"name": "sol-pure", "model": "gpt-5.6-sol", "kind": "pure", "path": BASE + "baseline-20261117T130630Z-sol-bird"},
    {
        "name": "sol62-pure",
        "model": "gpt-6.2-sol",
        "kind": "pure",
        "path": BASE + "baseline-gpt62-20261117T222452Z-gpt62-sol-bird",
    },
    {"name": "luna-p3", "model": "gpt-5.6-luna", "kind": "p3", "path": "runs/20261116-bird-minidev-7c4018"},
    {"name": "terra-p3", "model": "gpt-5.6-terra", "kind": "p3", "path": WT + "c1/runs/20261117-bird-minidev-fa7ed8"},
    {"name": "sol-p3", "model": "gpt-5.6-sol", "kind": "p3", "path": "runs/20261116-bird-minidev-1799ec"},
    {"name": "sol62-p3", "model": "gpt-6.2-sol", "kind": "p3", "path": WT + "c1/runs/20261118-p3-gpt62-sol-bird"},
]
BASELINE = "sol62-pure"

# 같은 조합의 반복 run(대표 run 포함). luna: 커밋 b99e1b4·CLI 지시 설정 적용 전. terra: minimal,task·생성 프롬프트 같음.
REPEATS: dict[str, list[str]] = {
    "luna-p3": [
        "runs/20261116-bird-minidev-7c4018",
        "runs/20261116-bird-minidev-36d9c4",
        WT + "c1/runs/20261116-bird-minidev-45f691",
        WT + "c1/runs/20261116-bird-minidev-5e061b",
        WT + "c2/runs/20261116-bird-minidev-10057b",
        WT + "c2/runs/20261116-bird-minidev-86a6a7",
        WT + "c2/runs/20261116-bird-minidev-cdde43",
        WT + "c3/runs/20261116-bird-minidev-52de92",
        WT + "c3/runs/20261116-bird-minidev-82d305",
        WT + "c3/runs/20261116-bird-minidev-fc6ed4",
        WT + "c4/runs/20261116-bird-minidev-9c89d2",
        WT + "c4/runs/20261116-bird-minidev-dd51f1",
        WT + "c5/runs/20261116-bird-minidev-ed4c46",
    ],
    "terra-p3": [
        WT + "c1/runs/20261117-bird-minidev-fa7ed8",
        WT + "c4/runs/20261117-bird-minidev-8c18e3",
        WT + "c1/runs/20261117-bird-minidev-862b23",
        WT + "c1/runs/20261117-bird-minidev-563e68",
        WT + "c1/runs/20261117-bird-minidev-e45a39",
    ],
}
# 부푼 토큰 보정 배수를 구하는 terra P3 짝(같은 모델·프롬프트, CLI 지시 설정만 다름)
INFLATION_PAIR = (WT + "c1/runs/20261117-bird-minidev-fa7ed8", WT + "c1/runs/20261117-bird-minidev-8a66a4")


class SourceError(Exception):
    pass


def run_id(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _check_positions(name: str, items: list[dict], questions: list[dict]) -> None:
    if len(items) != len(questions):
        raise SourceError(f"{name}: 문항 수 {len(items)}, 기대 {len(questions)}")
    for q, it in zip(questions, items, strict=True):
        if (it["position"], it["question_id"], it["db_id"]) != (q["position"], q["question_id"], q["db_id"]):
            raise SourceError(f"{name}: 위치 {q['position']}의 question_id·db_id가 공개 문제 파일과 다르다")
        it["difficulty"] = q["difficulty"]


def load_pure(root: Path, spec: dict, questions: list[dict], db_hashes: dict | None = None) -> dict:
    run = (Path(root) / spec["path"]).resolve()
    name = spec["name"]
    meta = R.read_json(run / "run.json")
    if meta.get("evidence") != "not_used":
        raise SourceError(f"{name}: evidence가 not_used가 아니다")
    if meta.get("reasoning_effort") != "medium":
        raise SourceError(f"{name}: 추론 단계가 medium이 아니다")
    if meta.get("model") != spec["model"]:
        raise SourceError(f"{name}: 모델이 {spec['model']}이 아니다")
    if int(meta.get("count") or 0) != len(questions):
        raise SourceError(f"{name}: run 문항 수가 다르다")
    ex = R.read_json(run / "ex.json")
    score = R.read_json(run / "score.json")
    if sum(int(e["official_match"]) for e in ex) != score["correct"]:
        raise SourceError(f"{name}: ex.json 정답 합이 score.json과 다르다")
    if db_hashes is not None:
        gen = R.read_json(run / "db_generation.json")
        for side in ("before", "after"):
            got = {k: v["sha256"] for k, v in gen[side].items() if k in db_hashes}  # 다른 DB(olist 등)는 보지 않는다
            if got != db_hashes:
                raise SourceError(f"{name}: db_generation.json {side} DB 해시가 지금 DB와 다르다")
    by_pos = {int(e["position"]): e for e in ex}
    items, flags = [], []
    for q in questions:
        pos = q["position"]
        d = R.item_dir(run, pos)
        started = R.read_json(d / "started.json")
        res = R.read_json(d / "result.json")
        cmd = res.get("command") or []
        if "codex" not in (cmd if isinstance(cmd, list) else str(cmd).split()):
            raise SourceError(f"{name}: 위치 {pos}의 호출이 codex가 아니다")
        usage = res.get("usage")
        sql = res.get("sql") or None
        item = {
            "position": int(started["position"]),
            "question_id": int(started["id"]),
            "db_id": str(started["db_id"]),
            "sql": sql,
            "ex": int(by_pos[pos]["official_match"]),
            "ms": float(res.get("elapsed_seconds") or 0.0) * 1000.0,
            "token_partial": False,
            "token_missing": usage is None,
            "recorded_exec": by_pos[pos].get("exec"),
            "gen_exception": res.get("status") != "ok",
        }
        item["tok_in"] = int((usage or {}).get("input_tokens") or 0)
        item["tok_cached"] = int((usage or {}).get("cached_input_tokens") or 0)
        item["tok_out"] = int((usage or {}).get("output_tokens") or 0)
        items.append(item)
    _check_positions(name, items, questions)
    if any(i["token_missing"] for i in items):
        flags.append("missing_item")
    versions = meta.get("versions_before") or {}
    return _finish(
        spec,
        items,
        flags,
        {
            "run_id": run_id(spec["path"]),
            "commit": "스냅샷 " + str(meta.get("snapshot", ""))[:12],
            "harness": "해당 없음(기준선 프로젝트의 자체 호출 인자)",
            "profile": None,
            "codex": versions.get("codex"),
            "python": versions.get("python"),
            "sqlite": versions.get("sqlite"),
            "score_correct": score["correct"],
        },
    )


def _call_usage(run: Path, pos: int) -> tuple[int, int, int]:
    """items/NNNN/calls/<k>/llm-meta.json의 usage 합(입력, 출력, usage 있는 호출 수)."""
    calls = R.item_dir(run, pos) / "calls"
    tin = tout = n = 0
    if calls.is_dir():
        for k in sorted(calls.iterdir(), key=lambda p: p.name):
            m = R.read_json_if(k / "llm-meta.json") or {}
            u = m.get("usage")
            if u:
                tin += int(u.get("input_tokens") or 0)
                tout += int(u.get("output_tokens") or 0)
                n += 1
    return tin, tout, n


def p3_harness(run: Path) -> tuple[list | None, str | None]:
    """첫 기록 호출의 CLI 지시 설정과 CLI 판. harness 칸이 없으면 None(적용 전 run)."""
    for pos in range(5):
        calls = R.item_dir(run, pos) / "calls"
        if not calls.is_dir():
            continue
        for k in sorted(calls.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
            m = R.read_json_if(k / "llm-meta.json")
            if m:
                return m.get("harness"), m.get("cli_version")
    return None, None


def load_p3(root: Path, spec: dict, questions: list[dict], db_hashes: dict | None = None, *, strict_model=True) -> dict:
    run = (Path(root) / spec["path"]).resolve()
    name = spec["name"]
    meta = R.read_json(run / "run.json")
    llm = meta.get("llm") or {}
    if (meta.get("evidence") or {}).get("mode", "not_used") != "not_used":
        raise SourceError(f"{name}: evidence가 not_used가 아니다")
    if llm.get("reasoning_effort") != "medium":
        raise SourceError(f"{name}: 추론 단계가 medium이 아니다")
    if llm.get("backend") != "codex-cli":
        raise SourceError(f"{name}: 백엔드가 codex-cli가 아니다")
    if strict_model and llm.get("model") != spec["model"]:
        raise SourceError(f"{name}: 모델이 {spec['model']}이 아니다")
    if int(meta.get("count") or 0) != len(questions):
        raise SourceError(f"{name}: run 문항 수가 다르다")
    if db_hashes is not None:
        got = {k: v["sha256"] for k, v in (meta.get("databases") or {}).items()}
        if got != db_hashes:
            raise SourceError(f"{name}: run.json의 DB 해시가 지금 DB와 다르다")
    manifest = R.read_json(run / "manifest.json")
    if _sha256(run / "records.jsonl") != (manifest.get("records") or {}).get("sha256"):
        raise SourceError(f"{name}: records.jsonl 해시가 manifest와 다르다")
    records = R.read_jsonl(run / "records.jsonl")
    ex = {int(e["position"]): e for e in R.read_jsonl(run / "ex.jsonl")}
    items, flags = [], []
    for rec in records:
        pos = int(rec["position"])
        e = ex[pos]
        if (int(e["question_id"]), e["db_id"]) != (int(rec["question_id"]), rec["db_id"]):
            raise SourceError(f"{name}: 위치 {pos}의 ex.jsonl과 records.jsonl이 다르다")
        lm = rec.get("llm") or {}
        partial = lm.get("input_tokens") is None or lm.get("output_tokens") is None
        if partial:
            tin, tout, _ = _call_usage(run, pos)
        else:
            tin, tout = int(lm["input_tokens"]), int(lm["output_tokens"])
        generated = rec.get("status") == "generated"
        items.append(
            {
                "position": pos,
                "question_id": int(rec["question_id"]),
                "db_id": str(rec["db_id"]),
                "sql": (rec.get("sql") or None) if generated else None,
                "ex": int(e["ex_match"]),
                "ms": float(rec.get("elapsed_ms") or 0.0),
                "tok_in": tin,
                "tok_out": tout,
                "token_partial": partial,
                "token_missing": False,
                "recorded_exec": (rec.get("final_exec") or {}).get("status"),
                "gen_exception": not generated,
            }
        )
    items.sort(key=lambda i: i["position"])
    _check_positions(name, items, questions)
    totals = manifest.get("llm_totals") or {}
    sum_in, sum_out = sum(i["tok_in"] for i in items), sum(i["tok_out"] for i in items)
    if totals.get("input_tokens") is not None and (sum_in, sum_out) != (
        totals["input_tokens"],
        totals.get("output_tokens"),
    ):
        raise SourceError(f"{name}: 문항별 토큰 합이 manifest.llm_totals와 다르다")
    harness, cli = p3_harness(run)
    if harness is None:
        flags.append("inflated")
    if any(i["token_partial"] for i in items):
        flags.append("partial")
    versions = meta.get("versions_start") or {}
    return _finish(
        spec,
        items,
        flags,
        {
            "run_id": run_id(spec["path"]),
            "commit": str((meta.get("git") or {}).get("head", ""))[:7],
            "src_tree": str((meta.get("git") or {}).get("src_tree", ""))[:12],
            "dirty": (meta.get("git") or {}).get("dirty"),
            "harness": ",".join(harness) if harness else "적용 전(harness 칸 없음)",
            "profile": llm.get("profile"),
            "codex": cli or versions.get("codex"),
            "python": versions.get("python"),
            "sqlite": versions.get("sqlite"),
            "questions_sha256": (meta.get("questions") or {}).get("sha256"),
            "workers": meta.get("workers"),
        },
    )


def _finish(spec: dict, items: list[dict], flags: list[str], info: dict) -> dict:
    for i in items:
        i["tok"] = i["tok_in"] + i["tok_out"]
    return {
        "name": spec["name"],
        "model": spec["model"],
        "kind": spec["kind"],
        "path": spec["path"],
        "flags": flags,
        "info": info,
        "items": items,
        "totals": {
            "correct": sum(i["ex"] for i in items),
            "tok_in": sum(i["tok_in"] for i in items),
            "tok_out": sum(i["tok_out"] for i in items),
            "tok": sum(i["tok"] for i in items),
            "ms": sum(i["ms"] for i in items),
            "gen_exceptions": sum(i["gen_exception"] for i in items),
            "token_partial_items": sum(i["token_partial"] for i in items),
            "token_missing_items": sum(i["token_missing"] for i in items),
        },
    }


def load(root: Path, spec: dict, questions: list[dict], db_hashes: dict | None = None, **kw) -> dict:
    if spec["kind"] == "pure":
        return load_pure(root, spec, questions, db_hashes)
    return load_p3(root, spec, questions, db_hashes, **kw)


def prompt_hashes(root: Path, path: str, positions: list[int]) -> dict[int, tuple[str | None, str | None]]:
    """위치별 (첫 호출 프롬프트 sha256, 첫 generate_sql 프롬프트 sha256). trace.json에서 읽는다."""
    run = (Path(root) / path).resolve()
    out = {}
    for pos in positions:
        t = R.read_json_if(R.item_dir(run, pos) / "trace.json") or {}
        calls = sorted(t.get("calls") or [], key=lambda c: c.get("k") or 0)
        first = calls[0].get("prompt_sha256") if calls else None
        gen = next((c.get("prompt_sha256") for c in calls if c.get("name") == "generate_sql"), None)
        out[pos] = (first, gen)
    return out


def load_questions(path: Path) -> list[dict]:
    """공개 문제 파일(네 칸 꼴 검사 포함)에서 위치·question_id·db_id·난이도만."""
    return [
        {"position": q.position, "question_id": q.question_id, "db_id": q.db_id, "difficulty": q.difficulty}
        for q in load_public_questions(path)
    ]
