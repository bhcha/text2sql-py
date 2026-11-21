"""harness.toml 쓰기(dataset/route_store.py)와 route-apply의 승인 흐름."""

import difflib
import hashlib
import tomllib

import pytest

from t2s.cli import data_cmds
from t2s.cli.main import main
from t2s.dataset import route_store as S
from t2s.runner import records as R

TOML = """[defaults]
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/Xenova/all-mpnet-base-v2"

[profiles.p3-ref]
backend = "codex-cli"
model = "gpt-5.6-terra"
reasoning_effort = "medium"
timeout_seconds = 150
item_timeout_seconds = 1530

[profiles.p3-claude]
# 설명 주석은 남는다
backend = "claude-code"
model = "claude-opus-5-5"
reasoning_effort = "medium"
timeout_seconds = 150
item_timeout_seconds = 1530

[profiles.session]
backend = "auto"

[pipeline]
example_rows = 3
refine_rounds = 3
"""
LLM = {
    "backend": "claude-code",
    "model": "claude-opus-5-5",
    "reasoning_effort": "medium",
    "timeout_seconds": 150,
    "item_timeout_seconds": 1530,
}


def basis(n=1, route="single"):
    return {
        "route": route,
        "verdict": "no_difference",
        "rule": "route-judge/1",
        "backend": "claude-code",
        "model": "claude-opus-5-5",
        "reasoning_effort": "medium",
        "dataset": "bird-minidev",
        "evidence": "not_used",
        "questions": 500,
        "harness_run": f"20261121-bird-minidev-00000{n}-harness",
        "single_run": f"20261121-bird-minidev-00000{n}-single",
        "harness_correct": 260,
        "single_correct": 255,
        "new_right": 30,
        "new_wrong": 25,
        "p": 0.590234,
        "harness_seconds": 1234.5,
        "measured": "2026-11-21",
        "cli": "2.1.0 (Claude Code)",
        "commit": "abc1234",
    }


def _diff(a: str, b: str) -> list[str]:
    return [x for x in difflib.ndiff(a.split("\n"), b.split("\n")) if x[:1] in "+-"]


def test_values():
    assert S.toml_value(True) == "true" and S.toml_value(3) == "3" and S.toml_value(0.5) == "0.5"
    assert S.toml_value('가"b') == '"가\\"b"'
    for bad in (float("nan"), None, [1]):
        with pytest.raises(S.StoreError):
            S.toml_value(bad)


def test_choose_profile():
    profiles = tomllib.loads(TOML)["profiles"]
    assert S.choose_profile(profiles, LLM, None) == ("p3-claude", False)
    terra = {**LLM, "backend": "codex-cli", "model": "gpt-5.6-terra"}
    assert S.choose_profile(profiles, terra, None) == ("gpt-5-6-terra-medium", True)  # p3-ref는 고르지 않는다
    with pytest.raises(S.StoreError):
        S.choose_profile(profiles, terra, "p3-ref")
    with pytest.raises(S.StoreError):
        S.choose_profile(profiles, terra, "Bad Name")
    assert S.choose_profile(profiles, terra, "mine") == ("mine", True)
    assert S.choose_profile({**profiles, "gpt-5-6-terra-medium": {"backend": "auto"}}, terra, None) == (
        "gpt-5-6-terra-medium-2",
        True,
    )


def test_existing_profile_gets_route_line_and_basis():
    plan = S.plan(TOML, profile="p3-claude", new_profile=False, llm=LLM, route="single", basis=basis())
    new = tomllib.loads(plan.text)
    assert new["profiles"]["p3-claude"]["route"] == "single"
    assert new["profiles"]["p3-claude"]["route_basis"] == [basis()]
    old = tomllib.loads(TOML)
    old["profiles"]["p3-claude"].update(route="single", route_basis=[basis()])
    assert new == old
    added = _diff(TOML, plan.text)
    assert all(x.startswith("+ ") for x in added)  # 지운 줄이 없다
    assert '+ route = "single"' in added and "+ [[profiles.p3-claude.route_basis]]" in added
    assert "# 설명 주석은 남는다" in plan.text
    # route 줄은 p3-claude 표 안(마지막 칸 줄 뒤)에 들어간다
    lines = plan.text.split("\n")
    assert (
        lines[lines.index("item_timeout_seconds = 1530", lines.index("[profiles.p3-claude]")) + 1] == 'route = "single"'
    )
    assert plan.shown[0].startswith('+ route = "single"')


