import json

from t2s.runner import records as R


def test_prediction_value():
    assert (
        R.prediction_value({"status": "generated", "sql": "SELECT 1", "db_id": "a"}) == "SELECT 1\t----- bird -----\ta"
    )
    for status in ("no_sql", "exception", "not_started"):
        v = R.prediction_value({"status": status, "sql": None, "db_id": "b"})
        assert v == "SELECT 1 FROM __no_sql__\t----- bird -----\tb"


def test_write_predictions_order(tmp_path):
    rows = [{"position": i, "status": "generated", "sql": f"S{i}", "db_id": "d"} for i in (2, 0, 10, 1)]
    sha = R.write_predictions(tmp_path, rows)
    data = json.loads((tmp_path / "predictions.json").read_text())
    assert list(data) == ["0", "1", "2", "10"]
    assert sha == R.sha256_file(tmp_path / "predictions.json")


def test_atomic_write_and_jsonl(tmp_path):
    R.write_json_atomic(tmp_path / "a" / "x.json", {"k": "값"})
    assert json.loads((tmp_path / "a/x.json").read_text(encoding="utf-8")) == {"k": "값"}
    assert not (tmp_path / "a/x.json.tmp").exists()
    R.write_jsonl(tmp_path / "r.jsonl", [{"a": 1}, {"b": 2}])
    assert R.read_jsonl(tmp_path / "r.jsonl") == [{"a": 1}, {"b": 2}]
    assert R.read_json_if(tmp_path / "missing.json") is None


def test_read_item_records_skips_attempts(tmp_path):
    R.write_json_atomic(R.item_dir(tmp_path, 0) / "record.json", {"position": 0})
    R.write_json_atomic(R.item_dir(tmp_path, 7) / "record.json", {"position": 7})
    old = tmp_path / "items" / "0007.attempt-1"
    old.mkdir()
    (old / "record.json").write_text('{"position": 7, "old": true}')
    (tmp_path / "items" / "0003").mkdir()  # 기록 없는 폴더
    got = R.read_item_records(tmp_path)
    assert set(got) == {0, 7} and "old" not in got[7]
    assert R.item_dir(tmp_path, 7).name == "0007"


def test_latest_run(tmp_path):
    assert R.latest_run(tmp_path) is None
    for name, at in (("a", "2026-11-13T01:00:00Z"), ("b", "2026-11-13T03:00:00Z"), ("c", "2026-11-13T02:00:00Z")):
        R.write_json_atomic(tmp_path / name / "run.json", {"started_at": at})
    (tmp_path / "ask").mkdir()
    assert R.latest_run(tmp_path) == "b"
    assert R.list_runs(tmp_path) == ["a", "b", "c"]


def test_pid_alive(tmp_path):
    import os
    import subprocess
    import sys

    assert R.pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not R.pid_alive(p.pid)
    assert not R.pid_alive(None)
