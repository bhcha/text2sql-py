import argparse
import json
from pathlib import Path

import pytest

from t2s.llm import select
from t2s.llm.claude_cli import ClaudeBackend
from t2s.llm.codex_cli import CodexBackend
from t2s.llm.fake import FakeBackend
from t2s.llm.select import ModelUnknown, backend_for, first_line, is_reference, provider_name, select_model

PROFILES = {
    "p3-ref": {
        "backend": "codex-cli",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "timeout_seconds": 150,
        "item_timeout_seconds": 1530,
    },
    "p3-claude": {
        "backend": "claude-code",
        "model": "claude-opus-5-5",
        "reasoning_effort": "medium",
        "timeout_seconds": 150,
        "item_timeout_seconds": 1530,
    },
    "session": {"backend": "auto"},
}
CWD = Path("/tmp/x/workspace/text2sql-py")


def sel(home, env=None, **kw):
    return select_model(profiles=PROFILES, env=env or {}, cwd=CWD, home=home, **kw)


def test_profiles(tmp_path):
    r = sel(tmp_path, profile="p3-ref")
    assert (r.backend, r.model, r.reasoning_effort, r.profile, r.model_source) == (
        "codex-cli",
        "gpt-5.6-terra",
        "medium",
        "p3-ref",
        "conversation",
    )
    assert r.item_timeout_seconds == 1530 and is_reference(r)
    r = sel(tmp_path, profile="p3-claude", model_source="session-self")
    assert r.backend == "claude-code" and r.model_source == "session-self" and not is_reference(r)
    with pytest.raises(ModelUnknown, match="모르는 프로필"):
        sel(tmp_path, profile="nope")


ALIAS_CASES = {
    "오푸스": ("claude-code", "claude-opus-5-5"),
    "opus": ("claude-code", "claude-opus-5-5"),
    "테라": ("codex-cli", "gpt-5.6-terra"),
    "terra": ("codex-cli", "gpt-5.6-terra"),
    "솔": ("codex-cli", "gpt-5.6-sol"),
    "sol": ("codex-cli", "gpt-5.6-sol"),
    "루나": ("codex-cli", "gpt-5.6-luna"),
    "luna": ("codex-cli", "gpt-5.6-luna"),
    "아스트라": ("codex-cli", "gpt-6-astra"),
    "Astra": ("codex-cli", "gpt-6-astra"),
}


@pytest.mark.parametrize("word", list(ALIAS_CASES))
def test_aliases(tmp_path, word):
    r = sel(tmp_path, model=word)
    assert (r.backend, r.model) == ALIAS_CASES[word]
    assert r.reasoning_effort == "medium" and r.effort_source == "default-medium"


def test_terra_medium_is_named_reference(tmp_path):
    r = sel(tmp_path, model="terra", effort="medium")
    assert r.profile == "p3-ref" and r.effort_source == "conversation"
    assert sel(tmp_path, model="terra", effort="high").profile is None


def test_prefixes_and_unknown(tmp_path):
    assert sel(tmp_path, model="claude-x-1").backend == "claude-code"
    assert sel(tmp_path, model="gpt-9").backend == "codex-cli"
    with pytest.raises(ModelUnknown) as e:
        sel(tmp_path, model="llama-3")
    assert str(e.value) == select.QUESTION


def test_session_self_effort(tmp_path):
    r = sel(
        tmp_path, {"CLAUDE_EFFORT": "high"}, backend="claude-code", model="claude-opus-5-5", model_source="session-self"
    )
    assert (r.reasoning_effort, r.effort_source) == ("high", "session-env")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "low"\n[x]\nmodel="no"\n'
    )
    r = sel(tmp_path, backend="codex-cli", model="gpt-5.6-terra", model_source="session-self")
    assert (r.reasoning_effort, r.effort_source) == ("low", "config-default")
    assert r.session_evidence["codex_config_model"] == "gpt-5.6-sol"
    r = sel(tmp_path / "empty", backend="claude-code", model="claude-opus-5-5", model_source="session-self")
    assert (r.reasoning_effort, r.effort_source) == ("medium", "default-medium")


