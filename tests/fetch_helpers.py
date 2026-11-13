"""받기 테스트용 작은 가짜 원격 자료(zip 안에 DB 둘·정답 둘, 모델 다섯)와 그에 맞는 해시 표."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from t2s.fetch.public_questions import build_public_items, serialize_public

MARK = "LEAKMARK-7Q"  # 정답·문항에 넣는 표식. 어떤 출력에도 나오면 안 된다.
EXTRACT_ROOT = "data/bird-minidev/extracted"
ZIP_PATH = "data/bird-minidev/download/minidev.zip"
DB_IDS = ("alpha", "beta")
MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "onnx/model.onnx")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def official_items() -> list[dict]:
    return [
        {
            "question_id": i,
            "db_id": DB_IDS[i % 2],
            "question": f"{MARK} question {i}?",
            "evidence": f"{MARK} evidence {i}",
            "SQL": f"SELECT '{MARK}' AS x{i}",
            "difficulty": "simple",
        }
        for i in range(3)
    ]


def db_bytes(tmp: Path, name: str) -> bytes:
    p = tmp / f"_{name}.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (name,))
    conn.commit()
    conn.close()
    data = p.read_bytes()
    p.unlink()
    return data


def make_fake_source(tmp: Path) -> tuple[Path, dict]:
    """가짜 원격 폴더(가짜 curl이 fake://로 읽는다)와 해시 표(bird_minidev.json과 같은 꼴)를 만든다."""
    src = tmp / "fake-source"
    files: dict[str, bytes] = {}
    for d in DB_IDS:
        files[f"minidev/MINIDEV/dev_databases/{d}/{d}.sqlite"] = db_bytes(tmp, d)
    official = official_items()
    q_bytes = json.dumps(official, indent=4).encode()
    sql_bytes = "".join(f"{it['SQL']}\t{it['db_id']}\n" for it in official).encode()
    files["minidev/MINIDEV/mini_dev_sqlite.json"] = q_bytes
    files["minidev/MINIDEV/mini_dev_sqlite_gold.sql"] = sql_bytes
    # 풀지 않아야 하는 것
    files["minidev/MINIDEV/dev_tables.json"] = b"[]"
    files["minidev/MINIDEV/dev_databases/alpha/database_description/t.csv"] = b"a,b\n"
    zpath = src / ZIP_PATH
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    zbytes = zpath.read_bytes()
    public = serialize_public(build_public_items(official)).encode()
    model_dir = src / "models/Xenova/all-mpnet-base-v2"
    model_sha = {}
    for f in MODEL_FILES:
        (model_dir / f).parent.mkdir(parents=True, exist_ok=True)
        data = f"model {f}".encode()
        (model_dir / f).write_bytes(data)
        model_sha[f] = sha(data)
    manifest = {
        "format": "t2s-fetch/1",
        "archive": {"url": "fake://" + str(zpath), "path": ZIP_PATH, "sha256": sha(zbytes), "size": len(zbytes)},
        "extract_root": EXTRACT_ROOT,
        "databases": [
            {
                "db_id": d,
                "member": f"minidev/MINIDEV/dev_databases/{d}/{d}.sqlite",
                "sha256": sha(files[f"minidev/MINIDEV/dev_databases/{d}/{d}.sqlite"]),
                "size": len(files[f"minidev/MINIDEV/dev_databases/{d}/{d}.sqlite"]),
            }
            for d in DB_IDS
        ],
        "answers": {
            "questions": {
                "member": "minidev/MINIDEV/mini_dev_sqlite.json",
                "path": "gold/bird-minidev/mini_dev_sqlite.json",
                "sha256": sha(q_bytes),
                "size": len(q_bytes),
            },
            "sql": {
                "member": "minidev/MINIDEV/mini_dev_sqlite_gold.sql",
                "path": "gold/bird-minidev/mini_dev_sqlite_gold.sql",
                "sha256": sha(sql_bytes),
                "size": len(sql_bytes),
            },
        },
        "questions_public": {
            "path": "data/bird-minidev/questions.public.json",
            "sha256": sha(public),
            "size": len(public),
            "count": 3,
        },
        "model": {
            "url_base": "fake://" + str(model_dir) + "/",
            "path": "data/models/Xenova/all-mpnet-base-v2",
            "sha256": model_sha,
        },
        "evaluator": "bird-minidev-abd11b6",
        "license_note": "라이선스 줄",
    }
    return src, manifest


FAKE_CURL = """#!{python}
# 시험용 가짜 curl: fake://<경로>를 -o 파일로 복사한다. 주소에 FAIL이 있으면 반만 쓰고 7로 끝낸다.
import shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
url = args[-1]
src = url[len("fake://"):]
if "FAIL" in url:
    open(out, "ab").write(b"partial")
    sys.exit(7)
try:
    shutil.copyfile(src, out)
except FileNotFoundError:
    sys.exit(22)
"""


def install_fake_curl(tmp: Path, monkeypatch) -> Path:
    import sys

    from t2s.fetch import download

    p = tmp / "fake-curl"
    p.write_text(FAKE_CURL.format(python=sys.executable))
    p.chmod(0o755)
    monkeypatch.setattr(download, "CURL", str(p))
    return p


# --- 스터디 Olist 받기(가짜 gh) ---

OLIST_REPO = "owner/fake-study"
OLIST_COMMIT = "a" * 40
OLIST_ALLOWED_KEYS = ["id", "question", "question_type", "task_conditions"]


def make_fake_olist_source(tmp: Path, *, extra_key: str | None = None) -> tuple[Path, dict]:
    """가짜 원격 폴더(가짜 gh가 contents/<remote>로 읽는다)와 해시 표(olist_study.json과 같은 꼴)를 만든다."""
    src = tmp / "fake-olist"
    item = {"id": "q1", "question": "how many?", "question_type": "count", "task_conditions": []}
    if extra_key:
        item[extra_key] = "x"
    blobs = {
        "data/olist.db": ("db", "data/olist/olist.db", db_bytes(tmp, "olist")),
        "evals/model_inputs.json": (
            "model_inputs",
            "data/olist/model_inputs.v0.3.json",
            json.dumps({"description": "d", "items": [item, {**item, "id": "q2"}]}).encode(),
        ),
        "evals/manifest.json": ("eval_manifest", "data/olist/manifest.v0.3.json", b'{"version": "0.3"}'),
        "evals/schema.json": ("schema_snapshot", "data/olist/schema.snapshot.json", b'{"tables": []}'),
        "evals/study.json": (
            "gold",
            "gold/olist/study-v0.3.json",
            json.dumps([{"id": "q1", "sql": f"SELECT '{MARK}'"}]).encode(),
        ),
    }
    files = []
    for remote, (role, path, data) in blobs.items():
        (src / remote).parent.mkdir(parents=True, exist_ok=True)
        (src / remote).write_bytes(data)
        files.append({"role": role, "remote": remote, "path": path, "size": len(data), "sha256": sha(data)})
    manifest = {
        "format": "t2s-fetch-olist/1",
        "source": {"repository": OLIST_REPO, "commit": OLIST_COMMIT},
        "files": files,
        "model_inputs_allowed_item_keys": OLIST_ALLOWED_KEYS,
        "license_note": "라이선스 줄",
    }
    return src, manifest


FAKE_GH = """#!{python}
# 시험용 가짜 gh. 호출마다 인자를 기록하고, contents/<remote>?ref=<commit>이면 가짜 폴더의 파일을 표준 출력으로 낸다.
import json, os, sys
args = sys.argv[1:]
src, log = {src!r}, {log!r}
with open(log, "a") as f:
    f.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print("gh version 9.9.9 (fake)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    print("Logged in to github.com account someone")
    sys.exit(0 if {auth} else 1)
if args[0] == "api" and "--jq" in args:
    sys.exit(0 if {access} else 1)
if args[0] == "api" and "-H" in args:
    path = args[-1]
    remote = path.split("/contents/", 1)[1].split("?ref=", 1)[0]
    if "FAIL" in remote:
        sys.stdout.write("partial")
        sys.stderr.write("HTTP 404: Not Found\\n")
        sys.exit(1)
    sys.stdout.buffer.write(open(os.path.join(src, remote), "rb").read())
    sys.exit(0)
sys.exit(2)
"""


def install_fake_gh(tmp: Path, monkeypatch, src: Path, *, auth: bool = True, access: bool = True) -> Path:
    """가짜 gh를 깔고 호출 기록 파일 경로를 돌려준다."""
    import sys

    from t2s.fetch import olist

    p = tmp / "fake-gh"
    log = tmp / "fake-gh.log"
    p.write_text(FAKE_GH.format(python=sys.executable, src=str(src), log=str(log), auth=auth, access=access))
    p.chmod(0o755)
    monkeypatch.setattr(olist, "GH", str(p))
    return log
