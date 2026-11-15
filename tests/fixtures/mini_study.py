"""작은 가짜 스터디 평가셋(소스 하나, 문항 4개, 가짜 정답)을 임시 루트에 만드는 도우미.

정답 SQL·채점 메모에는 MARK를, 문항 조건에는 COND_MARK를 넣는다. 둘 다 run 폴더·표준 출력·프롬프트에 나오면 안 된다.
기대 결과는 시험용 DB에서 정답 SQL을 실제로 돌려 만든다(수 허용 오차 문항만 일부러 조금 다르게 둔다).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

MARK = "LEAKMARK-7Q"
COND_MARK = "CONDMARK-3R"
DATASET = "olist"
EVALUATOR = "study-run-baseline-290ff9b"
ANSWERS = "study-v0.3.json"
INPUTS = "data/olist/model_inputs.v0.3.json"
IDS = ["t-001", "t-002", "t-003", "t-004"]
QUESTIONS = [
    "How many products are there?",
    "List all customer names.",
    "How many customers are there?",
    "How many products, roughly?",
]
GOLD_SQL = [
    f"SELECT COUNT(*) FROM product -- {MARK} gold zero",
    f"SELECT name FROM customer -- {MARK} gold one",
    f"SELECT COUNT(*) FROM customer -- {MARK} gold two",
    f"SELECT COUNT(*) FROM product WHERE 1 = 1 -- {MARK} gold three",
]
ROW_ORDER = ["required", "any", "required", "결과가 한 행이라 정렬 설명만 있음"]
CONDITIONS = [[f"{COND_MARK} 조건 {i}: 결과는 숫자 하나로 낸다(별칭 자유)"] for i in range(4)]
NOTES = [f"{MARK} 채점 메모 {i}: 사람이 쓴 설명이라 모델에 가면 안 된다" for i in range(4)]
TOML = f"""
[defaults]
dataset = "{DATASET}"
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/Xenova/all-mpnet-base-v2"

[datasets.{DATASET}]
kind = "single"

[datasets.{DATASET}.sources.{DATASET}]
kind = "sqlite"
path = "data/olist/olist.db"

[profiles.session]
backend = "auto"
"""


def _rows(db: Path, sql: str) -> tuple[list[str], list[list]]:
    con = sqlite3.connect(db)
    try:
        cur = con.execute(sql)
        return [c[0] for c in cur.description], [list(r) for r in cur.fetchall()]
    finally:
        con.close()


def gold_items(db: Path) -> list[dict]:
    items = []
    for i, sql in enumerate(GOLD_SQL):
        cols, rows = _rows(db, sql)
        if i == 3:
            rows = [[rows[0][0] + 0.00005]]  # 허용 오차 0.0001 안
        items.append(
            {
                "id": IDS[i],
                "question": QUESTIONS[i],
                "question_type": "sql",
                "gold_sql": sql,
                "expected_columns": cols,
                "expected_rows": rows,
                "comparison": {"row_order": ROW_ORDER[i], "numeric_abs_tolerance": 0.0001},
                "difficulty": "easy",
                "model_input": {"question": QUESTIONS[i], "task_conditions": CONDITIONS[i]},
                "note": NOTES[i],
            }
        )
    return items


def write_gold_toml(gold: Path) -> None:
    digest = hashlib.sha256((gold / ANSWERS).read_bytes()).hexdigest()
    (gold / "gold.toml").write_text(
        f'answers = "{ANSWERS}"\nevaluator = "{EVALUATOR}"\n\n[sha256]\nanswers = "{digest}"\n', encoding="utf-8"
    )


def write_gold(root: Path, items: list[dict]) -> None:
    gold = Path(root) / "gold" / DATASET
    gold.mkdir(parents=True, exist_ok=True)
    (gold / ANSWERS).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    write_gold_toml(gold)


def make_mini_study(root: Path, shop_db: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.toml").write_text(TOML, encoding="utf-8")
    db = root / "data/olist/olist.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(shop_db, db)
    inputs = {
        "description": "test inputs",
        "items": [
            {"id": IDS[i], "question": QUESTIONS[i], "question_type": "sql", "task_conditions": CONDITIONS[i]}
            for i in range(4)
        ],
    }
    (root / INPUTS).write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    write_gold(root, gold_items(db))
    return root
