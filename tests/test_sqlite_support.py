from sqlite_support import connect_rows


def test_connect_rows_returns_mapping_rows(tmp_path) -> None:
    connection = connect_rows(tmp_path / "state.sqlite")
    try:
        connection.execute("CREATE TABLE sample (name TEXT)")
        connection.execute("INSERT INTO sample (name) VALUES (?)", ("ombre",))
        row = connection.execute("SELECT name FROM sample").fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["name"] == "ombre"
