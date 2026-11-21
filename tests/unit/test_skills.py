"""스킬 글 검사: 두 위치에 있고, .agents 판은 부르는 표기만 다르고, 글 속 명령이 실제 하위 명령과 맞는다."""

import argparse
import re
from pathlib import Path

from t2s.cli.main import build_parser

REPO = Path(__file__).resolve().parents[2]
SKILLS = ("getbird", "getolist", "connect", "text2sql", "runset", "newmodel")
CALL = re.compile(r"(?<![\w/.-])/(text2sql|runset|getbird|getolist|connect|newmodel)(?![A-Za-z0-9_-])")
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


def test_newmodel_saves_recordable_judgment_without_asking():
    """newmodel은 기록할 수 있는 판정이면 묻지 않고 route-apply --yes로 저장한다. 기록할 수 없는 판정은 쓰지 않는다."""
    text = _text(".claude", "newmodel")
    assert "uv run t2s route-apply --yes" in text
    assert "묻지 않고 바로" in text and "기록할 수 없는 판정" in text
    assert "기록에 쓸 수 없음" in text and "p3-ref" in text and "커밋하지 않는다" in text
    assert "사용자 승인 없이" not in text and "승인했을 때만" not in text


def test_model_name_skills_mark_unstated_effort():
    """모델 이름으로 경로를 맞출 때 추론 단계를 말하지 않은 경우를 명령이 알 수 있게 표시한다."""
    for name in ("text2sql", "runset"):
        text = _text(".claude", name)
        assert "`--effort medium --effort-source default-medium`" in text, name
        assert "모델 이름과 추론 단계를 말했을 때도" in text and "`--profile p3-ref`" in text, name
