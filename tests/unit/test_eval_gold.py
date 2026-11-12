import pytest

from fixtures.mini_bird import make_mini_bird
from t2s.eval import gold as G


def test_load_and_read(tmp_path, shop_db):
    root = make_mini_bird(tmp_path / "r", shop_db)
    g = G.load_gold(root, "mini")
    assert g.evaluator == "bird-minidev-abd11b6" and g.questions_path.name == "answers.json"
    items = G.gold_items(g)
    lines = G.gold_lines(g)
    assert len(items) == 6 and len(lines) == 6 and lines[0].endswith("\tshop")
    assert G.work_dir(root, "x") == root / "gold" / "_work" / "x"


def test_hash_mismatch_message_has_no_content(tmp_path, shop_db):
    root = make_mini_bird(tmp_path / "r", shop_db)
    p = root / "gold/mini/answers.sql"
    p.write_text("SELECT SECRET_GOLD\tshop\n")
    with pytest.raises(G.GoldError) as e:
        G.load_gold(root, "mini")
    assert "answers.sql" in str(e.value) and "SECRET_GOLD" not in str(e.value)


def test_no_gold(tmp_path):
    with pytest.raises(G.NoGold):
        G.load_gold(tmp_path, "nothing")


def test_missing_key(tmp_path, shop_db):
    root = make_mini_bird(tmp_path / "r", shop_db)
    (root / "gold/mini/gold.toml").write_text('gold_sql = "answers.sql"\n')
    with pytest.raises(G.GoldError, match="칸이 없습니다"):
        G.load_gold(root, "mini")
