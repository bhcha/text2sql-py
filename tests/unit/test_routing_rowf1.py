import pytest

from t2s.eval.routing.rowf1 import row_f1, row_f1_set


def test_same_and_order_and_disjoint():
    assert row_f1([(1, "a"), (2, "b")], [(1, "a"), (2, "b")])["f1"] == 1.0
    assert row_f1([(2, "b"), (1, "a")], [(1, "a"), (2, "b")])["f1"] == 1.0
    assert row_f1([(3,)], [(1,), (2,)])["f1"] == 0.0


def test_duplicates_multiset():
    r = row_f1([(1,), (1,), (2,)], [(1,), (2,), (2,)])
    assert r["matched"] == 2
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["recall"] == pytest.approx(2 / 3)


def test_partial_overlap():
    r = row_f1([(1,), (2,), (3,), (4,)], [(1,), (2,)])
    assert (r["precision"], r["recall"]) == (0.5, 1.0)
    assert r["f1"] == pytest.approx(2 / 3)


def test_empty_edges():
    assert row_f1([], [])["f1"] == 1.0
    assert row_f1([], [(1,)])["f1"] == 0.0
    assert row_f1([(1,)], [])["f1"] == 0.0


def test_none_and_int_float_same_key():
    assert row_f1([(None, 1)], [(None, 1.0)])["f1"] == 1.0


def test_set_mode_ignores_duplicates():
    p, g = [(1,), (1,), (2,)], [(1,), (2,)]
    assert row_f1_set(p, g)["f1"] == 1.0
    assert row_f1(p, g)["f1"] < 1.0
