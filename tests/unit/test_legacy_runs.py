"""옛 run 폴더([paths] legacy_runs)는 읽기만 한다: report --against, route-judge, status --run-id가 읽고, resume은 거절한다."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from fixtures import mini_bird as M
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.dataset.config import ConfigError, load_config
from t2s.dataset.state import load_state
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")

OLD = "20261120-mini-abc123"  # 옛 이름 꼴(YYYYMMDD-데이터셋-16진)
OLD_H, OLD_C = f"{OLD}-harness", f"{OLD}-single"


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


def _tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def _scored_run(capsys, rid: str, *extra: str) -> None:
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--no-score", "--run-id", rid, *extra)
    assert code == 0, lines
    code, lines, _ = cli(capsys, "score", "--run", rid)
    assert code == 0, lines


def _to_legacy(root: Path, *rids: str) -> Path:
    """만든 run을 옛 run 폴더 자리로 옮기고 harness.toml에 [paths] legacy_runs를 적는다."""
    legacy = root / "old-runs"
    legacy.mkdir(exist_ok=True)
    for rid in rids:
        shutil.move(root / "runs" / rid, legacy / rid)
    toml = (root / "harness.toml").read_text(encoding="utf-8")
    if "legacy_runs" not in toml:
        toml = toml.replace('runs = "runs"\n', 'runs = "runs"\nlegacy_runs = ["old-runs"]\n', 1)
        (root / "harness.toml").write_text(toml, encoding="utf-8")
    return legacy


def test_config_reads_legacy_runs(root):
    assert load_config(root, load_state(root)).legacy_runs_dirs == ()
    _to_legacy(root)
    assert load_config(root, load_state(root)).legacy_runs_dirs == (root / "old-runs",)
    bad = (root / "harness.toml").read_text(encoding="utf-8").replace('["old-runs"]', '"old-runs"')
    (root / "harness.toml").write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(root, load_state(root))


def test_find_run_order_and_names(tmp_path):
    here, old = tmp_path / "sessions", tmp_path / "old"
    for base, rid in ((here, "a"), (old, "a"), (old, "b")):
        (base / rid).mkdir(parents=True)
        (base / rid / "run.json").write_text("{}", encoding="utf-8")
    (old / "c").mkdir()  # run.json 없음
    assert R.find_run(here, "a", (old,)) == (here / "a", False)
    assert R.find_run(here, "b", (old,)) == (old / "b", True)
    for rid in ("c", "zz", "", ".", "..", "../old/b", "b/x"):
        assert R.find_run(here, rid, (old,)) is None, rid


def test_report_against_legacy_run(root, capsys):
    _scored_run(capsys, OLD)
    legacy = _to_legacy(root, OLD)
    before = _tree(legacy)
    _scored_run(capsys, "new1")
    code, lines, _ = cli(capsys, "report", "--run", "new1", "--against", OLD)
    assert code == 0, lines
    assert any(x.startswith(f"기준 run {OLD} (EX ") for x in lines), lines
    assert "새로 맞음 0, 새로 틀림 0 (공통 6위치)" in lines
    assert _tree(legacy) == before  # 옛 폴더에 쓰지 않았다
    # 어디에도 없는 이름은 지금처럼 거절
    code, _, err = cli(capsys, "report", "--run", "new1", "--against", "20261120-mini-ffffff")
    assert code == 1 and "비교 대상을 읽지 못했습니다" in err


def test_status_and_resume_on_legacy_run(root, capsys):
    code, _, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--no-score", "--run-id", OLD)
    assert code == 0
    legacy = _to_legacy(root, OLD)
    before = _tree(legacy)
    code, lines, _ = cli(capsys, "status", "--run-id", OLD)
    assert (
        code == 0
        and lines[0].startswith(f"{OLD} · mini · evidence not_used · ")
        and lines[0].endswith(" · 옛 run(읽기만)")
    )
    code, _, err = cli(capsys, "resume", "--run-id", OLD, "--foreground")
    assert code == 1 and "이어 돌 수 없습니다: 옛 run 폴더(legacy_runs)의 run은 읽기만 합니다" in err
    code, _, err = cli(capsys, "resume", "--run-id", "20261120-mini-ffffff", "--foreground")
    assert code == 1 and "run이 없습니다" in err
    assert _tree(legacy) == before
    assert not (root / "runs" / OLD).exists()


def test_route_judge_reads_legacy_runs_without_writing(root, capsys):
    _scored_run(capsys, OLD_H, "--route", "harness")
    _scored_run(capsys, OLD_C, "--route", "single")
    legacy = _to_legacy(root, OLD_H, OLD_C)
    before = _tree(legacy)
    code, lines, _ = cli(capsys, "route-judge", "--harness", OLD_H, "--candidate", OLD_C)
    assert code == 0, lines
    assert any(x.startswith("경로 판정: ") for x in lines)
    assert lines[-1] == "판정 파일을 쓰지 않음: 하네스 run이 옛 run 폴더(legacy_runs)에 있어 읽기만 합니다"
    assert _tree(legacy) == before
    assert not R.judgment_path(root / "runs", OLD_H, OLD_C).exists()


def test_route_judge_new_harness_against_legacy_candidate_writes_in_new_folder(root, capsys):
    _scored_run(capsys, OLD_C, "--route", "single")
    legacy = _to_legacy(root, OLD_C)
    before = _tree(legacy)
    _scored_run(capsys, "h-new", "--route", "harness")
    code, lines, _ = cli(capsys, "route-judge", "--harness", "h-new", "--candidate", OLD_C)
    assert code == 0, lines
    assert R.judgment_path(root / "runs", "h-new", OLD_C).is_file()
    assert _tree(legacy) == before
