"""프롬프트 재생 대조 도구: 옛 run의 응답을 대본으로 되먹여 새 코드가 만든 프롬프트를 옛 prompt.txt와 바이트로 견준다.

테스트 폴더의 도구다(pytest가 모으지 않는다. 파일 이름이 test_로 시작하지 않는다). 모델을 부르지 않는다.
위치마다 옛 `items/<위치>/calls/<k>/response.txt`를 차례대로 내주는 ScriptedBackend로 파이프라인을 다시 돌린다.
실제 DB와 준비된 자산(값 색인·프로파일)과 임베딩 모델을 쓴다.

견주는 것: 호출 수, 호출마다 이름과 프롬프트 바이트, 최종 SQL, 상태, 최종 실행 상태.
견주지 못함: 옛 run에서 모델 호출이 실패했거나(응답 파일 없음, 실패 기록 있음) 다시 돌린 위치, 옛 호출 수가 기록과 다른 위치.

하지 않는 것: 정답 폴더를 읽지 않는다(생성 쪽만 쓰고 읽기 감시를 걸고 돈다). evidence used run과 단일 호출 run은
받지 않는다. 옛 run 폴더에 쓰지 않는다(새 산출물은 임시 폴더에 쓰고 끝나면 지운다).

쓰는 법(저장소 뿌리에서):
    uv run python tests/tools/replay_prompts.py --run <옛 run 이름> --spread 100
    uv run python tests/tools/replay_prompts.py --run <옛 run 이름> --positions 0-499
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from t2s.dataset.config import find_root, load_config
from t2s.dataset.questions import load_public_questions
from t2s.dataset.state import load_state
from t2s.llm import select
from t2s.llm.fake import ScriptedBackend, ScriptedCall
from t2s.pipeline import refine
from t2s.pipeline.run import PipelineSettings
from t2s.pipeline.value_search import CachedEmbedder
from t2s.prep import embed
from t2s.runner import batch
from t2s.runner import records as R
from t2s.runner.guard import install_guard


def parse_positions(text: str) -> list[int]:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def spread_positions(questions, count: int) -> list[int]:
    """DB마다 고르게(라운드 로빈) count개를 고른다. DB 안에서는 위치 순서로 고르게 띄운다."""
    by_db: dict[str, list[int]] = defaultdict(list)
    for q in questions:
        by_db[q.db_id].append(q.position)
    dbs = sorted(by_db)
    share = {db: count // len(dbs) for db in dbs}
    for db in dbs[: count % len(dbs)]:
        share[db] += 1
    picked: list[int] = []
    for db in dbs:
        pos = by_db[db]
        n = min(share[db], len(pos))
        if n == 0:
            continue
        step = len(pos) / n
        picked.extend(pos[int(i * step)] for i in range(n))
    return sorted(picked)


def old_script(item: Path) -> tuple[list[tuple[str, Path]] | None, str | None]:
    """옛 위치의 호출 대본 [(이름, 호출 폴더)]. 견줄 수 없으면 (None, 까닭)."""
    record = R.read_json(item / R.RECORD_FILE)
    if record.get("attempt", 1) != 1:
        return None, f"다시 돌린 위치(attempt {record.get('attempt')})"
    llm = record.get("llm") or {}
    if llm.get("failures"):
        return None, "옛 run에서 모델 호출 실패가 있음"
    calls = item / "calls"
    ks = sorted((c for c in calls.iterdir() if c.name.isdigit()), key=lambda c: int(c.name)) if calls.is_dir() else []
    if len(ks) != llm.get("call_count", -1):
        return None, f"호출 폴더 수 {len(ks)}가 기록의 호출 수 {llm.get('call_count')}와 다름"
    script = []
    for c in ks:
        if not (c / "response.txt").is_file() or not (c / "prompt.txt").is_file():
            return None, f"호출 {c.name}에 응답 또는 프롬프트 파일이 없음"
        meta = R.read_json(c / "llm-meta.json")
        script.append((meta["name"], c))
    return script, None


def replay_one(q, ctx: batch.RunContext, old_run: Path, scratch: Path) -> dict:
    item = R.item_dir(old_run, q.position)
    old_record = R.read_json(item / R.RECORD_FILE)
    script, why = old_script(item)
    if script is None:
        return {"position": q.position, "db_id": q.db_id, "result": "skipped", "why": why}
    backend = ScriptedBackend(
        [ScriptedCall(name, (c / "response.txt").read_text(encoding="utf-8", newline="")) for name, c in script]
    )
    here = dataclasses.replace(ctx, run=scratch, backend_factory=lambda: backend)
    record = batch.run_item(q, here, 1)
    problems: list[str] = []
    if len(backend.calls) != len(script):
        problems.append(f"호출 수 옛 {len(script)} 새 {len(backend.calls)}")
    for k, (call, (name, c)) in enumerate(zip(backend.calls, script, strict=False), start=1):
        if call["name"] != name:
            problems.append(f"호출 {k} 이름 옛 {name} 새 {call['name']}")
            continue
        if call["prompt"].encode("utf-8") != (c / "prompt.txt").read_bytes():
            problems.append(f"호출 {k}({name}) 프롬프트가 다름")
    if record.get("sql") != old_record.get("sql"):
        problems.append("최종 SQL이 다름")
    if record.get("status") != old_record.get("status"):
        problems.append(f"상태 옛 {old_record.get('status')} 새 {record.get('status')}")
    old_exec = (old_record.get("final_exec") or {}).get("status")
    new_exec = (record.get("final_exec") or {}).get("status")
    if old_exec != new_exec:
        problems.append(f"실행 상태 옛 {old_exec} 새 {new_exec}")
    return {
        "position": q.position,
        "db_id": q.db_id,
        "result": "same" if not problems else "different",
        "calls": len(script),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="옛 run 이름(runs 폴더 아래, 또는 legacy_runs 자리)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--positions", help="위치 범위(예: 0-99,120)")
    group.add_argument("--spread", type=int, help="DB마다 고르게 N개")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, help="위치별 결과를 JSON으로 쓸 파일(옛 run 폴더 밖)")
    args = ap.parse_args(argv)

    root = find_root()
    install_guard(root)  # 정답 폴더, 받은 zip, 공개 evidence 파일을 막는다
    config = load_config(root, load_state(root))
    found = R.find_run(config.runs_dir, args.run, config.legacy_runs_dirs)
    if found is None:
        print(f"run이 없습니다: {args.run}", file=sys.stderr)
        return 2
    old_run, _ = found
    meta = R.read_json(old_run / R.RUN_FILE)
    if (meta.get("evidence") or {}).get("mode") != "not_used":
        print("evidence not_used run만 받습니다", file=sys.stderr)
        return 2
    if R.route_of(meta) == "single":
        print("단일 호출 run은 받지 않습니다", file=sys.stderr)
        return 2
    spec = config.datasets.get(meta["dataset"])
    if spec is None:
        print(f"데이터셋이 설정에 없습니다: {meta['dataset']}", file=sys.stderr)
        return 2
    pipeline = dict(meta["pipeline"])
    old_template = pipeline.pop("refine_template_sha256", None)
    if old_template is not None and old_template != refine.template_sha256():
        print("주의: 고치기 템플릿 sha256이 옛 run과 다릅니다", file=sys.stderr)
    fingerprints = batch.fingerprints(spec, sorted(meta.get("databases") or {}))
    changed = [n for n, v in (meta.get("databases") or {}).items() if fingerprints.get(n) != v]
    if changed:
        print(f"주의: DB 지문이 옛 run과 다릅니다: {', '.join(changed)}", file=sys.stderr)
    questions = load_public_questions(root / meta["questions"]["path"])[: meta["count"]]
    positions = parse_positions(args.positions) if args.positions else spread_positions(questions, args.spread)
    if not positions or any(not 0 <= p < len(questions) for p in positions):
        print(f"위치가 비었거나 0~{len(questions) - 1} 밖입니다", file=sys.stderr)
        return 2
    targets = [questions[p] for p in positions]

    model_hashes = embed.check_model_files(config.embedding_model_dir)
    embedder = CachedEmbedder(batch.LockedEmbedder(embed.load_embedder(config.embedding_model_dir)))
    scratch = Path(tempfile.mkdtemp(prefix="t2s-replay-"))
    try:
        ctx = batch.RunContext(
            config=config,
            spec=spec,
            run=scratch,
            llm=select.ResolvedLlm.from_json(meta["llm"]),
            settings=PipelineSettings(**pipeline),
            embedder=embedder,
            model_hashes=model_hashes,
        )
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda q: replay_one(q, ctx, old_run, scratch), targets))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    after = batch.fingerprints(spec, sorted(meta.get("databases") or {}))
    moved = [n for n, v in fingerprints.items() if after.get(n) != v]
    if moved:
        print(f"DB 지문이 재생 앞뒤로 다릅니다: {', '.join(moved)}", file=sys.stderr)

    same = [r for r in results if r["result"] == "same"]
    diff = [r for r in results if r["result"] == "different"]
    skipped = [r for r in results if r["result"] == "skipped"]
    calls = sum(r.get("calls", 0) for r in results if r["result"] != "skipped")
    dbs = sorted({r["db_id"] for r in results})
    print(f"run {args.run} · 위치 {len(results)}개(DB {len(dbs)}개)")
    print(f"견줌 {len(same) + len(diff)} · 같음 {len(same)} · 다름 {len(diff)} · 견주지 못함 {len(skipped)}")
    print(f"견준 호출 {calls}")
    for r in diff:
        print(f"다름 위치 {r['position']}: {'; '.join(r['problems'])}")
    for r in skipped:
        print(f"견주지 못함 위치 {r['position']}: {r['why']}")
    if args.out:
        out = args.out.resolve()
        if out == old_run.resolve() or old_run.resolve() in out.parents:
            print("옛 run 폴더 안에는 쓰지 않습니다", file=sys.stderr)
            return 2
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 3 if moved else (0 if not diff else 1)


if __name__ == "__main__":
    sys.exit(main())
