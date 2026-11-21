"""경로 규칙(pipeline/route.py resolve_route)의 차례 1~7과 기록 쪽 상수·꼬리표."""

import tomllib
from pathlib import Path

import pytest

from t2s.cli import main as cli_main
from t2s.pipeline import route as RT
from t2s.pipeline.run import PipelineSettings
from t2s.runner import records as R

REPO = Path(__file__).resolve().parents[2]
BASE = PipelineSettings()
CLI = "codex-cli 0.200.0"


def basis(**kw):
    b = {
        "route": "single",
        "backend": "codex-cli",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "dataset": "bird-minidev",
        "measured": "2026-11-21",
        "cli": CLI,
    }
    b.update(kw)
    return b


def profiles(**prof):
    p = {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning_effort": "medium", "route": "single"}
    p.update(prof)
    if "route_basis" not in prof:
        p["route_basis"] = [basis(route=p.get("route", "single"))]
    return {"luna": p, "session": {"backend": "auto"}}


def resolve(profs, *, name="luna", backend="codex-cli", model="gpt-5.6-luna", effort="medium", source="conversation",
            model_arg=None, forced=None, dataset="bird-minidev", cli=CLI):  # fmt: skip
    return RT.resolve_route(
        profile_name=name,
        profiles=profs,
        base=BASE,
        llm_backend=backend,
        llm_model=model,
        llm_effort=effort,
        llm_model_source=source,
        model_arg=model_arg,
        forced=forced,
        dataset=dataset,
        cli_version=lambda b: cli,
    )


def test_measured_single():
    d = resolve(profiles())
    assert (d.applied, d.source, d.reason, d.warnings) == ("single", "profile", "measured", [])
    assert d.basis["model"] == "gpt-5.6-luna" and d.settings == BASE
    j = d.to_json()
    assert j["single_rules"] == "single/1" and j["pair"] is None and "settings" not in j
    assert RT.route_lines(d) == ["경로 single(단일 호출) · 프로필 luna · 근거 2026-11-21 bird-minidev"]


def test_session_alias_and_full_name_go_full():
    p = profiles()
    assert resolve(p, name=None, source="session-self").reason == "session"
    assert resolve(p, name=None, source="session-transcript").reason == "session"
    assert resolve(p, name="session", source="session-transcript").reason == "session"
    d = resolve(p, name=None, model_arg="luna")
    assert (d.applied, d.reason) == ("harness", "alias")
    assert resolve(p, name=None, model_arg="gpt-5.6-luna").reason == "no_profile"
    assert resolve(p, name=None, backend="fake", model="fake-model").reason == "no_profile"
    d = resolve(p, name=None, source="session-self")
    assert d.applied == "harness" and d.source == "default" and d.settings == BASE
    assert RT.route_lines(d) == ["경로 harness(전체 P3) · 까닭 세션 모델"]


def test_profile_without_route_or_harness():
    p = profiles()
    del p["luna"]["route"]
    assert resolve(p).reason == "no_route"
    assert resolve(profiles(route="harness")).reason == "no_route"


def test_alias_model_in_profile():
    p = profiles(model="gpt-5.6-luna-latest")
    p["luna"]["route_basis"] = [basis(model="gpt-5.6-luna-latest")]
    assert resolve(p, model="gpt-5.6-luna-latest").reason == "alias"
    p = profiles(model="luna")
    assert resolve(p, model="luna").reason == "alias"


def test_no_basis_and_basis_route_differs():
    assert resolve(profiles(route_basis=[])).reason == "no_basis"
    p = profiles()
    del p["luna"]["route_basis"]
    assert resolve(p).reason == "no_basis"
    d = resolve(profiles(route_basis=[basis(route="partial")]))
    assert (d.applied, d.reason) == ("harness", "no_basis")
    # 마지막 항목만 본다
    d = resolve(profiles(route_basis=[basis(route="harness"), basis(route="single")]))
    assert d.applied == "single"


@pytest.mark.parametrize(
    ("key", "value"),
    [("model", "gpt-5.6-lunA"), ("model", "gpt-5.6-luna "), ("reasoning_effort", "high"), ("backend", "claude-code")],
)
def test_basis_mismatch_by_one_character(key, value):
    d = resolve(profiles(route_basis=[basis(**{key: value})]))
    assert (d.applied, d.reason) == ("harness", "basis_mismatch")


def test_stale_cli_warns_but_keeps_route():
    d = resolve(profiles(), cli="codex-cli 0.201.0")
    assert d.applied == "single" and d.warnings == ["경로 근거가 낡았습니다(CLI codex-cli 0.200.0 → codex-cli 0.201.0)"]
    assert RT.route_lines(d)[1] == (
        "주의: 경로 근거가 낡았습니다(CLI codex-cli 0.200.0 → codex-cli 0.201.0). 경로는 그대로 single입니다. "
        "다시 재려면 /newmodel"
    )
    d = resolve(profiles(), cli=None)
    assert d.applied == "single" and "알 수 없음" in d.warnings[0]


def test_other_dataset_warns():
    d = resolve(profiles(), dataset="olist")
    assert d.applied == "single"
    assert d.warnings == ["근거 데이터셋이 다릅니다(bird-minidev → olist). 잠정 경로입니다"]


def test_partial_overrides():
    p = profiles(route="partial", pipeline={"value_search": False, "example_rows": 0})
    d = resolve(p)
    assert d.applied == "partial" and d.overrides == {"example_rows": 0, "value_search": False}
    assert d.settings.value_search is False and d.settings.example_rows == 0 and d.settings.refine_rounds == 3
    assert RT.route_lines(d)[0].startswith("경로 partial(example_rows=0, value_search=false) · 프로필 luna")
    with pytest.raises(RT.RouteError):
        resolve(profiles(route="partial", pipeline={"keyword_attempts": 1}))
    with pytest.raises(RT.RouteError):
        resolve(profiles(route="partial", pipeline={"value_search": "no"}))
    with pytest.raises(RT.RouteError):
        resolve(profiles(route="partial"))
    with pytest.raises(RT.RouteError):
        resolve(profiles(route="fast"))


def test_forced():
    p = profiles()
    d = resolve(p, name=None, forced="single")
    assert (d.applied, d.source, d.reason) == ("single", "forced", "forced")
    assert resolve(p, name=None, forced="harness").applied == "harness"
    with pytest.raises(RT.RouteError):
        resolve(p, name=None, forced="partial")
    pp = profiles(route="partial", pipeline={"refine_rounds": 0})
    d = resolve(pp, forced="partial")
    assert d.applied == "partial" and d.settings.refine_rounds == 0
    assert RT.route_lines(d)[0] == "경로 partial(refine_rounds=0) · 측정용 강제"


def test_route_constants_agree():
    assert RT.ROUTES == R.ROUTES == cli_main.ROUTES


def test_reference_profiles_are_harness():
    raw = tomllib.loads((REPO / "harness.toml").read_text(encoding="utf-8"))
    for name in ("p3-ref", "p3-claude"):
        prof = raw["profiles"][name]
        assert "route" not in prof and "route_basis" not in prof
        d = resolve(raw["profiles"], name=name, backend=prof["backend"], model=prof["model"])
        assert d.applied == "harness" and d.settings == PipelineSettings.from_mapping(raw["pipeline"])
    assert PipelineSettings.from_mapping(raw["pipeline"]) == PipelineSettings()


def test_records_route_helpers():
    assert R.route_of({}) == "harness" and R.route_of(None) == "harness"
    assert R.route_of({"route": {"applied": "single"}}) == "single"
    assert R.route_of({"route": {"applied": "weird"}}) == "harness"
    assert R.route_label({}) == "" and R.route_label({"route": {"applied": "harness"}}) == ""
    assert R.route_label({"route": {"applied": "single"}}) == " / 경로 single(단일 호출)"
    meta = {"route": {"applied": "partial", "overrides": {"example_rows": 0, "column_profile": False}}}
    assert R.route_label(meta) == " / 경로 partial(example_rows=0, column_profile=false)"
    d = resolve(profiles(route="partial", pipeline={"example_rows": 0, "column_profile": False}))
    assert R.route_label({"route": d.to_json()}) == " / 경로 " + RT.route_name(d.applied, d.overrides)
    assert R.route_warnings({"route": {"warnings": ["a"]}}) == ["a"] and R.route_warnings({}) == []
