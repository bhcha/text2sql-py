import pytest

from t2s.eval.routing import split

# DB별 문항 수(simple, moderate, challenging). BIRD Mini-Dev 500문항의 공개 문제 파일에서 센 값.
COUNTS = {
    "california_schools": (8, 17, 5),
    "card_games": (13, 33, 6),
    "codebase_community": (21, 23, 5),
    "debit_card_specializing": (14, 12, 4),
    "european_football_2": (14, 25, 12),
    "financial": (3, 22, 7),
    "formula_1": (28, 26, 12),
    "student_club": (21, 22, 5),
    "superhero": (14, 26, 12),
    "thrombosis_prediction": (7, 27, 16),
    "toxicology": (5, 17, 18),
}


def as_counts(items):
    return {db: dict(zip(split.LEVELS, v, strict=True)) for db, v in items}


def test_bird_split_matches_plan():
    counts = as_counts(COUNTS.items())
    a = split.assign(counts)
    assert a["A"] == [
        "california_schools",
        "card_games",
        "debit_card_specializing",
        "european_football_2",
        "student_club",
        "toxicology",
    ]
    assert a["B"] == ["codebase_community", "financial", "formula_1", "superhero", "thrombosis_prediction"]
    assert a["objective"] == 8
    assert a["ties"] == 2
    assert split.summarize(counts, a["A"]) == {"count": 251, "simple": 75, "moderate": 126, "challenging": 50}
    assert split.summarize(counts, a["B"]) == {"count": 249, "simple": 73, "moderate": 124, "challenging": 52}


def test_order_of_input_does_not_matter():
    a = split.assign(as_counts(COUNTS.items()))
    b = split.assign(as_counts(reversed(list(COUNTS.items()))))
    assert a == b


def test_tie_break_is_lexicographic():
    counts = as_counts([(n, (1, 0, 0)) for n in ("d", "c", "b", "a")])
    a = split.assign(counts)
    assert a["objective"] == 0
    assert a["A"] == ["a", "b"]  # (a,b), (a,c), (a,d) 가운데 사전 순 첫째
    assert a["ties"] == 3


def test_needs_two_dbs():
    with pytest.raises(ValueError):
        split.assign({})
    with pytest.raises(ValueError):
        split.assign(as_counts([("only", (1, 1, 1))]))