def _transcript(home: Path, session: str, models: list[str]) -> None:
    folder = home / ".claude/projects" / str(CWD).replace("/", "-").replace(".", "-")
    folder.mkdir(parents=True)
    lines = [json.dumps({"type": "user", "message": {}})]
    lines += [json.dumps({"type": "assistant", "message": {"model": m}}) for m in models]
    lines.append("not json")
    (folder / f"{session}.jsonl").write_text("\n".join(lines) + "\n")


def test_transcript(tmp_path):
    _transcript(tmp_path, "s1", ["claude-old", "claude-opus-5-5"])
    r = sel(tmp_path, {"CLAUDE_CODE_SESSION_ID": "s1"})
    assert (r.model, r.backend, r.model_source) == ("claude-opus-5-5", "claude-code", "session-transcript")
    assert (r.reasoning_effort, r.effort_source) == ("medium", "default-medium")
    # auto 프로필은 인자 없음과 같다
    r = sel(tmp_path, {"CLAUDE_CODE_SESSION_ID": "s1", "CLAUDE_EFFORT": "max"}, profile="session")
    assert r.model == "claude-opus-5-5" and r.effort_source == "session-env"


def test_no_evidence_asks(tmp_path):
    with pytest.raises(ModelUnknown) as e:
        sel(tmp_path, {"CLAUDE_CODE_SESSION_ID": "missing"}, profile="session")
    assert str(e.value) == select.QUESTION


def test_session_codex(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('model = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\n')
    r = sel(tmp_path, session="codex")
    assert (r.model, r.model_source, r.profile) == ("gpt-5.6-terra", "config-default", "p3-ref")


def test_first_line_forms(tmp_path):
    assert first_line(sel(tmp_path, profile="p3-ref")) == (
        "모델 gpt-5.6-terra · 추론 medium · 백엔드 codex-cli · 출처 대화(p3-ref)"
    )
    assert first_line(sel(tmp_path, model="opus")).endswith("출처 대화")
    r = sel(
        tmp_path, {"CLAUDE_EFFORT": "high"}, backend="claude-code", model="claude-opus-5-5", model_source="session-self"
    )
    assert (
        first_line(r)
        == "모델 claude-opus-5-5 · 추론 high · 백엔드 claude-code · 출처 세션(session-self, 추론 session-env)"
    )
    assert first_line(sel(tmp_path, backend="fake")) == "모델 fake-model · 추론 none · 백엔드 fake · 출처 가짜 백엔드"


def test_fake_ignores_rest(tmp_path):
    r = sel(tmp_path, backend="fake", profile="p3-ref", model="opus")
    assert (r.backend, r.model, r.model_source) == ("fake", "fake-model", "fake")


def test_backend_for_and_provider(tmp_path):
    assert isinstance(backend_for(sel(tmp_path, profile="p3-ref")), CodexBackend)
    assert isinstance(backend_for(sel(tmp_path, profile="p3-claude")), ClaudeBackend)
    assert isinstance(backend_for(sel(tmp_path, backend="fake")), FakeBackend)
    assert backend_for(sel(tmp_path, profile="p3-ref")).settings.timeout_seconds == 150
    assert provider_name("codex-cli") == "OpenAI" and provider_name("claude-code") == "Anthropic"
    assert provider_name("fake") == "없음(가짜 백엔드)"


def test_from_args_and_json(tmp_path):
    from t2s.cli import main as cli_main

    assert cli_main.BACKENDS == select.BACKENDS
    p = argparse.ArgumentParser()
    cli_main.add_model_arguments(p)
    args = p.parse_args(["--model", "opus"])
    r = select.select_from_args(args, PROFILES, "p3-ref", env={}, cwd=CWD, home=tmp_path)
    assert r.model == "claude-opus-5-5"  # --model이 있으면 기본 프로필을 쓰지 않는다
    args = p.parse_args([])
    assert select.select_from_args(args, PROFILES, "p3-ref", env={}, cwd=CWD, home=tmp_path).profile == "p3-ref"
    assert select.ResolvedLlm.from_json(r.to_json()) == r
