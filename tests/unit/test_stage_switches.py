"""단계 끄기 칸(value_search, column_profile, refine_rounds, example_rows): 끈 단계 밖의 프롬프트가 바이트 단위로 같다."""

import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.llm.base import sha256_text
from t2s.llm.fake import FakeBackend
from t2s.pipeline import attach, keywords, prompt, schema_text, value_search
from t2s.pipeline.run import PipelineSettings, answer
from t2s.prep import assets
from t2s.prep.embed import HashEmbedder

QUESTION = "How many Apple Juice orders are paid?"
# tests/unit/test_prompt_regression.py의 고정값(기본 설정, 같은 질문·같은 가짜 응답)
KW_SHA = "b027bbde739d7cdc8dd4f5b3dd747eb925806cdf0ad2fb75c1700f194e723660"
GEN_SHA = "301b06f36716d7deb739155c34fb83acd83df2761d44e9f0481118469ef11217"
RESPONSES = {"generate_sql": "SELECT * FROM nope", "refine_sql": "```sql\nSELECT * FROM nope2\n```"}


@pytest.fixture
def shop(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "assets"
    assets.build_assets(src, out, dataset="shop", source_name="shop", model_hashes={})
    return src, assets.load_assets(src, out, {})


def _run(shop, responses=None, **settings):
    src, loaded = shop
    fb = FakeBackend({**RESPONSES, **(responses or {})})
    trace = answer(QUESTION, src, loaded, fb, embedder=HashEmbedder(), settings=PipelineSettings(**settings))
    return fb.calls, trace


def _by_name(calls, name):
    return [c["prompt"] for c in calls if c["name"] == name]


def test_value_search_off_matches_empty_keywords(shop):
    off, trace = _run(shop, value_search=False)
    on_empty, _ = _run(shop, {"extract_keywords": "[]"})
    assert _by_name(off, "extract_keywords") == []
    assert _by_name(off, "generate_sql") == _by_name(on_empty, "generate_sql")
    assert _by_name(off, "refine_sql") == _by_name(on_empty, "refine_sql")
    assert len(_by_name(off, "refine_sql")) == 3
    for sid in ("extract_keywords", "retrieve_entity", "attach_values"):
        assert trace.stages[sid] == {"skipped": True}
    assert trace.stages["attach_profile"]["tables"] > 0


def test_column_profile_off_uses_value_text(shop):
    src, loaded = shop
    calls, trace = _run(shop, column_profile=False)
    schema = schema_text.build_schema(src, 3)
    kws = keywords.parse_keywords(FakeBackend().responses["extract_keywords"]).items
    vs = value_search.similar_entities(loaded.index, [str(x) for x in kws], value_search.CachedEmbedder(HashEmbedder()))
    attached = attach.attach_values(schema.tables, schema.blocks, vs.per_column, loaded.values)
    expected = prompt.combined_prompt(attached.text, QUESTION, None, src.dialect.name)
    [gen] = _by_name(calls, "generate_sql")
    assert gen == expected
    assert attach.PROFILE_LEAD not in gen
    assert attach.VALUES_LEAD in gen
    for p in _by_name(calls, "refine_sql"):
        assert attached.text in p and attach.PROFILE_LEAD not in p
    assert _by_name(calls, "extract_keywords") and sha256_text(_by_name(calls, "extract_keywords")[0]) == KW_SHA
    assert trace.stages["attach_profile"] == {"skipped": True}


def test_refine_off_keeps_other_prompts(shop):
    calls, trace = _run(shop, refine_rounds=0)
    assert _by_name(calls, "refine_sql") == []
    assert [sha256_text(p) for p in _by_name(calls, "extract_keywords")] == [KW_SHA]
    assert [sha256_text(p) for p in _by_name(calls, "generate_sql")] == [GEN_SHA]
    assert trace.sql == "SELECT * FROM nope" and trace.stages["execute"] == {"skipped": True}


def test_example_rows_off(shop):
    calls, _ = _run(shop, example_rows=0)
    assert [sha256_text(p) for p in _by_name(calls, "extract_keywords")] == [KW_SHA]
    [gen] = _by_name(calls, "generate_sql")
    assert "example rows" not in gen
    on, _ = _run(shop)
    assert "example rows" in _by_name(on, "generate_sql")[0]


def test_all_off_together(shop):
    calls, trace = _run(shop, value_search=False, column_profile=False, refine_rounds=0, example_rows=0)
    assert [c["name"] for c in calls] == ["generate_sql"]
    src, _ = shop
    schema = schema_text.build_schema(src, 0)
    assert calls[0]["prompt"] == prompt.combined_prompt(schema.text, QUESTION, None, src.dialect.name)
    assert trace.stages["attach_profile"] == {"skipped": True}


def test_from_mapping_checks_types():
    s = PipelineSettings.from_mapping({"value_search": False, "column_profile": True, "example_rows": 0})
    assert s.value_search is False and s.column_profile is True and s.example_rows == 0
    for bad in ({"value_search": 0}, {"column_profile": "no"}, {"example_rows": -1}, {"refine_rounds": True}):
        with pytest.raises(ValueError):
            PipelineSettings.from_mapping(bad)
    assert PipelineSettings.from_mapping({}) == PipelineSettings()
    assert PipelineSettings().value_search is True and PipelineSettings().column_profile is True


def test_old_pipeline_table_restores_defaults():
    old = {"example_rows": 3, "keyword_attempts": 3, "exec_timeout_seconds": 120, "refine_rounds": 3, "keep_rows": 5}
    s = PipelineSettings(**old)
    assert s.value_search is True and s.column_profile is True
