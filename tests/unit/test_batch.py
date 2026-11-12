import json
from pathlib import Path

import pytest

from t2s.dataset.config import Config, DatasetSpec, SourceSpec
from t2s.dataset.questions import PublicQuestion
from t2s.llm.base import LlmFailure, LlmSettings
from t2s.llm.fake import FakeBackend, _finish
from t2s.llm.select import ResolvedLlm
from t2s.pipeline.run import PipelineSettings
from t2s.pipeline.value_search import CachedEmbedder
from t2s.prep import assets
from t2s.prep.embed import HashEmbedder
from t2s.runner import batch
from t2s.runner import records as R

FAKE_LLM = ResolvedLlm("fake", "fake-model", "none", 1, 1, None, "fake", "fake", {})


@pytest.fixture
def env(tmp_path, shop_db):
    data = tmp_path / "data"
    spec = DatasetSpec(
        name="shop",
        kind="single",
        sources={"shop": SourceSpec("shop", "sqlite", shop_db)},
        questions=None,
        archive_sha256=None,
        origin="state",
        missing=None,
    )
    config = Config(
        root=tmp_path,
        default_dataset="shop",
        default_profile=None,
        data_dir=data,
        runs_dir=tmp_path / "runs",
        embedding_model_dir=tmp_path / "model",
        datasets={"shop": spec},
    )
    from t2s.datasource.sqlite import SqliteSource

    assets.build_assets(
        SqliteSource(shop_db),
        assets.asset_dir(data, "shop", "shop"),
        dataset="shop",
        source_name="shop",
        model_hashes={},
    )
    return config, spec


def make_ctx(env, factory=None, name="r1"):
    config, spec = env
    run = config.runs_dir / name
    run.mkdir(parents=True)
    return batch.RunContext(
        config=config,
        spec=spec,
        run=run,
        llm=FAKE_LLM,
        settings=PipelineSettings(),
        embedder=CachedEmbedder(batch.LockedEmbedder(HashEmbedder())),
        model_hashes={},
        backend_factory=factory,
    )


def qs(n, text="How many Apple Juice orders are paid?"):
    return [
        PublicQuestion(i, 100 + i, "shop", f"{text} #{i}", ["simple", "moderate", "challenging"][i % 3])
        for i in range(n)
    ]


def meta(env):
    _, spec = env
    return {"databases": batch.fingerprints(spec, ["shop"]), "llm": FAKE_LLM.to_json()}


def finish_run(ctx, env, questions, workers=4):
    batch.run_pool(questions, ctx, workers, {})
    return batch.finalize(ctx, questions, meta(env), started_monotonic=0)


def test_six_generated(env):
    ctx = make_ctx(env)
    questions = qs(6)
    m = finish_run(ctx, env, questions)
    rows = R.read_jsonl(ctx.run / "records.jsonl")
    assert [r["position"] for r in rows] == list(range(6))
    assert all(r["status"] == "generated" for r in rows)
    assert m["status_counts"] == {"generated": 6, "no_sql": 0, "exception": 0, "not_started": 0}
    assert m["exec_counts"]["rows"] == 6 and m["database_unchanged"] is True and m["halted"] is None
    assert m["llm_totals"]["call_count"] == 12 and m["llm_totals"]["tool_calls"] == 0
    assert m["records"]["sha256"] == R.sha256_file(ctx.run / "records.jsonl")
    folder = R.item_dir(ctx.run, 3)
    assert {p.name for p in folder.iterdir()} == {"record.json", "trace.json", "calls"}
    assert {p.name for p in (folder / "calls").iterdir()} == {"1", "2"}
    rec = json.loads((folder / "record.json").read_text())
    assert rec["final_exec"] == {"status": "rows", "row_count": 1, "column_count": 1, "error": None, "source": "round"}
    assert rec["question"] == "How many Apple Juice orders are paid? #3" and rec["attempt"] == 1
    assert rec["llm"]["call_count"] == 2 and rec["item_dir"] == "items/0003"
    preds = json.loads((ctx.run / "predictions.json").read_text())
    assert preds["5"] == "SELECT COUNT(*) FROM product\t----- bird -----\tshop"


