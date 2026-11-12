import os
import sys
import threading
import time

from t2s.llm import _proc


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_ok_stdout_stderr_exit_and_stdin(tmp_path):
    r = _proc.run_cli(
        py("import sys; d=sys.stdin.read(); print(d.upper()); print('e', file=sys.stderr); sys.exit(3)"),
        "héllo",
        cwd=tmp_path,
        env=None,
        timeout_seconds=10,
    )
    assert r.exit_code == 3 and r.stdout == "HÉLLO\n" and r.stderr == "e\n"
    assert not r.timed_out and r.spawn_error is None and r.elapsed_ms >= 0


def test_missing_binary(tmp_path):
    r = _proc.run_cli([str(tmp_path / "nope")], "x", cwd=tmp_path, env=None, timeout_seconds=5)
    assert r.exit_code is None and r.spawn_error and "FileNotFoundError" in r.spawn_error


def test_bad_bytes_are_replaced(tmp_path):
    r = _proc.run_cli(
        py("import sys; sys.stdout.buffer.write(b'a\\xffb')"), "", cwd=tmp_path, env=None, timeout_seconds=5
    )
    assert r.stdout == "a" + chr(0xFFFD) + "b"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # 좀비면 아직 보이지만 곧 거둬진다
    return True


def test_timeout_kills_grandchild(tmp_path):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(60)\n"
    )
    t0 = time.monotonic()
    r = _proc.run_cli(py(code), "", cwd=tmp_path, env=None, timeout_seconds=1.5)
    assert r.timed_out and time.monotonic() - t0 < 20
    grandchild = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(grandchild):
            break
        time.sleep(0.1)
    else:
        # 부모가 없어져 init이 거두기 전이면 좀비로 남을 수 있다. 신호 0이 실패하거나 좀비여야 한다
        import subprocess

        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(grandchild)], check=False, capture_output=True, text=True
        )
        state = state.stdout
        assert state.strip() in ("", "Z", "Z+")
    assert _proc.active_count() == 0


def test_kill_active(tmp_path):
    box = {}

    def go():
        box["r"] = _proc.run_cli(py("import time; time.sleep(60)"), "", cwd=tmp_path, env=None, timeout_seconds=60)

    th = threading.Thread(target=go)
    th.start()
    for _ in range(100):
        if _proc.active_count():
            break
        time.sleep(0.05)
    assert _proc.kill_active() == 1
    th.join(20)
    assert not th.is_alive()
    assert box["r"].exit_code != 0 and not box["r"].timed_out


def test_threads_do_not_mix(tmp_path):
    results = {}

    def go(i):
        results[i] = _proc.run_cli(
            py("import sys; print(sys.stdin.read()*2)"), f"p{i}", cwd=tmp_path, env=None, timeout_seconds=20
        )

    ths = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert {i: r.stdout for i, r in results.items()} == {i: f"p{i}p{i}\n" for i in range(6)}


def test_cli_version_cached(tmp_path, monkeypatch):
    exe = tmp_path / "v"
    counter = tmp_path / "count"
    exe.write_text(f"#!{sys.executable}\nopen({str(counter)!r}, 'a').write('x')\nprint('tool 1.2.3')\nprint('more')\n")
    exe.chmod(0o755)
    monkeypatch.setattr(_proc, "_versions", {})
    assert _proc.cli_version(str(exe)) == "tool 1.2.3"
    assert _proc.cli_version(str(exe)) == "tool 1.2.3"
    assert counter.read_text() == "x"
    assert _proc.cli_version(str(tmp_path / "missing")) is None
