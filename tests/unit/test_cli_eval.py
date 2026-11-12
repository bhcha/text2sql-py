"""작은 가짜 BIRD로 run → (하위 프로세스) score → report까지, 그리고 정답 없는 데이터셋."""

import hashlib
import json
import os

import pytest

from fixtures import mini_bird as M
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")


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


def test_run_scores_and_reports(root, capsys):
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", "fk")
    assert code == 0, lines
    run = root / "runs/fk"
    score = R.read_json(run / "score.json")
    assert score["cross_check"] is True and score["data_unchanged"] is True
    assert score["ex_total"]["denominator"] == 6
    assert score["leak"]["total"] == 0 and score["leak"]["prompts"] > 0
    assert score["label"].endswith(" / 가짜 백엔드·점수 아님") and "소표본" not in score["label"]
    assert (run / "report.json").is_file() and (run / "leak-audit.json").is_file()
    log = (run / "run.log").read_text(encoding="utf-8")
    assert "score: BIRD Mini-Dev / SQLite / ab12cd34 / 6문제" in log
    control = R.read_json(run / "control.json")
    assert control["phase"] == "done" and control["state"] == "finished"
    # run 폴더에 정답 글자가 없다
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in run.rglob("*") if p.is_file())
    for secret in (*M.EVIDENCE, M.GOLD_SQL[4], M.GOLD_SQL[5]):
        assert secret not in text
    assert not any(p.name.startswith(("gold", "diff")) for p in run.iterdir())

    code, lines, _ = cli(capsys, "score", "--run", "fk")
    assert code == 0 and lines[0].startswith("score: BIRD Mini-Dev") and lines[0].endswith("누설 0")
    code, lines, _ = cli(capsys, "report")
    assert code == 0 and lines[0] == score["label"] and len(lines) <= 12
    assert "비교 대상 없음" in lines
    code, lines, _ = cli(capsys, "leak-audit", "--run", "fk")
    assert (
        code == 0
        and lines[0].startswith("누설 검사: 프롬프트 ")
        and "정답 SQL 0, evidence 0, 다른 문항 정답 0, 프롬프트 누락 0" in lines[0]
    )
    code, lines, _ = cli(capsys, "status")
    assert lines[0] == "fk · mini · 끝남" and "생성 끝 예 · 채점 끝 예 · 보고 끝 예" in lines


def test_report_before_score(root, capsys):
    cli(capsys, "run", "--foreground", "--backend", "fake", "--limit", "5", "--no-score", "--run-id", "ns")
    code, lines, _ = cli(capsys, "report", "--run", "ns")
    assert code == 1 and lines == ["아직 채점 전입니다: t2s score --run ns"]
    code, lines, _ = cli(capsys, "score", "--run", "ns")
    assert code == 0 and "소표본·점수 아님 / 가짜 백엔드·점수 아님 →" in lines[0]


def test_dataset_without_gold(root, capsys, shop_db, tmp_path):
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    q = tmp_path / "q.txt"
    q.write_text("How many products?\nList product names.\nHow many orders are paid?\n")
    capsys.readouterr()
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--questions", str(q), "--run-id", "ng")
    assert code == 0
    log = (root / "runs/ng/run.log").read_text(encoding="utf-8")
    assert "정답 없음: 채점 생략" in log
    code, lines, _ = cli(capsys, "score", "--run", "ng")
    assert code == 4 and lines == ["정답 없음: 채점 생략"]
    code, lines, _ = cli(capsys, "report", "--run", "ng")
    assert code == 0 and lines[0] == "shop · 정답 없음: 채점 생략" and len(lines) == 6
    assert lines[2] == "문항 3 · 상태 generated 3, no_sql 0, exception 0, not_started 0"
    code, _, _ = cli(capsys, "leak-audit", "--run", "ng")
    assert code == 4
    assert not os.path.exists(root / "runs/ng/score.json")
    assert json.loads((root / "runs/ng/report.json").read_text())["kind"] == "no_gold"