def test_final_exec_extra_when_last_fix_not_run(env):
    responses = {
        "generate_sql": "SELECT code FROM product WHERE 0",
        "refine_sql": "```sql\nSELECT code FROM product WHERE 1 = 0\n```",
    }
    ctx = make_ctx(env, lambda: FakeBackend(responses))
    finish_run(ctx, env, qs(1))
    rec = R.read_jsonl(ctx.run / "records.jsonl")[0]
    assert rec["refine_calls"] == 3 and rec["stop"] == "max_rounds"
    assert rec["final_exec"]["source"] == "extra" and rec["final_exec"]["status"] == "empty"


class Boom:
    id = "fake"
    settings = LlmSettings("fake", "fake-model", "none", 1)

    def call(self, prompt, call_dir, *, name, attempt):
        if "BOOM" in prompt:
            raise RuntimeError("터짐")
        return FakeBackend().call(prompt, call_dir, name=name, attempt=attempt)


def test_item_exception_does_not_block(env):
    ctx = make_ctx(env, Boom)
    questions = qs(4)
    questions[1] = PublicQuestion(1, 101, "shop", "BOOM question", "moderate")
    finish_run(ctx, env, questions)
    rows = R.read_jsonl(ctx.run / "records.jsonl")
    assert [r["status"] for r in rows] == ["generated", "exception", "generated", "generated"]
    assert rows[1]["exception"]["type"] == "RuntimeError" and rows[1]["final_exec"] is None


class ByQuestion:
    """질문 글에 든 표시로 실패 종류를 고른다."""

    id = "fake"
    settings = LlmSettings("fake", "fake-model", "none", 1)

    def call(self, prompt, call_dir, *, name, attempt):
        for mark, kind in (("ZZLIMITZZ", "CodexUsageLimit"), ("ZZEXITZZ", "CodexExit")):
            if mark in prompt:
                return _finish(self.settings, prompt, call_dir, None, LlmFailure(kind, "가짜"), name, attempt)
        return FakeBackend().call(prompt, call_dir, name=name, attempt=attempt)


def marked(marks):
    return [PublicQuestion(i, i, "shop", f"{m} question {i}", "simple") for i, m in enumerate(marks)]


def test_usage_limit_halts_after_five(env):
    ctx = make_ctx(env, ByQuestion)
    m = finish_run(ctx, env, marked(["ZZLIMITZZ"] * 8), workers=1)
    assert m["halted"] == {"reason": "usage_limit", "after": 5}
    rows = R.read_jsonl(ctx.run / "records.jsonl")
    assert [r["status"] for r in rows] == ["exception"] * 5 + ["not_started"] * 3
    assert rows[6]["status_reason"].startswith("시작하지 않음: 사용량 한도")
    assert not R.item_dir(ctx.run, 6).exists()
    assert json.loads((ctx.run / "predictions.json").read_text())["7"].startswith("SELECT 1 FROM __no_sql__")


def test_success_resets_limit_count(env):
    ctx = make_ctx(env, ByQuestion)
    m = finish_run(ctx, env, marked(["ZZLIMITZZ"] * 4 + ["ok"] + ["ZZLIMITZZ"] * 4), workers=1)
    assert m["halted"] is None and m["status_counts"]["not_started"] == 0


def test_consecutive_failures_halt(env):
    ctx = make_ctx(env, ByQuestion)
    m = finish_run(ctx, env, marked(["ZZEXITZZ"] * 22), workers=1)
    assert m["halted"] == {"reason": "consecutive_failures", "after": 20}
    assert m["status_counts"] == {"generated": 0, "no_sql": 0, "exception": 20, "not_started": 2}


def test_stop_flag(env):
    ctx = make_ctx(env)
    batch.request_stop(ctx)
    m = finish_run(ctx, env, qs(3))
    assert m["halted"]["reason"] == "stop" and m["status_counts"]["not_started"] == 3


def test_no_sql_status(env):
    ctx = make_ctx(env, lambda: FakeBackend({"generate_sql": ""}))
    finish_run(ctx, env, qs(1))
    rec = R.read_jsonl(ctx.run / "records.jsonl")[0]
    assert rec["status"] == "no_sql" and rec["sql"] is None and rec["final_exec"] is None


def test_versions_and_git(tmp_path):
    v = batch.versions()
    assert set(v) >= {"python", "sqlite", "numpy", "onnxruntime", "tokenizers", "codex", "claude"}
    assert batch.git_info(tmp_path) == {"head": None, "src_tree": None, "dirty": None}
    repo = Path(__file__).resolve().parents[2]
    g = batch.git_info(repo)
    assert len(g["head"]) == 40 and g["src_tree"] and isinstance(g["dirty"], bool)
