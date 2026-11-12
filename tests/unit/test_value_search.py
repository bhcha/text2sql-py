import numpy as np
import pytest

from t2s.pipeline.value_search import (
    CachedEmbedder,
    EntityPacket,
    SearchPacket,
    column_value,
    dot,
    search_packets,
    select_per_column,
    similar_entities,
    via_edit_distance,
    via_embedding,
    via_jaccard,
)
from t2s.prep.value_index import build_shingle_index


class TableEmbedder:
    """글 → 정해 둔 2차원 벡터(나머지 칸 0). 계산한 글을 센다."""

    name = "table"

    def __init__(self, table):
        self.table = table
        self.seen = []

    def embed(self, text):
        self.seen.append(text)
        v = np.zeros(4, dtype=np.float32)
        v[:2] = self.table.get(text, (0.0, 0.0))
        return v

    def token_count(self, text):
        return 1


def unit(cos):
    return (cos, float(np.sqrt(1 - cos * cos)))


def test_column_value():
    assert column_value("a = b") == ("a", "b")
    assert column_value("a =") == ("a", None)
    assert column_value("abc") == (None, None)
    assert column_value("x= y = z") == ("x", "y = z")


def test_search_packets_split_and_sort():
    got = search_packets(["  New York city ", "a=b"])
    pairs = [(p.keyword, p.substring) for p in got]
    assert pairs == [
        ("a=b", "a=b"),
        ("a=b", "b"),
        ("New York city", "New York city"),
        ("New York city", "York city"),
        ("New York city", "New York"),
        ("New York city", "city"),
        ("New York city", "New"),
    ]


def test_search_packets_keeps_duplicates_stably():
    got = search_packets(["a a"])
    assert [(p.substring) for p in got] == ["a a", "a", "a"]


def test_via_jaccard_groups_table_then_column():
    values = [("t1", [("c1", ["abcd", "xyz"]), ("c2", ["abce"])]), ("t2", [("c3", ["abcd"])])]
    index = build_shingle_index(values)
    out = via_jaccard(index, [SearchPacket("k", "abcd")])
    assert [(p.table, p.column, p.value) for p in out] == [
        ("t1", "c1", "abcd"),
        ("t1", "c2", "abce"),
        ("t2", "c3", "abcd"),
    ]


def test_edit_distance_threshold():
    ps = [EntityPacket("k", "abc", "t", "c", v, 1.0) for v in ("ABC", "abxxxxxxx", "zzzz")]
    out = via_edit_distance(ps)
    assert [p.value for p in out] == ["ABC", "abxxxxxxx"]
    assert out[0].edit == 1.0 and out[1].edit == pytest.approx(2 * 2 / 12)


def test_embedding_threshold_and_keep_ratio():
    table = {"q": (1.0, 0.0), "v1": unit(0.95), "v2": unit(0.86), "v3": unit(0.6), "v4": unit(0.59)}
    emb = CachedEmbedder(TableEmbedder(table))
    margins: list = []
    ps = [EntityPacket("k", "q", "t", "c", v, 1.0, edit=1.0) for v in ("v1", "v2", "v3", "v4")]
    out = via_embedding(ps, emb, margins)
    assert [p.value for p in out] == ["v1", "v2", "v3"]
    per = select_per_column(out, margins)
    # 0.9 × 0.95 = 0.855: v2(0.86) 남고 v3(0.6) 빠짐
    assert per == [("t", [("c", ["v1", "v2"])])]
    assert {m["kind"] for m in margins} == {"embedding", "keep_ratio"}


def test_keep_ratio_on_edit_first():
    ps = [
        EntityPacket("k", "q", "t", "c", "a", 1.0, edit=1.0, embedding=0.7),
        EntityPacket("k", "q", "t", "c", "b", 1.0, edit=0.85, embedding=0.99),
    ]
    assert select_per_column(ps, []) == [("t", [("c", ["a"])])]


def test_dot_is_float64():
    a = np.asarray([0.1, 0.2], dtype=np.float32)
    assert dot(a, a) == float(np.float64(a[0]) ** 2 + np.float64(a[1]) ** 2)


def test_cached_embedder_once():
    inner = TableEmbedder({"x": (1.0, 0.0)})
    c = CachedEmbedder(inner)
    v1, v2 = c.embed("x"), c.embed("x")
    assert inner.seen == ["x"] and c.size() == 1
    assert v1.dtype == np.float64 and v1 is v2


def test_similar_entities_margins():
    values = [("t", [("c", ["New York", "Newark"])])]
    index = build_shingle_index(values)
    table = {"New York": (1.0, 0.0), "New": unit(0.80001), "York": (0.0, 1.0), "Newark": unit(0.6000001)}
    vs = similar_entities(index, ["New York"], CachedEmbedder(TableEmbedder(table)))
    assert vs.per_column == [("t", [("c", ["New York"])])]
    assert vs.min_margin is not None and vs.min_margin < 1e-4
    assert any(n["value"] == "Newark" and n["kind"] == "embedding" for n in vs.near)
    assert vs.comparisons > 0 and vs.margin_counts["0.0001"] >= 1
