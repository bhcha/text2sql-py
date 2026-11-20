import hashlib
import sqlite3
from collections import Counter

from t2s.eval.routing import execsig as X
from t2s.eval.routing.rowf1 import f1_counters


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, None if i == 6 else f"v{i}") for i in range(1, 8)])
    con.execute("CREATE TABLE n (a INTEGER, b TEXT)")
    con.execute("INSERT INTO n VALUES (1, NULL)")
    con.commit()
    con.close()


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_signals_and_safety(tmp_path):
    db = tmp_path / "x.sqlite"
    make_db(db)
    before = sha(db)
    ex = X.Executor({"x": db}, timeout_s=5)
    rows = ex.run("x", "SELECT a FROM t")
    assert rows["status"] == "rows" and rows["row_count"] == 7 and rows["column_count"] == 1
    assert X.signals(rows) == {"error": False, "empty": False, "null": False}
    assert X.signals(ex.run("x", "SELECT a FROM t WHERE a > 100"))["empty"]
    assert X.signals(ex.run("x", "SELECT * FROM n"))["null"]
    late = ex.run("x", "SELECT a, b FROM t ORDER BY a")  # NULL은 6행째에만
    assert late["null_head"] is False and late["null_any"] is True
    assert X.signals(late)["null"] is False
    assert ex.run("x", "SELEC a FROM t")["status"] == "error"
    multi = ex.run("x", "SELECT 1; SELECT 2")
    assert multi["status"] == "error" and multi["reason"] == "multi_statement"
    assert ex.run("x", "INSERT INTO t VALUES (9, 'z')")["status"] == "error"
    assert ex.run("x", "DROP TABLE t")["status"] == "error"
    assert ex.run("x", "SELECT a FROM t;")["status"] == "rows"  # 끝 ';' 하나는 한 문장
    assert X.signals(ex.run("x", None))["error"]
    ex.close()
    assert sha(db) == before
    for suffix in ("-wal", "-shm", "-journal"):
        assert not (tmp_path / f"x.sqlite{suffix}").exists()


def test_timeout(tmp_path):
    db = tmp_path / "x.sqlite"
    make_db(db)
    ex = X.Executor({"x": db}, timeout_s=0.05)
    slow = "WITH RECURSIVE c(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM c WHERE i < 100000000) SELECT count(*) FROM c"
    r = ex.run("x", slow)
    assert r["status"] == "timeout"
    assert X.signals(r)["error"]
    ex.close()


def test_cache_runs_once(tmp_path):
    db = tmp_path / "x.sqlite"
    make_db(db)
    ex = X.Executor({"x": db})
    ex.run("x", "SELECT a FROM t")
    ex.run("x", "SELECT a FROM t")
    assert ex.executed == 1
    path = tmp_path / "cache.jsonl"
    X.append_cache(path, ex.cache)
    assert X.load_cache(path) == ex.cache
    ex.close()


def test_row_hashes_feed_f1(tmp_path):
    db = tmp_path / "x.sqlite"
    make_db(db)
    ex = X.Executor({"x": db})
    a = ex.run("x", "SELECT a FROM t WHERE a <= 4")
    b = ex.run("x", "SELECT a * 1.0 FROM t WHERE a <= 2")  # 1.0과 1은 같은 행
    r = f1_counters(Counter(b["hashes"]), Counter(a["hashes"]))
    assert (r["precision"], r["recall"]) == (1.0, 0.5)
    assert X.set_digest(a["hashes"]) != X.set_digest(b["hashes"])
    ex.close()
