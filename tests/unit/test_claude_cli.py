from pathlib import Path

import pytest

from fixtures.fake_cli import make_fake_cli, read_log
from t2s.llm import claude_cli
from t2s.llm.base import LlmSettings
from t2s.llm.claude_cli import ClaudeBackend, child_env, claude_args, read_claude_events

SETTINGS = LlmSettings("claude-code", "claude-opus-5-5", "medium", 10)

# TS engines/packkit/llm/claude.ts claudeArgs
EXPECTED = [
    "-p", "--model", "claude-opus-5-5", "--effort", "medium", "--tools", "", "--strict-mcp-config",
    "--disable-slash-commands", "--setting-sources", "", "--permission-prompts", "none", "--no-session-persistence",
    "--output-format", "stream-json", "--verbose", "--system-prompt", "Answer the user's message directly. Do not call tools.",
]  # fmt: skip


def test_args_match_ts():
    assert claude_args(SETTINGS) == EXPECTED


def test_child_env_drops_session_names_only():
    env = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_SESSION_ID": "s",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_PID": "9",
        "CLAUDE_EFFORT": "high",
        "CLAUDE_CODE_MESSAGING_X": "y",
        "ANTHROPIC_API_KEY": "k",
        "CLAUDE_CONFIG_DIR": "/c",
        "PATH": "/bin",
    }
    kept, dropped = child_env(env)
    assert set(kept) == {"ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "PATH"}
    assert len(dropped) == 7


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = make_fake_cli(tmp_path, "claude")
    monkeypatch.setenv("T2S_CLAUDE_BIN", str(exe))
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "t")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR_FAKE", "keep")
    return log


def call(tmp_path, mode=None, monkeypatch=None, settings=SETTINGS):
    if mode and monkeypatch:
        monkeypatch.setenv("FAKE_CLI_MODE", mode)
    d = tmp_path / "call"
    return ClaudeBackend(settings).call("PROMPT", d, name="generate_sql", attempt=1), d


def test_ok(fake, tmp_path):
    out, d = call(tmp_path)
    assert out.failure is None and out.response == "SELECT 1"
    m = out.meta
    assert m["model_reported"] == "claude-opus-5-5" and m["model_reported_matches"] is True
    assert m["init"] == {"model": "claude-opus-5-5", "tools": 0, "mcp_servers": 0}
    assert m["tool_calls"] == 0 and m["usage"]["cache_read_input_tokens"] == 5 and m["num_turns"] == 1
    assert m["cli_version"] == "0.0.0 (Claude Code fake)" and m["thread_id"] == "fake-session"
    assert m["system_prompt"] == claude_cli.SYSTEM_PROMPT and m["work_dir_files"] == 0
    assert "CLAUDECODE" in m["env_dropped"] and "CLAUDE_CODE_MESSAGING_TOKEN" in m["env_dropped"]
    assert {p.name for p in d.iterdir()} == {
        "prompt.txt",
        "response.txt",
        "llm-meta.json",
        "cli-events.jsonl",
        "cli-stderr.txt",
    }
    [entry] = read_log(fake)
    assert "CLAUDE_CONFIG_DIR_FAKE" in entry["claude_env"]
    for name in (*claude_cli.DROPPED_ENV, "CLAUDE_CODE_MESSAGING_TOKEN"):
        assert name not in entry["claude_env"]
    assert entry["cwd_files"] == [] and not Path(entry["cwd"]).exists()
    assert entry["args"] == EXPECTED


def test_wrong_model(fake, tmp_path, monkeypatch):
    out, _ = call(tmp_path, "wrongmodel", monkeypatch)
    assert out.response == "SELECT 1" and out.meta["model_reported_matches"] is False


def test_tool(fake, tmp_path, monkeypatch):
    out, _ = call(tmp_path, "tool", monkeypatch)
    assert out.meta["tool_calls"] == 1 and out.meta["tool_items"] == ["Bash"]


@pytest.mark.parametrize(
    "mode,kind", [("limit", "ClaudeUsageLimit"), ("exit", "ClaudeExit"), ("noresponse", "ClaudeExit")]
)
def test_failures(fake, tmp_path, monkeypatch, mode, kind):
    out, d = call(tmp_path, mode, monkeypatch)
    assert out.failure.type == kind and out.response is None
    assert not (d / "response.txt").exists()


def test_timeout(fake, tmp_path, monkeypatch):
    out, _ = call(tmp_path, "hang", monkeypatch, settings=LlmSettings("claude-code", "m", "medium", 1.5))
    assert out.failure.type == "ClaudeTimeout"


def test_result_is_error_has_no_response():
    import json

    text = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "boom"})
    ev = read_claude_events(text)
    assert ev["response"] is None and ev["errors"] == ["boom"] and ev["is_error"]


def test_usage_limit_table():
    hit = (
        "Claude usage limit reached; resets at 3pm",
        "Rate limit",
        "429 Too Many Requests",
        "You are out of extra usage",
        "Weekly limit reached",
        "quota exceeded",
    )
    miss = ("model not found", "Invalid API key", "exit 1: something went wrong")
    for s in hit:
        assert claude_cli.USAGE_LIMIT.search(s), s
    for s in miss:
        assert not claude_cli.USAGE_LIMIT.search(s), s
