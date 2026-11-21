"""어떤 모델로 돌릴지 정한다. 대화 지정 > 세션 > 실패. 임의 기본값을 두지 않는다.

TS harness/lib/model-select.ts(고르는 차례, 별칭, 첫 줄 꼴, 참조 조건, 모르면 내는 질문)를 옮긴 자체 코드다.
프로필 표는 인자로 받는다(t2s.dataset을 import하지 않는다). 백엔드가 "auto"인 프로필은 인자가 없는 것과 같다.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from t2s.llm.base import LlmBackend, LlmSettings

QUESTION = '모델을 알 수 없습니다. 어떤 모델로 돌릴까요? 예: "terra medium" 또는 "opus"'
ALIASES: dict[str, tuple[str, str]] = {
    "오푸스": ("claude-code", "claude-opus-5-5"),
    "opus": ("claude-code", "claude-opus-5-5"),
    "테라": ("codex-cli", "gpt-5.6-terra"),
    "terra": ("codex-cli", "gpt-5.6-terra"),
    "솔": ("codex-cli", "gpt-5.6-sol"),
    "sol": ("codex-cli", "gpt-5.6-sol"),
    "루나": ("codex-cli", "gpt-5.6-luna"),
    "luna": ("codex-cli", "gpt-5.6-luna"),
    "아스트라": ("codex-cli", "gpt-6-astra"),
    "astra": ("codex-cli", "gpt-6-astra"),
}
DEFAULT_TIMEOUTS = (150, 1530)
BACKENDS = ("codex-cli", "claude-code", "fake")
REFERENCE = ("codex-cli", "gpt-5.6-terra", "medium")
REFERENCE_PROFILE = "p3-ref"


class ModelUnknown(Exception):
    pass


@dataclass(frozen=True)
class ResolvedLlm:
    backend: str
    model: str
    reasoning_effort: str
    timeout_seconds: float
    item_timeout_seconds: float
    profile: str | None
    model_source: str
    effort_source: str
    session_evidence: dict = field(default_factory=dict)

    def settings(self) -> LlmSettings:
        return LlmSettings(self.backend, self.model, self.reasoning_effort, self.timeout_seconds)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Mapping) -> ResolvedLlm:
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})


def backend_of(model: str) -> str:
    if model.startswith("claude-"):
        return "claude-code"
    if model.startswith("gpt-"):
        return "codex-cli"
    raise ModelUnknown(QUESTION)


def transcript_model(env: Mapping[str, str], cwd: Path, home: Path) -> str | None:
    """Claude Code 세션 기록의 마지막 assistant 모델. 공식 계약이 아니므로 못 읽으면 None."""
    session = env.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None
    folder = str(cwd).replace("/", "-").replace(".", "-")
    path = Path(home) / ".claude" / "projects" / folder / f"{session}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").rstrip().split("\n")
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "assistant":
            model = (event.get("message") or {}).get("model")
            if isinstance(model, str):
                return model
    return None


def codex_config(home: Path) -> dict[str, str | None]:
    """~/.codex/config.toml 맨 위 표의 model·model_reasoning_effort. 못 읽으면 둘 다 None."""
    path = Path(home) / ".codex" / "config.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"model": None, "effort": None}
    model = raw.get("model")
    effort = raw.get("model_reasoning_effort")
    return {
        "model": model if isinstance(model, str) else None,
        "effort": effort if isinstance(effort, str) else None,
    }


def _named(backend, model, effort, timeouts, profile, model_source, effort_source, evidence) -> ResolvedLlm:
    if profile is None and (backend, model, effort) == REFERENCE:
        profile = REFERENCE_PROFILE
    return ResolvedLlm(
        backend=backend,
        model=model,
        reasoning_effort=effort,
        timeout_seconds=timeouts[0],
        item_timeout_seconds=timeouts[1],
        profile=profile,
        model_source=model_source,
        effort_source=effort_source,
        session_evidence=evidence,
    )


def select_model(
    *,
    profile: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    model_source: str | None = None,
    effort_source: str | None = None,
    session: str | None = None,
    profiles: Mapping[str, Mapping],
    env: Mapping[str, str],
    cwd: Path,
    home: Path,
) -> ResolvedLlm:
    if backend == "fake":
        return ResolvedLlm("fake", "fake-model", "none", 1, 1, None, "fake", "fake", {})
    if backend is not None and backend not in BACKENDS:
        raise ModelUnknown(f"모르는 백엔드입니다: {backend}")
    if profile:
        p = profiles.get(profile)
        if p is None:
            raise ModelUnknown(f"모르는 프로필입니다: {profile}")
        if p.get("backend", "auto") != "auto":
            return _named(
                str(p["backend"]),
                str(p["model"]),
                str(p.get("reasoning_effort", "medium")),
                (p.get("timeout_seconds", DEFAULT_TIMEOUTS[0]), p.get("item_timeout_seconds", DEFAULT_TIMEOUTS[1])),
                profile,
                model_source or "conversation",
                effort_source or "conversation",
                {},
            )
    transcript = transcript_model(env, cwd, home)
    evidence: dict = {"transcript_model": transcript, "claude_effort_env": env.get("CLAUDE_EFFORT")}
    if model:
        alias = ALIASES.get(model.lower())
        name = alias[1] if alias else model
        chosen = backend or (alias[0] if alias else backend_of(name))
        source = model_source or "conversation"
        eff = effort
        eff_source = effort_source or ("conversation" if effort else "default-medium")
        if not eff and source == "session-self":
            if chosen == "claude-code" and env.get("CLAUDE_EFFORT"):
                eff = env["CLAUDE_EFFORT"]
                eff_source = "session-env"
            elif chosen == "codex-cli":
                cfg = codex_config(home)
                evidence["codex_config_model"] = cfg["model"]
                if cfg["effort"]:
                    eff = cfg["effort"]
                    eff_source = "config-default"
        return _named(chosen, name, eff or "medium", DEFAULT_TIMEOUTS, None, source, eff_source, evidence)
    if transcript:
        return _named(
            backend_of(transcript),
            transcript,
            env.get("CLAUDE_EFFORT") or "medium",
            DEFAULT_TIMEOUTS,
            None,
            "session-transcript",
            "session-env" if env.get("CLAUDE_EFFORT") else "default-medium",
            evidence,
        )
    if session == "codex":
        cfg = codex_config(home)
        if cfg["model"]:
            return _named(
                "codex-cli",
                cfg["model"],
                cfg["effort"] or "medium",
                DEFAULT_TIMEOUTS,
                None,
                "config-default",
                "config-default" if cfg["effort"] else "default-medium",
                {**evidence, "codex_config_model": cfg["model"]},
            )
    raise ModelUnknown(QUESTION)


def first_line(llm: ResolvedLlm) -> str:
    """시작할 때 첫 줄: 모델 · 추론 · 백엔드 · 출처."""
    if llm.backend == "fake":
        source = "가짜 백엔드"
    elif llm.model_source == "conversation":
        source = f"대화({llm.profile})" if llm.profile else "대화"
    else:
        source = f"세션({llm.model_source}, 추론 {llm.effort_source})"
    return f"모델 {llm.model} · 추론 {llm.reasoning_effort} · 백엔드 {llm.backend} · 출처 {source}"


def is_reference(llm: ResolvedLlm) -> bool:
    return (llm.backend, llm.model, llm.reasoning_effort) == REFERENCE


def provider_name(backend: str) -> str:
    return {"codex-cli": "OpenAI", "claude-code": "Anthropic"}.get(backend, "없음(가짜 백엔드)")


def backend_for(llm: ResolvedLlm) -> LlmBackend:
    if llm.backend == "codex-cli":
        from t2s.llm.codex_cli import CodexBackend

        return CodexBackend(llm.settings())
    if llm.backend == "claude-code":
        from t2s.llm.claude_cli import ClaudeBackend

        return ClaudeBackend(llm.settings())
    if llm.backend == "fake":
        from t2s.llm.fake import FakeBackend

        return FakeBackend()
    raise ModelUnknown(f"모르는 백엔드입니다: {llm.backend}")


def backend_bin(backend: str) -> str | None:
    """백엔드의 실행 파일 이름(가짜는 None)."""
    if backend == "codex-cli":
        from t2s.llm.codex_cli import codex_bin

        return codex_bin()
    if backend == "claude-code":
        from t2s.llm.claude_cli import claude_bin

        return claude_bin()
    return None


def select_from_args(
    args, profiles: Mapping[str, Mapping], default_profile: str | None, *, env: Mapping[str, str], cwd: Path, home: Path
) -> ResolvedLlm:
    """명령 인자(cli/main.py add_model_arguments)로 고른다. --profile·--model이 모두 없을 때만 설정의 기본 프로필을 쓴다."""
    return select_model(
        profile=args.profile or (None if args.model else default_profile),
        backend=args.backend,
        model=args.model,
        effort=args.effort,
        model_source=args.model_source,
        effort_source=args.effort_source,
        session=args.session,
        profiles=profiles,
        env=env,
        cwd=cwd,
        home=home,
    )
