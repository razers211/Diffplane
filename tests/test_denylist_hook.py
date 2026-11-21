from tools import check_denylist


def test_reports_file_and_line_never_the_value(tmp_path, monkeypatch, capsys):
    deny = tmp_path / "deny.txt"
    deny.write_text("# comment\nSecretSite\n")
    clean, dirty = tmp_path / "a.txt", tmp_path / "b.txt"
    clean.write_text("nothing here\n")
    dirty.write_text("ok\nthe secretsite fabric\n")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("DIFFPLANE_DENYLIST", str(deny))
    assert check_denylist.main([str(clean)]) == 0
    assert check_denylist.main([str(clean), str(dirty)]) == 1
    out = capsys.readouterr().out
    assert f"{dirty}:2:" in out and "secretsite" not in out.lower()


def test_skipped_without_a_list_or_in_ci(monkeypatch, tmp_path):
    dirty = tmp_path / "b.txt"
    dirty.write_text("anything\n")
    monkeypatch.delenv("DIFFPLANE_DENYLIST", raising=False)
    assert check_denylist.main([str(dirty)]) == 0
    monkeypatch.setenv("DIFFPLANE_DENYLIST", str(dirty))
    monkeypatch.setenv("CI", "true")
    assert check_denylist.main([str(dirty)]) == 0
