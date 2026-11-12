import time

import pytest

from t2s.datasource.sqlite import SqliteSource

LONG = {
    "cross_join": "SELECT COUNT(*) FROM big a, big b, big c",
    "recursive_cte": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r",
    "order_by_random": "SELECT a.n, b.n FROM big a, big b, big c ORDER BY random() LIMIT 1",
    "group_by": "SELECT a.n * 4000000 + b.n * 2000 + c.n AS k, COUNT(*) FROM big a, big b, big c GROUP BY k LIMIT 1",
    "like_scan": "SELECT COUNT(*) FROM big a, big b, big c WHERE a.payload || b.payload || c.payload LIKE '%zzz%'",
}


@pytest.mark.parametrize("name", sorted(LONG))
def test_long_query_times_out(shop_db, name):
    src = SqliteSource(shop_db)
    started = time.monotonic()
    r = src.execute(LONG[name], 1, 5)
    took = time.monotonic() - started
    assert r.status == "timeout", r
    assert r.error == "exceeded 1 seconds"
    assert took < 1.5
    # 끊긴 뒤 같은 소스로 다음 질의가 정상
    assert src.execute("SELECT COUNT(*) FROM customer", 1, 5).head == [(5,)]
