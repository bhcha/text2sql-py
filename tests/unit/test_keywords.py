import hashlib

import pytest

from t2s.pipeline import keywords
from t2s.pipeline.keywords import keywords_prompt, parse_keywords


def test_template_bytes_unchanged():
    data = keywords.TEMPLATE_FILE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == keywords.TEMPLATE_SHA256
    assert keywords.TEMPLATE_SHA256.startswith("4ffe8fdc") and keywords.TEMPLATE_SHA256.endswith("eba17")


def test_fill_two_placeholders():
    p = keywords_prompt("How many {x} rows?")
    assert "Question: How many {x} rows?\nHint: \n" in p
    assert "{QUESTION}" not in p and "{HINT}" not in p
    assert p.count("How many {x} rows?") == 1


def test_unknown_placeholder_is_error(monkeypatch):
    monkeypatch.setattr(keywords, "template_text", lambda: "Q {QUESTION} {OTHER}")
    with pytest.raises(KeyError):
        keywords_prompt("q")


def test_plain_list():
    r = parse_keywords('["a", "b c"]')
    assert r.ok and r.items == ["a", "b c"] and r.why is None


def test_python_fence_and_leading_space():
    r = parse_keywords('Here:\n```python\n   ["x", "y"]\n```\nthanks')
    assert r.items == ["x", "y"]
    assert parse_keywords('\n\t  ["z"]').items == ["z"]


def test_empty():
    r = parse_keywords("  \n ")
    assert not r.ok and r.why == "empty output"


def test_string_is_not_a_list():
    r = parse_keywords('"abc"')
    assert not r.ok and r.why == "not a list (str)"
    assert parse_keywords("b'x'").why == "not a list (bytes)"
    assert parse_keywords("3").why == "not a list (int)"


def test_tuple_set_dict_orders():
    assert parse_keywords('"a", "b"').items == ["a", "b"]
    assert parse_keywords('("a",)').items == ["a"]
    assert parse_keywords('{"z", "a", "m", "a"}').items == ["z", "a", "m"]
    assert parse_keywords('{"k2": 1, "k1": 2, "k2": 3}').items == ["k2", "k1"]


def test_mixed_items_kept():
    r = parse_keywords('["a", 1, None, 2.5]')
    assert r.ok and r.items == ["a", 1, None, 2.5]
    assert sum(1 for x in r.items if not isinstance(x, str)) == 3


@pytest.mark.parametrize("text", ["1+1", "[1+1]", "foo()", "[x]", "__import__('os')", "[1, 2"])
def test_expressions_fail(text):
    r = parse_keywords(text)
    assert not r.ok and r.why


def test_indentation_error():
    assert not parse_keywords('["a"]\n  ["b"]').ok


def test_comment_and_concatenation():
    assert parse_keywords('["a" "b",  # note\n "c"]').items == ["ab", "c"]
