import pytest

from t2s.eval.report import sign_test_p
from t2s.eval.routing import sim


def row(kind, ex, tok, ms=None, err=None, empty=None, null=None, exec_ms=None):
    n = len(ex)
    return {
        "kind": kind,
        "ex": list(ex),
        "tok": list(tok),
        "ms": list(ms or [10.0] * n),
        "exec_ms": list(exec_ms or [1.0] * n),
        "err": list(err or [False] * n),
        "empty": list(empty or [False] * n),
        "null": list(null or [False] * n),
        "f1": [float(x) for x in ex],
    }


# 문항 6개, 조합 3개. cheap < mid < base(기준) 순서로 싸다.
T = {
    "cheap": row(
        "pure",
        [1, 0, 0, 1, 0, 1],
        [10] * 6,
        err=[False, True, False, False, False, False],
        empty=[False, False, True, False, False, False],
        null=[False, False, False, False, True, False],
    ),
    "mid": row("p3", [1, 1, 0, 1, 1, 0], [20] * 6, err=[False, False, True, False, False, False]),
    "base": row("pure", [1, 1, 1, 0, 1, 1], [100] * 6),
}
POS = list(range(6))


def test_fixed_sums():
    r = sim.evaluate(T, {}, sim.fixed("base"), POS)
    assert (r["correct"], r["tok"], r["ms"]) == (5, 600, 60)


def test_two_stage_costs_and_ex():
    c = {"name": "x", "kind": "cas", "sig": "E", "chain": ("cheap", "base")}
    r = sim.evaluate(T, {}, c, POS)
    # 위치 1만 오류 → 올림
    assert r["escalated"] == [1, 0]
    assert r["tok"] == 6 * 10 + 100
    assert r["ms"] == 6 * 10 + 6 * 1.0 + 10  # 순수 중간 단계는 실행 시간을 더한다
    assert r["correct"] == 1 + 1 + 0 + 1 + 0 + 1
    assert r["final"][1] == "base"


def test_signal_sets_differ():
    up = {}
    for s in sim.SIGNAL_SETS:
        c = {"name": s, "kind": "cas", "sig": s, "chain": ("cheap", "base")}
        up[s] = sim.evaluate(T, {}, c, POS)["escalated"][0]
    assert up == {"E": 1, "EZ": 2, "EZN": 3}


def test_three_stage_stops():
    c = {"name": "x", "kind": "cas", "sig": "EZ", "chain": ("cheap", "mid", "base")}
    r = sim.evaluate(T, {}, c, POS)
    # 위치 1(오류), 2(0행) → mid. mid에서 위치 2만 오류 → base
    assert r["reached"] == [6, 2, 1]
    assert r["escalated"] == [2, 1, 0]
    assert r["tok"] == 60 + 2 * 20 + 100
    assert r["final"][1] == "mid" and r["final"][2] == "base"
    assert r["correct"] == 1 + 1 + 1 + 1 + 0 + 1


def test_all_escalate_equals_last():
    c = {"name": "all", "kind": "all", "sig": None, "chain": ("cheap", "mid", "base")}
    r = sim.evaluate(T, {}, c, POS)
    assert r["correct"] == sum(T["base"]["ex"])
    assert r["tok"] == 60 + 120 + 600


def test_mismatch_adds_pair_cost():
    pairs = {"mid": {"tok": [5] * 6, "ms": [2.0] * 6, "disagree": [True, False, False, False, False, False]}}
    c = {"name": "d", "kind": "dis", "sig": "D", "chain": ("mid", "base")}
    r = sim.evaluate(T, pairs, c, POS)
    assert r["escalated"][0] == 1
    assert r["tok"] == 6 * 20 + 6 * 5 + 100
    assert r["ms"] == 6 * 10 + 6 * 2.0 + 10  # P3 단계는 실행 시간을 더하지 않는다
    c2 = {**c, "sig": "D+EZN"}
    assert sim.evaluate(T, pairs, c2, POS)["escalated"][0] == 2  # 위치 0(불일치) + 위치 2(오류)


def test_chain_building():
    order = [f"c{i}" for i in range(8)]
    ch = sim.chains(order)
    assert sum(1 for c in ch if len(c) == 2) == 28
    assert sum(1 for c in ch if len(c) == 3) == 56
    for c in ch:
        assert [order.index(x) for x in c] == sorted(order.index(x) for x in c)
    cands = sim.candidates(order, ("c0",))
    assert sum(1 for c in cands if c["kind"] == "cas") == 84 * 3
    assert sum(1 for c in cands if c["kind"] == "dis") == (7 + 21) * 2  # c0으로 시작하는 사슬


def test_cost_order_uses_tune_only():
    assert sim.cost_order(T, POS) == ["cheap", "mid", "base"]


