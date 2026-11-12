"""fixtures/shop.sql에서 손으로 계산한 기대값."""

DESC1 = "A long description text that is over twenty-five chars #1"
DESC2 = "A long description text that is over twenty-five chars #2"

# 값 목록 규칙 1~9번:
# - product.code(TEXT 기본 키)와 order.code(같은 이름)는 기본 키 이름이라 빠진다 → 2
# - homepage_url·created_date·ownerId·customer_id는 이름 조건으로 빠진다 → 4
# - big.payload는 값 2,000개·평균 31자라 크기 조건으로 빠진다 → 1
# - mixed.empty_t는 모두 NULL이라 합이 NULL → 세지 않고 빠진다
# - mixed.lower_t는 소문자 text로 선언했지만 table_info가 TEXT로 돌려주므로 남는다
# - mixed.num_text(INT_TEXT)는 정수 친화라 숫자로 저장되고 str()로 "3", "2.5"
# - 값이 없는 테이블(big, cats, empty_table)도 [테이블, []]로 들어간다. sqlite_sequence는 건너뛴다
VALUES = [
    ("product", [("product_name", ["Apple Juice", "Banana Milk", "Café Latte"]), ("description", [DESC1, DESC2])]),
    ("order", [("status", ["paid", "shipped"]), ("customer_ref", ["1", "2"])]),
    ("customer", [("name", ["가나다라", "a😀bc", "ab", "ｚｚｚ", "xyz"])]),
    ("big", []),
    ("cats", []),
    ("mixed", [("num_text", ["3", "2.5"]), ("lower_t", ["q", "r", "s"])]),
    ("empty_table", []),
]
PRIMARY_KEY_NAMES = ["code", "order_no", "cid"]
EXCLUDED_BY_PRIMARY_KEY = 2
EXCLUDED_BY_NAME = 4
EXCLUDED_BY_SIZE = 1
VALUE_COUNT = 19
# 3글자 미만: "1", "2", "ab", "3", "q", "r", "s"
VALUES_WITHOUT_SHINGLE = 7

# 프로파일(일부 열)
PROFILE_TABLES = [
    ("product", 3),
    ("order", 3),
    ("sqlite_sequence", 1),
    ("customer", 5),
    ("big", 2000),
    ("cats", 20),
    ("mixed", 7),
    ("empty_table", 0),
]
PROFILE_COLUMNS = {
    ("cats", "c19"): {"kind": "values", "chess_distinct": 19, "categorical": True, "distinct": 19, "nulls": 0},
    ("cats", "c19n"): {
        "kind": "range",
        "chess_distinct": 20,
        "categorical": False,
        "min": "1",
        "max": "19",
        "nulls": 1,
    },
    ("cats", "c20"): {"kind": "range", "chess_distinct": 20, "categorical": False, "min": "1", "max": "20"},
    ("big", "payload"): {"kind": "length", "min_length": 31, "max_length": 31, "categorical": False},
    ("big", "raw"): {"kind": "counts", "types": {"blob": 2000}},
    ("mixed", "empty_t"): {"kind": "empty", "nulls": 7, "distinct": 0, "chess_distinct": 1, "types": {}},
    ("empty_table", "label"): {"kind": "empty", "rows": 0},
    ("product", "price"): {"kind": "values", "types": {"real": 2}, "nulls": 1},
}
# 섞인 형식: GROUP BY typeof 순서(blob, integer, real, text)와 값 정렬(수 내림차순, 같으면 숫자 < 글(대문자 < 소문자) < BLOB)
MIXED_M_TYPES = {"blob": 1, "integer": 2, "real": 1, "text": 3}
MIXED_M_VALUES = [
    {"repr": "2", "type": "integer", "count": 2},
    {"repr": "'a'", "type": "text", "count": 2},
    {"repr": "2.5", "type": "real", "count": 1},
    {"repr": "'B'", "type": "text", "count": 1},
    {"repr": "b'\\x00'", "type": "blob", "count": 1},
]
# 수가 모두 1일 때 글은 UTF-8 바이트 순
CUSTOMER_NAME_ORDER = ["'ab'", "'a😀bc'", "'xyz'", "'가나다라'", "'ｚｚｚ'"]
C19_FIRST = {"repr": "19", "type": "integer", "count": 2}
PROFILE_COUNTS = {
    "tables": 8,
    "columns": 28,
    "kinds": {"values": 20, "range": 3, "length": 1, "counts": 1, "empty": 3},
    "categorical": 23,
    "values_listed": 71,
    "mixed_types": 2,
}
