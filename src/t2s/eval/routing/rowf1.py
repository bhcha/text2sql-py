"""실행 결과 행 단위 F1(RAGAS DataCompyScore의 행 방식과 같은 뜻의 자체 계산, 의존 없음).

행은 값의 튜플(열 순서 그대로, 열 이름 무시). 다중집합 교집합으로 겹침을 센다. 공식 EX가 정본이고 이 값은 부분 점수 참고용이다.
RAGAS 구현과 같은 수가 나온다고 보장하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable


def _f1(matched: int, pred_count: int, gold_count: int) -> dict:
    if pred_count == 0 and gold_count == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "matched": 0, "pred_count": 0, "gold_count": 0}
    precision = matched / pred_count if pred_count else 0.0
    recall = matched / gold_count if gold_count else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched": matched,
        "pred_count": pred_count,
        "gold_count": gold_count,
    }


def f1_counters(pred: Counter, gold: Counter) -> dict:
    """행(또는 행 해시) 다중집합 두 개의 F1."""
    matched = sum(min(n, gold[k]) for k, n in pred.items() if k in gold)
    return _f1(matched, sum(pred.values()), sum(gold.values()))


def row_f1(pred_rows: Iterable[tuple], gold_rows: Iterable[tuple]) -> dict:
    return f1_counters(Counter(tuple(r) for r in pred_rows), Counter(tuple(r) for r in gold_rows))


def set_f1_counters(pred: Counter, gold: Counter) -> dict:
    """검사용 집합 방식: 중복을 지운 뒤 같은 식. 이 값의 F1이 1인 것과 공식 EX 1이 같아야 한다."""
    return f1_counters(Counter(set(pred)), Counter(set(gold)))


def row_f1_set(pred_rows: Iterable[tuple], gold_rows: Iterable[tuple]) -> dict:
    return set_f1_counters(Counter(tuple(r) for r in pred_rows), Counter(tuple(r) for r in gold_rows))
