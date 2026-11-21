"""모델 단위 경로(single | harness | partial)를 정한다. 파일을 읽거나 쓰지 않는 순수 함수다.

자체 코드다. 프로필(harness.toml `[profiles.<이름>]`)의 `route` 칸과 근거 배열 `route_basis`를 보고, run·ask를 시작할 때
`resolve_route`가 위에서부터 다음 차례로 정한다.
1. 측정용 강제(`--route`)면 그 경로.
2. 프로필을 이름(`--profile`)으로 고르지 않았으면 2-1~2-3.
   2-1. 가짜 백엔드·세션 모델·별칭이면 전체 P3.
   2-2. 대화로 정한 모델(`--model` 전체 이름, 출처 conversation)이고 추론 단계를 대화로 정했거나 말하지 않았으면
        (default-medium, 추론 단계는 기본값 medium으로 본다. newmodel이 같은 규칙으로 재고 저장한다) 모델 이름으로 맞춘다:
        참조 프로필(p3-ref)·backend auto를 뺀 프로필 가운데 백엔드·모델·추론 단계가 글자 그대로 같은 프로필마다
        3~7을 이름으로 고른 것처럼 따져, 근거가 맞는 기록(route harness인 기록도 센다)만 남긴다.
        하나도 없으면 맞춘 프로필이 없을 때는 전체 P3(no_profile), 있을 때는 파일에서 먼저 나오는 맞춘 프로필의 까닭.
        남은 기록의 경로(partial이면 바꾼 칸까지)가 모두 같으면 파일에서 먼저 나오는 프로필(route-apply가 고르는 프로필과
        같은 차례)의 경로, 서로 다르면 전체 P3(ambiguous). 출처는 profile-match로 남긴다.
        추론 단계를 기본값 medium으로 보았으면 route 칸 assumed_effort와 화면 경로 줄에 남긴다.
        추론 단계의 출처가 그 밖(설정 기본값·세션 환경)이면 맞추지 않고 전체 P3(no_effort).
   2-3. 그 밖에는 전체 P3(no_profile).
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
    "no_effort": "추론 단계를 대화로 정하지 않음",
    "ambiguous": "맞는 프로필 여럿의 경로가 다름",
}
REFERENCE_PROFILE = "p3-ref"  # t2s.llm.select.REFERENCE_PROFILE과 같다. 경로를 갖지 않는다(모델 이름으로 맞추지 않음)
MATCH_SOURCE = "profile-match"
DEFAULT_EFFORT_SOURCE = "default-medium"  # t2s.llm.select가 대화 모델에 추론 단계 말이 없을 때 남기는 출처
DEFAULT_EFFORT = "medium"


class RouteError(ValueError):
    pass


@dataclass
class RouteDecision:
    applied: str
    requested: str | None
    source: str  # default | profile | profile-match | forced
    reason: str
    profile: str | None
    basis: dict | None = None
    warnings: list[str] = field(default_factory=list)
    overrides: dict | None = None
    settings: PipelineSettings | None = None  # 적용할 설정(single이면 공통 설정, 최종 실행 시간 제한에만 씀)
    matches: list[str] | None = None  # 모델 이름으로 맞출 때 근거가 맞은 프로필 이름들(파일 차례)
    assumed_effort: str | None = None  # 추론 단계 말이 없어 기본값으로 보고 모델 이름으로 맞췄을 때 그 값(medium)

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
            "matches": None if self.matches is None else list(self.matches),
            "assumed_effort": self.assumed_effort,
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
    llm_effort_source: str,
    model_arg: str | None,
    forced: str | None,
    dataset: str | None,
    cli_version: Callable[[str], str | None],
) -> RouteDecision:
    """경로를 정한다. 프로필 값이 틀렸으면 RouteError."""
    profile = profiles.get(profile_name) if profile_name else None
    requested = None if profile is None else _requested(profile_name, profile)

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

    now = {"backend": llm_backend, "model": llm_model, "reasoning_effort": llm_effort}
    cli_now: list[str | None] = []  # CLI 판은 한 번만 묻는다

    def cli() -> str | None:
        if not cli_now:
            cli_now.append(cli_version(llm_backend))
        return cli_now[0]

    # 2. 이름으로 고른 프로필이 없음
    if profile is None or profile.get("backend", "auto") == "auto":
        if llm_backend == "fake":
            return full("no_profile")
        if profile is not None or llm_model_source in SESSION_SOURCES:
            return full("session")
        if model_arg is not None and _is_alias(model_arg):
            return full("alias")
        if profile is None and model_arg is not None and llm_model_source == "conversation":
            assumed = llm_effort_source == DEFAULT_EFFORT_SOURCE and llm_effort == DEFAULT_EFFORT
            if llm_effort_source != "conversation" and not assumed:
                return full("no_effort")
            decision = _match(profiles, now, base, dataset, cli)
            if assumed:
                decision.assumed_effort = DEFAULT_EFFORT
            return decision
        return full("no_profile")

    return _from_profile(profile_name, profile, requested, "profile", now, base, dataset, cli)


def _requested(name: str | None, profile: Mapping) -> str | None:
    requested = profile.get("route")
    if requested is not None and requested not in ROUTES:
        raise RouteError(f"프로필 {name}의 route는 {', '.join(ROUTES)} 가운데 하나입니다: {requested!r}")
    return requested


def _from_profile(
    name: str | None,
    profile: Mapping,
    requested: str | None,
    source: str,
    now: Mapping[str, str],
    base: PipelineSettings,
    dataset: str | None,
    cli: Callable[[], str | None],
) -> RouteDecision:
    """3~7. source가 profile-match면 route harness인 기록도 근거를 따져 measured로 돌려준다(경로가 다른 기록을 가리려고)."""

    def full(reason: str) -> RouteDecision:
        return RouteDecision(HARNESS, requested, "default", reason, name, settings=base)

    # 3. 경로 칸 없음
    if requested is None or (requested == HARNESS and source == "profile"):
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
    if any(not isinstance(basis.get(k), str) or basis.get(k) != now[k] for k in BASIS_MATCH):
        return full("basis_mismatch")

    # 7. 프로필의 경로
    warnings: list[str] = []
    cli_then = basis.get("cli")
    cli_now = cli()
    if cli_then != cli_now:
        warnings.append(f"경로 근거가 낡았습니다(CLI {cli_then or '기록 없음'} → {cli_now or '알 수 없음'})")
    if dataset is not None and basis.get("dataset") != dataset:
        warnings.append(f"근거 데이터셋이 다릅니다({basis.get('dataset')} → {dataset}). 잠정 경로입니다")
    if requested == PARTIAL:
        over = partial_overrides(profile)
        if basis.get("overrides") != json.dumps(over, sort_keys=True, ensure_ascii=False, separators=(",", ":")):
            return full("basis_mismatch")
        return RouteDecision(PARTIAL, requested, source, "measured", name, basis, warnings, over, replace(base, **over))
    return RouteDecision(requested, requested, source, "measured", name, basis, warnings, settings=base)


def _match(
    profiles: Mapping[str, Mapping],
    now: Mapping[str, str],
    base: PipelineSettings,
    dataset: str | None,
    cli: Callable[[], str | None],
) -> RouteDecision:
    """2-2. 모델 이름(백엔드·모델·추론 단계)이 글자 그대로 같은 프로필의 기록으로 경로를 정한다."""
    want = (now["backend"], now["model"], now["reasoning_effort"])
    tried: list[RouteDecision] = []
    for name, p in profiles.items():
        if name == REFERENCE_PROFILE or not isinstance(p, Mapping) or p.get("backend", "auto") == "auto":
            continue
        if (p.get("backend"), p.get("model"), p.get("reasoning_effort", "medium")) != want:
            continue
        tried.append(_from_profile(name, p, _requested(name, p), MATCH_SOURCE, now, base, dataset, cli))
    if not tried:
        return RouteDecision(HARNESS, None, "default", "no_profile", None, settings=base)
    found = [d for d in tried if d.reason == "measured"]
    if not found:
        return tried[0]
    names = [str(d.profile) for d in found]
    if len({route_name(d.applied, d.overrides) for d in found}) > 1:
        return RouteDecision(HARNESS, None, "default", "ambiguous", None, settings=base, matches=names)
    found[0].matches = names
    return found[0]


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
    if d.source in ("profile", MATCH_SOURCE):
        b = d.basis or {}
        how = "(모델 이름으로 맞춤)" if d.source == MATCH_SOURCE else ""
        head = f"경로 {name} · 프로필 {d.profile}{how} · 근거 {b.get('measured')} {b.get('dataset')}"
    elif d.source == "forced":
        head = f"경로 {name} · {REASON_TEXT['forced']}"
    elif d.reason == "ambiguous":
        head = f"경로 {name} · 까닭 {REASON_TEXT['ambiguous']}({', '.join(d.matches or [])})"
    else:
        head = f"경로 {name} · 까닭 {REASON_TEXT.get(d.reason, d.reason)}"
    if d.assumed_effort:
        head += f" · 추론 단계는 기본값 {d.assumed_effort}으로 봄"
    tail = [f"주의: {w}. 경로는 그대로 {d.applied}입니다. 다시 재려면 /newmodel" for w in d.warnings]
    return [head, *tail]


def shown_route_lines(d: RouteDecision) -> list[str]:
    """화면에 보일 줄. 경로를 따로 정하지 않은 전체 P3(프로필에 경로 요청이 없고 경고도 없음)는 아무 줄도 더하지 않는다
    (기존 run·ask 출력이 그대로다). 맞는 프로필 여럿의 경로가 달라 전체 P3로 도는 때(ambiguous)와 추론 단계를 기본값
    medium으로 보고 맞췄으나 기록이 없어 전체 P3로 도는 때(assumed_effort)는 보인다. 까닭은 run.json·ask.json의
    route 칸에 늘 남는다."""
    plain = (
        d.applied == HARNESS
        and d.source == "default"
        and d.requested in (None, HARNESS)
        and not d.warnings
        and d.reason != "ambiguous"
        and not d.assumed_effort
    )
    return [] if plain else route_lines(d)
