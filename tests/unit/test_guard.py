"""읽기 감시. 훅은 뗄 수 없으므로 하위 프로세스에서 시험한다.

막지 못하는 것(기록용): os.listdir 같은 폴더 목록 보기, 하위 프로세스, C 확장의 직접 파일 열기.
"""

import json
import os
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    r"""
    import json, os, sqlite3, sys
    from pathlib import Path
    from t2s.runner.guard import GoldAccessBlocked, install_guard
    root = Path(sys.argv[1])
    install_guard(root)
    install_guard(root)  # 두 번째는 아무것도 하지 않는다
    g = str(root / "gold")
    cases = {
        "open": lambda: open(g + "/x.txt"),
        "path_read_text": lambda: Path(g, "x.txt").read_text(),
        "os_open": lambda: os.open(g + "/x.txt", os.O_RDONLY),
        "sqlite_plain": lambda: sqlite3.connect(g + "/x.sqlite"),
        "sqlite_uri": lambda: sqlite3.connect("file:" + g + "/x.sqlite?mode=ro", uri=True),
        "symlink": lambda: open(str(root / "link" / "x.txt")),
        "download_zip": lambda: open(str(root / "data/bird-minidev/download/minidev.zip"), "rb"),
        "relative": lambda: (os.chdir(root), open("gold/x.txt")),
        "allowed_data": lambda: open(str(root / "data/ok.txt")).read(),
        "allowed_db": lambda: sqlite3.connect("file:" + str(root / "data/ok.sqlite") + "?mode=ro", uri=True).close(),
        "listdir_gold": lambda: os.listdir(g),
    }
    out = {}
    for name, fn in cases.items():
        try:
            fn()
            out[name] = "ok"
        except GoldAccessBlocked:
            out[name] = "blocked"
        except Exception as e:
            out[name] = "other:" + type(e).__name__
    print(json.dumps(out))
    """
)


def test_guard_blocks_gold(tmp_path):
    root = tmp_path / "repo"
    (root / "gold").mkdir(parents=True)
    (root / "gold" / "x.txt").write_text("secret-marker")
    (root / "data" / "bird-minidev" / "download").mkdir(parents=True)
    (root / "data" / "bird-minidev" / "download" / "minidev.zip").write_bytes(b"zip")
    (root / "data" / "ok.txt").write_text("fine")
    import sqlite3

    sqlite3.connect(root / "data" / "ok.sqlite").close()
    os.symlink(root / "gold", root / "link")
    r = subprocess.run([sys.executable, "-c", SCRIPT, str(root)], capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    for name in (
        "open",
        "path_read_text",
        "os_open",
        "sqlite_plain",
        "sqlite_uri",
        "symlink",
        "download_zip",
        "relative",
    ):
        assert out[name] == "blocked", name
    assert out["allowed_data"] == "ok"
    assert out["allowed_db"] == "ok"
    # 막지 않는 것(기록용): 폴더 목록
    assert out["listdir_gold"] == "ok"
    assert "secret-marker" not in r.stdout + r.stderr


def test_is_protected(tmp_path):
    from t2s.runner.guard import is_protected

    assert is_protected(tmp_path, tmp_path / "gold")
    assert is_protected(tmp_path, tmp_path / "gold" / "a" / "b")
    assert is_protected(tmp_path, tmp_path / "data" / "x" / "download" / "f.zip")
    assert not is_protected(tmp_path, tmp_path / "golden.txt")
    assert not is_protected(tmp_path, tmp_path / "data" / "x" / "extracted" / "f")
    assert not is_protected(tmp_path, 3)