def test_second_basis_keeps_first_and_replaces_route_line():
    first = S.plan(TOML, profile="p3-claude", new_profile=False, llm=LLM, route="single", basis=basis(1))
    second = S.plan(
        first.text, profile="p3-claude", new_profile=False, llm=LLM, route="harness", basis=basis(2, "harness")
    )
    prof = tomllib.loads(second.text)["profiles"]["p3-claude"]
    assert prof["route"] == "harness"
    assert [b["route"] for b in prof["route_basis"]] == ["single", "harness"]
    changed = _diff(first.text, second.text)
    assert changed.count('- route = "single"') == 1 and changed[:2] == ['- route = "single"', '+ route = "harness"']
    assert [x for x in changed if x.startswith("- ")] == ['- route = "single"']
    assert second.shown[0] == '~ route = "single" → route = "harness"'
    with pytest.raises(S.StoreError):
        S.plan(second.text, profile="p3-claude", new_profile=False, llm=LLM, route="single", basis=basis(2))


def test_new_profile_block():
    terra = {**LLM, "backend": "codex-cli", "model": "gpt-5.6-luna"}
    b = {**basis(), "backend": "codex-cli", "model": "gpt-5.6-luna"}
    plan = S.plan(TOML, profile="gpt-5-6-luna-medium", new_profile=True, llm=terra, route="single", basis=b)
    prof = tomllib.loads(plan.text)["profiles"]["gpt-5-6-luna-medium"]
    assert prof == {**{k: terra[k] for k in S.NEW_PROFILE_KEYS}, "route": "single", "route_basis": [b]}
    assert all(x.startswith("+ ") for x in _diff(TOML, plan.text))
    assert plan.text.startswith(TOML.rstrip("\n"))
    with pytest.raises(S.StoreError):
        S.plan(TOML, profile="p3-claude", new_profile=True, llm=terra, route="single", basis=b)
    with pytest.raises(S.StoreError):
        S.plan(TOML, profile="p3-ref", new_profile=False, llm=terra, route="single", basis=b)


# ── route-apply 명령 ─────────────────────────────────────


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setenv("T2S_ROOT", str(r))
    monkeypatch.setattr(data_cmds, "install_guard", lambda root: None)
    h, c = "20261121-bird-minidev-000001-harness", "20261121-bird-minidev-000001-single"
    for rid, role, other in ((h, "harness", c), (c, "single", h)):
        R.write_json_atomic(
            r / "runs" / rid / "run.json",
            {
                "run_id": rid,
                "started_at": "2026-11-21T00:00:00Z",
                "llm": LLM,
                "route": {
                    "applied": role,
                    "pair": {"id": "20261121-bird-minidev-000001", "role": role, "other": other},
                },
            },
        )
    judgment = {"usable": True, "reasons": [], "route": "single", "basis": basis()}
    R.write_json_atomic(R.judgment_path(r / "runs", h, c), judgment)
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def test_apply_needs_yes(root, capsys):
    digest = hashlib.sha256((root / "harness.toml").read_bytes()).hexdigest()
    code, lines, _ = cli(capsys, "route-apply")
    assert code == 3
    assert lines[0] == "harness.toml에 더할 글(프로필 p3-claude, 있는 프로필, 경로 single):"
    assert lines[-1] == "승인하면 --yes를 붙여 다시 실행: t2s route-apply --yes"
    assert hashlib.sha256((root / "harness.toml").read_bytes()).hexdigest() == digest
    code, lines, _ = cli(capsys, "route-apply", "--yes")
    assert code == 0 and lines[-1].startswith("harness.toml에 썼습니다(프로필 p3-claude, route = single)")
    prof = tomllib.loads((root / "harness.toml").read_text())["profiles"]["p3-claude"]
    assert prof["route"] == "single" and len(prof["route_basis"]) == 1
    code, _, err = cli(capsys, "route-apply", "--yes")
    assert code == 1 and "같은 측정의 근거가 이미 있습니다" in err
    assert not (root / "harness.toml.tmp").exists()


def test_apply_refuses_unusable_or_held(root, capsys):
    path = R.judgment_path(root / "runs", "20261121-bird-minidev-000001-harness", "20261121-bird-minidev-000001-single")
    R.write_json_atomic(
        path, {"usable": False, "reasons": ["소표본", "가짜 백엔드"], "route": "single", "basis": basis()}
    )
    code, _, err = cli(capsys, "route-apply", "--yes")
    assert code == 1 and "소표본, 가짜 백엔드" in err
    R.write_json_atomic(path, {"usable": True, "reasons": [], "route": None, "basis": {**basis(), "route": None}})
    code, _, err = cli(capsys, "route-apply", "--yes")
    assert code == 1 and "경로 값이 틀렸습니다" in err
    assert tomllib.loads((root / "harness.toml").read_text()) == tomllib.loads(TOML)
    R.write_json_atomic(path, {"usable": True, "reasons": [], "route": "single", "basis": basis()})
    code, _, err = cli(capsys, "route-apply", "--profile", "p3-ref", "--yes")
    assert code == 1 and "참조 프로필 p3-ref에는 쓰지 않습니다" in err
    assert tomllib.loads((root / "harness.toml").read_text()) == tomllib.loads(TOML)
