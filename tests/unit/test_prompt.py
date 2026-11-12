from t2s.pipeline.prompt import COT, TAIL, combined_prompt, comment_prompt, cot_wizard

COMMENT = (
    "-- External Knowledge: None\n"
    "-- Using valid SQLite and understading External Knowledge, answer the following questions for the tables provided"
    " above.\n"
    "-- How many?"
)


def test_comment_three_lines():
    assert comment_prompt("How many?", None) == COMMENT


def test_cot_and_tail():
    assert cot_wizard() == COT == "\nGenerate the SQL after thinking step by step: "
    assert TAIL == "\nSELECT "


def test_combined():
    p = combined_prompt("SCHEMA", "How many?", None)
    assert p == "SCHEMA\n\n" + COMMENT + COT + TAIL
    assert p.endswith("step by step: \nSELECT ")


def test_dialect_name():
    assert "-- Using valid PostgreSQL and understading" in comment_prompt("q", None, "PostgreSQL")
