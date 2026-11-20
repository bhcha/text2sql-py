"""라우팅 시뮬레이션 계산(파일·DB를 읽지 않는 순수 함수).

표 T: 조합 이름 → {"kind": "pure"|"p3", "ex", "tok", "ms", "exec_ms", "err", "empty", "null", "f1"}(위치 순 목록).
짝 표 pairs: 조합 이름 → {"tok", "ms", "disagree"}(반복 run 불일치 신호용).
비용 규칙: 올려 보낸 문항은 시도한 모든 조합의 토큰·시간을 더한다. 순수 조합을 중간 단계로 쓰면 신호를 보려고 실행한 시간을
더한다. 불일치 신호를 쓰는 단계는 짝 run의 토큰·시간을 그 단계의 모든 문항에 더한다.
"""

from __future__ import annotations

import itertools
import random
import statistics

from t2s.eval.report import VARIATION_LIMIT, sign_test_p

FULL = 500  # 손실 기준의 분모(13/500)
SAVING_MIN = 0.15
SIGNAL_SETS = ("E", "EZ", "EZN")
MISMATCH_SETS = ("D", "D+EZN")
MAX_STAGES = 3


def allowed_loss(n: int) -> float:
    return VARIATION_LIMIT * n / FULL


def loss_ok(base_correct: int, correct: int, n: int) -> bool:
    return base_correct - correct <= allowed_loss(n)


def saving(tok: float, base_tok: float) -> float:
    return 1.0 - tok / base_tok if base_tok else 0.0


def cost_order(T: dict, positions: list[int]) -> list[str]:
    """조정용 묶음에서 문항당 평균 토큰이 작은 순(동점은 이름 순)."""
    return sorted(T, key=lambda c: (sum(T[c]["tok"][i] for i in positions) / max(1, len(positions)), c))


def chains(order: list[str], lengths=(2, 3)) -> list[tuple[str, ...]]:
    return [c for k in lengths for c in itertools.combinations(order, k)]


def cand_name(kind: str, sig: str | None, chain: tuple[str, ...]) -> str:
    if kind == "fixed":
        return "fixed:" + chain[0]
    return f"{kind}:{sig}:" + ">".join(chain)


def candidates(order: list[str], pair_combos=()) -> list[dict]:
    """나 항목(실행 결과 캐스케이드)과 다 항목(반복 run 불일치 캐스케이드) 후보."""
    out = []
    for ch in chains(order):
        for s in SIGNAL_SETS:
            out.append({"name": cand_name("cas", s, ch), "kind": "cas", "sig": s, "chain": ch})
    for ch in chains(order):
        if ch[0] in pair_combos:
            for s in MISMATCH_SETS:
                out.append({"name": cand_name("dis", s, ch), "kind": "dis", "sig": s, "chain": ch})
    return out


def fixed(combo: str) -> dict:
    return {"name": cand_name("fixed", None, (combo,)), "kind": "fixed", "sig": None, "chain": (combo,)}


def _sig(T: dict, combo: str, i: int, sig: str) -> bool:
    row = T[combo]
    if row["err"][i]:
        return True
    if "Z" in sig and row["empty"][i]:
        return True
    return "N" in sig and bool(row["null"][i])


def escalates(T: dict, pairs: dict, cand: dict, stage: int, i: int) -> bool:
    combo = cand["chain"][stage]
    kind, sig = cand["kind"], cand["sig"]
    if kind == "cas":
        return _sig(T, combo, i, sig)
    if kind == "dis":
        if stage == 0:
            d = bool(pairs[combo]["disagree"][i])
            return d or (sig == "D+EZN" and _sig(T, combo, i, "EZN"))
        return _sig(T, combo, i, "EZN")
    if kind == "oracle-esc":
        return not T[combo]["ex"][i]
    if kind == "all":
        return True
    raise ValueError(kind)


def _attempt_cost(T: dict, pairs: dict, cand: dict, stage: int, i: int, last: bool) -> tuple[float, float]:
    combo = cand["chain"][stage]
    row = T[combo]
    tok, ms = row["tok"][i], row["ms"][i]
    if not last and row["kind"] == "pure" and cand["kind"] in ("cas", "dis"):
        ms += row["exec_ms"][i]  # 신호를 보려고 순수 조합의 SQL을 한 번 실행한 시간
    if cand["kind"] == "dis" and stage == 0:
        tok += pairs[combo]["tok"][i]
        ms += pairs[combo]["ms"][i]
    return tok, ms


