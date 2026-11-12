from t2s.dataset.bird_minidev import discover_sources


def test_discover_in_name_order(tmp_path):
    base = tmp_path / "MINIDEV" / "dev_databases"
    for db in ("zeta", "alpha", "nodb"):
        (base / db).mkdir(parents=True)
    (base / "zeta" / "zeta.sqlite").write_bytes(b"")
    (base / "alpha" / "alpha.sqlite").write_bytes(b"")
    found = discover_sources(tmp_path / "MINIDEV", "dev_databases")
    assert list(found) == ["alpha", "zeta"]
    assert found["alpha"].path == base / "alpha" / "alpha.sqlite"
    assert found["alpha"].kind == "sqlite"


def test_missing_folder(tmp_path):
    assert discover_sources(tmp_path / "nope", "dev_databases") == {}
