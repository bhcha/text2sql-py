"""값 검색용 문장 임베딩: Xenova/all-mpnet-base-v2 ONNX fp32를 onnxruntime + tokenizers로 직접 돌린다.

계산은 TS engines/pure-values/pack/embed.ts와 같게 한다: 한 번에 한 문장, 최대 384토큰, 평균 풀링, L2 정규화.
모델 파일은 내려받지 않는다(받기는 `t2s fetch model`). 적재 전에 다섯 파일의 sha256을 대조한다.
numpy·onnxruntime·tokenizers는 함수 안에서 import한다(모델 없이 도는 명령이 무거운 모듈을 올리지 않게).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

MODEL_ID = "Xenova/all-mpnet-base-v2"
MODEL_REVISION = "e086c5e0b3a57b0ce46dd6d9c0662948860b35f3"
MODEL_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "onnx/model.onnx")
# TS data/pure-values/<db>/index-manifest.json의 model.files_sha256과 같은 값
MODEL_SHA256 = {
    "config.json": "a783505a4e839b61700aa61d249d381d00addfb9cf3221359e2d30a6da6c6499",
    "tokenizer.json": "d00dad6f80b7eab804479a58634a58a50966366627f847e47848bac52873995d",
    "tokenizer_config.json": "3ea32220c2e21af4f0be95e2a43bdc8ac693f8c35c2255127ef680689b469461",
    "special_tokens_map.json": "15a9b70f6a5defe4feac823a82e6412e5d5bfd8eab09d8dbcf5ac241f14a7756",
    "onnx/model.onnx": "a488b290590d86da3b81c502b242343ca8312e83cfc618b2cd1ae50c09d8f669",
}
MAX_SEQ_LENGTH = 384
DIM = 768
EMBEDDER_NAME = f"{MODEL_ID}@{MODEL_REVISION[:8]}/onnx-fp32/mean-norm/max{MAX_SEQ_LENGTH}"  # TS REAL_EMBEDDER와 같은 글
TEST_EMBEDDER_NAME = "test-hash"

_hash_cache: dict[tuple[str, int, int], str] = {}


class EmbeddingModelMissing(Exception):
    pass


class EmbeddingFailed(Exception):
    pass


def _sha256_file(path: Path) -> str:
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    _hash_cache[key] = h.hexdigest()
    return _hash_cache[key]


def model_file_hashes(model_dir: Path) -> dict[str, str]:
    """다섯 파일의 sha256. 없는 파일은 빈 글."""
    out: dict[str, str] = {}
    for f in MODEL_FILES:
        p = Path(model_dir) / f
        out[f] = _sha256_file(p) if p.is_file() else ""
    return out


def check_model_files(model_dir: Path) -> dict[str, str]:
    """MODEL_SHA256과 다르면 EmbeddingModelMissing. 같으면 해시 표를 돌려준다."""
    got = model_file_hashes(model_dir)
    bad = [f for f in MODEL_FILES if got[f] != MODEL_SHA256[f]]
    if bad:
        missing = [f for f in bad if not got[f]]
        what = "없음" if len(missing) == len(bad) else "해시 다름"
        raise EmbeddingModelMissing(f"임베딩 모델 파일 {what}: {', '.join(bad)} ({model_dir})")
    return got


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> np.ndarray: ...  # float32, 768, L2 정규화

    def token_count(self, text: str) -> int: ...


def mean_pool_normalize(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """주의 마스크로 평균한 뒤 L2 정규화(0 나누기는 1e-12로 막음). hidden은 [seq, dim] 또는 [1, seq, dim]."""
    import numpy as np

    h = np.asarray(hidden, dtype=np.float64)
    m = np.asarray(mask).reshape(-1).astype(np.float64)
    if h.ndim == 3:
        h = h[0]
    keep = m != 0
    count = float(keep.sum())
    summed = h[keep].sum(axis=0) if count else np.zeros(h.shape[-1], dtype=np.float64)
    mean = summed / max(count, 1e-9)
    norm = max(float(np.sqrt((mean * mean).sum())), 1e-12)
    return (mean / norm).astype(np.float32)


class OnnxEmbedder:
    name = EMBEDDER_NAME

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
        self._tokenizer.no_padding()
        self._counter = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
        self._counter.no_truncation()
        self._counter.no_padding()
        self._session = ort.InferenceSession(
            str(Path(model_dir) / "onnx" / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def token_count(self, text: str) -> int:
        """입력 토큰 수(자르기 전, 특수 토큰 포함)."""
        return len(self._counter.encode(text).ids)

    def embed(self, text: str) -> np.ndarray:
        import numpy as np

        try:
            enc = self._tokenizer.encode(text)
            ids = np.asarray([enc.ids], dtype=np.int64)
            mask = np.asarray([enc.attention_mask], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            (hidden,) = self._session.run(["last_hidden_state"], {k: v for k, v in feed.items() if k in self._inputs})
            return mean_pool_normalize(hidden, mask)
        except Exception as e:
            raise EmbeddingFailed(f"임베딩 계산 실패: {e}") from e


def load_embedder(model_dir: Path) -> Embedder:
    """해시 대조 뒤 적재한다. 내려받지 않는다."""
    check_model_files(model_dir)
    try:
        return OnnxEmbedder(model_dir)
    except Exception as e:
        raise EmbeddingFailed(f"임베딩 모델을 적재하지 못했습니다: {e}") from e


class HashEmbedder:
    """테스트 대역(모델 없음). TS hashEmbedder와 같은 계산. 측정에 섞이지 않게 이름이 test-hash다."""

    name = TEST_EMBEDDER_NAME

    def token_count(self, text: str) -> int:
        return len(text.split()) + 2

    def embed(self, text: str) -> np.ndarray:
        import numpy as np

        v = np.zeros(DIM, dtype=np.float32)
        for word in text.lower().split():
            h = hashlib.sha256(word.encode("utf-8")).digest()
            for i in range(8):
                v[int.from_bytes(h[i * 2 : i * 2 + 2], "little") % DIM] += 1
        norm = float(np.sqrt((v * v).sum())) or 1.0
        return (v / norm).astype(np.float32)
