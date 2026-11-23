"""스킬화 같음 시험(겹 1): 저장소 루트 배치(옛 길)와 .text2sql 배치(새 길)가 같은 출력·파일·종료 코드를 낸다.

옛 길: 임시 루트(루트에 harness.toml, git 저장소)에서 `python -m t2s.cli.main <명령>`.
새 길: 임시 작업 폴더에서 `init --root` 뒤 T2S_ROOT·T2S_ENGINE_HOME(임시 엔진 자리, 표식 포함)을 주고 같은 명령.
같은 작은 자료(fixtures의 shop.sql, mini_bird)와 가짜 백엔드로 돈다. 모델 호출이 없다.
값 검색에 임베딩 모델이 들므로 모델 파일이 없으면 건너뛴다(하위 프로세스라 테스트 대역을 끼울 수 없다).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fixtures import mini_bird as M
from t2s import engine

pytest.importorskip("func_timeout")

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "data" / "models" / "Xenova" / "all-mpnet-base-v2"
PACKAGE = Path(engine.__file__).resolve().parent
COMMIT = "c" * 40

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        not (MODEL_DIR / "onnx" / "model.onnx").is_file(), reason="임베딩 모델 파일 없음(t2s fetch model)"
    ),
]


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


class Way:
    """한 배치(옛 길 또는 새 길)에서 명령을 돌리고 화면·종료 코드를 모은다."""

    def __init__(self, name: str, cwd: Path, root: Path, extra_env: dict[str, str]):
        self.name, self.cwd, self.root = name, cwd, root
        env = {k: v for k, v in os.environ.items() if not k.startswith(("T2S_", "CLAUDE_CODE_", "CODEX_"))}
        env.update({"T2S_ROOT": str(root), **extra_env})
        self.env = env
        self.log: list[tuple[str, int, str]] = []

    def __call__(self, *argv: str) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, "-m", "t2s.cli.main", *argv],
            cwd=self.cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        text = r.stdout + "\n--stderr--\n" + r.stderr
        self.log.append((" ".join(argv), r.returncode, text))
        return r.returncode, text


def _toml() -> str:
    return M.TOML.replace(
        'embedding_model = "data/models/Xenova/all-mpnet-base-v2"', f'embedding_model = "{MODEL_DIR}"'
    )


def _engine_home(tmp: Path) -> Path:
    """임시 엔진 자리: assets/(기본 설정·기준선), src/t2s(지금 패키지 사본), 그 자리의 파일로 만든 표식."""
    home = tmp / "engine"
    (home / "assets").mkdir(parents=True)
    (home / "assets/harness.toml").write_text(_toml(), encoding="utf-8")
    shutil.copytree(REPO / "baselines", home / "assets/baselines")
    shutil.copytree(PACKAGE, home / "src/t2s", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    files = engine.folder_files(home, skip_stamp=True)
    stamp = {
        "engine": "p3",
        "version": "0.1.0",
        "source_repo": "text2sql-py",
        "commit": COMMIT,
        "src_tree": "t" * 40,
        "dirty": False,
        "files": files,
        "checksum": engine.files_checksum(files),
    }
    (home / engine.STAMP_FILE).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    return home


def _mini_data(root: Path, shop_db: Path, scratch: Path) -> None:
    """작은 BIRD 꼴 자료(data/, gold/)를 root에 둔다. harness.toml은 건드리지 않는다."""
    made = M.make_mini_bird(scratch, shop_db)
    for name in ("data", "gold"):
        shutil.copytree(made / name, root / name)


@pytest.fixture
def ways(tmp_path, shop_db):
    old_root = tmp_path / "old"
    old_root.mkdir()
    (old_root / "harness.toml").write_text(_toml(), encoding="utf-8")
    shutil.copytree(REPO / "baselines", old_root / "baselines")
    _mini_data(old_root, shop_db, tmp_path / "scratch-old")
    (old_root / ".gitignore").write_text("data/\ngold/\nruns/\nvar/\n", encoding="utf-8")
    _git(old_root, "init", "-q")
    _git(old_root, "add", ".")
    _git(old_root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    old = Way("old", old_root, old_root, {})

    home = _engine_home(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    new_root = work / ".text2sql"
    new = Way("new", work, new_root, {"T2S_ENGINE_HOME": str(home), "T2S_CALLER_CWD": str(work)})
    code, text = new("init", "--root", str(new_root))
    assert code == 0, text
    new.log.clear()  # init은 새 길에만 있다
    assert (new_root / "harness.toml").read_text(encoding="utf-8") == _toml()
    _mini_data(new_root, shop_db, tmp_path / "scratch-new")
    db = tmp_path / "user-shop.sqlite"
    shutil.copy(shop_db, db)
    return old, new, db, tmp_path


MASKS = [
    (re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}"), "<ask>"),
    (re.compile(r"\d{8}-[a-z0-9]+-[0-9a-f]{6}(-(harness|single))?"), "<run>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?"), "<시각>"),
    (re.compile(r"\d+(\.\d+)?(초|ms|s\b)"), "<시간>"),
    (re.compile(r"pid \d+"), "pid <n>"),
    (re.compile(r"문항 시간 (기록 없음|[\d.]+배)"), "문항 시간 <비>"),  # 함께 돈 두 run의 시간 비(잴 때마다 다르다)
    (re.compile(r"구현 Python [0-9a-f]{7,40}"), "구현 Python <커밋>"),
]


def mask(text: str, way: Way) -> str:
    text = text.replace(str(way.root), "<루트>").replace(str(way.cwd), "<부른 폴더>")
    for pattern, repl in MASKS:
        text = pattern.sub(repl, text)
    return text


# 잴 때마다 다른 칸, 배치에 따라 다른 칸(git·engine), 그런 칸을 담은 파일의 해시(integrity의 records·run·ex 해시)
VOLATILE_KEY = re.compile(
    r"(_ms|_at|seconds|elapsed|duration|pid|^git$|^engine$|^command$|^run_id$|^started$"
    r"|^records_sha256$|^run_sha256$|^ex_sha256$)"
)


def norm(value, way: Way):
    if isinstance(value, dict):
        return {k: norm(v, way) for k, v in value.items() if not VOLATILE_KEY.search(k)}
    if isinstance(value, list):
        return [norm(v, way) for v in value]
    if isinstance(value, str):
        return mask(value, way)
    return value


def _read(path: Path):
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_pair(way: Way) -> str:
    deadline = time.monotonic() + 300
    text = ""
    while time.monotonic() < deadline:
        _, text = way("route-status")
        way.log.pop()  # 기다리는 동안의 출력은 견주지 않는다(진행 정도가 다를 수 있다)
        if "두 run 모두 채점 끝" in text:
            return text
        time.sleep(1)
    raise AssertionError(f"{way.name}: 짝 run이 끝나지 않음\n{text}")


def test_old_and_new_layout_match(ways):
    old, new, db, tmp = ways
    steps = [
        ("datasets",),
        ("register", "shop", "--sqlite", str(db)),
        ("register", "shop", "--sqlite", str(db), "--yes"),
        ("prepare", "shop"),
        ("prepare", M.DATASET),
        ("use", M.DATASET),
        ("ask", "--backend", "fake", M.QUESTIONS[0]),
        ("run", "--backend", "fake", "--limit", "5", "--foreground", "--no-score", "--run-id", "r5"),
        ("status", "--run-id", "r5"),
        ("score", "--run", "r5"),
        ("report", "--run", "r5"),
        ("leak-audit", "--run", "r5"),
        ("run", "--backend", "fake", "--limit", "2", "--foreground", "--no-score", "--run-id", "r2"),
        ("stop", "--run-id", "r2"),
        ("resume", "--run-id", "r2", "--foreground"),
        ("route-start", "--backend", "fake", "--limit", "3"),
    ]
    for argv in steps:
        for way in (old, new):
            way(*argv)
    for way in (old, new):
        _wait_pair(way)
        way("route-status")
        way("route-judge")

    expected_codes = {"register shop --sqlite " + str(db): 3}
    assert len(old.log) == len(new.log)
    for (cmd_o, code_o, text_o), (cmd_n, code_n, text_n) in zip(old.log, new.log, strict=True):
        assert cmd_o.replace(str(old.root), "") == cmd_n.replace(str(new.root), "")
        assert code_o == code_n, (cmd_o, text_o, text_n)
        assert code_o == expected_codes.get(cmd_o, code_o)
        assert mask(text_o, old) == mask(text_n, new), (cmd_o, mask(text_o, old), mask(text_n, new))
    codes = {cmd: code for cmd, code, _ in old.log}
    assert codes["register shop --sqlite " + str(db)] == 3 and codes["ask --backend fake " + M.QUESTIONS[0]] == 0
    assert codes["score --run r5"] == 0 and codes["report --run r5"] == 0

    # 만든 파일이 가린 칸 말고 같다
    for rel in ("runs/r5/records.jsonl", "runs/r5/predictions.json", "runs/r5/ex.jsonl", "runs/r5/score.json"):
        assert norm(_read(old.root / rel), old) == norm(_read(new.root / rel), new), rel

    score_o, score_n = _read(old.root / "runs/r5/score.json"), _read(new.root / "runs/r5/score.json")
    assert score_o["integrity"]["predictions_sha256"] == score_n["integrity"]["predictions_sha256"]
    ex_o = (old.root / "runs/r5/ex.jsonl").read_text(encoding="utf-8")
    assert ex_o == (new.root / "runs/r5/ex.jsonl").read_text(encoding="utf-8").replace(str(new.root), str(old.root))

    # 새 길의 run.json에만 engine 칸이 있고 git.head가 표식의 커밋이다
    meta_old = _read(old.root / "runs/r5/run.json")
    meta_new = _read(new.root / "runs/r5/run.json")
    assert "engine" not in meta_old and meta_old["git"]["head"] == _git(old.root, "rev-parse", "HEAD")
    assert meta_new["engine"]["matches"] is True and meta_new["git"] == {
        "head": COMMIT,
        "src_tree": "t" * 40,
        "dirty": False,
    }
    # 새 길의 자료는 작업 폴더 안에만 생긴다
    assert not (tmp / "work" / "runs").exists() and not (tmp / "work" / "var").exists()
    assert (new.root / "var/state.json").is_file() and (new.root / "runs/r5").is_dir()
