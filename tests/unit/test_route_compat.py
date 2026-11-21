"""경로 칸을 더해도 기존 프로필·옛 run의 라벨·채점·보고가 바뀌지 않는다. 단일 호출 run의 기록과 꼬리표."""

import hashlib

import pytest

from fixtures import mini_bird as M
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.eval import bird, study
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")

META = {
    "dataset_kind": "bird-minidev",
    "archive_sha256": "cc48ba16" + "0" * 56,
    "git": {"head": "208444d" + "0" * 33, "dirty": False},
    "llm": {"backend": "codex-cli", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
}
BIRD_LABEL = (
    "BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정"
    " / gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python 208444d"
)
STUDY_LABEL = (
    "Olist 스터디 평가셋 v0.3 / SQLite / 131eaf49 / 49문제 / EX(스터디 결과 비교) / evidence not_used / 로컬 측정"
    " / gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python 208444d"
)


def test_labels_without_route_or_harness_unchanged():
    for meta in (META, {**META, "route": {"applied": "harness"}}, {**META, "route": None}):
        assert bird.label(meta, 500, False) == BIRD_LABEL
        assert study.label(meta, 49, False, "Olist 스터디 평가셋 v0.3", "131eaf49" + "0" * 56) == STUDY_LABEL


def test_labels_with_single_and_partial():
    single = {**META, "route": {"applied": "single"}}
    assert bird.label(single, 500, False) == BIRD_LABEL.replace(
        " / 1회 실행 / ", " / 1회 실행 / 경로 single(단일 호출) / "
    )
    partial = {**META, "route": {"applied": "partial", "overrides": {"value_search": False, "refine_rounds": 0}}}
    expected = STUDY_LABEL.replace(
        " / 1회 실행 / ", " / 1회 실행 / 경로 partial(value_search=false, refine_rounds=0) / "
    )
    assert study.label(partial, 49, False, "Olist 스터디 평가셋 v0.3", "131eaf49" + "0" * 56) == expected


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys):
    r = M.make_mini_bird(tmp_path / "repo", shop_db)
    model = r / "data/models/Xenova/all-mpnet-base-v2"
    fake = {}
    for f in embed.MODEL_FILES:
        (model / f).parent.mkdir(parents=True, exist_ok=True)
        (model / f).write_bytes(f"fake {f}".encode())
        fake[f] = hashlib.sha256(f"fake {f}".encode()).hexdigest()
    monkeypatch.setattr(embed, "MODEL_SHA256", fake)
    monkeypatch.setattr(embed, "load_embedder", lambda d: embed.HashEmbedder())
    monkeypatch.setenv("T2S_ROOT", str(r))
    monkeypatch.setattr(data_cmds, "install_guard", lambda root: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root: None)
    assert main(["prepare"]) == 0
    capsys.readouterr()
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def _strip_new_fields(run):
    meta = R.read_json(run / "run.json")
    meta.pop("route")
    for k in ("value_search", "column_profile"):
        meta["pipeline"].pop(k)
    R.write_json_atomic(run / "run.json", meta)


def test_old_form_run_scores_and_reports_the_same(root, capsys):
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--no-score", "--run-id", "new")
    assert code == 0 and not any(x.startswith("경로 ") for x in lines)
    meta = R.read_json(root / "runs/new/run.json")
    assert meta["route"]["applied"] == "harness" and meta["route"]["reason"] == "no_profile"
    assert meta["route"]["pair"] is None and meta["pipeline"]["value_search"] is True
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--no-score", "--run-id", "old")
    _strip_new_fields(root / "runs/old")
    reports = {}
    for rid in ("new", "old"):
        code, lines, _ = cli(capsys, "score", "--run", rid)
        assert code == 0
        code, lines, _ = cli(capsys, "report", "--run", rid)
        assert code == 0
        reports[rid] = [x for x in lines if not x.startswith("runs/")]
    assert reports["new"] == reports["old"]
    assert reports["old"][0] == R.read_json(root / "runs/old/score.json")["label"]
    assert "경로" not in reports["old"][0]
    assert not any("경로가 달라" in x for x in reports["new"])


