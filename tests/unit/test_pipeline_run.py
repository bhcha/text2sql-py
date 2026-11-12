import json

import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.llm.fake import FakeBackend
from t2s.pipeline.run import PipelineSettings, answer
from t2s.pipeline.trace import STAGE_IDS
from t2s.prep import assets
from t2s.prep.embed import HashEmbedder

QUESTION = "How many Apple Juice orders are paid?"


@pytest.fixture
def shop(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "assets"
    assets.build_assets(src, out, dataset="shop", source_name="shop", model_hashes={})
    return src, assets.load_assets(src, out, {})


def run(shop, llm, **kw):
    src, loaded = shop
    return answer(QUESTION, src, loaded, llm, embedder=HashEmbedder(), **kw)


def test_full_run_fills_all_stages(shop, tmp_path):
    src, _ = shop
    before = src.fingerprint()
    fb = FakeBackend()
    t = run(shop, fb, calls_dir=tmp_path / "calls")
    assert t.exception is None and t.sql == "SELECT COUNT(*) FROM product"
    assert [s for s in STAGE_IDS if t.stages[s] is None] == ["refine"]
    assert [c.name for c in t.calls] == ["extract_keywords", "generate_sql"]
    assert [c.k for c in t.calls] == [1, 2]
    for k in ("1", "2"):
        assert {p.name for p in (tmp_path / "calls" / k).iterdir()} == {"prompt.txt", "response.txt", "llm-meta.json"}
    gen_prompt = fb.calls[1]["prompt"]
    assert "/* values similar to the question: " in gen_prompt and "/* column profile: " in gen_prompt
    assert "product_name = ['Apple Juice']" in gen_prompt
    assert t.stages["prompt"]["chars"] == len(gen_prompt)
    assert t.stages["extract"]["branch"] == "starts_sql"
    assert t.stages["execute"]["stop"] == "no_refine_needed"
    assert set(t.versions) == {"python", "sqlite", "extract_rules", "keyword_rules", "refine_template_sha256"}
    assert set(t.timing) >= {"keywords_ms", "values_ms", "fix_ms", "total_ms"}
    json.dumps(t.to_json())
    assert src.fingerprint() == before


def test_keyword_retries_then_continues(shop):
    fb = FakeBackend({"extract_keywords": "not a list at all"})
    t = run(shop, fb)
    kw = t.stages["extract_keywords"]
    assert (kw["ok"], kw["attempts"], kw["keywords"], kw["calls"]) == (False, 3, None, [1, 2, 3])
    assert [c.name for c in t.calls] == ["extract_keywords"] * 3 + ["generate_sql"]
    assert [c.attempt for c in t.calls[:3]] == [1, 2, 3]
    assert len({c["prompt"] for c in fb.calls[:3]}) == 1
    assert t.sql is not None and t.stages["retrieve_entity"]["search_strings"] == 0


def test_keyword_second_attempt_ok(shop):
    fb = FakeBackend({"extract_keywords": ["oops", '["paid"]']})
    t = run(shop, fb)
    assert t.stages["extract_keywords"]["attempts"] == 2 and t.stages["extract_keywords"]["ok"]
    assert [c.parse_ok for c in t.calls[:2]] == [False, True]


def test_non_string_keyword_skips_values(shop):
    t = run(shop, FakeBackend({"extract_keywords": '["paid", 3]'}))
    rv = t.stages["retrieve_entity"]
    assert rv["per_column"] == [] and rv["error"] and t.stages["attach_values"]["attached"] == []
    assert t.stages["extract_keywords"]["non_string"] == 1


def test_empty_generation(shop):
    t = run(shop, FakeBackend({"generate_sql": " \n"}))
    assert t.sql is None and t.exception["type"] == "EmptyResponse"
    assert t.stages["execute"] is None and t.stages["refine"] is None and t.rounds == []


def test_call_failure(shop):
    t = run(shop, FakeBackend(fail="limit"))
    assert t.sql is None and t.exception["type"] == "FakeUsageLimit"
    assert "호출 1(extract_keywords)" in t.exception["message"]
    assert t.stages["schema"] is not None and t.stages["extract_keywords"]["ok"] is False
    assert t.stages["prompt"] is None and len(t.calls) == 1 and t.calls[0].failure == "FakeUsageLimit"


def test_two_refines(shop, tmp_path):
    fb = FakeBackend(
        {
            "generate_sql": "SELECT * FROM nope",
            "refine_sql": [
                "```sql\nSELECT code FROM product WHERE 0\n```",
                "```sql\nSELECT COUNT(*) FROM product\n```",
            ],
        }
    )
    t = run(shop, fb, calls_dir=tmp_path / "c")
    assert [c.k for c in t.calls] == [1, 2, 3, 4]
    assert [(c.name, c.attempt) for c in t.calls[2:]] == [("refine_sql", 1), ("refine_sql", 2)]
    ref = t.stages["refine"]
    assert (ref["calls"], ref["stop"], ref["final"], ref["final_executed"]) == (2, "no_refine_needed", "new", True)
    assert [r["k"] for r in ref["rounds"]] == [3, 4]
    assert ref["fk"]["lines"] == 2
    assert [r["reason"] for r in t.stages["execute"]["rounds"]] == ["error", "no_rows", "ok"]
    assert t.sql == "SELECT COUNT(*) FROM product"
    assert (tmp_path / "c" / "4" / "prompt.txt").read_text(encoding="utf-8").count("[Query to fix]") == 1


def test_refine_failure_is_trace_exception(shop):
    fb = FakeBackend({"generate_sql": "SELECT * FROM nope"})
    t = run(shop, fb, settings=PipelineSettings(refine_rounds=3))
    assert t.sql is not None
    t2 = answer(
        QUESTION,
        shop[0],
        shop[1],
        _FailOn("refine_sql"),
        embedder=HashEmbedder(),
    )
    assert t2.sql is None and t2.exception["type"] == "FakeExit"
    assert t2.stages["execute"]["stop"] == "refine_call_failed"


class _FailOn(FakeBackend):
    def __init__(self, name):
        super().__init__({"generate_sql": "SELECT * FROM nope"})
        self.fail_name = name

    def call(self, prompt, call_dir, *, name, attempt):
        if name == self.fail_name:
            self.fail = "exit"
        out = super().call(prompt, call_dir, name=name, attempt=attempt)
        self.fail = None
        return out


def test_settings_from_mapping():
    s = PipelineSettings.from_mapping(
        {
            "example_rows": 3,
            "keyword_attempts": 2,
            "exec_timeout_seconds": 10,
            "refine_rounds": 3,
            "refine_template": "builtin",
        }
    )
    assert s.keyword_attempts == 2 and s.exec_timeout_seconds == 10
    with pytest.raises(ValueError):
        PipelineSettings.from_mapping({"refine_template": "mac"})
    with pytest.raises(ValueError):
        PipelineSettings.from_mapping({"unknown": 1})
