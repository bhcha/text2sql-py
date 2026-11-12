"""모듈 경계 검사(패키지 사이 import 표, 정답 격리, 무거운 모듈).

이 테스트를 고쳐서 통과시키지 않는다(AGENTS 규칙 2). 경계를 바꿔야 하면 사용자 확인을 받는다.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "t2s"

# 패키지 → import해도 되는 다른 t2s 패키지. 아직 없는 패키지도 미리 적어 둔다(파일이 생기면 바로 검사된다).
ALLOWED: dict[str, set[str]] = {
    "": set(),  # t2s/__init__.py
    "datasource": set(),
    "llm": set(),
    "fetch": set(),
    "prep": {"datasource"},
    "pipeline": {"datasource", "prep", "llm"},
    "dataset": {"datasource"},
    "runner": {"dataset", "pipeline", "prep", "llm", "datasource"},
    "eval": {"dataset", "runner"},
    "cli": {"datasource", "dataset", "prep", "pipeline", "llm", "runner", "eval", "fetch"},
}
# eval이 runner에서 가져와도 되는 것은 기록 꼴뿐이다.
EVAL_RUNNER_ALLOWED = {"t2s.runner.records"}
# 이 패키지를 import해도 되는 단 한 파일
ONLY_IMPORTER = {"eval": "cli/eval_cmds.py", "fetch": "cli/fetch_cmd.py"}
# 정답 글자를 가져도 되는 구역
GOLD_TEXT_ZONES = ("eval/", "fetch/")
GOLD_TEXT_EXCEPTION = ("runner/guard.py", "PROTECTED_DIRS")
GOLD_PATTERNS = (re.compile(r"(^|/)gold(/|$)"), re.compile(r"mini_dev_sqlite"), re.compile(r"evidence"))
CONNECT_ALLOWED = ("datasource/sqlite.py",)
CONNECT_ZONES = ("eval/",)
HEAVY = {"numpy", "onnxruntime", "tokenizers"}
TEMPLATES = SRC / "pipeline" / "templates"


def package_of(rel: str) -> str:
    parts = rel.split("/")
    return "" if len(parts) == 1 else parts[0]


def module_name(rel: str) -> str:
    mod = "t2s." + rel[:-3].replace("/", ".")
    return mod.removesuffix(".__init__")


def imported_modules(rel: str, tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    here = module_name(rel).split(".")
    if not rel.endswith("__init__.py"):
        here = here[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[: len(here) - (node.level - 1)]
                name = ".".join(base + ([node.module] if node.module else []))
            else:
                name = node.module or ""
            out.append((node.lineno, name))
            for a in node.names:
                out.append((node.lineno, f"{name}.{a.name}"))
    return out


def docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def check_source(rel: str, source: str) -> list[str]:
    """한 파일의 위반 목록. rel은 src/t2s 기준 경로."""
    tree = ast.parse(source)
    lines = source.splitlines()
    problems: list[str] = []
    pkg = package_of(rel)
    allowed = ALLOWED.get(pkg)
    if allowed is None:
        problems.append(f"{rel}: import 표에 없는 패키지 {pkg}")
        allowed = set()

    # 1. import 표
    for lineno, name in imported_modules(rel, tree):
        if not (name == "t2s" or name.startswith("t2s.")):
            continue
        parts = name.split(".")
        if len(parts) < 2:
            continue
        target = parts[1]
        if target in ("__version__",) or target == pkg:
            continue
        if target not in ALLOWED:
            # t2s.__version__ 같은 최상위 이름
            continue
        if target not in allowed:
            problems.append(f"{rel}:{lineno}: {pkg or 't2s'} 는 t2s.{target} 를 import할 수 없다")
            continue
        if target in ONLY_IMPORTER and rel != ONLY_IMPORTER[target]:
            problems.append(f"{rel}:{lineno}: t2s.{target} 는 {ONLY_IMPORTER[target]} 만 import할 수 있다")
        eval_ok = any(name == m or name.startswith(m + ".") for m in EVAL_RUNNER_ALLOWED)
        if pkg == "eval" and target == "runner" and not eval_ok:
            problems.append(f"{rel}:{lineno}: eval 은 runner.records 만 import할 수 있다")

    # 2. 정답 글자(주석·독스트링 제외)
    if not rel.startswith(GOLD_TEXT_ZONES):
        docs = docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
                continue
            if any(p.search(node.value) for p in GOLD_PATTERNS):
                line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
                if rel == GOLD_TEXT_EXCEPTION[0] and GOLD_TEXT_EXCEPTION[1] in line:
                    continue
                problems.append(f"{rel}:{node.lineno}: 정답 글자가 든 문자열")

    # 3. sqlite3.connect는 한 곳에서만
    if rel not in CONNECT_ALLOWED and not rel.startswith(CONNECT_ZONES):
        for node in ast.walk(tree):
            f = node.func if isinstance(node, ast.Call) else None
            if (
                isinstance(f, ast.Attribute)
                and f.attr == "connect"
                and isinstance(f.value, ast.Name)
                and f.value.id == "sqlite3"
            ):
                problems.append(f"{rel}:{node.lineno}: sqlite3.connect 호출")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "sqlite3"
                and any(a.name == "connect" for a in node.names)
            ):
                problems.append(f"{rel}:{node.lineno}: sqlite3.connect import")

    # 4. 무거운 모듈은 함수 안에서만 import
    top_level = _top_level_imports(tree)
    for lineno, name in top_level:
        if name.split(".")[0] in HEAVY:
            problems.append(f"{rel}:{lineno}: {name} 를 모듈 맨 위에서 import")
    return problems


def _top_level_imports(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def visit(stmts: list[ast.stmt]) -> None:
        for s in stmts:
            if isinstance(s, ast.Import):
                out.extend((s.lineno, a.name) for a in s.names)
            elif isinstance(s, ast.ImportFrom) and not s.level:
                out.append((s.lineno, s.module or ""))
            elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            elif isinstance(s, ast.ClassDef):
                visit(s.body)
            elif isinstance(s, ast.If) and _is_type_checking(s.test):
                visit(s.orelse)  # 형 검사 때만 읽히는 import는 실행되지 않는다
            else:
                for field in ("body", "orelse", "finalbody", "handlers"):
                    sub = getattr(s, field, None)
                    if isinstance(sub, list):
                        visit(
                            [x for x in sub if isinstance(x, ast.stmt)]
                            + [y for h in sub if isinstance(h, ast.ExceptHandler) for y in h.body]
                        )

    visit(tree.body)  # type: ignore[attr-defined]
    return out


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def all_sources() -> list[tuple[str, str]]:
    return [(p.relative_to(SRC).as_posix(), p.read_text(encoding="utf-8")) for p in sorted(SRC.rglob("*.py"))]


def test_repository_follows_rules():
    problems = [p for rel, src in all_sources() for p in check_source(rel, src)]
    assert problems == []


def test_generation_commands_do_not_load_fetch_or_eval():
    """ask·run·prepare가 도는 프로세스에는 t2s.fetch와 t2s.eval이 올라오지 않는다."""
    code = (
        "import importlib, importlib.util, sys\n"
        "for m in ('t2s.cli.main', 't2s.cli.data_cmds', 't2s.cli.ask_cmd', 't2s.cli.run_cmds',\n"
        "          't2s.pipeline.run', 't2s.llm.fake',\n"
        "          't2s.runner.batch', 't2s.runner.records', 't2s.llm.select', 't2s.llm.codex_cli',\n"
        "          't2s.llm.claude_cli'):\n"
        "    try:\n"
        "        found = importlib.util.find_spec(m) is not None\n"
        "    except ModuleNotFoundError:\n"
        "        found = False\n"
        "    if found:\n"
        "        importlib.import_module(m)\n"
        "bad = [m for m in sys.modules if m.startswith(('t2s.fetch', 't2s.eval'))]\n"
        "print(','.join(bad))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


# 5. 자기 검사: 규칙을 어긴 가짜 모듈 글이 잡히는가


def test_self_check_import_table():
    assert check_source("datasource/x.py", "import t2s.prep.value_index\n")
    assert check_source("prep/x.py", "from t2s.dataset import config\n")
    assert check_source("dataset/x.py", "from t2s.pipeline import run\n")
    assert check_source("fetch/x.py", "from t2s.datasource import base\n")
    assert check_source("cli/data_cmds.py", "from t2s.fetch import bird\n")
    assert check_source("cli/ask_cmd.py", "import t2s.eval.gold\n")
    assert check_source("eval/x.py", "from t2s.runner import batch\n")
    assert check_source("prep/x.py", "from ..dataset import config\n")
    assert not check_source("cli/fetch_cmd.py", "from t2s.fetch import bird\n")
    assert not check_source("eval/x.py", "from t2s.runner.records import x\n")
    assert not check_source("prep/x.py", "from t2s.datasource.base import DataSource\n")


def test_self_check_gold_text():
    assert check_source("dataset/x.py", "P = 'gold/bird-minidev'\n")
    assert check_source("dataset/x.py", "P = 'gold'\n")
    assert check_source("prep/x.py", "x = f'{a}/gold/{b}'\n")
    assert check_source("prep/x.py", "P = 'mini_dev_sqlite.json'\n")
    assert check_source("prep/x.py", "K = 'evidence'\n")
    assert not check_source("prep/x.py", '"""gold/ 는 독스트링에서 괜찮다"""\n# gold/ 주석도\nX = "golden"\n')
    assert not check_source("fetch/x.py", "P = 'gold/bird-minidev'\n")
    assert check_source("runner/guard.py", "OTHER = 'gold'\n")
    assert not check_source("runner/guard.py", 'PROTECTED_DIRS = ("gold",)\n')


def test_self_check_connect_and_heavy():
    assert check_source("prep/x.py", "import sqlite3\nsqlite3.connect('a')\n")
    assert check_source("prep/x.py", "from sqlite3 import connect\n")
    assert not check_source("datasource/sqlite.py", "import sqlite3\nsqlite3.connect('a')\n")
    assert check_source("prep/embed.py", "import numpy as np\n")
    assert check_source("prep/embed.py", "try:\n    import onnxruntime\nexcept ImportError:\n    pass\n")
    assert not check_source("prep/embed.py", "def f():\n    import numpy as np\n    return np\n")
    assert not check_source("prep/embed.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import numpy\n")


# 6. 생성 파이프라인·모델 쪽 검사


def test_self_check_import_table_pipeline():
    assert check_source("llm/x.py", "from t2s.datasource import base\n")
    assert check_source("pipeline/x.py", "from t2s.dataset import config\n")
    assert check_source("pipeline/x.py", "from t2s.runner import guard\n")
    assert check_source("pipeline/x.py", "import t2s.eval.gold\n")
    assert not check_source("pipeline/x.py", "from t2s.prep import assets\nfrom t2s.llm.base import LlmBackend\n")


def test_self_check_gold_text_pipeline():
    assert check_source("pipeline/x.py", "K = 'evidence'\n")
    assert check_source("llm/x.py", "P = 'mini_dev_sqlite.json'\n")


def test_templates_have_no_gold_text():
    files = sorted(TEMPLATES.glob("*.txt"))
    assert "extract_keywords.txt" in [f.name for f in files]
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not any(p.search(text) for p in GOLD_PATTERNS), f.name


def test_pipeline_and_fake_do_not_load_heavy_modules():
    code = (
        "import importlib, importlib.util, sys\n"
        "for m in ('t2s.pipeline.run', 't2s.llm.fake', 't2s.llm.select', 't2s.runner.records'):\n"
        "    if importlib.util.find_spec(m) is not None:\n"
        "        importlib.import_module(m)\n"
        "print(','.join(m for m in ('numpy', 'onnxruntime', 'tokenizers') if m in sys.modules))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


# 7. 모델 선택·명령 쪽 검사


def test_self_check_import_table_backends():
    assert check_source("llm/select.py", "from t2s.dataset import config\n")
    assert check_source("llm/codex_cli.py", "from t2s.runner import guard\n")
    assert check_source("cli/ask_cmd.py", "from t2s.eval import report\n")
    assert not check_source("llm/select.py", "from t2s.llm.codex_cli import CodexBackend\n")


# 8. 실행 기록·채점 쪽 검사

EVAL_DEPS = {"func_timeout", "psycopg2", "pymysql"}
VENDOR_DIR = SRC / "eval" / "vendor" / "bird-minidev-abd11b6"
VENDOR_SHA256 = {
    "evaluation_ex.py": "da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89",
    "evaluation_utils.py": "f6943d249caac5aeaef9bce21d43dbf29dcef85a0c965a76df032a9542f308bf",
}


def test_self_check_import_table_batch_eval():
    assert check_source("runner/batch.py", "from t2s.eval import gold\n")
    assert check_source("runner/records.py", "import t2s.cli.main\n")
    assert check_source("eval/bird.py", "from t2s.pipeline import run\n")
    assert check_source("eval/bird.py", "from t2s.llm import base\n")
    assert check_source("eval/bird.py", "from t2s.prep import assets\n")
    assert check_source("eval/bird.py", "from t2s.datasource import sqlite\n")
    assert check_source("eval/report.py", "from t2s.runner import batch\n")
    assert check_source("cli/run_cmds.py", "from t2s.eval import bird\n")
    assert not check_source("cli/eval_cmds.py", "from t2s.eval import bird, report, leak_audit\n")
    assert not check_source(
        "eval/bird.py", "from t2s.runner.records import read_jsonl\nfrom t2s.dataset.config import Config\n"
    )
    assert not check_source(
        "runner/batch.py", "from t2s.pipeline.run import answer\nfrom t2s.llm.select import backend_for\n"
    )


def test_self_check_gold_text_batch_eval():
    assert check_source("runner/batch.py", "K = 'evidence'\n")
    assert check_source("cli/eval_cmds.py", "P = 'gold/_work'\n")
    assert check_source("cli/run_cmds.py", "P = 'mini_dev_sqlite_gold.sql'\n")
    assert not check_source("eval/bird.py", "P = 'gold/_work'\n")


def records_imports_t2s(source: str) -> list[str]:
    tree = ast.parse(source)
    return [name for _, name in imported_modules("runner/records.py", tree) if name == "t2s" or name.startswith("t2s.")]


def test_records_is_stdlib_only():
    assert records_imports_t2s((SRC / "runner" / "records.py").read_text(encoding="utf-8")) == []
    assert records_imports_t2s("from t2s.llm import base\n")
    assert records_imports_t2s("from . import batch\n")


def test_scoring_process_does_not_load_generation_side():
    code = (
        "import sys\n"
        "from t2s.cli.main import build_parser\n"
        "build_parser()\n"
        "import t2s.cli.eval_cmds\n"
        "bad = ('t2s.pipeline', 't2s.llm', 't2s.prep', 't2s.datasource', 'numpy', 'onnxruntime', 'tokenizers')\n"
        "print(','.join(m for m in sys.modules if m.startswith(bad)))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


def _eval_dep_imports(rel: str, source: str) -> list[str]:
    tree = ast.parse(source)
    return [name for _, name in imported_modules(rel, tree) if name.split(".")[0] in EVAL_DEPS]


def test_generation_side_does_not_import_eval_deps():
    bad = [(rel, n) for rel, src in all_sources() if not rel.startswith("eval/") for n in _eval_dep_imports(rel, src)]
    assert bad == []
    assert _eval_dep_imports("runner/x.py", "import func_timeout\n")


def test_vendor_hashes():
    import hashlib
    import json

    hashes = json.loads((VENDOR_DIR / "HASHES.json").read_text(encoding="utf-8"))["sha256"]
    assert hashes == VENDOR_SHA256
    for name, digest in hashes.items():
        assert hashlib.sha256((VENDOR_DIR / name).read_bytes()).hexdigest() == digest
