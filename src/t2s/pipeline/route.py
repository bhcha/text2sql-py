"""모델 단위 경로(single | harness | partial)를 정한다. 파일을 읽거나 쓰지 않는 순수 함수다.

자체 코드다. 프로필(harness.toml `[profiles.<이름>]`)의 `route` 칸과 근거 배열 `route_basis`를 보고, run·ask를 시작할 때
`resolve_route`가 위에서부터 다음 차례로 정한다.
1. 측정용 강제(`--route`)면 그 경로.
2. 프로필을 이름(`--profile`)으로 고르지 않았으면 전체 P3(세션 모델·별칭·`--model` 전체 이름·가짜 백엔드).
3. 고른 프로필에 `route`가 없거나 harness면 전체 P3.
4. 프로필의 모델 이름이 별칭이거나 `latest`를 품으면 전체 P3.
5. route가 single·partial인데 근거가 없거나 마지막 근거의 route가 다르면 전체 P3.
6. 마지막 근거의 백엔드·모델·추론 단계가 프로필과 글자 그대로 같지 않으면 전체 P3(사용자 결정: 한 글자라도 다르면 근거
   없는 조합).
7. 그 밖에는 프로필의 경로. 근거의 CLI 판·데이터셋이 지금과 다르면 경고만 남기고 경로는 바꾸지 않는다.
근거의 수치는 판정에 쓰지 않고 조합·CLI 판 대조에만 쓴다. 문항 조건(힌트) 방식의 대조는 부르는 쪽(cli/run_cmds.py)이 한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from t2s.llm.select import ALIASES
from t2s.pipeline.run import PipelineSettings, check_values
from t2s.pipeline.single import SINGLE_RULES_VERSION

SINGLE, HARNESS, PARTIAL = "single", "harness", "partial"
ROUTES = (SINGLE, HARNESS, PARTIAL)  # t2s.runner.records.ROUTES·cli/main.py ROUTES와 같다(test_route_resolve가 대조)
PARTIAL_KEYS = ("example_rows", "value_search", "column_profile", "refine_rounds")
BASIS_KEY = "route_basis"
BASIS_MATCH = ("backend", "model", "reasoning_effort")
SESSION_SOURCES = ("session-self", "session-transcript", "config-default", "session-env")
REASON_TEXT = {
    "forced": "측정용 강제",
    "session": "세션 모델",
    "alias": "별칭",
    "no_profile": "프로필 없이 정한 모델",
    "no_route": "프로필에 경로 없음",
    "no_basis": "경로 근거 없음",
    "basis_mismatch": "근거와 백엔드·모델·추론 단계가 다름",
    "measured": "측정 근거",
}


class RouteError(ValueError):
    pass


@dataclass
class RouteDecision:
    applied: str
    requested: str | None
    source: str  # default | profile | forced
    reason: str
    profile: str | None
    basis: dict | None = None
    warnings: list[str] = field(default_factory=list)
    overrides: dict | None = None
    settings: PipelineSettings | None = None  # 적용할 설정(single이면 공통 설정, 최종 실행 시간 제한에만 씀)

    def to_json(self) -> dict:
        return {
            "applied": self.applied,
            "requested": self.requested,
            "source": self.source,
            "reason": self.reason,
            "profile": self.profile,
            "basis": None if self.basis is None else dict(self.basis),
            "warnings": list(self.warnings),
            "overrides": None if self.overrides is None else dict(self.overrides),
            "single_rules": SINGLE_RULES_VERSION if self.applied == SINGLE else None,
            "pair": None,
        }


def partial_overrides(profile: Mapping) -> dict:
    """프로필의 `pipeline` 표(부분 덮어쓰기). PARTIAL_KEYS 밖의 칸, 형·범위가 틀린 값은 RouteError."""
    raw = profile.get("pipeline")
    if not isinstance(raw, Mapping) or not raw:
        raise RouteError("partial 경로에는 프로필의 pipeline 표(바꿀 칸)가 있어야 합니다")
    unknown = sorted(set(raw) - set(PARTIAL_KEYS))
    if unknown:
        raise RouteError(f"프로필 pipeline 표에 모르는 칸이 있습니다: {', '.join(unknown)}")
    try:
        check_values(raw)
    except ValueError as e:
        raise RouteError(str(e)) from e
    return {k: raw[k] for k in PARTIAL_KEYS if k in raw}


def _is_alias(model: str) -> bool:
    return model.lower() in ALIASES or "latest" in model.lower()


def resolve_route(
    *,
    profile_name: str | None,
    profiles: Mapping[str, Mapping],
    base: PipelineSettings,
    llm_backend: str,
    llm_model: str,
    llm_effort: str,
    llm_model_source: str,
    model_arg: str | None,
    forced: str | None,
    dataset: str | None,
    cli_version: Callable[[str], str | None],
) -> RouteDecision:
    """경로를 정한다. 프로필 값이 틀렸으면 RouteError."""
    profile = profiles.get(profile_name) if profile_name else None
    requested = None if profile is None else profile.get("route")
    if requested is not None and requested not in ROUTES:
        raise RouteError(f"프로필 {profile_name}의 route는 {', '.join(ROUTES)} 가운데 하나입니다: {requested!r}")

    def full(reason: str) -> RouteDecision:
        return RouteDecision(HARNESS, requested, "default", reason, profile_name, settings=base)

    # 1. 측정용 강제
    if forced is not None:
        if forced not in ROUTES:
            raise RouteError(f"--route는 {', '.join(ROUTES)} 가운데 하나입니다: {forced!r}")
        if forced == PARTIAL:
            if profile is None:
                raise RouteError("--route partial은 --profile로 고른 프로필의 pipeline 표가 있어야 합니다")
            over = partial_overrides(profile)
            return RouteDecision(
                PARTIAL, forced, "forced", "forced", profile_name, overrides=over, settings=replace(base, **over)
            )
        return RouteDecision(forced, forced, "forced", "forced", profile_name, settings=base)

    # 2. 이름으로 고른 프로필이 없음
    if profile is None or profile.get("backend", "auto") == "auto":
        if llm_backend == "fake":
            return full("no_profile")
        if profile is not None or llm_model_source in SESSION_SOURCES:
            return full("session")
        if model_arg is not None and _is_alias(model_arg):
            return full("alias")
        return full("no_profile")

    # 3. 경로 칸 없음
    if requested in (None, HARNESS):
        return full("no_route")
    # 4. 별칭 모델
    if _is_alias(str(profile.get("model", ""))):
        return full("alias")
    # 5. 근거 없음
    basis_list = profile.get(BASIS_KEY)
    if not isinstance(basis_list, list) or not basis_list or not isinstance(basis_list[-1], Mapping):
        return full("no_basis")
    basis = dict(basis_list[-1])
    if basis.get("route") != requested:
        return full("no_basis")
    # 6. 조합이 근거와 글자 그대로 같은가
    now = {"backend": llm_backend, "model": llm_model, "reasoning_effort": llm_effort}
    if any(not isinstance(basis.get(k), str) or basis.get(k) != now[k] for k in BASIS_MATCH):
        return full("basis_mismatch")

    # 7. 프로필의 경로
    warnings: list[str] = []
    cli_then = basis.get("cli")
    cli_now = cli_version(llm_backend)
    if cli_then != cli_now:
        warnings.append(f"경로 근거가 낡았습니다(CLI {cli_then or '기록 없음'} → {cli_now or '알 수 없음'})")
    if dataset is not None and basis.get("dataset") != dataset:
        warnings.append(f"근거 데이터셋이 다릅니다({basis.get('dataset')} → {dataset}). 잠정 경로입니다")
    if requested == PARTIAL:
        over = partial_overrides(profile)
        if basis.get("overrides") != json.dumps(over, sort_keys=True, ensure_ascii=False, separators=(",", ":")):
            return full("basis_mismatch")
        return RouteDecision(
            PARTIAL,
            requested,
            "profile",
            "measured",
            profile_name,
            basis,
            warnings,
            over,
            replace(base, **over),
        )
    return RouteDecision(SINGLE, requested, "profile", "measured", profile_name, basis, warnings, settings=base)


def route_name(applied: str, overrides: Mapping | None = None) -> str:
    """화면에 보이는 경로 이름. t2s.runner.records.route_label과 같은 꼴의 괄호 글."""
    if applied == SINGLE:
        return "single(단일 호출)"
    if applied == PARTIAL:
        return f"partial({', '.join(f'{k}={_val(v)}' for k, v in (overrides or {}).items())})"
    return "harness(전체 P3)"


def _val(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def route_lines(d: RouteDecision) -> list[str]:
    """시작할 때 모델 첫 줄 바로 아래에 보이는 줄: 경로 한 줄과 경고마다 한 줄."""
    name = route_name(d.applied, d.overrides)
    if d.source == "profile":
        b = d.basis or {}
        head = f"경로 {name} · 프로필 {d.profile} · 근거 {b.get('measured')} {b.get('dataset')}"
    elif d.source == "forced":
        head = f"경로 {name} · {REASON_TEXT['forced']}"
    else:
        head = f"경로 {name} · 까닭 {REASON_TEXT.get(d.reason, d.reason)}"
    tail = [f"주의: {w}. 경로는 그대로 {d.applied}입니다. 다시 재려면 /newmodel" for w in d.warnings]
    return [head, *tail]


def shown_route_lines(d: RouteDecision) -> list[str]:
    """화면에 보일 줄. 경로를 따로 정하지 않은 전체 P3(프로필에 경로 요청이 없고 경고도 없음)는 아무 줄도 더하지 않는다
    (기존 run·ask 출력이 그대로다). 까닭은 run.json·ask.json의 route 칸에 늘 남는다."""
    plain = d.applied == HARNESS and d.source == "default" and d.requested in (None, HARNESS) and not d.warnings
    return [] if plain else route_lines(d)
