from fixtures import expected_shop as E
from t2s.datasource.sqlite import SqliteSource
from t2s.prep import value_index as vi


def test_excluded_by_name_boundaries():
    for name in (
        "customer_id",
        "Order ID",
        "homepage_url",
        "EMAIL",
        "website",
        "start_time",
        "Phone",
        "created_date",
        "address",
        "ownerId",
    ):
        assert vi.excluded_by_name(name), name
    for name in ("name", "status", "identity", "ID", "id", "Idea"):
        assert not vi.excluded_by_name(name), name


def test_keep_by_size_boundaries():
    assert vi.keep_by_size("product_name", 4_999_999, 1000)
    assert not vi.keep_by_size("product_name", 5_000_000, 1000)
    assert vi.keep_by_size("x", 2_000_000 - 1, 100_000)  # 평균 < 25
    assert not vi.keep_by_size("x", 2_000_000, 100_000)
    assert not vi.keep_by_size("x", 2500, 100)  # 평균 25는 남지 않음, 수 100은 < 100 아님
    assert vi.keep_by_size("x", 2499, 100)
    assert vi.keep_by_size("x", 10_000_000, 99)  # 수 < 100


def test_unique_values_matches_hand_expectation(shop_db):
    r = vi.unique_values(SqliteSource(shop_db))
    assert r.values == E.VALUES
    assert r.primary_key_names == E.PRIMARY_KEY_NAMES
    assert r.excluded_by_primary_key == E.EXCLUDED_BY_PRIMARY_KEY
    assert r.excluded_by_name == E.EXCLUDED_BY_NAME
    assert r.excluded_by_size == E.EXCLUDED_BY_SIZE
    assert r.query_errors == 0


def test_shingles():
    assert list(vi.shingles("abcab")) == ["abc", "bca", "cab"]
    assert list(vi.shingles("aaaa")) == ["aaa"]
    assert list(vi.shingles("ab")) == []
    assert list(vi.shingles("a😀bc")) == ["a😀b", "😀bc"]  # 코드 포인트 단위


def test_build_index_from_fixture(shop_db):
    r = vi.unique_values(SqliteSource(shop_db))
    idx = vi.build_shingle_index(r.values)
    assert len(idx.entries) == E.VALUE_COUNT
    assert len(idx.empty) == E.VALUES_WITHOUT_SHINGLE
    assert idx.entries[0] == vi.ValueEntry("product", "product_name", "Apple Juice")


def _small():
    values = [("t", [("c", ["abcd", "abc", "ab", "xbcd", "q"])])]
    return vi.build_shingle_index(values)


def test_build_shingle_index_small():
    idx = _small()
    assert idx.sizes == [2, 1, 0, 2, 0]
    assert idx.postings == {"abc": [0, 1], "bcd": [0, 3], "xbc": [3]}
    assert idx.empty == [2, 4]


def test_jaccard_top_n():
    idx = _small()
    # 질의 "abcd": {abc, bcd}. 0번 2/(2+2-2)=1, 1번 1/(2+1-1)=0.5, 3번 1/(2+2-1)=1/3
    assert vi.jaccard_top_n(idx, "abcd") == [(0, 1.0), (1, 0.5), (3, 1 / 3)]
    # 조각 없는 질의는 조각 없는 값들과 1.0
    assert vi.jaccard_top_n(idx, "z") == [(2, 1.0), (4, 1.0)]
    assert vi.jaccard_top_n(idx, "abcd", top_n=1) == [(0, 1.0)]
    # 동점이면 번호 순: "bcd"는 0번 1/2, 3번 1/2
    assert vi.jaccard_top_n(idx, "bcd") == [(0, 0.5), (3, 0.5)]
    assert vi.jaccard_top_n(idx, "zzz") == []


def test_top_n_cut_at_ten():
    values = [("t", [("c", [f"abc{i:02d}" for i in range(15)])])]
    idx = vi.build_shingle_index(values)
    hits = vi.jaccard_top_n(idx, "abc")
    assert len(hits) == 10
    assert [h[0] for h in hits] == list(range(10))
