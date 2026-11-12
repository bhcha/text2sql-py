import json
import struct

from t2s.prep import value_index as vi


def test_write_index_bytes_small(tmp_path):
    values = [("t", [("c", ["abcd", "abc", "ab"])]), ("u", [])]
    idx = vi.build_shingle_index(values)
    files = vi.write_index(tmp_path, values, idx)
    assert set(files) == set(vi.INDEX_FILES)
    assert (tmp_path / "values.json").read_bytes() == b'{"values":[["t",[["c",["abcd","abc","ab"]]]],["u",[]]]}\n'
    assert (tmp_path / "shingles.json").read_bytes() == b'{"shingles":["abc","bcd"]}\n'
    assert (tmp_path / "postings.u32").read_bytes() == struct.pack("<3I", 0, 1, 0)
    assert (tmp_path / "offsets.u32").read_bytes() == struct.pack("<3I", 0, 2, 3)
    assert (tmp_path / "sizes.u32").read_bytes() == struct.pack("<3I", 2, 1, 0)


def test_non_ascii_kept_verbatim(tmp_path):
    values = [("t", [("c", ["가나다"])])]
    vi.write_index(tmp_path, values, vi.build_shingle_index(values))
    assert (tmp_path / "values.json").read_bytes() == '{"values":[["t",[["c",["가나다"]]]]]}\n'.encode()


def test_shingle_order_is_utf16(tmp_path):
    # U+FF5A(전각 z)와 U+1F600(이모지): 코드 포인트 순은 ｚ < 😀, UTF-16 코드 단위 순은 😀(D83D) < ｚ(FF5A)
    values = [("t", [("c", ["ｚｚｚ", "😀bc"])])]
    vi.write_index(tmp_path, values, vi.build_shingle_index(values))
    keys = json.loads((tmp_path / "shingles.json").read_text(encoding="utf-8"))["shingles"]
    assert keys == ["😀bc", "ｚｚｚ"]
    assert sorted(keys) == ["ｚｚｚ", "😀bc"]  # Python 기본 정렬과는 다르다
    assert (tmp_path / "postings.u32").read_bytes() == struct.pack("<2I", 1, 0)


def test_read_index_roundtrip(tmp_path, shop_db):
    from t2s.datasource.sqlite import SqliteSource

    r = vi.unique_values(SqliteSource(shop_db))
    idx = vi.build_shingle_index(r.values)
    vi.write_index(tmp_path, r.values, idx)
    values, back = vi.read_index(tmp_path)
    assert values == r.values
    assert back.sizes == idx.sizes
    assert back.postings == idx.postings
    assert back.empty == idx.empty
    assert back.entries == idx.entries
