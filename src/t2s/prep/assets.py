"""자산(값 색인 다섯 파일 + 프로파일) 만들기, manifest, 낡음 판정.

자산 폴더는 data/assets/<데이터셋>/<소스>/다. 파일은 `<폴더>.part/`에 쓰고 다 되면 바꿔 넣는다(반쯤 된 자산이 남지 않는다).
prep 코드 네 파일 가운데 하나라도 바뀌면(주석만 바뀌어도) 자산은 낡은 것이 된다.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from t2s.datasource.base import DataSource
from t2s.prep import embed, profile, value_index

ASSET_FORMAT = "t2s-assets/1"
MANIFEST_FILE = "manifest.json"
PREP_CODE_FILES = ("prep/value_index.py", "prep/profile.py", "datasource/base.py", "datasource/sqlite.py")
ASSET_FILES = (*value_index.INDEX_FILES, profile.PROFILE_FILE)
PACKAGE_DIR = Path(__file__).resolve().parent.parent


class AssetsStale(Exception):
    pass


class PrepFailed(Exception):
    pass


@dataclass(frozen=True)
class AssetState:
    state: str  # ok | missing | stale
    reasons: list[str] = field(default_factory=list)


@dataclass
class LoadedAssets:
    manifest: dict
    values: value_index.UniqueValues
    index: value_index.ShingleIndex
    profile: dict


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def prep_code_hashes() -> dict[str, str]:
    return {f: _sha256(PACKAGE_DIR / f) for f in PREP_CODE_FILES}


def asset_dir(data_dir: Path, dataset: str, source: str) -> Path:
    return Path(data_dir) / "assets" / dataset / source


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_assets(
    source: DataSource, out_dir: Path, *, dataset: str, source_name: str, model_hashes: dict[str, str]
) -> dict:
    """값 색인과 프로파일을 만들고 manifest 내용을 돌려준다. 준비 중 DB가 바뀌면 PrepFailed(자산을 남기지 않음)."""
    started = time.monotonic()
    out_dir = Path(out_dir)
    part = out_dir.with_name(out_dir.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    before = (source.fingerprint(), source.side_files())
    try:
        uv = value_index.unique_values(source)
        index = value_index.build_shingle_index(uv.values)
        files = value_index.write_index(part, uv.values, index)
        prof = profile.profile_source(source, source_name)
        files[profile.PROFILE_FILE] = profile.write_profile(part, prof)
        after = (source.fingerprint(), source.side_files())
        if after != before:
            raise PrepFailed(f"준비하는 동안 DB가 바뀌었습니다: {source.describe()}")
        fp = before[0]
        manifest = {
            "format": ASSET_FORMAT,
            "dataset": dataset,
            "source": source_name,
            "created_at": _now(),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "database": {"path": source.describe(), "size": fp.size, "sha256": fp.sha256},
            "database_unchanged": True,
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "prep_code_sha256": prep_code_hashes(),
            "embedder": {"name": embed.EMBEDDER_NAME, "files_sha256": dict(model_hashes)},
            "files": files,
            "counts": {
                "values": len(index.entries),
                "value_columns": sum(len(cols) for _, cols in uv.values),
                "shingles": len(index.postings),
                "values_without_shingle": len(index.empty),
                "primary_key_names": len(uv.primary_key_names),
                "text_columns_excluded_by_primary_key": uv.excluded_by_primary_key,
                "excluded_by_name": uv.excluded_by_name,
                "excluded_by_size": uv.excluded_by_size,
                "value_query_errors": uv.query_errors,
                **profile.profile_counts(prof),
            },
        }
        (part / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    old = out_dir.with_name(out_dir.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out_dir.exists():
        out_dir.rename(old)
    part.rename(out_dir)
    shutil.rmtree(old, ignore_errors=True)
    return manifest


def read_manifest(out_dir: Path) -> dict | None:
    p = Path(out_dir) / MANIFEST_FILE
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def asset_state(source: DataSource, out_dir: Path, model_hashes: dict[str, str]) -> AssetState:
    manifest = read_manifest(out_dir)
    if manifest is None:
        return AssetState("missing", ["자산 없음"])
    reasons: list[str] = []
    if manifest.get("format") != ASSET_FORMAT:
        reasons.append("꼴 다름")
    fp = source.fingerprint()
    db = manifest.get("database", {})
    if db.get("size") != fp.size or db.get("sha256") != fp.sha256:
        reasons.append("DB 바뀜")
    if manifest.get("prep_code_sha256") != prep_code_hashes():
        reasons.append("코드 바뀜")
    if manifest.get("embedder", {}).get("files_sha256") != dict(model_hashes):
        reasons.append("모델 바뀜")
    files = manifest.get("files", {})
    for name in ASSET_FILES:
        p = Path(out_dir) / name
        if name not in files or not p.is_file() or _sha256(p) != files[name].get("sha256"):
            reasons.append("자산 파일 바뀜")
            break
    return AssetState("stale", reasons) if reasons else AssetState("ok", [])


def load_assets(source: DataSource, out_dir: Path, model_hashes: dict[str, str]) -> LoadedAssets:
    st = asset_state(source, out_dir, model_hashes)
    if st.state != "ok":
        raise AssetsStale(f"자산이 {st.state}입니다({', '.join(st.reasons)}): {out_dir} (t2s prepare를 다시)")
    values, index = value_index.read_index(Path(out_dir))
    return LoadedAssets(
        manifest=read_manifest(out_dir) or {},
        values=values,
        index=index,
        profile=profile.read_profile(Path(out_dir)),
    )
