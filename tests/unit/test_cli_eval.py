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
    assert lines[0] == "fk · mini · evidence not_used · 끝남" and "생성 끝 예 · 채점 끝 예 · 보고 끝 예" in lines


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


@pytest.fixture
def study_root(tmp_path, monkeypatch, shop_db, capsys):
    from fixtures import mini_study as MS

    r = MS.make_mini_study(tmp_path / "srepo", shop_db)
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
    return r, MS


def test_study_run_scores_and_reports(study_root, capsys):
    root, MS = study_root
    code, lines, errs = cli(
        capsys, "run", "--foreground", "--backend", "fake", "--questions", str(root / MS.INPUTS), "--run-id", "sf"
    )
    assert code == 0, lines
    run = root / "runs/sf"
    meta = R.read_json(run / "run.json")
    q = meta["questions"]
    assert q["ids"] == MS.IDS and q["input_format"] == "study-model-inputs" and q["conditions"] == "not_used"
    assert q["source_sha256"] == hashlib.sha256((root / MS.INPUTS).read_bytes()).hexdigest()
    score = R.read_json(run / "score.json")
    assert score["ex_total"]["correct"] == 2 and score["ex_total"]["denominator"] == 4
    assert score["cross_check"] is True and score["data_unchanged"] is True
    assert score["leak"]["total"] == 0 and score["leak"]["prompts"] > 0
    assert score["label"].startswith("Olist 스터디 평가셋 v0.3 / SQLite / ")
    assert score["label"].endswith(" / 가짜 백엔드·점수 아님") and "소표본" not in score["label"]
    log = (run / "run.log").read_text(encoding="utf-8")
    assert "score: Olist 스터디 평가셋 v0.3" in log and "정답 대조 True" in log
    # 프롬프트에 문항 조건·정답 글이 없고, run 폴더 어디에도 정답·조건 글이 없다
    prompts = list(run.rglob("prompt.txt"))
    assert prompts
    for p in prompts:
        text = p.read_text(encoding="utf-8")
        assert MS.COND_MARK not in text and MS.MARK not in text
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in run.rglob("*") if p.is_file())
    assert MS.MARK not in text and MS.COND_MARK not in text
    work = root / "gold/_work/sf/detail.jsonl"
    assert work.is_file()

    code, lines, errs = cli(capsys, "score", "--run", "sf")
    assert code == 0 and lines[0].startswith("score: Olist") and lines[0].endswith("누설 0")
    code, rep, errs2 = cli(capsys, "report")
    assert code == 0 and rep[0] == score["label"] and "비교 대상 없음" in rep
    assert rep[2].startswith("정답 대조 True · DB 불변 True")
    code, against, _ = cli(capsys, "report", "--against", "sf")
    assert code == 0 and "부호 검정 p=1.000. 4문항·1회 실행이라 낫다·못하다로 읽지 않는다" in against
    code, leak, _ = cli(capsys, "leak-audit", "--run", "sf")
    assert code == 0 and "문항 조건·채점 메모 0" in leak[0]
    out = "\n".join(lines + rep + against + leak) + errs + errs2
    assert MS.MARK not in out and MS.COND_MARK not in out

    code, _, err = cli(capsys, "score", "--predictions", str(work), "--run-id", "x")
    assert code == 2 and "--predictions" in err and not (root / "runs/x").exists()