def evaluate(T: dict, pairs: dict, cand: dict, positions: list[int], forced: dict | None = None) -> dict:
    """후보 하나를 문항 집합에서 채점. forced: 단계 → 올려 보낼 위치 집합(무작위 기준용)."""
    chain = cand["chain"]
    tok = ms = 0.0
    correct = 0
    final, f1s = {}, []
    reached = [0] * len(chain)
    escalated = [0] * len(chain)
    for i in positions:
        for stage in range(len(chain)):
            last = stage == len(chain) - 1
            reached[stage] += 1
            t, m = _attempt_cost(T, pairs, cand, stage, i, last)
            tok += t
            ms += m
            if last:
                break
            up = (i in forced[stage]) if forced is not None else escalates(T, pairs, cand, stage, i)
            if not up:
                break
            escalated[stage] += 1
        combo = chain[stage]
        final[i] = combo
        correct += T[combo]["ex"][i]
        f = T[combo]["f1"][i]
        if f is not None:
            f1s.append(f)
    return {
        "name": cand["name"],
        "stages": len(chain),
        "n": len(positions),
        "correct": correct,
        "tok": tok,
        "ms": ms,
        "reached": reached,
        "escalated": escalated,
        "final": final,
        "f1_mean": statistics.fmean(f1s) if f1s else None,
    }


def oracle_pick(T: dict, combos: list[str], positions: list[int]) -> dict:
    """맞힌 조합 가운데 그 문항 토큰이 가장 적은 것(없으면 가장 적은 것). 비용은 고른 조합 하나."""
    tok = ms = 0.0
    correct = 0
    final = {}
    for i in positions:
        right = [c for c in combos if T[c]["ex"][i]]
        pool = right or combos
        c = min(pool, key=lambda x: (T[x]["tok"][i], x))
        final[i] = c
        tok += T[c]["tok"][i]
        ms += T[c]["ms"][i]
        correct += T[c]["ex"][i]
    return {"n": len(positions), "correct": correct, "tok": tok, "ms": ms, "final": final}


def compare_to_base(T: dict, base: str, final: dict) -> dict:
    """새로 맞음 a(후보만 맞힘), 새로 틀림 b(기준만 맞힘), 부호 검정 p."""
    a = sum(1 for i, c in final.items() if T[c]["ex"][i] and not T[base]["ex"][i])
    b = sum(1 for i, c in final.items() if not T[c]["ex"][i] and T[base]["ex"][i])
    return {"newly_correct": a, "regressed": b, "p": sign_test_p(a, b)}


def sentence(cmp: dict, n: int) -> str:
    """AGENTS 규칙 6 문장. 묶음·500문항 모두 차이 13 이하 또는 p ≥ 0.05면 차이 없음."""
    a, b, p = cmp["newly_correct"], cmp["regressed"], cmp["p"]
    head = f"새로 맞음 {a}, 새로 틀림 {b}, |차이|={abs(a - b)}, p={p:.3f}"
    if abs(a - b) <= VARIATION_LIMIT or p >= 0.05:
        return head + " → 차이 없음(변동 범위)"
    return head + " → 변동 범위 밖(기존 run 재조합, 새 측정 아님. 낫다·못하다로 읽지 않는다)"


def _key(r: dict, base_tok: float):
    return (-saving(r["tok"], base_tok), r["stages"], -r["correct"], r["name"])


def select(results: list[dict], base: dict) -> tuple[dict | None, dict | None]:
    """조정용 고르기: 손실 기준 통과 후보 중 토큰 절감률 최대(동점: 단계 적음, 정답 많음, 이름 순).
    통과 후보가 없으면 (None, 손실이 가장 작은 후보)."""
    ok = [r for r in results if loss_ok(base["correct"], r["correct"], r["n"])]
    if ok:
        return min(ok, key=lambda r: _key(r, base["tok"])), None
    least = min(results, key=lambda r: (base["correct"] - r["correct"], _key(r, base["tok"])))
    return None, least


