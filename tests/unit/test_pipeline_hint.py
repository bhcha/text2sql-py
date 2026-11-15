"""evidence used: 같은 글이 키워드·생성·고치기 프롬프트에 글자 그대로 들어가는지(가짜 백엔드, shop 픽스처)."""

import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.llm.base import sha256_text
from t2s.llm.fake import FakeBackend
from t2s.pipeline.run import HINT_RULES_VERSION, answer
from t2s.prep import assets
from t2s.prep.embed import HashEmbedder

QUESTION = "How many Apple Juice orders are paid?"
HINT = "paid refers to status = 'paid'; {QUESTION} {schema} {HINT} stay as written"


@pytest.fixture
def shop(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "assets"
    assets.build_assets(src, out, dataset="shop", source_name="shop", model_hashes={})
    return src, assets.load_assets(src, out, {})


def _run(shop, hint):
    src, loaded = shop
    fb = FakeBackend({"generate_sql": "SELECT * FROM nope"})
    t = answer(QUESTION, src, loaded, fb, embedder=HashEmbedder(), hint=hint)
    return t, {c["name"]: c["prompt"] for c in fb.calls}, fb


def test_hint_goes_into_three_prompts_verbatim(shop):
    t, prompts, fb = _run(shop, HINT)
    assert set(prompts) == {"extract_keywords", "generate_sql", "refine_sql"}
    assert f"Question: {QUESTION}\nHint: {HINT}\n" in prompts["extract_keywords"]
    assert f"-- External Knowledge: {HINT}\n" in prompts["generate_sql"]
    assert f"[Extra knowledge]\n{HINT}\n" in prompts["refine_sql"]
    # 넣은 글 안의 중괄호는 다시 바꾸지 않는다
    for p in prompts.values():
        assert "{QUESTION} {schema} {HINT}" in p
    refine_prompts = [c["prompt"] for c in fb.calls if c["name"] == "refine_sql"]
    assert all(HINT in p for p in refine_prompts) and len(refine_prompts) >= 1
    assert t.stages["comment"] == {"knowledge": "given", "chars": len(HINT), "sha256": sha256_text(HINT)}
    assert t.versions["hint_rules"] == HINT_RULES_VERSION


def test_empty_hint_stays_empty(shop):
    t, prompts, _ = _run(shop, "")
    assert "\nHint: \n" in prompts["extract_keywords"]
    assert "-- External Knowledge: \n" in prompts["generate_sql"]
    assert "-- External Knowledge: None" not in prompts["generate_sql"]
    assert "[Extra knowledge]\n\n" in prompts["refine_sql"]
    assert t.stages["comment"]["knowledge"] == "given" and t.stages["comment"]["chars"] == 0


def test_no_hint_keeps_not_used_shape(shop):
    t, prompts, _ = _run(shop, None)
    assert "-- External Knowledge: None\n" in prompts["generate_sql"]
    assert t.stages["comment"] == {"knowledge": "None"} and "hint_rules" not in t.versions
