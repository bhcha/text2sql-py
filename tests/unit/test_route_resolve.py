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
            model_arg=None, forced=None, dataset="bird-minidev", cli=CLI, effort_source="conversation"):  # fmt: skip
    return RT.resolve_route(
        profile_name=name,
        profiles=profs,
        base=BASE,
        llm_backend=backend,
        llm_model=model,
        llm_effort=effort,
        llm_model_source=source,
        llm_effort_source=effort_source,
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
    # 추론 단계를 말하지 않았으면 기본값 medium으로 보고 맞춘다. 맞는 프로필이 없는 모델은 전체 P3
    d = resolve(p, name=None, model_arg="gpt-5.6-luna", effort_source="default-medium")
    assert (d.applied, d.source, d.reason, d.assumed_effort) == ("single", "profile-match", "measured", "medium")
    d = resolve(p, name=None, model_arg="gpt-5.6-luna", effort_source="config-default")
    assert (d.applied, d.source, d.reason) == ("harness", "default", "no_effort")
    assert resolve(p, name=None, model="gpt-5.6-sol", model_arg="gpt-5.6-sol").reason == "no_profile"
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
    p = profiles(
        route="partial",
        pipeline={"value_search": False, "example_rows": 0},
        route_basis=[basis(route="partial", overrides='{"example_rows":0,"value_search":false}')],
    )
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
    d = resolve(
        profiles(
            route="partial",
            pipeline={"example_rows": 0, "column_profile": False},
            route_basis=[basis(route="partial", overrides='{"column_profile":false,"example_rows":0}')],
        )
    )
    assert R.route_label({"route": d.to_json()}) == " / 경로 " + RT.route_name(d.applied, d.overrides)
    assert R.route_warnings({"route": {"warnings": ["a"]}}) == ["a"] and R.route_warnings({}) == []


def test_partial_overrides_must_match_basis():
    pipe = {"value_search": False, "example_rows": 0}
    d = resolve(profiles(route="partial", pipeline=pipe))  # 근거에 overrides 칸 없음
    assert (d.applied, d.source, d.reason, d.overrides) == ("harness", "default", "basis_mismatch", None)
    for other in (
        '{"example_rows":1,"value_search":false}',
        '{"example_rows":0}',
        '{"example_rows": 0, "value_search": false}',
    ):
        d = resolve(profiles(route="partial", pipeline=pipe, route_basis=[basis(route="partial", overrides=other)]))
        assert (d.applied, d.reason) == ("harness", "basis_mismatch") and d.settings == BASE
    same = '{"example_rows":0,"value_search":false}'
    d = resolve(profiles(route="partial", pipeline=pipe, route_basis=[basis(route="partial", overrides=same)]))
    assert (d.applied, d.source, d.reason) == ("partial", "profile", "measured")
    assert d.overrides == {"example_rows": 0, "value_search": False} and d.basis["overrides"] == same


# ── 모델 이름으로 맞추기(차례 2-2) ─────────────────────────


def by_name(profs, **kw):
    kw.setdefault("model_arg", kw.get("model", "gpt-5.6-luna"))
    return resolve(profs, name=None, **kw)


def test_model_name_exact_match_follows_profile_route():
    d = by_name(profiles())
    assert (d.applied, d.source, d.reason, d.profile, d.warnings) == ("single", "profile-match", "measured", "luna", [])
    assert d.basis["model"] == "gpt-5.6-luna" and d.settings == BASE and d.matches == ["luna"]
    j = d.to_json()
    assert j["source"] == "profile-match" and j["profile"] == "luna" and j["matches"] == ["luna"]
    assert j["single_rules"] == "single/1"
    assert RT.shown_route_lines(d) == [
        "경로 single(단일 호출) · 프로필 luna(모델 이름으로 맞춤) · 근거 2026-11-21 bird-minidev"
    ]
    # 이름으로 고른 것과 경로·근거·경고가 같다
    named = resolve(profiles())
    assert (named.applied, named.basis, named.warnings) == (d.applied, d.basis, d.warnings)


def test_model_name_other_effort_or_backend_goes_full():
    for kw in ({"effort": "high"}, {"backend": "claude-code"}, {"model": "gpt-5.6-luna-x"}):
        d = by_name(profiles(), **kw)
        assert (d.applied, d.source, d.reason, d.profile) == ("harness", "default", "no_profile", None), kw
        assert RT.shown_route_lines(d) == []


def test_model_name_alias_session_fake_go_full():
    p = profiles()
    assert by_name(p, model_arg="luna").reason == "alias"
    assert by_name(p, model_arg="gpt-5.6-luna-latest", model="gpt-5.6-luna").reason == "alias"
    for source in ("session-self", "session-transcript", "config-default"):
        d = by_name(p, source=source)
        assert (d.applied, d.source, d.reason) == ("harness", "default", "session")
    d = by_name(p, backend="fake", model="fake-model", model_arg=None)
    assert (d.applied, d.reason) == ("harness", "no_profile")
    d = by_name(p, backend="fake")
    assert (d.applied, d.reason) == ("harness", "no_profile")
    # --model 없이(기본 프로필 등) 정한 모델은 맞추지 않는다
    assert resolve(p, name=None, model_arg=None).reason == "no_profile"


def test_model_name_needs_effort_from_conversation_or_default():
    for es in ("config-default", "session-env"):
        d = by_name(profiles(), effort_source=es)
        assert (d.applied, d.reason) == ("harness", "no_effort")
        assert d.assumed_effort is None and d.to_json()["assumed_effort"] is None
    # default-medium인데 추론 단계가 medium이 아니면(앞뒤가 맞지 않음) 맞추지 않는다
    d = by_name(profiles(), effort="high", effort_source="default-medium")
    assert (d.applied, d.reason, d.assumed_effort) == ("harness", "no_effort", None)


def test_model_name_default_medium_single_record():
    d = by_name(profiles(), effort_source="default-medium")
    assert (d.applied, d.source, d.reason, d.profile, d.matches) == (
        "single", "profile-match", "measured", "luna", ["luna"]
    )  # fmt: skip
    assert d.assumed_effort == "medium" and d.to_json()["assumed_effort"] == "medium"
    assert RT.shown_route_lines(d) == [
        (
            "경로 single(단일 호출) · 프로필 luna(모델 이름으로 맞춤) · 근거 2026-11-21 bird-minidev"
            " · 추론 단계는 기본값 medium으로 봄"
        )
    ]
    # 추론 단계를 대화로 정한 때는 기본값 표시가 없다
    d = by_name(profiles())
    assert d.assumed_effort is None and d.to_json()["assumed_effort"] is None
    assert "기본값" not in RT.shown_route_lines(d)[0]


def test_model_name_default_medium_harness_record():
    p = {"h": {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning_effort": "medium", "route": "harness",
               "route_basis": [basis(route="harness")]}}  # fmt: skip
    d = by_name(p, effort_source="default-medium")
    assert (d.applied, d.source, d.reason, d.profile, d.assumed_effort) == (
        "harness", "profile-match", "measured", "h", "medium"
    )  # fmt: skip
    assert RT.shown_route_lines(d) == [
        (
            "경로 harness(전체 P3) · 프로필 h(모델 이름으로 맞춤) · 근거 2026-11-21 bird-minidev"
            " · 추론 단계는 기본값 medium으로 봄"
        )
    ]


def test_model_name_default_medium_without_record_goes_full():
    d = by_name(profiles(), model="gpt-5.6-sol", effort_source="default-medium")
    assert (d.applied, d.source, d.reason, d.profile) == ("harness", "default", "no_profile", None)
    assert d.to_json()["assumed_effort"] == "medium"
    # 기록이 없어 전체 P3로 도는 것과 추론 단계를 기본값으로 본 것이 화면에 나온다
    no_record = ["경로 harness(전체 P3) · 까닭 프로필 없이 정한 모델 · 추론 단계는 기본값 medium으로 봄"]
    assert RT.shown_route_lines(d) == no_record
    # 기본값으로 본 medium의 기록이 없고 high 기록만 있으면 전체 P3
    d = by_name(profiles(reasoning_effort="high", route_basis=[basis(reasoning_effort="high")]),
                effort_source="default-medium")  # fmt: skip
    assert (d.applied, d.source, d.reason) == ("harness", "default", "no_profile")
    # 근거가 맞지 않는 기록뿐이면 전체 P3(그 까닭과 기본값 표시)
    d = by_name(profiles(route_basis=[basis(model="gpt-5.6-luna-2")]), effort_source="default-medium")
    assert (d.applied, d.reason) == ("harness", "basis_mismatch")
    assert RT.shown_route_lines(d) == [
        "경로 harness(전체 P3) · 까닭 근거와 백엔드·모델·추론 단계가 다름 · 추론 단계는 기본값 medium으로 봄"
    ]


def test_model_name_explicit_effort_differs_from_record_goes_full():
    d = by_name(profiles(), effort="high")
    assert (d.applied, d.source, d.reason, d.profile, d.assumed_effort) == (
        "harness", "default", "no_profile", None, None
    )  # fmt: skip
    d = by_name(profiles(), effort="low", effort_source="conversation")
    assert (d.applied, d.reason) == ("harness", "no_profile")


def test_model_name_match_without_valid_basis_goes_full_with_reason():
    d = by_name(profiles(route_basis=[basis(model="gpt-5.6-luna-2")]))
    assert (d.applied, d.source, d.reason, d.requested, d.profile) == (
        "harness", "default", "basis_mismatch", "single", "luna"
    )  # fmt: skip
    assert RT.shown_route_lines(d) == ["경로 harness(전체 P3) · 까닭 근거와 백엔드·모델·추론 단계가 다름"]
    assert by_name(profiles(route_basis=[])).reason == "no_basis"
    d = by_name(profiles(route=None, route_basis=[basis()]))
    assert (d.applied, d.reason) == ("harness", "no_route") and RT.shown_route_lines(d) == []


def _two(route_b, **b_kw):
    p = profiles()
    second = {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning_effort": "medium", "route": route_b}
    second.update(b_kw)
    second.setdefault("route_basis", [basis(route=route_b)])
    return {"luna": p["luna"], "luna-2": second, "session": p["session"]}


def test_model_name_two_matches_with_different_routes_go_full():
    d = by_name(_two("harness"))
    assert (d.applied, d.source, d.reason, d.profile) == ("harness", "default", "ambiguous", None)
    assert d.matches == ["luna", "luna-2"] and d.to_json()["matches"] == ["luna", "luna-2"]
    assert RT.shown_route_lines(d) == ["경로 harness(전체 P3) · 까닭 맞는 프로필 여럿의 경로가 다름(luna, luna-2)"]
    over = {"value_search": False}
    import json as _json

    text = _json.dumps(over, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    d = by_name(_two("partial", pipeline=over, route_basis=[basis(route="partial", overrides=text)]))
    assert (d.applied, d.reason) == ("harness", "ambiguous")


def test_model_name_two_matches_with_same_route_pick_first_in_file():
    d = by_name(_two("single", route_basis=[basis(measured="2026-12-01")]))
    assert (d.applied, d.source, d.profile, d.matches) == ("single", "profile-match", "luna", ["luna", "luna-2"])
    assert d.basis["measured"] == "2026-11-21"
    # 근거가 맞지 않는 프로필은 견주는 데 세지 않는다
    d = by_name(_two("harness", route_basis=[basis(route="harness", reasoning_effort="high")]))
    assert (d.applied, d.source, d.profile, d.matches) == ("single", "profile-match", "luna", ["luna"])


def test_model_name_only_harness_record():
    p = {"h": {"backend": "codex-cli", "model": "gpt-5.6-luna", "route": "harness",
               "route_basis": [basis(route="harness")]}}  # fmt: skip
    d = by_name(p)
    assert (d.applied, d.source, d.reason, d.profile) == ("harness", "profile-match", "measured", "h")
    assert RT.shown_route_lines(d) == [
        "경로 harness(전체 P3) · 프로필 h(모델 이름으로 맞춤) · 근거 2026-11-21 bird-minidev"
    ]


def test_model_name_stale_cli_keeps_route_and_warns():
    d = by_name(profiles(), cli="codex-cli 0.300.0", dataset="olist")
    assert (d.applied, d.source) == ("single", "profile-match")
    assert d.warnings == [
        f"경로 근거가 낡았습니다(CLI {CLI} → codex-cli 0.300.0)",
        "근거 데이터셋이 다릅니다(bird-minidev → olist). 잠정 경로입니다",
    ]
    assert RT.shown_route_lines(d)[1] == (
        f"주의: 경로 근거가 낡았습니다(CLI {CLI} → codex-cli 0.300.0). 경로는 그대로 single입니다. 다시 재려면 /newmodel"
    )


def test_model_name_never_matches_reference_profile():
    ref = {"backend": "codex-cli", "model": "gpt-5.6-terra", "reasoning_effort": "medium", "route": "single",
           "route_basis": [basis(model="gpt-5.6-terra")]}  # fmt: skip
    d = by_name({"p3-ref": ref}, model="gpt-5.6-terra")
    assert (d.applied, d.reason) == ("harness", "no_profile")


def test_model_name_partial_match():
    over = {"refine_rounds": 0}
    import json as _json

    text = _json.dumps(over, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    d = by_name(profiles(route="partial", pipeline=over, route_basis=[basis(route="partial", overrides=text)]))
    assert (d.applied, d.source, d.overrides) == ("partial", "profile-match", over)
    assert d.settings.refine_rounds == 0


def test_model_name_bad_route_value_in_matching_profile_is_error():
    with pytest.raises(RT.RouteError):
        by_name(profiles(route="fast"))


def test_forced_ignores_model_name_match():
    d = by_name(profiles(), forced="harness")
    assert (d.applied, d.source, d.reason) == ("harness", "forced", "forced")
