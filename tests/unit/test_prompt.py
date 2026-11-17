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


def test_output_spec():
    from t2s.pipeline.prompt import OUTPUT_SPEC

    p = combined_prompt("SCHEMA", "How many?", None, output_spec=True)
    assert p == "SCHEMA\n\n" + COMMENT + OUTPUT_SPEC
    assert COT not in p and not p.endswith(TAIL) and p.endswith("\nSQL: ")
    assert combined_prompt("SCHEMA", "How many?", None, output_spec=False) == "SCHEMA\n\n" + COMMENT + COT + TAIL


def test_output_spec_extract():
    from t2s.pipeline.extract import extract_sql

    r = "Output columns: school name\nOne row = one school\nSQL:\n```sql\nSELECT name FROM s WHERE x = 1;\n```"
    assert extract_sql(r).sql == "SELECT name FROM s WHERE x = 1"
    r2 = "Output columns: count of schools\nOne row = the whole result\nSQL: SELECT COUNT(*) FROM s"
    assert extract_sql(r2).sql == "SELECT COUNT(*) FROM s"


def test_plan():
    import pytest

    from t2s.pipeline.extract import extract_sql
    from t2s.pipeline.prompt import PLAN

    p = combined_prompt("SCHEMA", "How many?", None, plan=True)
    assert p == "SCHEMA\n\n" + COMMENT + PLAN and p.endswith("\nSQL: ") and "Output columns" not in p
    with pytest.raises(ValueError):
        combined_prompt("SCHEMA", "q", None, plan=True, output_spec=True)
    r = "Plan: join a to b on id; filter x = 1; count rows.\nSQL:\n```sql\nSELECT COUNT(*) FROM a JOIN b ON a.id = b.id;\n```"
    assert extract_sql(r).sql == "SELECT COUNT(*) FROM a JOIN b ON a.id = b.id"


def test_bare_tail():
    import pytest

    from t2s.pipeline.extract import extract_sql

    p = combined_prompt("SCHEMA", "How many?", None, bare_tail=True)
    assert p == "SCHEMA\n\n" + COMMENT + "\nSQL: " and COT not in p
    with pytest.raises(ValueError):
        combined_prompt("SCHEMA", "q", None, bare_tail=True, plan=True)
    assert extract_sql("```sql\nSELECT 1;\n```").sql == "SELECT 1"
