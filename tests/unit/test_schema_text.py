from t2s.datasource.sqlite import SqliteSource
from t2s.pipeline.schema_text import build_schema, nice_look_table

CUSTOMER_BLOCK = (
    "CREATE TABLE customer (\n    cid INTEGER PRIMARY KEY,\n    name TEXT\n)"
    " \n "
    "/* \n 3 example rows: \n SELECT * FROM customer LIMIT 3; \n "
    "cid name \n  1 가나다라 \n  2 a😀bc \n  3   ab "
    " \n */"
)


def test_nice_look_table_widths_and_rjust():
    assert nice_look_table(["a", "long"], [(1, "x"), (22, None)]) == " a long \n 1    x \n22 None "


def test_nice_look_table_counts_code_points():
    # 비ASCII·BMP 밖 글자도 Python len() 기준(코드 포인트)
    assert nice_look_table(["n"], [("가나",), ("😀",)]) == " n \n가나 \n 😀 "


def test_nice_look_table_no_rows():
    assert nice_look_table(["id", "label"], []) == "id label \n"


def test_nice_look_table_str_forms():
    out = nice_look_table(["v"], [(None,), (1.0,), (1e-05,), (b"\x00'",)])
    assert out.splitlines() == ["       v ", "    None ", "     1.0 ", "   1e-05 ", 'b"\\x00\'" ']


def test_build_schema_shop(shop_db):
    s = build_schema(SqliteSource(shop_db), 3)
    assert s.tables == ["product", "order", "sqlite_sequence", "customer", "big", "cats", "mixed", "empty_table"]
    assert s.text == "\n\n".join(s.blocks)
    assert s.blocks[s.tables.index("customer")] == CUSTOMER_BLOCK
    assert s.rows["sqlite_sequence"] == 1 and s.rows["empty_table"] == 0 and s.rows["product"] == 3


def test_only_order_by_group_are_backticked(shop_db):
    s = build_schema(SqliteSource(shop_db), 3)
    order = s.blocks[s.tables.index("order")]
    assert "SELECT * FROM `order` LIMIT 3;" in order
    assert "SELECT * FROM product LIMIT 3;" in s.blocks[0]
    assert "SELECT * FROM sqlite_sequence LIMIT 3;" in s.blocks[2]


def test_blob_and_float_in_rows(shop_db):
    s = build_schema(SqliteSource(shop_db), 3)
    big = s.blocks[s.tables.index("big")]
    assert "b'0001'" in big
    product = s.blocks[0]
    assert " 1.5 " in product and "None" in product


def test_num_rows_zero_is_ddl_only(shop_db):
    src = SqliteSource(shop_db)
    s = build_schema(src, 0)
    assert s.blocks == [src.ddl(t) for t in src.tables()]
    assert s.rows == {}


def test_empty_table_block(shop_db):
    s = build_schema(SqliteSource(shop_db), 3)
    assert s.blocks[-1].endswith(" \n */") and "id label \n \n */" in s.blocks[-1]
