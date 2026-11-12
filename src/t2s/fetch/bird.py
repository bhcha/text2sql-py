"""BIRD Mini-Dev 받기: zip → 풀기(DB 11 + 정답 2) → 정답 분리 → gold.toml → 공개 문항 → 모델 → 마지막 검사.

이미 끝난 단계는 해시만 대조하고 건너뛴다. 열 설명 CSV 75개와 dev_tables.json은 풀지 않는다(퓨어 조건, 파일 배치로 보장).
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from importlib import resources
from pathlib import Path

from t2s.fetch import download
from t2s.fetch.download import check_file, fetch_file, sha256_file
from t2s.fetch.model import fetch_model
from t2s.fetch.public_questions import (
    PublicQuestionsError,
    build_public_items,
    check_public_against_official,
    serialize_public,
)
from t2s.fetch.steps import FetchStopped, format_summary, hash_mismatch, megabytes, select_members, short_hash

MANIFEST_NAME = "bird_minidev.json"
MIN_FREE_BYTES = 5 * 1024**3
GOLD_TOML = "gold.toml"


def load_manifest() -> dict:
    return json.loads(resources.files("t2s.fetch").joinpath(MANIFEST_NAME).read_text(encoding="utf-8"))


def _at(root: Path, rel: str) -> Path:
    if os.path.isabs(rel) or ".." in rel.split("/"):
        raise FetchStopped("경로", f"허용되지 않는 경로: {rel}")
    return Path(root) / rel


def preflight(root: Path) -> str:
    step = "1단계"
    free = shutil.disk_usage(root).free
    problems = []
    if free < MIN_FREE_BYTES:
        problems.append(f"디스크 여유 부족({free / 1024**3:.1f}GB)")
    if not shutil.which(download.CURL):
        problems.append("curl 없음")
    if problems:
        raise FetchStopped(step, ", ".join(problems))
    return f"사전 확인: 디스크 여유 {free / 1024**3:.0f}GB, curl 있음"


def _answers(manifest: dict) -> list[dict]:
    return [manifest["answers"]["questions"], manifest["answers"]["sql"]]


def extract(root: Path, manifest: dict) -> str:
    step = "3단계"
    final = _at(root, manifest["extract_root"])
    part = final.with_name(final.name + ".part")
    dbs = manifest["databases"]
    answers = _answers(manifest)
    db_paths = [final / d["member"] for d in dbs]
    gold_paths = [_at(root, a["path"]) for a in answers]
    present = [p.exists() for p in db_paths + gold_paths]
    if all(present):
        for d, p in zip(dbs, db_paths, strict=True):
            check_file(p, d["sha256"], d["size"], d["db_id"], step)
        for a, p in zip(answers, gold_paths, strict=True):
            check_file(p, a["sha256"], a["size"], p.name, step)
        return f"풀기: 건너뜀(DB {len(dbs)} + 정답 {len(answers)}, 해시 {len(dbs) + len(answers)} 일치)"
    if any(present) or final.exists():
        raise FetchStopped(
            step,
            f"{manifest['extract_root']} 또는 정답 폴더가 일부만 있다",
            f"{manifest['extract_root']}와 정답 폴더를 지우고 다시 /getbird를 부른다",
        )

    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    members = [d["member"] for d in dbs] + [a["member"] for a in answers]
    sizes = {d["member"]: (d["sha256"], d["size"], d["db_id"]) for d in dbs}
    sizes.update({a["member"]: (a["sha256"], a["size"], Path(a["path"]).name) for a in answers})
    try:
        _unzip(_at(root, manifest["archive"]["path"]), part, members, step)
        found = sorted(p.relative_to(part).as_posix() for p in part.rglob("*") if p.is_file())
        if found != sorted(members):
            raise FetchStopped(step, f"풀린 파일 수 {len(found)} (기대 {len(members)})")
        for m in members:
            sha, size, label = sizes[m]
            check_file(part / m, sha, size, label, step)
        # 정답 두 파일은 대조한 뒤 바로 정답 폴더로 옮기고, 풀린 자리에 남기지 않는다.
        for a, target in zip(answers, gold_paths, strict=True):
            source = part / a["member"]
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            if source.exists():
                raise FetchStopped(step, "정답 파일이 풀린 자리에 남아 있다")
            check_file(target, a["sha256"], a["size"], target.name, step)
        _remove_empty_dirs(part)
        if any(p.name.endswith(("-wal", "-shm")) for p in part.rglob("*")):
            raise FetchStopped(step, "풀린 폴더에 -wal·-shm 파일이 있다")
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    os.replace(part, final)
    return f"풀기: DB {len(dbs)} + 정답 {len(answers)}, 해시 {len(dbs) + len(answers)} 일치"


def _remove_empty_dirs(base: Path) -> None:
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


def _unzip(zip_path: Path, part: Path, members: list[str], step: str) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            sel = select_members(zf.namelist(), members)
            if sel.unsafe:
                raise FetchStopped(step, f"안전하지 않은 항목 {len(sel.unsafe)}개")
            if sel.missing:
                raise FetchStopped(step, f"빠진 항목 {len(sel.missing)}개: {', '.join(sel.missing[:3])}")
            for name in sel.files:
                dest = part / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
    except zipfile.BadZipFile:
        raise FetchStopped(step, "zip을 읽지 못했다(손상)") from None


def write_gold_toml(root: Path, manifest: dict) -> str:
    step = "4단계"
    q, s = manifest["answers"]["questions"], manifest["answers"]["sql"]
    target = _at(root, q["path"]).parent / GOLD_TOML
    body = (
        f'questions_with_answers = "{Path(q["path"]).name}"\n'
        f'gold_sql = "{Path(s["path"]).name}"\n'
        f'evaluator = "{manifest["evaluator"]}"\n'
        "\n"
        "[sha256]\n"
        f'questions_with_answers = "{q["sha256"]}"\n'
        f'gold_sql = "{s["sha256"]}"\n'
    )
    if target.exists():
        if target.read_text(encoding="utf-8") != body:
            raise FetchStopped(
                step, f"이미 있는 {GOLD_TOML}의 내용이 해시 표와 다르다", f"{GOLD_TOML}을 지우고 다시 부른다"
            )
        return f"{GOLD_TOML}: 일치"
    tmp = target.with_name(target.name + ".part")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return f"{GOLD_TOML}: 만듦"


def public_questions(root: Path, manifest: dict) -> str:
    step = "5단계"
    entry = manifest["questions_public"]
    source = _at(root, manifest["answers"]["questions"]["path"])
    try:
        official = json.loads(source.read_text(encoding="utf-8"))
        items = build_public_items(official)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise FetchStopped(step, "공식 문제 파일을 JSON으로 읽지 못했다") from None
    except PublicQuestionsError as e:
        raise FetchStopped(step, str(e)) from None
    errors = check_public_against_official(items, official)
    if errors:
        raise FetchStopped(step, f"공개 문항 유도 검사 실패 {len(errors)}건: {errors[0]}")
    text = serialize_public(items)
    dest = _at(root, entry["path"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_text(text, encoding="utf-8")
    actual = sha256_file(part)
    if actual != entry["sha256"]:
        part.unlink()
        raise FetchStopped(step, hash_mismatch("questions.public.json", entry["sha256"], actual))
    if dest.exists():
        part.unlink()
        existing = sha256_file(dest)
        if existing != entry["sha256"]:
            raise FetchStopped(step, hash_mismatch("이미 있는 questions.public.json", entry["sha256"], existing))
        return f"공개 문항: 유지({len(items)}문항, {short_hash(entry['sha256'])})"
    os.replace(part, dest)
    return f"공개 문항: 만듦({len(items)}문항, {short_hash(entry['sha256'])})"


def final_check(root: Path, manifest: dict) -> str:
    step = "7단계"
    n = 0
    a = manifest["archive"]
    check_file(_at(root, a["path"]), a["sha256"], a["size"], "minidev.zip", step)
    n += 1
    final = _at(root, manifest["extract_root"])
    for d in manifest["databases"]:
        check_file(final / d["member"], d["sha256"], d["size"], d["db_id"], step)
        n += 1
    for ans in _answers(manifest):
        check_file(_at(root, ans["path"]), ans["sha256"], ans["size"], Path(ans["path"]).name, step)
        n += 1
    pub = manifest["questions_public"]
    check_file(_at(root, pub["path"]), pub["sha256"], pub["size"], "questions.public.json", step)
    n += 1
    for name, sha in manifest["model"]["sha256"].items():
        check_file(_at(root, manifest["model"]["path"]) / name, sha, None, f"모델 {name}", step)
        n += 1
    count = len(json.loads(_at(root, pub["path"]).read_text(encoding="utf-8")))
    if count != pub["count"]:
        raise FetchStopped(step, f"공개 문항 수 {count} (기대 {pub['count']})")
    sql_path = _at(root, manifest["answers"]["sql"]["path"])
    sql_lines = sum(1 for line in sql_path.read_text(encoding="utf-8").splitlines() if line.strip())
    if sql_lines != pub["count"]:
        raise FetchStopped(step, f"정답 SQL {sql_lines}줄 (기대 {pub['count']})")
    side = [p.name for p in final.rglob("*") if p.name.endswith(("-wal", "-shm"))]
    if side:
        raise FetchStopped(step, f"풀린 폴더에 부속 파일 {len(side)}개")
    return f"마지막 검사: 문제 {count} / DB {len(manifest['databases'])} / 정답 {sql_lines}줄 / 해시 {n}개 일치"


def fetch_bird(root: Path, *, manifest: dict | None = None) -> list[str]:
    """요약 줄들. 실패는 FetchStopped."""
    m = manifest or load_manifest()
    root = Path(root)
    steps = [preflight(root)]
    a = m["archive"]
    r = fetch_file(
        dest=_at(root, a["path"]),
        url=a["url"],
        sha256=a["sha256"],
        size=a["size"],
        label="minidev.zip",
        step="2단계",
    )
    detail = f" {megabytes(a['size'])}MB" if r != "건너뜀" else ""
    steps.append(f"zip: {r}{detail}, sha256 {short_hash(a['sha256'])} 일치")
    steps.append(extract(root, m))
    steps.append(write_gold_toml(root, m))
    steps.append(public_questions(root, m))
    steps.append(fetch_model(root, m))
    steps.append(final_check(root, m))
    return format_summary("getbird 완료", steps, m["license_note"])


def fetch_model_only(root: Path, *, manifest: dict | None = None) -> list[str]:
    m = manifest or load_manifest()
    steps = [
        preflight(Path(root)),
        fetch_model(Path(root), m, step="2단계"),
    ]
    return format_summary("fetch model 완료", steps, "모델: Xenova/all-mpnet-base-v2 (리비전 e086c5e0)")
