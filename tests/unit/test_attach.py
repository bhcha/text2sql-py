import pytest

from t2s.pipeline.attach import (
    ProfileMissing,
    attach_profile,
    attach_values,
    column_item,
    guard_comment,
    order_columns,
    profile_line,
    types_text,
    value_list,
    values_line,
)

INDEX = [("t", [("b", ["x"]), ("a", ["y"])]), ("u", [("c", ["z"])])]


def test_guard_and_value_list():
    assert guard_comment("a*/b") == "a*\\x2fb"
    assert value_list(["a", "Men's", "x\"y'z", "e*/"]) == "['a', \"Men's\", 'x\"y\\'z', 'e*\\x2f']"


def test_values_line_form():
    assert values_line([("a", ["1", "2"]), ("b", []), ("c", ["x"])]) == (
        "/* values similar to the question: a = ['1', '2']; c = ['x'] */"
    )
    assert values_line([("a", [])]) is None


def test_order_columns_index_order_and_dedupe():
    got = order_columns("t", [("zz", ["q"]), ("a", ["y", "y", "w"]), ("b", ["x"])], INDEX)
    assert got == [("b", ["x"]), ("a", ["y", "w"]), ("zz", ["q"])]


def test_attach_values():
    out = attach_values(["t", "u", "v"], ["T", "U", "V"], [("t", [("a", ["y", "y"])]), ("w", [("c", ["1"])])], INDEX)
    assert out.blocks == ["T \n /* values similar to the question: a = ['y'] */", "U", "V"]
    assert out.text == "\n\n".join(out.blocks)
    assert out.attached == [("t", [("a", ["y"])])]
    assert out.unmatched_tables == ["w"]
    assert out.chars_added == len(out.text) - len("T\n\nU\n\nV")


def test_attach_values_nothing_found():
    out = attach_values(["t"], ["T"], [], INDEX)
    assert out.text == "T" and out.attached == [] and out.chars_added == 0


def _col(name, kind, **kw):
    base = {"name": name, "rows": 3, "nulls": 1, "distinct": 2, "types": {"text": 2}, "kind": kind}
    base.update(kw)
    return base


def test_column_item_kinds():
    assert column_item(_col("e", "empty", types={})) == "e: 2 distinct, 1 null"
    assert column_item(_col("c", "counts")) == "c: 2 distinct, 1 null"
    assert column_item(_col("v", "values", values=[{"repr": "'a'"}, {"repr": "'*/'"}])) == (
        "v: 2 distinct, 1 null, values ['a', '*\\x2f']"
    )
    assert column_item(_col("r", "range", min="1", max="2.5")) == "r: 2 distinct, 1 null, min 1, max 2.5"
    assert column_item(_col("l", "length", min_length=1, max_length=9)) == "l: 2 distinct, 1 null, length 1-9"


def test_types_text_order():
    assert types_text({"text": 3}) is None
    assert types_text({"text": 3, "integer": 2, "blob": 1, "real": 1}) == "text 3, integer 2, blob 1, real 1"
    assert types_text({"text": 0, "integer": 2}) is None


def test_profile_line_and_attach():
    profile = {"tables": [{"name": "t", "columns": [_col("a", "counts")]}, {"name": "u", "columns": []}]}
    assert profile_line([]) is None
    out = attach_profile(["t", "u"], ["T", "U"], profile)
    line = " \n /* column profile: a: 2 distinct, 1 null */"
    assert out.blocks == ["T" + line, "U"]
    assert out.line_chars == {"t": len(line), "u": 0}
    assert out.chars_added == len(line)


def test_profile_missing():
    with pytest.raises(ProfileMissing):
        attach_profile(["t"], ["T"], {"tables": []})
