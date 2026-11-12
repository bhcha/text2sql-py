"""열 프로파일 묶기·쓰기.

출처: 범주형 판정 질의 한 줄은 CHESS `src/database_utils/schema_generator.py` `_load_schema_into_cache` 103-104행
(커밋 3d6e835f858d26885d21d4bc0215aeecf855efbe, Apache License 2.0)을 복사했다(datasource/sqlite.py column_profile 안).
나머지(행 수·NULL 수·형식 분포·값 전부·최소 최대·길이)는 TS engines/pure-profile/pack/profile.ts를 다시 구현한 자체 코드다.
TS profile.json과 같은 바이트로 쓴다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from t2s.datasource.base import DataSource

PROFILE_FILE = "profile.json"
CATEGORICAL_LIMIT = 20


def profile_source(source: DataSource, name: str) -> dict:
    """{"db_id": name, "tables": [{"name", "rows", "columns": [...]}, ...]}. sqlite_sequence도 넣는다."""
    tables = []
    for table in source.tables():
        columns = [c.name for c in source.columns(table)]
        rows = source.table_row_count(table)
        tables.append(
            {"name": table, "rows": rows, "columns": [source.column_profile(table, c).to_json() for c in columns]}
        )
    return {"db_id": name, "tables": tables}


def serialize_profile(profile: dict) -> bytes:
    return (json.dumps(profile, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_profile(out_dir: Path, profile: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = serialize_profile(profile)
    (out_dir / PROFILE_FILE).write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def read_profile(out_dir: Path) -> dict:
    return json.loads((out_dir / PROFILE_FILE).read_text(encoding="utf-8"))


def profile_counts(profile: dict) -> dict:
    """TS profile-manifest.json counts의 앞 여섯 칸과 같은 뜻."""
    columns = [c for t in profile["tables"] for c in t["columns"]]
    kinds: dict[str, int] = {}
    for c in columns:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    return {
        "tables": len(profile["tables"]),
        "columns": len(columns),
        "kinds": kinds,
        "categorical": sum(1 for c in columns if c["categorical"]),
        "values_listed": sum(len(c.get("values", [])) for c in columns),
        "mixed_types": sum(1 for c in columns if len(c["types"]) > 1),
    }