def test_single_route_run(root, capsys):
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--route", "single", "--run-id", "s1")
    assert code == 0, lines
    assert "경로 single(단일 호출) · 측정용 강제" in lines
    assert any(x.startswith("시작: s1") and x.endswith("· 경로 single") for x in lines)
    run = root / "runs/s1"
    meta = R.read_json(run / "run.json")
    assert meta["route"]["applied"] == "single" and meta["route"]["single_rules"] == "single/1"
    rows = R.read_jsonl(run / "records.jsonl")
    assert all(r["llm"]["call_count"] == 1 and r["status"] == "generated" for r in rows)
    assert all(r["final_exec"]["source"] == "extra" for r in rows)
    assert sorted(p.name for p in (run / "items/0000/calls").iterdir()) == ["1"]
    score = R.read_json(run / "score.json")
    assert " / 1회 실행 / 경로 single(단일 호출) / 구현 Python " in score["label"]
    assert score["leak"]["total"] == 0
    code, lines, _ = cli(capsys, "status", "--run-id", "s1")
    assert "경로 single(단일 호출)" in lines and len(lines) <= 10
    code, _, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", "h1")
    code, lines, _ = cli(capsys, "report", "--run", "s1", "--against", "h1")
    assert code == 0 and "경로가 달라 재현 비교 아님" in lines
    assert R.read_json(run / "report.json")["route"]["applied"] == "single"


FAKE_PROFILE = """
[profiles.fk]
backend = "fake"
model = "fake-model"
reasoning_effort = "none"
route = "single"

[[profiles.fk.route_basis]]
route = "single"
backend = "fake"
model = "fake-model"
reasoning_effort = "none"
dataset = "mini"
measured = "2026-11-21"
"""


def test_ask_and_run_follow_profile_route(root, capsys, monkeypatch):
    import json

    from t2s.cli import ask_cmd

    monkeypatch.setattr(ask_cmd, "install_guard", lambda root: None)
    (root / "harness.toml").write_text((root / "harness.toml").read_text() + FAKE_PROFILE, encoding="utf-8")
    code, lines, _ = cli(capsys, "ask", "--profile", "fk", "--source", "shop", "How many products?")
    assert code == 0, lines
    assert lines[1] == "경로 single(단일 호출) · 프로필 fk · 근거 2026-11-21 mini"
    folder = root / lines[-1].split(": ", 1)[1]
    record = json.loads((folder / "ask.json").read_text(encoding="utf-8"))
    assert record["route"]["applied"] == "single" and record["route"]["source"] == "profile"
    assert sorted(p.name for p in (folder / "calls").iterdir()) == ["1"]
    assert any(x.startswith("호출 1번") for x in lines)

    code, lines, _ = cli(
        capsys, "run", "--foreground", "--profile", "fk", "--no-score", "--run-id", "pf", "--limit", "2"
    )
    assert code == 0 and "경로 single(단일 호출) · 프로필 fk · 근거 2026-11-21 mini" in lines
    meta = R.read_json(root / "runs/pf/run.json")
    assert meta["route"]["applied"] == "single" and meta["route"]["basis"]["dataset"] == "mini"
    # 근거에 evidence 방식이 없으면 경고(경로는 그대로)
    assert meta["route"]["warnings"] == ["근거 evidence 조건이 다릅니다(기록 없음 → not_used). 잠정 경로입니다"]
    code, lines, _ = cli(capsys, "status", "--run-id", "pf")
    assert any(x.startswith("경로 single(단일 호출) · 주의 1건") for x in lines) and len(lines) <= 10


NAMED_PROFILE = """
[profiles.nm]
backend = "codex-cli"
model = "gpt-test-9"
reasoning_effort = "medium"
route = "single"

[[profiles.nm.route_basis]]
route = "single"
backend = "codex-cli"
model = "gpt-test-9"
reasoning_effort = "medium"
dataset = "mini"
evidence = "not_used"
measured = "2026-11-21"
cli = "codex-cli 9.9.9"
"""
NAMED_ARGS = ("--backend", "codex-cli", "--model", "gpt-test-9", "--effort", "medium", "--model-source", "conversation")
NAMED_LINE = "경로 single(단일 호출) · 프로필 nm(모델 이름으로 맞춤) · 근거 2026-11-21 mini"