def summary(r: dict, base: dict, T: dict, base_name: str) -> dict:
    cmp = compare_to_base(T, base_name, r["final"])
    out = {k: r[k] for k in ("name", "n", "correct", "tok", "ms") if k in r}
    out.update(
        {
            "stages": r.get("stages"),
            "loss": base["correct"] - r["correct"],
            "allowed_loss": allowed_loss(r["n"]),
            "loss_ok": loss_ok(base["correct"], r["correct"], r["n"]),
            "saving": saving(r["tok"], base["tok"]),
            "time_saving": saving(r["ms"], base["ms"]),
            "saving_ok": saving(r["tok"], base["tok"]) >= SAVING_MIN,
            "reached": r.get("reached"),
            "escalated": r.get("escalated"),
            "f1_mean": r.get("f1_mean"),
            **cmp,
            "sentence": sentence(cmp, r["n"]),
        }
    )
    return out


def random_baseline(T: dict, pairs: dict, cand: dict, positions: list[int], rates: list[float], seeds=range(1000)):
    """같은 사슬에서 단계마다 같은 비율을 무작위로 올려 보낸다(random.Random(seed), 위치 오름차순 고정)."""
    pos = sorted(positions)
    corrects, toks = [], []
    for seed in seeds:
        rng = random.Random(seed)
        forced = {}
        reached = pos
        for stage in range(len(cand["chain"]) - 1):
            m = round(rates[stage] * len(reached))
            up = sorted(rng.sample(reached, m))
            forced[stage] = set(up)
            reached = up
        r = evaluate(T, pairs, cand, pos, forced=forced)
        corrects.append(r["correct"])
        toks.append(r["tok"])
    return {
        "seeds": len(corrects),
        "correct_mean": statistics.fmean(corrects),
        "correct_p2_5": _pct(corrects, 2.5),
        "correct_p97_5": _pct(corrects, 97.5),
        "tok_mean": statistics.fmean(toks),
        "tok_p2_5": _pct(toks, 2.5),
        "tok_p97_5": _pct(toks, 97.5),
    }


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    k = (len(s) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def rates_of(r: dict) -> list[float]:
    return [(e / n if n else 0.0) for e, n in zip(r["escalated"][:-1], r["reached"][:-1], strict=True)]


def direction(T: dict, pairs: dict, tune: list[int], conf: list[int], base_name: str, seeds=range(1000)) -> dict:
    """조정용에서 후보 하나를 고르고 확인용에서 채점한다."""
    order = cost_order(T, tune)
    cands = candidates(order, tuple(pairs))
    base_t = evaluate(T, pairs, fixed(base_name), tune)
    base_c = evaluate(T, pairs, fixed(base_name), conf)
    tune_res = [evaluate(T, pairs, c, tune) for c in cands]
    picked, least = select(tune_res, base_t)
    by_name = {c["name"]: c for c in cands}
    out = {
        "order": order,
        "candidate_count": len(cands),
        "candidate_count_by_kind": {k: sum(1 for c in cands if c["kind"] == k) for k in ("cas", "dis")},
        "base_tune": summary(base_t, base_t, T, base_name),
        "base_conf": summary(base_c, base_c, T, base_name),
        "fixed_tune": [summary(evaluate(T, pairs, fixed(c), tune), base_t, T, base_name) for c in order],
        "fixed_conf": [summary(evaluate(T, pairs, fixed(c), conf), base_c, T, base_name) for c in order],
        "tune_pass_count": sum(1 for r in tune_res if loss_ok(base_t["correct"], r["correct"], r["n"])),
    }
    conf_all = [summary(evaluate(T, pairs, c, conf), base_c, T, base_name) for c in cands]
    out["conf_top"] = sorted(conf_all, key=lambda s: (-s["saving"], s["name"]))[:10]
    out["conf_top_loss_ok"] = sorted([s for s in conf_all if s["loss_ok"]], key=lambda s: (-s["saving"], s["name"]))[
        :10
    ]
    if picked is None:
        out["picked"] = None
        out["least_loss_tune"] = summary(least, base_t, T, base_name)
        out["pass"] = False
        out["fail_reason"] = "조정용에서 손실 기준을 넘는 후보 없음"
        return out
    cand = by_name[picked["name"]]
    conf_r = evaluate(T, pairs, cand, conf)
    cs = summary(conf_r, base_c, T, base_name)
    out["picked"] = cand["name"]
    out["picked_tune"] = summary(picked, base_t, T, base_name)
    out["picked_conf"] = cs
    out["conf_final"] = conf_r["final"]
    out["pass"] = cs["loss_ok"] and cs["saving_ok"]
    out["fail_reason"] = (
        None
        if out["pass"]
        else (
            ("확인용 손실 초과" if not cs["loss_ok"] else "") + (" 확인용 절감 15% 미만" if not cs["saving_ok"] else "")
        ).strip()
    )
    if len(cand["chain"]) > 1 and seeds:
        rb = random_baseline(T, pairs, cand, conf, rates_of(conf_r), seeds)
        rb["beats_random"] = cs["correct"] > rb["correct_p97_5"]
        out["random"] = rb
    allc = {**cand, "kind": "all", "name": "all:" + ">".join(cand["chain"])}
    ar = evaluate(T, pairs, allc, conf)
    last = evaluate(T, pairs, fixed(cand["chain"][-1]), conf)
    out["all_escalate_check"] = {
        "correct": ar["correct"],
        "last_alone_correct": last["correct"],
        "tok": ar["tok"],
        "chain_tok_sum": sum(sum(T[c]["tok"][i] for i in conf) for c in cand["chain"])
        + (sum(pairs[cand["chain"][0]]["tok"][i] for i in conf) if cand["kind"] == "dis" else 0),
    }
    return out


def oracles(T: dict, pairs: dict, positions: list[int], base_name: str, pure: list[str], order: list[str]) -> dict:
    base = evaluate(T, pairs, fixed(base_name), positions)
    out = {}
    for name, combos in (("oracle-pick-4", pure), ("oracle-pick-8", list(T))):
        r = oracle_pick(T, combos, positions)
        r["name"] = name
        out[name] = summary(r, base, T, base_name)
    best = None
    for ch in chains(order):
        c = {"name": "oracle-esc:" + ">".join(ch), "kind": "oracle-esc", "sig": None, "chain": ch}
        s = summary(evaluate(T, pairs, c, positions), base, T, base_name)
        if s["loss_ok"] and (best is None or s["saving"] > best["saving"]):
            best = s
    out["oracle-esc-best"] = best
    return out


def procedure(T: dict, pairs: dict, groups: dict[str, list[int]], base_name: str, pure: list[str], seeds=range(1000)):
    """2겹 교차. 방향 1: 조정 A → 확인 B, 방향 2: 조정 B → 확인 A."""
    dirs = {
        "1": direction(T, pairs, groups["A"], groups["B"], base_name, seeds),
        "2": direction(T, pairs, groups["B"], groups["A"], base_name, seeds),
    }
    out = {"directions": dirs}
    both = all(d["pass"] for d in dirs.values())
    out["both_pass"] = both
    allpos = sorted(groups["A"] + groups["B"])
    base_all = evaluate(T, pairs, fixed(base_name), allpos)
    # 500문항 합산: 문항마다 그 문항이 확인용이던 방향의 후보
    if all(d["picked"] for d in dirs.values()):
        final = {**dirs["1"]["conf_final"], **dirs["2"]["conf_final"]}
        tok = dirs["1"]["picked_conf"]["tok"] + dirs["2"]["picked_conf"]["tok"]
        ms = dirs["1"]["picked_conf"]["ms"] + dirs["2"]["picked_conf"]["ms"]
        correct = sum(T[c]["ex"][i] for i, c in final.items())
        cmp = compare_to_base(T, base_name, final)
        a, b = cmp["newly_correct"], cmp["regressed"]
        stmt = "차이 없음(변동 범위)" if abs(a - b) <= VARIATION_LIMIT or cmp["p"] >= 0.05 else "변동 범위 밖"
        out["combined"] = {
            "n": len(final),
            "correct": correct,
            "base_correct": base_all["correct"],
            "tok": tok,
            "base_tok": base_all["tok"],
            "saving": saving(tok, base_all["tok"]),
            "ms": ms,
            "time_saving": saving(ms, base_all["ms"]),
            **cmp,
            "statement": stmt,
        }
    # 두 방향이 다른 후보를 고르면 둘 다 양쪽 확인용에서 채점
    picks = [d["picked"] for d in dirs.values() if d["picked"]]
    out["same_pick"] = len(set(picks)) == 1 and len(picks) == 2
    cross = {}
    order_all = cost_order(T, allpos)
    by_name = {c["name"]: c for c in candidates(order_all, tuple(pairs)) + candidates(dirs["1"]["order"], tuple(pairs))}
    by_name.update({c["name"]: c for c in candidates(dirs["2"]["order"], tuple(pairs))})
    for name in sorted(set(picks)):
        c = by_name[name]
        ra = summary(
            evaluate(T, pairs, c, groups["A"]), evaluate(T, pairs, fixed(base_name), groups["A"]), T, base_name
        )
        rb = summary(
            evaluate(T, pairs, c, groups["B"]), evaluate(T, pairs, fixed(base_name), groups["B"]), T, base_name
        )
        rall = summary(evaluate(T, pairs, c, allpos), base_all, T, base_name)
        cross[name] = {"A": ra, "B": rb, "all": rall}
    out["cross"] = cross
    if both:
        out["stage2"] = max(picks, key=lambda nm: (cross[nm]["all"]["saving"], nm))
    else:
        out["stage2"] = None
    out["oracles"] = {
        g: oracles(T, pairs, groups[g], base_name, pure, cost_order(T, groups["B" if g == "A" else "A"]))
        for g in ("A", "B")
    }
    out["oracles"]["all"] = oracles(T, pairs, allpos, base_name, pure, order_all)
    return out


# ── 최상위 모델 배분 방식의 손익분기점 ──────────────────────


def breakeven(base_tok: float, assigned_tok: float, n: int, target: float = SAVING_MIN) -> float:
    """d_max = ((1 − 목표)·T_기준 − C_배정) ÷ N. 문항당 배분 판단 토큰 상한."""
    return ((1 - target) * base_tok - assigned_tok) / n


def perfect_split(T: dict, low: str, high: str, positions: list[int]) -> dict:
    """완벽한 배분: low가 맞힌 문항은 low로, 나머지는 high로."""
    final = {i: (low if T[low]["ex"][i] else high) for i in positions}
    tok = sum(T[c]["tok"][i] for i, c in final.items())
    correct = sum(T[c]["ex"][i] for i, c in final.items())
    f = sum(1 for c in final.values() if c == high) / len(positions)
    return {"low": low, "high": high, "correct": correct, "f_high": f, "assigned_tok": tok, "final": final}


def min_tok_split(T: dict, combos: list[str], positions: list[int], min_correct: int) -> dict | None:
    """정답 min_correct 이상을 지키는 배분 가운데 배정 토큰이 가장 적은 것(완벽한 배분 가정).

    문항마다 가장 싼 조합에서 출발해, 정답이 모자라면 '가장 싼 맞힌 조합'으로 바꾸는
    추가 토큰이 적은 문항부터 바꾼다. 문항마다 정답 기여가 0 또는 1이라 이 탐욕이 최소다.
    토큰 미기록(0) 문항도 0으로 센다. 정답 하한에 못 미치면 None.
    """
    final, ups = {}, []
    for i in positions:
        c0 = min(combos, key=lambda x: (T[x]["tok"][i], not T[x]["ex"][i], x))
        final[i] = c0
        right = [c for c in combos if T[c]["ex"][i]]
        if not T[c0]["ex"][i] and right:
            c1 = min(right, key=lambda x: (T[x]["tok"][i], x))
            ups.append((T[c1]["tok"][i] - T[c0]["tok"][i], i, c1))
    need = min_correct - sum(T[c]["ex"][i] for i, c in final.items())
    if need > len(ups):
        return None
    for _, i, c1 in sorted(ups)[: max(need, 0)]:
        final[i] = c1
    tok = sum(T[c]["tok"][i] for i, c in final.items())
    correct = sum(T[c]["ex"][i] for i, c in final.items())
    zero = sum(1 for i, c in final.items() if T[c]["tok"][i] == 0)
    return {"correct": correct, "assigned_tok": tok, "zero_tok_items": zero, "final": final}


def grid(base_mean: float, low_mean: float, high_mean: float, target: float = SAVING_MIN) -> list[dict]:
    """상위 비율 f 격자에서 평균 비용 근사의 d_max(배정이 무작위일 때의 기대값)."""
    out = []
    for k in range(1, 10):
        f = k / 10
        out.append({"f": f, "d_max": (1 - target) * base_mean - (1 - f) * low_mean - f * high_mean})
    return out
