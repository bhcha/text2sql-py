"""경로 판정 규칙(route-judge/1): 경계값, 스터디 규칙, 비용 지킴, 쓸 수 없는 측정의 까닭."""

import copy

import pytest

from t2s.eval import route_judge as J


def test_bird_boundaries():
    # d=13이면 p가 작아도 차이 없음
    assert J.verdict(13, 0, study=False)[0] == "no_difference"
    v, p = J.verdict(14, 0, study=False)
    assert v == "harness_better" and p < 0.05
    # d=14이지만 p ≥ 0.05
    v, p = J.verdict(60, 46, study=False)
    assert v == "no_difference" and p >= 0.05
    v, p = J.verdict(0, 14, study=False)
    assert v == "other_better" and p < 0.05
    assert J.verdict(0, 0, study=False) == ("no_difference", 1.0)


def test_study_rule_uses_p_only():
    assert J.verdict(8, 0, study=True)[0] == "harness_better"  # d=8 ≤ 13이어도 p < 0.05면 판정
    assert J.verdict(0, 8, study=True)[0] == "other_better"
    v, p = J.verdict(10, 3, study=True)
    assert v == "inconclusive" and round(p, 3) == 0.092
    assert J.decide_route("inconclusive", "single", COST, COST)[0] is None


COST = {"calls": 10, "input_tokens": 1000, "output_tokens": 100, "item_seconds": 50.0, "elapsed_seconds": 60}


def test_cost_guard():
    cheap = {**COST, "input_tokens": 100, "output_tokens": 10, "item_seconds": 5.0}
    dear = {**COST, "input_tokens": 5000, "output_tokens": 500, "item_seconds": 90.0}
    assert J.decide_route("no_difference", "single", COST, cheap)[0] == "single"
    route, why = J.decide_route("no_difference", "single", COST, dear)
    assert route == "harness" and "더 싸지 않다" in why
    # 토큰만 크고 시간은 작으면 후보
    assert J.decide_route("no_difference", "single", COST, {**dear, "item_seconds": 1.0})[0] == "single"
    route, why = J.decide_route("no_difference", "partial", COST, {**COST, "input_tokens": None})
    assert route == "partial" and "토큰 미확인" in why
    assert J.decide_route("harness_better", "single", COST, cheap)[0] == "harness"
    assert J.decide_route("other_better", "partial", COST, dear)[0] == "partial"


def _side(route="harness"):
    meta = {
        "dataset": "bird-minidev",
        "questions": {"sha256": "q" * 64},
        "count": 500,
        "sample": False,
        "llm": {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning_effort": "medium"},
        "git": {"head": "a" * 40, "src_tree": "t" * 40, "dirty": False},
        "versions_start": {"codex": "codex-cli 0.200.0"},
        "route": {"applied": route, "overrides": None},
    }
    manifest = {
        "halted": None,
        "database_unchanged": True,
        "llm_totals": {"tool_calls": 0},
        "versions_end": {"codex": "codex-cli 0.200.0"},
    }
    score = {
        "sample": False,
        "status_counts": {"not_started": 0},
        "leak": {"total": 0},
        "cross_check": True,
        "data_unchanged": True,
    }
    return J.RunSide("r-" + route, meta, manifest, score, {}, [], {})


def test_good_pair_is_usable():
    assert J.usability(_side(), _side("single")) == []
    assert J.usability(_side(), _side("partial")) == []


@pytest.mark.parametrize(
    ("which", "path", "value", "reason"),
    [
        ("c", "meta.sample", True, "소표본"),
        ("h", "meta.llm", {"backend": "fake", "model": "fake-model", "reasoning_effort": "none"}, "가짜 백엔드"),
        ("c", "meta.git", {"head": "a" * 40, "src_tree": "t" * 40, "dirty": True}, "미커밋 코드"),
        ("c", "meta.git", {"head": "b" * 40, "src_tree": "t" * 40, "dirty": False}, "커밋이 다름"),
        ("c", "meta.questions", {"sha256": "x" * 64}, "문항이 다름"),
        ("c", "meta.count", 499, "문항이 다름"),
        ("c", "meta.dataset", "olist", "데이터셋이 다름"),
        ("h", "manifest.halted", {"reason": "usage_limit"}, "멈췄음"),
        ("c", "score.status_counts", {"not_started": 3}, "시작하지 않은"),
        ("c", "score.leak", {"total": 1}, "누설"),
        ("h", "score.leak", None, "누설"),
        ("c", "score.cross_check", False, "채점기 대조"),
        ("h", "manifest.database_unchanged", False, "DB가 바뀜"),
        ("c", "manifest.llm_totals", {"tool_calls": 2}, "도구 호출"),
        ("h", "manifest.versions_end", {"codex": "codex-cli 0.201.0"}, "CLI 판이 측정 중 바뀜"),
        ("c", "meta.versions_start", {"codex": "codex-cli 0.199.0"}, "CLI 판"),
        ("c", "meta.llm", {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning_effort": "high"}, "추론 단계"),
    ],
)
def test_unusable_reasons(which, path, value, reason):
    h, c = _side(), _side("single")
    target = h if which == "h" else c
    obj = getattr(target, path.split(".")[0])
    obj[path.split(".")[1]] = copy.deepcopy(value)
    reasons = J.usability(h, c)
    assert any(reason in r for r in reasons), reasons


def test_roles_must_be_harness_and_candidate():
    assert any("하네스 쪽" in r for r in J.usability(_side("single"), _side("single")))
    assert any("후보 run의 경로" in r for r in J.usability(_side(), _side()))
    h = _side()
    h.meta["route"]["overrides"] = {"example_rows": 0}
    assert any("하네스 쪽" in r for r in J.usability(h, _side("single")))
