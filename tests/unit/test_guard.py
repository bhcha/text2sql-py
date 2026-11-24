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


HINT_SCRIPT = textwrap.dedent(
    r"""
    import json, sys
    from pathlib import Path
    from t2s.runner.guard import GoldAccessBlocked, install_guard
    root = Path(sys.argv[1])
    if sys.argv[2] == "allow":
        install_guard(root, allow_hints=True)
    else:
        install_guard(root)
    out = {}
    for name, rel in (("hints", "data/x/hints.public.json"), ("gold", "gold/x.txt"), ("run_hints", "runs/r/hints.json")):
        try:
            open(str(root / rel)).read()
            out[name] = "ok"
        except GoldAccessBlocked:
            out[name] = "blocked"
    print(json.dumps(out))
    """
)


def test_guard_hint_files(tmp_path):
    root = tmp_path / "repo"
    (root / "gold").mkdir(parents=True)
    (root / "gold" / "x.txt").write_text("g")
    (root / "data" / "x").mkdir(parents=True)
    (root / "data" / "x" / "hints.public.json").write_text("[]")
    (root / "runs" / "r").mkdir(parents=True)
    (root / "runs" / "r" / "hints.json").write_text("[]")
    got = {}
    for mode in ("default", "allow"):
        r = subprocess.run(
            [sys.executable, "-c", HINT_SCRIPT, str(root), mode], capture_output=True, text=True, check=True
        )
        got[mode] = json.loads(r.stdout)
    assert got["default"] == {"hints": "blocked", "gold": "blocked", "run_hints": "ok"}
    assert got["allow"] == {"hints": "ok", "gold": "blocked", "run_hints": "ok"}


def test_is_protected_hints(tmp_path):
    from t2s.runner.guard import is_protected

    hints = tmp_path / "data" / "bird-minidev" / "hints.public.json"
    assert is_protected(tmp_path, hints)
    assert not is_protected(tmp_path, hints, allow_hints=True)
    assert is_protected(tmp_path, tmp_path / "gold" / "a", allow_hints=True)
    assert not is_protected(tmp_path, tmp_path / "data" / "bird-minidev" / "questions.public.json")


SEMANTIC_SCRIPT = textwrap.dedent(
    r"""
    import json, os, sqlite3, sys
    from pathlib import Path
    from t2s.runner.guard import GoldAccessBlocked, install_guard
    root = Path(sys.argv[1])
    mode = sys.argv[2]
    install_guard(root, allow_hints=mode in ("hints", "both"), allow_semantic=mode in ("semantic", "both"))
    cases = {
        "approved": lambda: open(str(root / "data/x/semantic.approved.json")).read(),
        "inbox": lambda: open(str(root / "var/semantic-inbox/x.json")).read(),
        "inbox_write": lambda: open(str(root / "var/semantic-inbox/new.json"), "w").close(),
        "approved_write": lambda: open(str(root / "data/y/semantic.approved.json"), "w").close(),
        "relative": lambda: (os.chdir(root), open("data/x/semantic.approved.json").read()),
        "dotdot": lambda: open(str(root / "data/x/../x/semantic.approved.json")).read(),
        "symlink": lambda: open(str(root / "link_inbox" / "x.json")).read(),
        "sqlite_uri": lambda: sqlite3.connect(
            "file:" + str(root / "var/semantic-inbox/x.json") + "?mode=ro", uri=True
        ).close(),
        "hints": lambda: open(str(root / "data/x/hints.public.json")).read(),
        "gold": lambda: open(str(root / "gold/x.txt")).read(),
        "download": lambda: open(str(root / "data/x/download/a.zip"), "rb").read(),
        "run_copy": lambda: open(str(root / "runs/r/semantic.json")).read(),
        "state": lambda: open(str(root / "var/state.json")).read(),
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

SEMANTIC_FILES = ("approved", "inbox", "inbox_write", "approved_write", "relative", "dotdot", "symlink", "sqlite_uri")


def test_guard_semantic_files(tmp_path):
    root = tmp_path / "repo"
    for rel, text in (
        ("gold/x.txt", "g"),
        ("data/x/hints.public.json", "[]"),
        ("data/x/semantic.approved.json", "{}"),
        ("var/semantic-inbox/x.json", "{}"),
        ("data/x/download/a.zip", "z"),
        ("runs/r/semantic.json", "{}"),
        ("var/state.json", "{}"),
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root / "data/y").mkdir(parents=True)
    os.symlink(root / "var/semantic-inbox", root / "link_inbox")
    got = {}
    for mode in ("default", "hints", "semantic", "both"):
        r = subprocess.run(
            [sys.executable, "-c", SEMANTIC_SCRIPT, str(root), mode], capture_output=True, text=True, check=True
        )
        got[mode] = json.loads(r.stdout)
    for mode in ("default", "hints"):
        for name in SEMANTIC_FILES:
            assert got[mode][name] == "blocked", (mode, name)
    for mode in ("semantic", "both"):
        for name in SEMANTIC_FILES:
            assert got[mode][name] == "ok", (mode, name)
    # allow_semantic는 공개 힌트 파일을 열어 주지 않고, allow_hints는 정의 파일을 열어 주지 않는다
    assert got["semantic"]["hints"] == "blocked" and got["hints"]["hints"] == "ok" and got["both"]["hints"] == "ok"
    for mode in got:
        assert got[mode]["gold"] == "blocked" and got[mode]["download"] == "blocked", mode
        assert got[mode]["run_copy"] == "ok" and got[mode]["state"] == "ok", mode


def test_is_protected_semantic(tmp_path):
    from t2s.runner.guard import is_protected

    approved = tmp_path / "data" / "olist" / "semantic.approved.json"
    inbox = tmp_path / "var" / "semantic-inbox" / "olist.json"
    for p in (approved, inbox, tmp_path / "var" / "semantic-inbox"):
        assert is_protected(tmp_path, p)
        assert is_protected(tmp_path, p, allow_hints=True)
        assert not is_protected(tmp_path, p, allow_semantic=True)
    hints = tmp_path / "data" / "olist" / "hints.public.json"
    assert is_protected(tmp_path, hints, allow_semantic=True)
    assert is_protected(tmp_path, tmp_path / "gold" / "a", allow_hints=True, allow_semantic=True)
    assert is_protected(tmp_path, tmp_path / "data" / "x" / "download" / "f", allow_semantic=True)
    assert not is_protected(tmp_path, tmp_path / "var" / "state.json")
    assert not is_protected(tmp_path, tmp_path / "var" / "semantic-inbox-old.json")
    assert not is_protected(tmp_path, tmp_path / "data" / "olist" / "semantic.approved.json.bak")
