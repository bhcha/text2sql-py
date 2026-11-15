"""evidence not_used 프롬프트 회귀: 키워드·생성·고치기 프롬프트가 evidence 모드를 넣기 전과 글자 하나 다르지 않다.

아래 sha256은 evidence 모드를 넣기 전 코드(커밋 55b57aa)로 shop 픽스처와 가짜 백엔드에서 뽑아 고정했다.
"""

import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.llm.base import sha256_text
from t2s.llm.fake import FakeBackend
from t2s.pipeline.run import answer
from t2s.prep import assets
from t2s.prep.embed import HashEmbedder

QUESTION = "How many Apple Juice orders are paid?"

# (호출 이름, 차례) -> 프롬프트 sha256
EXPECTED = {
    ("extract_keywords", 1): "b027bbde739d7cdc8dd4f5b3dd747eb925806cdf0ad2fb75c1700f194e723660",
    ("generate_sql", 1): "301b06f36716d7deb739155c34fb83acd83df2761d44e9f0481118469ef11217",
    ("refine_sql", 1): "bcd0d497d83dc5f92d60c41342a691179552140a7c7700e87d482be3a0ca354b",
    ("refine_sql", 2): "16f1e7e08d62ef19cf3215fceeb712ddd821367de8e2be44a695d5046eb41a08",
    ("refine_sql", 3): "16f1e7e08d62ef19cf3215fceeb712ddd821367de8e2be44a695d5046eb41a08",
}


@pytest.fixture
def shop(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "assets"
    assets.build_assets(src, out, dataset="shop", source_name="shop", model_hashes={})
    return src, assets.load_assets(src, out, {})


def _prompts(shop, **kw) -> dict:
    src, loaded = shop
    fb = FakeBackend({"generate_sql": "SELECT * FROM nope", "refine_sql": "```sql\nSELECT * FROM nope2\n```"})
    answer(QUESTION, src, loaded, fb, embedder=HashEmbedder(), **kw)
    return {(c["name"], c["attempt"]): sha256_text(c["prompt"]) for c in fb.calls}


def test_not_used_prompts_unchanged(shop):
    assert _prompts(shop) == EXPECTED
