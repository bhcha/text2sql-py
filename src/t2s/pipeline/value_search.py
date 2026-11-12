"""③ 값 검색: 키워드와 비슷한 DB 값을 열마다 고른다.

출처(복사 후 수정): CHESS(https://github.com/ShayanTalaei/CHESS, 커밋 3d6e835f858d26885d21d4bc0215aeecf855efbe,
Apache License 2.0, LICENSES/Apache-2.0-CHESS.txt)
- `src/workflow/agents/information_retriever/tool_kit/retrieve_entity.py`
  `_column_value` 75-90행, `_get_similar_entities` 189-220행, `_get_to_search_values` 222-250행,
  `_get_similar_entities_via_LSH` 252-266행, `_get_similar_entities_via_edit_distance` 268-275행,
  `_get_similar_entities_via_embedding` 277-306행, 문턱 34-36행(0.3, 0.6).
- `src/database_utils/db_values/search.py` `query_lsh` 48-78행(결과를 테이블 → 열로 묶는 순서).
바꾼 것:
- `DatabaseManager().query_lsh`(MinHash LSH)를 정확한 3-gram 자카드 상위 10(prep.value_index.jaccard_top_n)으로 바꿨다.
  같은 자카드면 색인 순서다.
- OpenAI 임베딩(`text-embedding-3-small`)을 로컬 all-mpnet-base-v2(prep.embed)로 바꿨다. 한꺼번에 임베딩하던 것을 글 하나씩
  (같은 글은 캐시)로 바꿨다. 내적은 두 벡터를 float64로 바꿔 계산한다.
- 사전에 담던 묶음을 자료형으로 바꿨다. langchain·GCP 초기화를 뺐다.
- 문턱과의 거리(여유)를 기록한다(min_margin, near). 결과에는 영향이 없다.
문턱 0.3·0.6·0.9배와 정렬·묶는 순서는 원본 그대로다. TS engines/pure-fix-h/pack/values.ts와 같은 결과를 낸다.
numpy는 함수 안에서만 import한다.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from t2s.prep.value_index import ShingleIndex, jaccard_top_n

if TYPE_CHECKING:
    import numpy as np

    from t2s.prep.embed import Embedder

EDIT_DISTANCE_THRESHOLD = 0.3
EMBEDDING_SIMILARITY_THRESHOLD = 0.6
KEEP_RATIO = 0.9
NEAR_MARGIN = 1e-4
MARGIN_BUCKETS = (1e-3, 1e-4, 1e-5, 1e-6)
PerColumn = list[tuple[str, list[tuple[str, list[str]]]]]  # [(테이블, [(열, [값…])…])…], 처음 나온 순서


@dataclass(frozen=True)
class SearchPacket:
    keyword: str
    substring: str


@dataclass
class EntityPacket:
    keyword: str
    substring: str
    table: str
    column: str
    value: str
    jaccard: float
    edit: float | None = None
    embedding: float | None = None


@dataclass
class ValueSearch:
    packets: list[SearchPacket]
    jaccard_hits: int
    after_edit: int
    after_embedding: int
    per_column: PerColumn
    min_margin: float | None  # 임베딩 문턱과의 최소 거리
    near: list[dict]  # 거리가 NEAR_MARGIN보다 작은 비교들
    comparisons: int = 0  # 문턱과 비교한 수(임베딩 0.6 + 열별 0.9배)
    margin_counts: dict[str, int] = field(default_factory=dict)  # MARGIN_BUCKETS마다 그보다 가까운 비교 수


class CachedEmbedder:
    """같은 글은 한 번만 계산한다. 돌려주는 벡터는 float64다."""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.name = embedder.name
        self._cache: dict[str, np.ndarray] = {}

    def embed(self, text: str) -> np.ndarray:
        import numpy as np

        hit = self._cache.get(text)
        if hit is None:
            hit = np.asarray(self.embedder.embed(text), dtype=np.float32).astype(np.float64)
            self._cache[text] = hit
        return hit

    def size(self) -> int:
        return len(self._cache)


def dot(a: np.ndarray, b: np.ndarray) -> float:
    import numpy as np

    return float(np.dot(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)))


def column_value(text: str) -> tuple[str | None, str | None]:
    """`=`가 있으면 (앞.strip(), 뒤.strip() 또는 None)."""
    if "=" in text:
        left_equal = text.find("=")
        first_part = text[:left_equal].strip()
        second_part = text[left_equal + 1 :].strip() if len(text) > left_equal + 1 else None
        return first_part, second_part
    return None, None


def search_packets(keywords: list[str]) -> list[SearchPacket]:
    to_search_values: list[SearchPacket] = []
    for raw in keywords:
        keyword = raw.strip()
        to_search_values.append(SearchPacket(keyword, keyword))
        if " " in keyword:
            for i in range(len(keyword)):
                if keyword[i] == " ":
                    to_search_values.append(SearchPacket(keyword, keyword[:i]))
                    to_search_values.append(SearchPacket(keyword, keyword[i + 1 :]))
        _, hint_value = column_value(keyword)
        if hint_value:
            to_search_values.append(SearchPacket(keyword, hint_value))
    to_search_values.sort(key=lambda p: (p.keyword, len(p.substring), p.substring), reverse=True)
    return to_search_values


def via_jaccard(index: ShingleIndex, packets: list[SearchPacket]) -> list[EntityPacket]:
    out: list[EntityPacket] = []
    for packet in packets:
        grouped: dict[str, dict[str, list[tuple[int, float]]]] = {}
        for i, jaccard in jaccard_top_n(index, packet.substring):
            e = index.entries[i]
            grouped.setdefault(e.table, {}).setdefault(e.column, []).append((i, jaccard))
        for table, cols in grouped.items():
            for column, hits in cols.items():
                for i, jaccard in hits:
                    out.append(
                        EntityPacket(packet.keyword, packet.substring, table, column, index.entries[i].value, jaccard)
                    )
    return out


def via_edit_distance(packets: list[EntityPacket]) -> list[EntityPacket]:
    out: list[EntityPacket] = []
    for p in packets:
        edit = difflib.SequenceMatcher(None, p.substring.lower(), p.value.lower()).ratio()
        if edit >= EDIT_DISTANCE_THRESHOLD:
            p.edit = edit
            out.append(p)
    return out


def _margin(margins: list, p: EntityPacket, similarity: float, threshold: float, kind: str) -> None:
    margins.append(
        {
            "keyword": p.keyword,
            "substring": p.substring,
            "table": p.table,
            "column": p.column,
            "value": p.value,
            "similarity": similarity,
            "threshold": threshold,
            "kind": kind,
            "margin": abs(similarity - threshold),
        }
    )


def via_embedding(packets: list[EntityPacket], embedder: CachedEmbedder, margins: list) -> list[EntityPacket]:
    groups: dict[str, dict[str, list[EntityPacket]]] = {}
    for p in packets:
        groups.setdefault(p.keyword, {}).setdefault(p.substring, []).append(p)
    out: list[EntityPacket] = []
    for subs in groups.values():
        for substring, entity_packets in subs.items():
            target = embedder.embed(substring)
            for p in entity_packets:
                similarity = dot(embedder.embed(p.value), target)
                _margin(margins, p, similarity, EMBEDDING_SIMILARITY_THRESHOLD, "embedding")
                if similarity >= EMBEDDING_SIMILARITY_THRESHOLD:
                    p.embedding = similarity
                    out.append(p)
    return out


def select_per_column(packets: list[EntityPacket], margins: list) -> PerColumn:
    selected: dict[str, dict[str, list[EntityPacket]]] = {}
    for p in packets:
        selected.setdefault(p.table, {}).setdefault(p.column, []).append(p)
    out: PerColumn = []
    for table, column_values in selected.items():
        columns: list[tuple[str, list[str]]] = []
        for column, values in column_values.items():
            max_edit = max(v.edit for v in values if v.edit is not None)
            kept = [v for v in values if v.edit is not None and v.edit >= KEEP_RATIO * max_edit]
            max_embedding = max(v.embedding for v in kept if v.embedding is not None)
            threshold = KEEP_RATIO * max_embedding
            chosen = []
            for v in kept:
                assert v.embedding is not None
                if v.embedding != max_embedding:
                    _margin(margins, v, v.embedding, threshold, "keep_ratio")
                if v.embedding >= threshold:
                    chosen.append(v.value)
            columns.append((column, chosen))
        out.append((table, columns))
    return out


def similar_entities(index: ShingleIndex, keywords: list[str], embedder: CachedEmbedder) -> ValueSearch:
    margins: list[dict] = []
    packets = search_packets(keywords)
    lsh = via_jaccard(index, packets)
    edit = via_edit_distance(lsh)
    emb = via_embedding(edit, embedder, margins)
    per_column = select_per_column(emb, margins)
    values = [m["margin"] for m in margins]
    return ValueSearch(
        packets=packets,
        jaccard_hits=len(lsh),
        after_edit=len(edit),
        after_embedding=len(emb),
        per_column=per_column,
        min_margin=min(values) if values else None,
        near=[{k: v for k, v in m.items() if k != "margin"} for m in margins if m["margin"] < NEAR_MARGIN],
        comparisons=len(values),
        margin_counts={f"{b:g}": sum(1 for v in values if v < b) for b in MARGIN_BUCKETS},
    )
