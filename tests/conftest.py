"""공통 fixture. 시험용 SQLite는 손으로 쓴 SQL 글(fixtures/shop.sql)에서 임시 폴더에 만든다."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FIXTURES = TESTS / "fixtures"

if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


def make_db(path: Path, sql: str, *, wal: bool = False) -> Path:
    conn = sqlite3.connect(path)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=wal")
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def shop_db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "shop.sqlite", (FIXTURES / "shop.sql").read_text(encoding="utf-8"))
