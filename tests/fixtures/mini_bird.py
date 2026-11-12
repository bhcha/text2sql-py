"""작은 가짜 BIRD 꼴 데이터셋(DB 하나, 문항 6개, 가짜 정답)을 임시 루트에 만드는 도우미.

정답 글자가 든 이 도우미는 tests/ 아래라 import 규칙의 정답 글자 검사 대상이 아니다(검사는 src/t2s만 본다).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

DATASET = "mini"
ARCHIVE = "ab12cd34" + "0" * 56
DIFFS = ["simple", "moderate", "challenging", "simple", "moderate", "challenging"]
QUESTIONS = [
    "How many products are there?",
    "Which products cost more than 1?",
    "How many orders are there?",
    "List customer names.",
    "List product codes.",
    "What is the highest price?",
]
GOLD_SQL = [
    "SELECT COUNT(*) FROM product",
    "SELECT product_name FROM product WHERE price > 1",
    'SELECT COUNT(*) FROM "order"',
    "SELECT name FROM customer",
    "SELECT code FROM product ORDER BY code -- gold four is long enough to be noticed",
    "SELECT MAX(price) FROM product WHERE price IS NOT NULL AND 1 = 1 -- gold five",
]
EVIDENCE = [f"hidden evidence sentence number {i} for leak checks" for i in range(6)]
TOML = f"""
[defaults]
dataset = "{DATASET}"
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/Xenova/all-mpnet-base-v2"

[datasets.{DATASET}]
kind = "bird-minidev"
root = "data/mini/MINIDEV"
databases = "dev_databases"
questions = "data/mini/questions.public.json"
archive_sha256 = "{ARCHIVE}"

[profiles.session]
backend = "auto"
"""


def make_mini_bird(root: Path, shop_db: Path, *, wal: bool = False) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.toml").write_text(TOML, encoding="utf-8")
    db_dir = root / "data/mini/MINIDEV/dev_databases/shop"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "shop.sqlite"
    shutil.copy(shop_db, db)
    if wal:
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=wal")
        conn.close()
    public = [
        {"question_id": 900 + i, "db_id": "shop", "question": q, "difficulty": DIFFS[i]}
        for i, q in enumerate(QUESTIONS)
    ]
    (root / "data/mini/questions.public.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    gold = root / "gold" / DATASET
    gold.mkdir(parents=True, exist_ok=True)
    items = [
        {
            "question_id": 900 + i,
            "db_id": "shop",
            "question": q,
            "evidence": EVIDENCE[i],
            "SQL": GOLD_SQL[i],
            "difficulty": DIFFS[i],
        }
        for i, q in enumerate(QUESTIONS)
    ]
    (gold / "answers.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    (gold / "answers.sql").write_text("".join(f"{s}\tshop\n" for s in GOLD_SQL), encoding="utf-8")
    write_gold_toml(gold)
    return root


def write_gold_toml(gold: Path) -> None:
    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    (gold / "gold.toml").write_text(
        'questions_with_answers = "answers.json"\n'
        'gold_sql = "answers.sql"\n'
        'evaluator = "bird-minidev-abd11b6"\n'
        "[sha256]\n"
        f'questions_with_answers = "{sha(gold / "answers.json")}"\n'
        f'gold_sql = "{sha(gold / "answers.sql")}"\n',
        encoding="utf-8",
    )


def predictions_jsonl(path: Path, sqls: list[str | None]) -> Path:
    path.write_text("".join(json.dumps({"position": i, "db_id": "shop", "sql": s}) + "\n" for i, s in enumerate(sqls)))
    return path
