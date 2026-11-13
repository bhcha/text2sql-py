"""스킬 글 검사: 두 위치에 있고, .agents 판은 부르는 표기만 다르고, 글 속 명령이 실제 하위 명령과 맞는다."""

import argparse
import re
from pathlib import Path

from t2s.cli.main import build_parser

REPO = Path(__file__).resolve().parents[2]
SKILLS = ("getbird", "getolist", "connect", "text2sql", "runset")
CALL = re.compile(r"(?<![\w/.-])/(text2sql|runset|getbird|getolist|connect)(?![A-Za-z0-9_-])")
COMMAND = re.compile(r"uv run t2s ([a-z][a-z-]*)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("하위 명령이 없다")


def _text(where: str, name: str) -> str:
    return (REPO / where / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_both_locations_and_only_call_notation_differs():
    for name in SKILLS:
        claude = _text(".claude", name)
        agents = _text(".agents", name)
        assert CALL.sub(r"$\1", claude) == agents, name
        assert claude.startswith(f"---\nname: {name}\n"), name


def test_commands_exist():
    subs = _subparsers()
    flags = {opt for p in subs.values() for a in p._actions for opt in a.option_strings}
    for name in SKILLS:
        text = _text(".claude", name)
        for cmd in COMMAND.findall(text):
            assert cmd in subs, (name, cmd)
        for flag in FLAG.findall(text):
            assert flag in flags, (name, flag)


def test_no_section_sign():
    for where in (".claude", ".agents"):
        for name in SKILLS:
            assert "§" not in _text(where, name), (where, name)
