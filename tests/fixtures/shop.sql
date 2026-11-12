-- 손으로 쓴 시험용 DB. tests/conftest.py의 shop_db가 임시 폴더에 SQLite 파일로 만든다(이진 파일은 커밋하지 않는다).
-- 담은 경우와 기대값은 tests/fixtures/expected_shop.py에 있다.

CREATE TABLE product (
    code TEXT PRIMARY KEY,
    product_name TEXT,
    homepage_url TEXT,
    created_date TEXT,
    ownerId TEXT,
    price REAL,
    description TEXT
);

-- 예약어 이름, AUTOINCREMENT(sqlite_sequence가 생긴다), 다른 테이블의 TEXT 기본 키와 같은 이름의 열,
-- 열을 적은 외래키와 열을 안 적은 외래키
CREATE TABLE "order" (
    order_no INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT REFERENCES product(code),
    customer_id TEXT,
    status TEXT,
    customer_ref TEXT REFERENCES customer
);

-- 비ASCII·BMP 밖 글자, 3글자 미만 값
CREATE TABLE customer (
    cid INTEGER PRIMARY KEY,
    name TEXT
);

-- 2,000행: 값 100개 이상에 평균 25자 이상인 열(값 목록에서 빠짐), BLOB만 있는 열, 시간 제한 시험용
CREATE TABLE big (
    n INTEGER,
    payload TEXT,
    raw BLOB
);

-- 범주형 경계: 서로 다른 값 19개 / 19개 + NULL / 20개
CREATE TABLE cats (
    c19 INTEGER,
    c19n INTEGER,
    c20 INTEGER
);

-- 섞인 형식(형식 없는 열), 이름에 TEXT가 든 정수 친화 열, 소문자 text 열(table_info가 대문자로 돌려준다), 모두 NULL인 TEXT 열
CREATE TABLE mixed (
    m,
    num_text INT_TEXT,
    lower_t text,
    empty_t TEXT
);

-- 빈 테이블
CREATE TABLE empty_table (
    id INTEGER,
    label TEXT
);

INSERT INTO product VALUES
    ('P1', 'Apple Juice', 'http://a.example', '2024-01-01', 'u1', 1.5, 'A long description text that is over twenty-five chars #1'),
    ('P2', 'Banana Milk', 'http://b.example', '2024-01-02', 'u2', 2, 'A long description text that is over twenty-five chars #2'),
    ('P3', 'Café Latte', 'http://c.example', '2024-01-03', 'u3', NULL, 'A long description text that is over twenty-five chars #1');

INSERT INTO "order" (code, customer_id, status, customer_ref) VALUES
    ('P1', 'C1', 'paid', '1'),
    ('P2', 'C2', 'shipped', '2'),
    ('P1', 'C1', 'paid', NULL);

INSERT INTO customer VALUES
    (1, '가나다라'),
    (2, 'a😀bc'),
    (3, 'ab'),
    (4, 'ｚｚｚ'),
    (5, 'xyz');

INSERT INTO big
    WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 2000)
    SELECT n, 'payload-value-padding-text-' || printf('%04d', n), CAST(printf('%04d', n) AS BLOB) FROM s;

INSERT INTO cats
    WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 20)
    SELECT min(n, 19), CASE WHEN n = 20 THEN NULL ELSE n END, n FROM s;

INSERT INTO mixed VALUES
    (2, 3, 'q', NULL),
    (2.5, 2.5, 'r', NULL),
    ('B', 3, 's', NULL),
    ('a', NULL, NULL, NULL),
    (x'00', NULL, NULL, NULL),
    (2, NULL, NULL, NULL),
    ('a', NULL, NULL, NULL);
