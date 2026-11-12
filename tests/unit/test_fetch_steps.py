import pytest

from t2s.fetch import steps


@pytest.mark.parametrize(
    "name",
    ["/etc/passwd", "a/../b", "../a", "./a", "a/./b", "a\\b", "a\x01b", "a*b", "a?b", "a[b]", ""],
)
def test_unsafe_members(name):
    assert not steps.is_safe_member(name)


def test_safe_member():
    assert steps.is_safe_member("minidev/MINIDEV/dev_databases/a/a.sqlite")


def test_select_members():
    names = ["x/", "x/a", "x/b", "../evil", "x/extra"]
    sel = steps.select_members(names, ["x/a", "x/c"])
    assert sel.files == ["x/a"]
    assert sel.missing == ["x/c"]
    assert sel.unsafe == ["../evil"]


def test_curl_args():
    a = steps.curl_args("https://e/x.zip", "/t/x.zip.part")
    assert a[:4] == ["-fL", "-sS", "-C", "-"]
    assert a[-3:] == ["-o", "/t/x.zip.part", "https://e/x.zip"]
    assert "--speed-limit" in a and "--retry" in a


def test_hash_text():
    assert steps.short_hash("abcdef0123") == "abcdef01"
    assert steps.hash_mismatch("zip", "a" * 64, "b" * 64) == "zip sha256 불일치 (기대 aaaaaaaa / 실제 bbbbbbbb)"


def test_format_summary_limit():
    lines = steps.format_summary("h", ["a", "b"], "l")
    assert lines == ["h", "1 a", "2 b", "l"]
    steps.format_summary("h", ["s"] * 8, "l")
    with pytest.raises(ValueError):
        steps.format_summary("h", ["s"] * 9, "l")


def test_fetch_stopped_lines():
    e = steps.FetchStopped("3단계", "무엇", "조치")
    assert e.lines() == ["중단 3단계: 무엇", "조치"]
