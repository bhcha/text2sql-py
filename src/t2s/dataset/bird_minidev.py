"""BIRD Mini-Dev 폴더 꼴에서 소스(DB 11개)를 찾는 어댑터."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from t2s.dataset.config import SourceSpec


def discover_sources(root: Path, databases: str) -> dict[str, SourceSpec]:
    """`<root>/<databases>/<db_id>/<db_id>.sqlite`가 있는 폴더를 이름 순으로 찾는다. 소스 이름은 db_id."""
    from t2s.dataset.config import SourceSpec

    folder = Path(root) / databases
    out: dict[str, SourceSpec] = {}
    if not folder.is_dir():
        return out
    for d in sorted(p for p in folder.iterdir() if p.is_dir()):
        db = d / f"{d.name}.sqlite"
        if db.is_file():
            out[d.name] = SourceSpec(name=d.name, kind="sqlite", path=db)
    return out
