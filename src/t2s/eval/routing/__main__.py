"""라우팅 1단계 오프라인 시뮬레이션 실행: uv run python -m t2s.eval.routing [--reuse-cache <폴더>] [--workers N]

새 모델 호출이 없다. 기존 run 기록을 읽고, 예측 SQL과 정답 SQL을 읽기 전용으로 다시 실행해 신호와 행 F1을 만든다.
결과는 gold/_work/routing-step1/(있으면 -2, -3 …)에만 쓴다. 터미널에는 요약만 낸다(SQL·행 내용을 내지 않는다).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import t2s.runner.records as R
from t2s.dataset.config import find_root, load_config
from t2s.dataset.state import load_state
from t2s.eval import gold as G
from t2s.eval.bird import _generating, db_state
from t2s.eval.routing import execsig as X
from t2s.eval.routing import render, sim, sources, split
from t2s.eval.routing.rowf1 import f1_counters, set_f1_counters

DATASET = "bird-minidev"
WORK = "routing-step1"
PAIR_COMBOS = ("luna-p3", "terra-p3")


def out_dir(root: Path) -> Path:
    base = G.work_dir(root, WORK)
    if not base.exists():
        return base
    k = 2
    while (p := base.with_name(f"{WORK}-{k}")).exists():
        k += 1
    return p


def disagree(a: dict, b: dict) -> bool:
    """두 실행 결과의 답이 다른가(행 집합 비교). 한쪽이라도 오류·시간 초과·SQL 없음이면 불일치, 둘 다 0행이면 일치."""
    if a["status"] in ("error", "timeout") or b["status"] in ("error", "timeout"):
        return True
    if a["status"] == "empty" and b["status"] == "empty":
        return False
    da, db = X.set_digest(a["hashes"]), X.set_digest(b["hashes"])
    if da is None or db is None:
        return True
    return da != db


def f1_of(pred: dict, gold: dict) -> tuple[float | None, float | None]:
    """(다중집합 F1, 집합 F1). 정답이 오류·시간 초과·행이 너무 많으면 None. 예측 오류면 0."""
    if gold["status"] in ("error", "timeout") or gold["hashes"] is None:
        return None, None
    if pred["status"] in ("error", "timeout"):
        return 0.0, 0.0
    if pred["hashes"] is None:
        return None, None
    p, g = Counter(pred["hashes"]), Counter(gold["hashes"])
    return f1_counters(p, g)["f1"], set_f1_counters(p, g)["f1"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m t2s.eval.routing")
    ap.add_argument("--reuse-cache", help="앞 실행의 결과 폴더(gold/_work 아래 이름). 그 exec-cache.jsonl을 읽어 온다")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=X.DEFAULT_TIMEOUT)
    ap.add_argument("--seeds", type=int, default=1000)
    args = ap.parse_args(argv)
    t0 = time.monotonic()

    root = find_root()
    config = load_config(root, load_state(root))
    spec = config.datasets[DATASET]
    if spec.missing:
        print(f"자료 없음: {spec.missing}")
        return 4
    busy = _generating(config, DATASET, "")
    if busy:
        print(f"같은 데이터셋을 생성 중인 run이 있어 시작하지 않습니다: {busy}")
        return 1
    questions = sources.load_questions(spec.questions)
    db_paths = {name: Path(s.path) for name, s in spec.sources.items()}
    hashes_before, side_before = db_state(spec)

    # 1. run 읽기
    combos = {s["name"]: sources.load(root, s, questions, hashes_before) for s in sources.RUNS}
    repeats: dict[str, dict[str, dict]] = {}
    prompt_check: dict[str, dict] = {}
    positions = [q["position"] for q in questions]
    for combo, paths in sources.REPEATS.items():
        rep_path = next(s["path"] for s in sources.RUNS if s["name"] == combo)
        rep_hash = sources.prompt_hashes(root, rep_path, positions)
        repeats[combo] = {}
        for path in paths:
            rid = sources.run_id(path)
            spec_r = {"name": f"{combo}@{rid}", "model": combos[combo]["model"], "kind": "p3", "path": path}
            run = combos[combo] if path == rep_path else sources.load(root, spec_r, questions, hashes_before)
            ph = sources.prompt_hashes(root, path, positions)
            same_first = sum(1 for p in positions if ph[p][0] is not None and ph[p][0] == rep_hash[p][0])
            same_gen = sum(1 for p in positions if ph[p][1] is not None and ph[p][1] == rep_hash[p][1])
            kept = same_first == len(positions)
            prompt_check[f"{combo}@{rid}"] = {
                "same_first_prompt": same_first,
                "same_generate_prompt": same_gen,
                "kept": kept,
                "correct": run["totals"]["correct"],
                "gen_exceptions": run["totals"]["gen_exceptions"],
                "commit": run["info"]["commit"],
                "harness": run["info"]["harness"],
            }
            if kept:
                repeats[combo][rid] = run

    # 2. 정답 SQL
    gold = G.load_gold(root, DATASET)
    lines = G.gold_lines(gold)
    if len(lines) != len(questions):
        raise SystemExit("정답 SQL 줄 수가 문항 수와 다릅니다")
    gold_sql = []
    for q, line in zip(questions, lines, strict=True):
        sql, db = line.rsplit("\t", 1)
        if db.strip() != q["db_id"]:
            raise SystemExit(f"정답 SQL 위치 {q['position']}의 db_id가 공개 문제 파일과 다릅니다")
        gold_sql.append(sql)

    # 3. 실행(읽기 전용, 캐시)
    out = out_dir(root)
    out.mkdir(parents=True)
    cache_path = out / "exec-cache.jsonl"
    cache: dict = {}
    if args.reuse_cache:
        src = G.work_dir(root, args.reuse_cache) / "exec-cache.jsonl"
        cache = X.load_cache(src)
        X.append_cache(cache_path, cache)
    all_runs = list(combos.values()) + [r for c in repeats.values() for r in c.values() if r["name"] not in combos]
    tasks: dict[int, dict[str, str]] = {}
    for q in questions:
        p = q["position"]
        want = {X.sql_key(q["db_id"], gold_sql[p]): gold_sql[p]}
        for run in all_runs:
            sql = run["items"][p]["sql"]
            if sql and sql.strip():
                want[X.sql_key(q["db_id"], sql)] = sql
        tasks[p] = {k: v for k, v in want.items() if k not in cache}
    todo = [(questions[p]["db_id"], list(t.items())) for p, t in sorted(tasks.items()) if t]
    todo.sort(key=lambda t: t[0])
    n_exec = sum(len(t[1]) for t in todo)
    print(f"실행할 SQL {n_exec}개(캐시 {len(cache)}개), 작업자 {args.workers}", flush=True)
    done = 0
    with mp.get_context("spawn").Pool(
        args.workers, initializer=X.init_worker, initargs=({k: str(v) for k, v in db_paths.items()}, args.timeout)
    ) as pool:
        for res in pool.imap_unordered(X.work, todo, chunksize=1):
            cache.update(res)
            X.append_cache(cache_path, res)
            done += len(res)
            if done and done % 1000 < len(res):
                print(f"  실행 {done}/{n_exec} ({time.monotonic() - t0:.0f}초)", flush=True)
    hashes_after, side_after = db_state(spec)
    if hashes_after != hashes_before or side_after != side_before:
        print("실행 앞뒤 DB 해시 또는 부속 파일이 다릅니다. 멈춥니다.")
        return 1

    def res_of(run: dict, p: int) -> dict:
        sql = run["items"][p]["sql"]
        if not sql or not sql.strip():
            return dict(X.NO_SQL)
        return cache[X.sql_key(questions[p]["db_id"], sql)]

    gold_res = [cache[X.sql_key(q["db_id"], gold_sql[q["position"]])] for q in questions]

    # 4. 표 만들기
    def table_row(run: dict, kind: str) -> dict:
        row = {k: [] for k in ("ex", "tok", "ms", "exec_ms", "err", "empty", "null", "f1", "f1set", "status")}
        row["kind"] = kind
        for p in positions:
            it = run["items"][p]
            r = res_of(run, p)
            s = X.signals(r)
            f1, f1s = f1_of(r, gold_res[p])
            row["ex"].append(it["ex"])
            row["tok"].append(it["tok"])
            row["ms"].append(it["ms"])
            row["exec_ms"].append(r["ms"])
            row["err"].append(s["error"])
            row["empty"].append(s["empty"])
            row["null"].append(s["null"])
            row["f1"].append(f1)
            row["f1set"].append(f1s)
            row["status"].append(r["status"])
        return row

    T = {name: table_row(run, run["kind"]) for name, run in combos.items()}

    def pick_pair(combo: str, rep_id: str, pool: dict[str, dict]) -> str | None:
        cands = sorted(rid for rid, r in pool.items() if rid != rep_id and r["totals"]["gen_exceptions"] == 0)
        return cands[0] if cands else None

    def pair_row(rep: dict, pair: dict) -> dict:
        return {
            "tok": [pair["items"][p]["tok"] for p in positions],
            "ms": [pair["items"][p]["ms"] for p in positions],
            "disagree": [disagree(res_of(rep, p), res_of(pair, p)) for p in positions],
        }

    rep_ids = {c: combos[c]["info"]["run_id"] for c in PAIR_COMBOS}
    pair_ids = {c: pick_pair(c, rep_ids[c], repeats[c]) for c in PAIR_COMBOS}
    pairs = {c: pair_row(combos[c], repeats[c][pair_ids[c]]) for c in PAIR_COMBOS if pair_ids[c]}

    # 5. 묶음
    counts: dict[str, dict[str, int]] = {}
    for q in questions:
        counts.setdefault(q["db_id"], {lv: 0 for lv in split.LEVELS})[q["difficulty"]] += 1
    asg = split.assign(counts)
    group_of = {db: g for g in ("A", "B") for db in asg[g]}
    groups = {g: [q["position"] for q in questions if group_of[q["db_id"]] == g] for g in ("A", "B")}
    pure = [s["name"] for s in sources.RUNS if s["kind"] == "pure"]
    seeds = range(args.seeds)

    # 6. 본 절차
    main_res = sim.procedure(T, pairs, groups, sources.BASELINE, pure, seeds)

    # 7. 민감도(무작위 기준 생략)
    def swap_table(combo: str, run: dict) -> dict:
        T2 = dict(T)
        T2[combo] = table_row(run, "p3")
        return T2

    def verdict(res: dict) -> dict:
        d = res["directions"]
        return {
            "both_pass": res["both_pass"],
            "stage2": res["stage2"],
            "picks": {k: d[k]["picked"] for k in d},
            "pass": {k: d[k]["pass"] for k in d},
            "conf": {
                k: (
                    {x: d[k]["picked_conf"][x] for x in ("loss", "saving", "time_saving", "correct")}
                    if d[k]["picked"]
                    else None
                )
                for k in d
            },
        }

    sens: dict = {"rep_swap": {}, "pair_swap": {}}
    for combo in PAIR_COMBOS:
        pool = repeats[combo]
        for rid, run in sorted(pool.items()):
            if combo == "terra-p3" and run["totals"]["gen_exceptions"]:
                continue
            T2 = swap_table(combo, run)
            pid = pick_pair(combo, rid, pool)
            pairs2 = dict(pairs)
            if pid:
                pairs2[combo] = pair_row(run, pool[pid])
            else:
                pairs2.pop(combo, None)
            v = verdict(sim.procedure(T2, pairs2, groups, sources.BASELINE, pure, seeds=()))
            v.update({"correct": run["totals"]["correct"], "pair": pid})
            sens["rep_swap"][f"{combo}@{rid}"] = v
        for rid, run in sorted(pool.items()):
            if rid == rep_ids[combo]:
                continue
            pairs2 = dict(pairs)
            pairs2[combo] = pair_row(combos[combo], run)
            res2 = sim.procedure(T, pairs2, groups, sources.BASELINE, pure, seeds=())
            v = verdict(res2)
            # 다 항목 후보만 보았을 때 조정용 최선의 확인용 성적
            dis_best = {}
            for k, (tune, conf) in {"1": (groups["A"], groups["B"]), "2": (groups["B"], groups["A"])}.items():
                order = sim.cost_order(T, tune)
                cands = [
                    c for c in sim.candidates(order, tuple(pairs2)) if c["kind"] == "dis" and c["chain"][0] == combo
                ]
                bt = sim.evaluate(T, pairs2, sim.fixed(sources.BASELINE), tune)
                bc = sim.evaluate(T, pairs2, sim.fixed(sources.BASELINE), conf)
                tr = [sim.evaluate(T, pairs2, c, tune) for c in cands]
                picked, _ = sim.select(tr, bt) if tr else (None, None)
                if picked:
                    c = next(x for x in cands if x["name"] == picked["name"])
                    s = sim.summary(sim.evaluate(T, pairs2, c, conf), bc, T, sources.BASELINE)
                    dis_best[k] = {x: s[x] for x in ("name", "loss", "saving", "loss_ok", "saving_ok")}
                else:
                    dis_best[k] = None
            v["dis_best"] = dis_best
            sens["pair_swap"][f"{combo}@{rid}"] = v
    # 부푼 토큰 보정(추정)
    m_new = R.read_json((root / sources.INFLATION_PAIR[0]).resolve() / "manifest.json")["llm_totals"]["input_tokens"]
    m_old = R.read_json((root / sources.INFLATION_PAIR[1]).resolve() / "manifest.json")["llm_totals"]["input_tokens"]
    factor = m_new / m_old
    T3 = dict(T)
    pairs3 = dict(pairs)
    for name, run in combos.items():
        if "inflated" in run["flags"]:
            row = dict(T[name])
            row["tok"] = [it["tok_in"] * factor + it["tok_out"] for it in run["items"]]
            T3[name] = row
            if name in pairs3:
                pr = repeats[name][pair_ids[name]]
                pairs3[name] = {**pairs[name], "tok": [it["tok_in"] * factor + it["tok_out"] for it in pr["items"]]}
    sens["inflation"] = {
        "factor": factor,
        "new_input": m_new,
        "old_input": m_old,
        **verdict(sim.procedure(T3, pairs3, groups, sources.BASELINE, pure, seeds=())),
    }

    # 8. 손익분기점
    base_items = combos[sources.BASELINE]["items"]
    base_tok = sum(T[sources.BASELINE]["tok"])
    N = len(positions)
    be_rows = []
    for low in ("luna-pure", "terra-pure", "sol-pure", "luna-p3", "terra-p3"):
        ps = sim.perfect_split(T, low, sources.BASELINE, positions)
        be_rows.append(
            {
                "low": low,
                "correct": ps["correct"],
                "f_high": ps["f_high"],
                "assigned_tok": ps["assigned_tok"],
                "d_max": sim.breakeven(base_tok, ps["assigned_tok"], N),
                "low_alone_correct": sum(T[low]["ex"]),
                "low_mean_tok": statistics.fmean(T[low]["tok"]),
            }
        )
    for name, combos_ in (("oracle-pick-4", pure), ("oracle-pick-8", list(T))):
        r = sim.oracle_pick(T, combos_, positions)
        be_rows.append(
            {
                "low": name,
                "correct": r["correct"],
                "f_high": sum(1 for c in r["final"].values() if c == sources.BASELINE) / N,
                "assigned_tok": r["tok"],
                "d_max": sim.breakeven(base_tok, r["tok"], N),
            }
        )
    base_mean = base_tok / N
    grids = {
        low: sim.grid(base_mean, statistics.fmean(T[low]["tok"]), base_mean)
        for low in ("luna-pure", "terra-pure", "sol-pure")
    }
    present = [it for it in base_items if not it["token_missing"]]
    ins = sorted(it["tok_in"] for it in present)
    floor = {
        "input_min": ins[0],
        "input_median": statistics.median(ins),
        "cached_at_min": next(it["tok_cached"] for it in present if it["tok_in"] == ins[0]),
        "cached_median": statistics.median(it["tok_cached"] for it in present),
        "output_median": statistics.median(it["tok_out"] for it in present),
        "items": len(present),
    }
    breakeven = {"base_tok": base_tok, "base_mean": base_mean, "rows": be_rows, "grid": grids, "floor": floor}
    min_correct = sum(T[sources.BASELINE]["ex"]) - int(sim.allowed_loss(N))
    breakeven["loss_bound"] = {"min_correct": min_correct, "rows": []}
    for name, combos_ in (("min-tok-4", pure), ("min-tok-8", list(T))):
        r = sim.min_tok_split(T, combos_, positions, min_correct)
        if r is not None:
            r.pop("final")
            r["d_max"] = sim.breakeven(base_tok, r["assigned_tok"], N)
        breakeven["loss_bound"]["rows"].append({"low": name, **(r or {})})

    # 9. 신호의 질·F1·기록 일치
    quality = {}
    for name, row in T.items():
        q = {}
        wrong = [p for p in positions if not row["ex"][p]]
        for s in sim.SIGNAL_SETS:
            fired = [p for p in positions if sim._sig(T, name, p, s)]
            fw = sum(1 for p in fired if not row["ex"][p])
            q[s] = {
                "fired": len(fired),
                "fired_wrong": fw,
                "precision": fw / len(fired) if fired else None,
                "recall": fw / len(wrong) if wrong else None,
            }
        quality[name] = q
    f1info = {}
    for name, row in T.items():
        vals = [v for v in row["f1"] if v is not None]
        by_lv = {}
        for lv in split.LEVELS:
            xs = [row["f1"][p] for p in positions if questions[p]["difficulty"] == lv and row["f1"][p] is not None]
            by_lv[lv] = statistics.fmean(xs) if xs else None
        mism = [p for p in positions if row["f1set"][p] is not None and (row["f1set"][p] == 1.0) != bool(row["ex"][p])]
        f1info[name] = {
            "mean": statistics.fmean(vals) if vals else None,
            "by_difficulty": by_lv,
            "none": sum(1 for v in row["f1"] if v is None),
            "ex0_f1_ge_half": sum(1 for p in positions if not row["ex"][p] and (row["f1"][p] or 0) >= 0.5),
            "set_vs_official_mismatch": len(mism),
            "mismatch_positions": mism,
        }
    agree = {}
    for name, run in combos.items():
        same = 0
        for p in positions:
            rec = run["items"][p]["recorded_exec"]
            ours = T[name]["status"][p]
            if run["kind"] == "p3":
                if rec is None:
                    same += ours == "error"
                else:
                    same += rec == ours
            else:
                same += rec == {"rows": "ok", "empty": "ok"}.get(ours, ours)
        agree[name] = same
    gold_status = {s: sum(1 for r in gold_res if r["status"] == s) for s in ("rows", "empty", "error", "timeout")}

    # 10. 쓰기
    items_out = []
    for q in questions:
        p = q["position"]
        items_out.append(
            {
                "position": p,
                "question_id": q["question_id"],
                "db_id": q["db_id"],
                "difficulty": q["difficulty"],
                "group": group_of[q["db_id"]],
                "gold_status": gold_res[p]["status"],
                "gold_rows": gold_res[p]["row_count"],
                "combos": {
                    name: {
                        "ex": T[name]["ex"][p],
                        "tok": T[name]["tok"][p],
                        "ms": T[name]["ms"][p],
                        "exec_ms": T[name]["exec_ms"][p],
                        "status": T[name]["status"][p],
                        "err": T[name]["err"][p],
                        "empty": T[name]["empty"][p],
                        "null": T[name]["null"][p],
                        "f1": T[name]["f1"][p],
                        "token_partial": combos[name]["items"][p]["token_partial"],
                        "token_missing": combos[name]["items"][p]["token_missing"],
                    }
                    for name in T
                },
                "disagree": {c: pairs[c]["disagree"][p] for c in pairs},
            }
        )
    with open(out / "items.jsonl", "w", encoding="utf-8") as f:
        for row in items_out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def strip_final(d):
        if isinstance(d, dict):
            return {k: strip_final(v) for k, v in d.items() if k not in ("conf_final", "final")}
        if isinstance(d, list):
            return [strip_final(x) for x in d]
        return d

    combos_info = {
        name: {
            "model": run["model"],
            "kind": run["kind"],
            "path": run["path"],
            "flags": run["flags"],
            "info": run["info"],
            "totals": run["totals"],
            "by_group": {
                g: {
                    "correct": sum(T[name]["ex"][p] for p in groups[g]),
                    "tok": sum(T[name]["tok"][p] for p in groups[g]),
                    "ms": sum(T[name]["ms"][p] for p in groups[g]),
                    "err": sum(T[name]["err"][p] for p in groups[g]),
                    "empty": sum(T[name]["empty"][p] for p in groups[g]),
                    "null": sum(T[name]["null"][p] for p in groups[g]),
                    "f1_mean": statistics.fmean([T[name]["f1"][p] for p in groups[g] if T[name]["f1"][p] is not None]),
                }
                for g in ("A", "B")
            },
            "recorded_exec_agree": agree[name],
        }
        for name, run in combos.items()
    }
    summary = {
        "format": "t2s-routing-step1/1",
        "dataset": DATASET,
        "archive_sha256": spec.archive_sha256,
        "baseline": sources.BASELINE,
        "split": {
            **asg,
            "A_summary": split.summarize(counts, asg["A"]),
            "B_summary": split.summarize(counts, asg["B"]),
            "counts": counts,
        },
        "combos": combos_info,
        "pairs": {c: {"representative": rep_ids[c], "pair": pair_ids[c]} for c in PAIR_COMBOS},
        "pair_disagree": {c: sum(pairs[c]["disagree"]) for c in pairs},
        "prompt_check": prompt_check,
        "main": strip_final(main_res),
        "sensitivity": sens,
        "breakeven": breakeven,
        "signal_quality": quality,
        "f1": f1info,
        "gold_status": gold_status,
        "exec": {
            "timeout_s": args.timeout,
            "executed": n_exec,
            "cache_entries": len(cache),
            "reused_from": args.reuse_cache,
            "db_sha256_before": hashes_before,
            "db_sha256_after": hashes_after,
            "side_files_before": len(side_before),
            "side_files_after": len(side_after),
        },
        "env": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
        "inputs_sha256": {
            name: sources._sha256(
                (root / run["path"]).resolve() / ("records.jsonl" if run["kind"] == "p3" else "ex.json")
            )
            for name, run in combos.items()
        },
        "elapsed_seconds": round(time.monotonic() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "tables.md").write_text(render.tables(summary), encoding="utf-8")

    # 11. 요약 출력
    d = main_res["directions"]
    rel = out.relative_to(root)
    print(f"결과: {rel}/ (summary.json, tables.md, items.jsonl, exec-cache.jsonl)")
    print(f"묶음 A {len(groups['A'])}문항, B {len(groups['B'])}문항")
    for k in ("1", "2"):
        if d[k]["picked"]:
            c = d[k]["picked_conf"]
            print(
                f"방향 {k}: {d[k]['picked']} 확인용 손실 {c['loss']}(허용 {c['allowed_loss']:.3f}) "
                f"토큰 절감 {c['saving']:.1%} 시간 절감 {c['time_saving']:.1%} → {'통과' if d[k]['pass'] else '탈락'}"
            )
        else:
            print(f"방향 {k}: {d[k]['fail_reason']}")
    print("판정: " + (f"2단계 후보 라우터 {main_res['stage2']}" if main_res["stage2"] else "2단계로 가지 않음"))
    print(f"걸린 시간 {summary['elapsed_seconds']}초")
    return 0


if __name__ == "__main__":
    sys.exit(main())