def test_ask_run_and_route_start_by_model_name(root, capsys, monkeypatch):
    """모델 이름으로 불러도 글자 그대로 같은 기록이 있으면 ask·run이 그 경로를 따른다. route-start는 강제 그대로다.
    모델 호출은 가짜 백엔드로 바꿔 끼운다(경로 판정은 codex-cli 조합 그대로 본다)."""
    import json

    from t2s.cli import ask_cmd
    from t2s.llm import select
    from t2s.llm.fake import FakeBackend
    from t2s.runner import batch

    monkeypatch.setattr(ask_cmd, "install_guard", lambda root: None)
    monkeypatch.setattr(select, "backend_for", lambda llm, single=False: FakeBackend())
    monkeypatch.setattr(batch, "backend_for", lambda llm, single=False: FakeBackend())
    monkeypatch.setattr(select, "backend_bin", lambda backend: None)
    monkeypatch.setattr(batch, "cli_version", lambda backend: "codex-cli 9.9.9")
    (root / "harness.toml").write_text((root / "harness.toml").read_text() + NAMED_PROFILE, encoding="utf-8")

    code, lines, _ = cli(capsys, "ask", *NAMED_ARGS, "--source", "shop", "How many products?")
    assert code == 0, lines
    assert (
        lines[0].startswith("모델 gpt-test-9 · 추론 medium · 백엔드 codex-cli · 출처 대화") and lines[1] == NAMED_LINE
    )
    folder = root / lines[-1].split(": ", 1)[1]
    route = json.loads((folder / "ask.json").read_text(encoding="utf-8"))["route"]
    assert (route["applied"], route["source"], route["profile"], route["matches"]) == (
        "single", "profile-match", "nm", ["nm"]
    )  # fmt: skip
    assert route["basis"]["cli"] == "codex-cli 9.9.9" and route["warnings"] == []
    assert sorted(p.name for p in (folder / "calls").iterdir()) == ["1"]

    # 추론 단계를 말하지 않으면(기본 medium) 맞추지 않는다
    code, lines, _ = cli(capsys, "ask", *NAMED_ARGS[:4], "--model-source", "conversation", "--source", "shop", "Hi?")
    assert code == 0 and not any(x.startswith("경로 ") for x in lines)
    folder = root / lines[-1].split(": ", 1)[1]
    route = json.loads((folder / "ask.json").read_text(encoding="utf-8"))["route"]
    assert (route["applied"], route["reason"]) == ("harness", "no_effort")

    code, lines, _ = cli(
        capsys, "run", "--foreground", *NAMED_ARGS, "--no-probe", "--no-score", "--run-id", "nm1", "--limit", "2"
    )
    assert code == 0 and NAMED_LINE in lines, lines
    meta = R.read_json(root / "runs/nm1/run.json")
    assert (meta["route"]["applied"], meta["route"]["source"], meta["route"]["profile"]) == (
        "single", "profile-match", "nm"
    )  # fmt: skip
    assert meta["route"]["warnings"] == []
    rows = R.read_jsonl(root / "runs/nm1/records.jsonl")
    assert all(r["llm"]["call_count"] == 1 for r in rows)

    # 근거의 CLI 판만 다르면 경로는 그대로이고 경고가 남는다
    monkeypatch.setattr(batch, "cli_version", lambda backend: "codex-cli 10.0.0")
    code, lines, _ = cli(capsys, "ask", *NAMED_ARGS, "--source", "shop", "How many products?")
    assert code == 0 and lines[1] == NAMED_LINE
    assert lines[2].startswith("주의: 경로 근거가 낡았습니다(CLI codex-cli 9.9.9 → codex-cli 10.0.0)")

    # 측정(route-start)은 기록에 끌려가지 않고 두 경로를 강제한다
    class _P:
        pid = 424242

    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: _P())
    code, lines, _ = cli(capsys, "route-start", *NAMED_ARGS, "--no-probe", "--limit", "2")
    assert code == 0, lines
    assert lines.count("경로 harness(전체 P3) · 측정용 강제") == 1 and "경로 single(단일 호출) · 측정용 강제" in lines
    assert NAMED_LINE not in lines
    pairs = sorted((root / "runs").glob("*-harness")) + sorted((root / "runs").glob("*-single"))
    sources = {R.read_json(r / "run.json")["route"]["source"] for r in pairs}
    assert len(pairs) == 2 and sources == {"forced"}
