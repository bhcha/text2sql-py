import hashlib
import json

from t2s.llm.base import CALL_NAMES, LlmSettings, is_usage_limit, sha256_text, usage_totals, write_call_files
from t2s.llm.fake import DEFAULT_RESPONSES, FakeBackend, ScriptedBackend, ScriptedCall

META_KEYS = {
    "backend",
    "model",
    "reasoning_effort",
    "timeout_seconds",
    "elapsed_ms",
    "tool_calls",
    "usage",
    "prompt_chars",
    "prompt_sha256",
    "response_sha256",
    "name",
    "attempt",
}


def test_sha256_text_is_utf8():
    assert sha256_text("가나") == hashlib.sha256("가나".encode()).hexdigest()


def test_call_names():
    assert CALL_NAMES == ("extract_keywords", "generate_sql", "refine_sql")
    assert set(DEFAULT_RESPONSES) == set(CALL_NAMES)


def test_fake_picks_by_name_not_prompt():
    fb = FakeBackend()
    out = fb.call("SELECT anything refine", None, name="generate_sql", attempt=1)
    assert out.response == DEFAULT_RESPONSES["generate_sql"]
    assert out.failure is None
    assert fb.calls == [{"name": "generate_sql", "attempt": 1, "prompt": "SELECT anything refine"}]


def test_fake_list_responses_in_order_then_repeat_last():
    fb = FakeBackend({"refine_sql": ["a", "b"]})
    got = [fb.call("p", None, name="refine_sql", attempt=i).response for i in range(1, 4)]
    assert got == ["a", "b", "b"]


def test_fake_failures():
    out = FakeBackend(fail="exit").call("p", None, name="extract_keywords", attempt=1)
    assert out.response is None and out.failure.type == "FakeExit"
    out = FakeBackend(fail="limit").call("p", None, name="extract_keywords", attempt=1)
    assert out.failure.type == "FakeUsageLimit"


def test_call_files(tmp_path):
    fb = FakeBackend()
    out = fb.call("질문 프롬프트", tmp_path / "1", name="extract_keywords", attempt=2)
    assert (tmp_path / "1" / "prompt.txt").read_text(encoding="utf-8") == "질문 프롬프트"
    assert (tmp_path / "1" / "response.txt").read_text(encoding="utf-8") == DEFAULT_RESPONSES["extract_keywords"]
    meta = json.loads((tmp_path / "1" / "llm-meta.json").read_text(encoding="utf-8"))
    assert set(meta) == META_KEYS
    assert meta == out.meta
    assert meta["prompt_chars"] == 7 and meta["attempt"] == 2 and meta["name"] == "extract_keywords"
    assert meta["prompt_sha256"] == sha256_text("질문 프롬프트")
    assert meta["elapsed_ms"] == 0 and meta["tool_calls"] == 0 and meta["usage"] is None


def test_no_files_without_call_dir(tmp_path):
    FakeBackend().call("p", None, name="generate_sql", attempt=1)
    assert list(tmp_path.iterdir()) == []


def test_failed_call_writes_no_response(tmp_path):
    FakeBackend(fail="exit").call("p", tmp_path / "1", name="generate_sql", attempt=1)
    assert sorted(p.name for p in (tmp_path / "1").iterdir()) == ["llm-meta.json", "prompt.txt"]


def test_write_call_files_keeps_newlines(tmp_path):
    write_call_files(tmp_path / "c", "a\r\nb", "x\n", {"k": 1})
    assert (tmp_path / "c" / "prompt.txt").read_bytes() == b"a\r\nb"


def test_scripted_in_order():
    sb = ScriptedBackend([ScriptedCall("extract_keywords", "[]"), ScriptedCall("generate_sql", "SELECT 1")])
    assert sb.remaining() == 2
    assert sb.call("p", None, name="extract_keywords", attempt=1).response == "[]"
    assert (sb.used(), sb.remaining()) == (1, 1)
    assert sb.call("p", None, name="generate_sql", attempt=1).response == "SELECT 1"
    out = sb.call("p", None, name="refine_sql", attempt=1)
    assert out.failure.type == "ScriptExhausted"
    assert (sb.used(), sb.remaining()) == (2, 0)


def test_scripted_mismatch_does_not_consume():
    sb = ScriptedBackend([ScriptedCall("generate_sql", "SELECT 1")])
    out = sb.call("p", None, name="extract_keywords", attempt=1)
    assert out.failure.type == "ScriptMismatch"
    assert sb.remaining() == 1
    assert sb.call("p", None, name="generate_sql", attempt=1).response == "SELECT 1"


def test_default_settings():
    assert FakeBackend().settings == LlmSettings("fake", "fake-model", "none", 1)
    assert ScriptedBackend([]).settings.backend == "scripted"


def test_usage_totals():
    claude = {"input_tokens": 50, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 4}
    assert usage_totals(claude) == (65, 4)
    codex = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7, "reasoning_output_tokens": 3}
    assert usage_totals(codex) == (100, 7)
    assert usage_totals(None) is None
    assert usage_totals({}) == (0, 0)


def test_is_usage_limit():
    assert is_usage_limit("CodexUsageLimit") and is_usage_limit("FakeUsageLimit") and is_usage_limit("ClaudeUsageLimit")
    assert not is_usage_limit("CodexExit") and not is_usage_limit(None) and not is_usage_limit("")
