"""고치기 템플릿 검사."""

import re

from t2s.pipeline.refine import TEMPLATE_FIELDS, template_sha256, template_text


def test_braces_only_for_fields():
    text = template_text()
    assert re.findall(r"\{(\w+)\}", text) == list(TEMPLATE_FIELDS)
    assert text.count("{") == text.count("}") == len(TEMPLATE_FIELDS)


def test_sql_fence_and_ending():
    text = template_text()
    assert "```sql\n{sql}\n```" in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert len(template_sha256()) == 64
