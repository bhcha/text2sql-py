from conftest import make_db
from t2s.datasource.sqlite import SqliteSource


def test_wal_db_side_files_untouched(tmp_path):
    db = make_db(tmp_path / "w.sqlite", "CREATE TABLE t (a TEXT); INSERT INTO t VALUES ('x');", wal=True)
    # 만들기 연결을 닫으면 -wal·-shm이 지워진다. 머리는 WAL(18-19바이트 = 2)로 남는다.
    assert db.read_bytes()[18:20] == b"\x02\x02"
    src = SqliteSource(db)
    before_names = sorted(p.name for p in tmp_path.iterdir())
    before = (src.fingerprint(), src.side_files())
    assert src.execute("SELECT * FROM t", 5, 5).head == [("x",)]
    src.tables()
    src.column_profile("t", "a")
    assert sorted(p.name for p in tmp_path.iterdir()) == before_names
    assert (src.fingerprint(), src.side_files()) == before