def test_oracles():
    r = sim.oracle_pick(T, ["cheap", "mid", "base"], POS)
    assert r["correct"] == 6
    assert r["final"][0] == "cheap" and r["final"][1] == "mid" and r["final"][2] == "base"
    none = {"a": row("pure", [0, 0], [5, 9]), "b": row("pure", [0, 0], [7, 3])}
    r2 = sim.oracle_pick(none, ["a", "b"], [0, 1])
    assert r2["final"] == {0: "a", 1: "b"}
    c = {"name": "o", "kind": "oracle-esc", "sig": None, "chain": ("cheap", "base")}
    r3 = sim.evaluate(T, {}, c, POS)
    assert r3["escalated"][0] == 3  # cheap이 틀린 위치 1, 2, 4


def test_random_is_seeded_and_keeps_rate():
    c = {"name": "x", "kind": "cas", "sig": "EZN", "chain": ("cheap", "base")}
    a = sim.random_baseline(T, {}, c, POS, [0.5], seeds=range(20))
    b = sim.random_baseline(T, {}, c, POS, [0.5], seeds=range(20))
    assert a == b
    assert a["tok_mean"] == 60 + 3 * 100  # 늘 3문항을 올린다


def test_loss_rule():
    assert sim.allowed_loss(251) == pytest.approx(6.526)
    assert sim.allowed_loss(249) == pytest.approx(6.474)
    for n in (251, 249):
        assert sim.loss_ok(100, 94, n)
        assert not sim.loss_ok(100, 93, n)
    assert sim.loss_ok(100, 105, 251)
    assert sim.saving(85, 100) == pytest.approx(0.15)
    assert sim.saving(85, 100) >= sim.SAVING_MIN - 1e-12


def _res(name, correct, tok, stages=2, n=250):
    return {"name": name, "correct": correct, "tok": tok, "stages": stages, "n": n}


def test_select_rules():
    base = {"correct": 100, "tok": 1000}
    picked, least = sim.select([_res("a", 90, 100), _res("b", 92, 500)], base)
    assert picked is None and least["name"] == "b"
    picked, _ = sim.select([_res("b", 95, 700, 3), _res("a", 95, 700, 2), _res("c", 99, 900)], base)
    assert picked["name"] == "a"  # 같은 절감이면 단계 적은 것
    picked, _ = sim.select([_res("b", 96, 700), _res("a", 95, 700)], base)
    assert picked["name"] == "b"  # 그다음 정답 많은 것


def _cross_table():
    # 위치 0~9: A 묶음, 10~19: B 묶음. cheap은 A에서는 기준과 같이 맞히고 B에서는 많이 틀린다.
    ex_base = [1] * 20
    ex_cheap = [1] * 10 + [0] * 10
    return {
        "cheap": row("p3", ex_cheap, [10] * 20),
        "base": row("pure", ex_base, [100] * 20),
    }


def test_cross_fold_fails_on_confirm():
    T2 = _cross_table()
    groups = {"A": list(range(10)), "B": list(range(10, 20))}
    res = sim.procedure(T2, {}, groups, "base", ["base"], seeds=range(5))
    d1 = res["directions"]["1"]
    # 조정 A에서는 cheap 단독과 같은 사슬이 통과하지만 확인 B에서는 손실이 커서 탈락
    assert d1["picked"] is not None
    assert d1["picked_conf"]["loss"] > sim.allowed_loss(10)
    assert d1["pass"] is False
    assert res["both_pass"] is False and res["stage2"] is None


def test_sign_counts():
    final = {i: "cheap" for i in POS}
    cmp = sim.compare_to_base(T, "base", final)
    assert (cmp["newly_correct"], cmp["regressed"]) == (1, 3)
    assert cmp["p"] == pytest.approx(sign_test_p(1, 3))
    assert sign_test_p(20, 3) == pytest.approx(0.00048828125, rel=1e-12)


def test_breakeven():
    assert sim.breakeven(1000, 700, 10) == pytest.approx((850 - 700) / 10)
    assert sim.breakeven(1000, 900, 10) < 0
    g = sim.grid(100.0, 50.0, 100.0)
    assert g[0]["f"] == 0.1 and g[0]["d_max"] == pytest.approx(85 - 0.9 * 50 - 0.1 * 100)
    ps = sim.perfect_split(T, "cheap", "base", POS)
    assert ps["correct"] == 6 and ps["f_high"] == pytest.approx(3 / 6)
    assert ps["assigned_tok"] == 3 * 10 + 3 * 100


def test_missing_tokens_count_as_zero():
    T2 = {"base": row("pure", [1, 0], [0, 100])}  # 위치 0 토큰 미기록(0)
    assert sim.evaluate(T2, {}, sim.fixed("base"), [0, 1])["tok"] == 100