def test_study_score_rejects_plain_question_run(study_root, capsys, tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("How many products?\n")
    code, _, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--questions", str(q), "--run-id", "tx")
    assert code == 1  # 이어 부른 채점이 실패한다(문항 id 기록이 없음)
    code, _, err = cli(capsys, "score", "--run", "tx")
    assert code == 1 and "스터디 모델 입력 파일" in err


def _gold_path_hits(root, run) -> list[str]:
    """run 폴더 파일 중 정답 폴더 경로 글자(절대 경로, 또는 자리 표시 <gold> 아닌 gold/ 경로)가 든 것."""
    import re

    marks = {str(root / "gold"), str(root.resolve() / "gold")}
    bare = re.compile(r"(?<![<\w.-])gold[/\\]")
    hits = []
    for p in run.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            hits += [f"{p.relative_to(run)}: {m}" for m in marks if m in text]
            hits += [f"{p.relative_to(run)}: gold/" for _ in bare.findall(text)[:1]]
    return hits


def test_run_folder_has_no_gold_path(root, capsys):
    """채점한 run(전체·소표본) 폴더 어디에도 정답 폴더 경로 글자가 없다. 채점기 명령은 <gold>로 남는다."""
    for rid, extra in (("full", []), ("part", ["--limit", "5"])):
        code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", rid, *extra)
        assert code == 0, lines
        assert cli(capsys, "report", "--run", rid)[0] == 0
        assert cli(capsys, "leak-audit", "--run", rid)[0] == 0
        run = root / "runs" / rid
        assert _gold_path_hits(root, run) == []
        command = R.read_json(run / "score.json")["evaluator"]["command"]
        gold_args = [a for a in command if a.startswith("<gold>/")]
        if rid == "full":
            assert len(gold_args) == 2 and gold_args[1].startswith("<gold>/_work/full/")
        else:
            assert len(gold_args) == 2 and all(a.startswith("<gold>/_work/part/") for a in gold_args)
    assert (root / "gold/_work/part").is_dir()


def test_score_error_masks_gold_path(root, capsys):
    """채점 오류 글(run 안에서는 run.log에 남는다)도 정답 폴더 경로를 <gold>로 쓴다."""
    from t2s.eval import gold as G

    assert G.mask(f"없음: {root / 'gold'}/x/y.sql", root) == "없음: <gold>/x/y.sql"
    assert G.mask(str(root / "gold"), root) == "<gold>"
    assert G.mask(f"{root}/golden/a", root) == f"{root}/golden/a"
    cli(capsys, "run", "--foreground", "--backend", "fake", "--limit", "5", "--no-score", "--run-id", "ge")
    (root / "gold" / M.DATASET / "answers.sql").unlink()
    code, _, err = cli(capsys, "score", "--run", "ge")
    assert code == 1 and "<gold>/" in err and str(root / "gold") not in err


def test_study_run_folder_has_no_gold_path(study_root, capsys):
    root, MS = study_root
    code, lines, _ = cli(
        capsys, "run", "--foreground", "--backend", "fake", "--questions", str(root / MS.INPUTS), "--run-id", "sg"
    )
    assert code == 0, lines
    assert cli(capsys, "leak-audit", "--run", "sg")[0] == 0
    assert _gold_path_hits(root, root / "runs/sg") == []
    assert (root / "gold/_work/sg/detail.jsonl").is_file()


def test_status_shows_codex_sandbox_hint(root, capsys, monkeypatch):
    """문항 예외가 Codex 샌드박스 오류면 status가 안내 한 줄을 더한다."""
    from t2s.cli.data_cmds import CODEX_SANDBOX_HINT
    from t2s.llm.base import LlmFailure, LlmOutcome
    from t2s.llm.fake import FakeBackend
    from t2s.runner import batch

    class Sandboxed(FakeBackend):
        def call(self, prompt, call_dir, *, name, attempt):
            msg = f"호출 1({name}): exit 1: Error: failed to initialize in-process app-server client: x"
            return LlmOutcome(response=None, failure=LlmFailure("CodexExit", msg), meta={})

    monkeypatch.setattr(batch, "backend_for", lambda llm, single=False: Sandboxed())
    cli(capsys, "run", "--foreground", "--backend", "fake", "--limit", "2", "--no-score", "--run-id", "sb")
    code, lines, _ = cli(capsys, "status", "--run-id", "sb")
    assert code == 0 and any(x.startswith("마지막 예외: ") for x in lines)
    assert lines.count(CODEX_SANDBOX_HINT) == 1 and len(lines) <= 11
