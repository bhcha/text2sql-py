"""kind 글자 → 데이터 소스 구현."""

from __future__ import annotations

from t2s.datasource.base import DataSource, DataSourceError
from t2s.datasource.sqlite import SqliteSource

KINDS = ("sqlite",)


def open_source(kind: str, *, path: str | None = None) -> DataSource:
    if kind == "sqlite":
        if not path:
            raise DataSourceError("sqlite 소스에는 path가 있어야 합니다")
        return SqliteSource(path)
    raise DataSourceError(f"지원하지 않는 소스 종류: {kind} (지금은 {', '.join(KINDS)}만)")
