from pathlib import Path

from nephthys.ai import kb
from nephthys.ai.kb import load_kb
from nephthys.ai.kb import normalise_source


def test_load_kb_recursive_sorted_and_skips_empty(tmp_path: Path):
    (tmp_path / "b.md").write_text("B")
    (tmp_path / "a.md").write_text("A")
    (tmp_path / "empty.md").write_text("  \n")
    (tmp_path / "notes.txt").write_text("not markdown")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("C")
    docs = load_kb(tmp_path)
    assert [d.path for d in docs] == ["a.md", "b.md", "sub/c.md"]


def test_load_kb_missing_dir(tmp_path: Path):
    assert load_kb(tmp_path / "nope") == []


def test_load_kb_size_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(kb, "MAX_TOTAL_CHARS", 10)
    (tmp_path / "a.md").write_text("12345678")
    (tmp_path / "b.md").write_text("12345678")
    assert [d.path for d in load_kb(tmp_path)] == ["a.md"]


def test_repo_kb_placeholders_exist():
    paths = {d.path for d in load_kb(Path(__file__).parents[2] / "kb")}
    assert {"faq.md", "ip.md", "rules.md"} <= paths


def test_normalise_source():
    assert normalise_source("kb/faq.md") == "faq.md"
    assert normalise_source("./faq.md") == "faq.md"
    assert normalise_source(" sub\\x.md ") == "sub/x.md"
