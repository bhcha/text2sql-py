import json
import os
import tempfile
from pathlib import Path

import pytest

from fixtures.fake_cli import make_fake_cli, read_log
from t2s.llm import codex_cli
from t2s.llm.base import LlmSettings
from t2s.llm.codex_cli import CodexBackend, codex_args, read_codex_events

SETTINGS = LlmSettings("codex-cli", "gpt-5.6-terra", "medium", 10)
REPO = Path(__file__).resolve().parents[2]

# TS engines/packkit/llm/codex.ts codexArgs가 내는 배열(글자까지 같아야 한다)
EXPECTED = [
    "exec", "-m", "gpt-5.6-terra", "-c", 'model_reasoning_effort="medium"', "-c", 'web_search="disabled"',
    "-c", "project_doc_max_bytes=0",
    "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "apps", "--disable", "browser_use",
    "--disable", "computer_use", "--disable", "plugins", "--disable", "multi_agent", "--disable", "image_generation",
    "--disable", "view_image", "--disable", "hooks", "--disable", "skill_search",
    "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
    "--color", "never", "--json", "-o", "/c/response.txt", "-C", "/w", "-",
]  # fmt: skip


def test_args_match_ts():
    assert codex_args(SETTINGS, "/c/response.txt", "/w") == EXPECTED
    assert codex_args(SETTINGS, "x", "y").count("--disable") == 11


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    return log


def call(tmp_path, mode=None, monkeypatch=None, call_dir="call", settings=SETTINGS):
    if mode and monkeypatch:
        monkeypatch.setenv("FAKE_CLI_MODE", mode)
    d = None if call_dir is None else tmp_path / call_dir
    return CodexBackend(settings).call("PROMPT ü", d, name="generate_sql", attempt=1), d


def test_ok(fake, tmp_path, monkeypatch):
    out, d = call(tmp_path)
    assert out.failure is None and out.response == "SELECT 1"
    m = out.meta
    assert m["usage"]["input_tokens"] == 100 and m["thread_id"] == "fake-thread" and m["tool_calls"] == 0
    assert m["cli_version"] == "codex-cli fake" and m["exit_code"] == 0 and m["timed_out"] is False
    assert m["work_dir_outside_repo"] is True and m["work_dir_files"] == 0
    assert {p.name for p in d.iterdir()} == {
        "prompt.txt",
        "response.txt",
        "llm-meta.json",
        "cli-events.jsonl",
        "cli-stderr.txt",
    }
    assert (d / "prompt.txt").read_text(encoding="utf-8") == "PROMPT ü"
    assert json.loads((d / "llm-meta.json").read_text())["response_sha256"] == m["response_sha256"]
    assert "<work>" in m["command"] and "response.txt" in m["command"]
    [entry] = read_log(fake)
    assert entry["prompt"] == "PROMPT ü"
    work = Path(entry["cwd"]).resolve()
    assert entry["cwd_files"] == []
    assert not str(work).startswith(str(REPO))
    assert work.name.startswith("t2s-llm-")
    assert str(work).startswith(os.path.realpath(tempfile.gettempdir()))
    assert not work.exists()
    assert entry["args"][entry["args"].index("-C") + 1] == entry["cwd"] or Path(entry["args"][-2]).resolve() == work


def test_tool_counted_once(fake, tmp_path, monkeypatch):
    out, _ = call(tmp_path, "tool", monkeypatch)
    assert out.meta["tool_calls"] == 1 and out.meta["tool_items"] == ["command_execution"]


@pytest.mark.parametrize(
    "mode,kind",
    [("exit", "CodexExit"), ("limit", "CodexUsageLimit"), ("noresponse", "CodexExit")],
)
def test_failures(fake, tmp_path, monkeypatch, mode, kind):
    out, d = call(tmp_path, mode, monkeypatch)
    assert out.failure.type == kind and out.response is None
    assert not (d / "response.txt").exists()
    assert out.failure.message.startswith("exit ")


def test_limit_message(fake, tmp_path, monkeypatch):
    out, _ = call(tmp_path, "limit", monkeypatch)
    assert "usage limit" in out.failure.message and out.meta["errors"]


def test_timeout(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_PIDFILE", str(tmp_path / "pid"))
    out, _ = call(tmp_path, "hang", monkeypatch, settings=LlmSettings("codex-cli", "m", "medium", 1.5))
    assert out.failure.type == "CodexTimeout" and out.response is None and out.meta["timed_out"]


def test_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("T2S_CODEX_BIN", str(tmp_path / "no-codex"))
    out, _ = call(tmp_path)
    assert out.failure.type == "CodexExit" and "실행하지 못했습니다" in out.failure.message


def test_no_call_dir(fake, tmp_path):
    out, _ = call(tmp_path, call_dir=None)
    assert out.response == "SELECT 1"
    assert not (tmp_path / "call").exists()


def test_events_skip_bad_lines():
    text = "not json\n" + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}) + "\n[1]\n\n"
    ev = read_codex_events(text)
    assert ev["usage"] == {"input_tokens": 1} and ev["tool_calls"] == 0


def test_usage_limit_regex():
    for s in ("You've hit your usage limit.", "Rate limit exceeded", "HTTP 429", "quota", "Too Many Requests"):
        assert codex_cli.USAGE_LIMIT.search(s), s
    for s in ("model not found", "exit 1", "4290 rows"):
        assert not codex_cli.USAGE_LIMIT.search(s), s
