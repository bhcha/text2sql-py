import hashlib

import numpy as np
import pytest

from conftest import REPO
from t2s.prep import embed

MODEL_DIR = REPO / "data" / "models" / "Xenova" / "all-mpnet-base-v2"


def test_mean_pool_normalize():
    hidden = np.array([[[1.0, 0.0], [3.0, 4.0], [100.0, 100.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]])
    v = embed.mean_pool_normalize(hidden, mask)
    # 평균 (2, 2) → 정규화 (1/√2, 1/√2)
    assert v.dtype == np.float32
    assert np.allclose(v, [2**-0.5, 2**-0.5])
    zero = embed.mean_pool_normalize(np.zeros((1, 2, 3)), np.array([[1, 1]]))
    assert np.all(zero == 0)


def test_hash_embedder_deterministic():
    e = embed.HashEmbedder()
    a, b = e.embed("Hello world"), e.embed("hello  WORLD")
    assert a.shape == (embed.DIM,)
    assert np.array_equal(a, b)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-6
    assert e.name == "test-hash"
    assert e.token_count("a b c") == 5


def test_hash_embedder_matches_ts_rule():
    v = embed.HashEmbedder().embed("x")
    h = hashlib.sha256(b"x").digest()
    idx = [int.from_bytes(h[i * 2 : i * 2 + 2], "little") % embed.DIM for i in range(8)]
    want = np.zeros(embed.DIM)
    for i in idx:
        want[i] += 1
    want /= np.linalg.norm(want)
    assert np.allclose(v, want)


def test_missing_model_dir(tmp_path):
    with pytest.raises(embed.EmbeddingModelMissing):
        embed.check_model_files(tmp_path / "none")
    assert embed.model_file_hashes(tmp_path / "none") == dict.fromkeys(embed.MODEL_FILES, "")
    with pytest.raises(embed.EmbeddingModelMissing):
        embed.load_embedder(tmp_path / "none")


def test_wrong_hash_rejected(tmp_path):
    for f in embed.MODEL_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"fake")
    with pytest.raises(embed.EmbeddingModelMissing, match="해시 다름"):
        embed.check_model_files(tmp_path)


def test_embedder_name_matches_ts():
    assert embed.EMBEDDER_NAME == "Xenova/all-mpnet-base-v2@e086c5e0/onnx-fp32/mean-norm/max384"


@pytest.mark.model
@pytest.mark.skipif(not (MODEL_DIR / "onnx" / "model.onnx").is_file(), reason="임베딩 모델 파일 없음(t2s fetch model)")
def test_real_model():
    e = embed.load_embedder(MODEL_DIR)
    a = e.embed("How many schools are in Alameda County?")
    b = e.embed("How many schools are in Alameda County?")
    c = e.embed("Alameda")
    assert a.shape == (768,) and a.dtype == np.float32
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5
    assert np.array_equal(a, b)
    assert 0.0 < float(a @ c) < 1.0
    assert e.token_count("hello world") == 4
    assert e.token_count("word " * 1000) > embed.MAX_SEQ_LENGTH
