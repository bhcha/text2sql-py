"""harness.toml 읽기와 저장소 루트 찾기.

사용자가 손으로 쓰는 harness.toml은 읽기만 한다(tomllib). 프로그램이 쓰는 등록 기록은 var/state.json(state.py)에 있고,
둘을 합쳐 Config를 만든다. 정답 관련 칸은 읽지 않는다.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from t2s.dataset.bird_minidev import discover_sources

if TYPE_CHECKING:
    from t2s.dataset.state import State

CONFIG_FILE = "harness.toml"
ROOT_ENV = "T2S_ROOT"
DATASET_KINDS = ("bird-minidev", "single")
MISSING_BIRD = "자료 없음: /getbird"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str
    path: Path


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    kind: str  # "bird-minidev" | "single"
    sources: dict[str, SourceSpec]  # 이름 순서 고정
    questions: Path | None
    archive_sha256: str | None
    origin: str  # "harness.toml" | "state"
    missing: str | None  # 자료가 없으면 까닭


@dataclass(frozen=True)
class Config:
    root: Path
    default_dataset: str | None
    default_profile: str | None
    data_dir: Path
    runs_dir: Path
    embedding_model_dir: Path
    datasets: dict[str, DatasetSpec]
    profiles: dict[str, dict] = field(default_factory=dict)
    pipeline: dict = field(default_factory=dict)


def find_root(start: Path | None = None) -> Path:
    """환경변수 T2S_ROOT, 없으면 지금 폴더에서 위로 올라가며 harness.toml을 찾는다."""
    env = os.environ.get(ROOT_ENV)
    if env:
        root = Path(env).resolve()
        if not (root / CONFIG_FILE).is_file():
            raise ConfigError(f"{ROOT_ENV}={env} 에 {CONFIG_FILE}이 없습니다")
        return root
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_FILE).is_file():
            return d
    raise ConfigError(f"{CONFIG_FILE}을 찾지 못했습니다(저장소 루트에서 실행하거나 {ROOT_ENV}를 둔다)")


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _single_sources(name: str, raw_sources: dict, root: Path) -> dict[str, SourceSpec]:
    out: dict[str, SourceSpec] = {}
    for sname, s in raw_sources.items():
        if not isinstance(s, dict) or "kind" not in s or "path" not in s:
            raise ConfigError(f"데이터셋 {name}의 소스 {sname}에 kind·path가 없습니다")
        out[sname] = SourceSpec(name=sname, kind=str(s["kind"]), path=_resolve(root, str(s["path"])))
    return out


def _dataset_from_toml(name: str, raw: dict, root: Path) -> DatasetSpec:
    kind = raw.get("kind")
    if kind == "bird-minidev":
        base = _resolve(root, str(raw.get("root", "")))
        databases = str(raw.get("databases", "dev_databases"))
        folder = base / databases
        sources = discover_sources(base, databases) if folder.is_dir() else {}
        missing = None if sources else MISSING_BIRD
        q = raw.get("questions")
        return DatasetSpec(
            name=name,
            kind=kind,
            sources=sources,
            questions=_resolve(root, str(q)) if q else None,
            archive_sha256=raw.get("archive_sha256"),
            origin="harness.toml",
            missing=missing,
        )
    if kind == "single":
        sources = _single_sources(name, raw.get("sources", {}), root)
        missing = None if sources and all(s.path.is_file() for s in sources.values()) else "DB 파일 없음"
        q = raw.get("questions")
        return DatasetSpec(
            name=name,
            kind=kind,
            sources=sources,
            questions=_resolve(root, str(q)) if q else None,
            archive_sha256=None,
            origin="harness.toml",
            missing=missing,
        )
    raise ConfigError(f"데이터셋 {name}의 kind가 {kind!r}입니다({', '.join(DATASET_KINDS)} 가운데 하나)")


def _dataset_from_state(name: str, raw: dict, root: Path) -> DatasetSpec:
    if raw.get("kind") != "single":
        raise ConfigError(f"등록 기록 {name}의 kind가 single이 아닙니다")
    sources = _single_sources(name, raw.get("sources", {}), root)
    missing = None if sources and all(s.path.is_file() for s in sources.values()) else "DB 파일 없음"
    return DatasetSpec(
        name=name,
        kind="single",
        sources=sources,
        questions=None,
        archive_sha256=None,
        origin="state",
        missing=missing,
    )


def load_config(root: Path, state: State) -> Config:
    path = root / CONFIG_FILE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"{path}이 없습니다") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}를 읽지 못했습니다: {e}") from e
    defaults = raw.get("defaults", {})
    paths = raw.get("paths", {})
    datasets: dict[str, DatasetSpec] = {}
    for name, d in raw.get("datasets", {}).items():
        datasets[name] = _dataset_from_toml(name, d, root)
    for name, d in state.registered.items():
        if name in datasets:
            raise ConfigError(f"등록한 데이터셋 이름 {name}이 {CONFIG_FILE}의 데이터셋과 겹칩니다")
        datasets[name] = _dataset_from_state(name, d, root)
    return Config(
        root=root,
        default_dataset=defaults.get("dataset"),
        default_profile=defaults.get("profile"),
        data_dir=_resolve(root, str(paths.get("data", "data"))),
        runs_dir=_resolve(root, str(paths.get("runs", "runs"))),
        embedding_model_dir=_resolve(root, str(paths.get("embedding_model", "data/models/Xenova/all-mpnet-base-v2"))),
        datasets=datasets,
        profiles=dict(raw.get("profiles", {})),
        pipeline=dict(raw.get("pipeline", {})),
    )
