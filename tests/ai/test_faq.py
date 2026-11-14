"""FAQ entries: the file format, validation, and live reload."""

import os
from pathlib import Path

import pytest

from nephthys.ai import faq
from nephthys.ai.faq import FAQError
from nephthys.ai.faq import FAQStore
from nephthys.ai.faq import load_entries
from nephthys.ai.faq import parse_entry

REPO_KB = Path(__file__).parents[2] / "kb"

ENTRY = """---
id: server-ip
description: How to join: the server address (IP) and versions.
---
The address is `play.example.com`.

  Indented line, **bold**, a link: https://example.com/x?y=1
Trailing spaces stay.  
"""


def write(path: Path, text: str, mtime: int | None = None) -> None:
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def entry(entry_id: str, answer: str = "answer", extra: str = "") -> str:
    return f"---\nid: {entry_id}\ndescription: about {entry_id}\n{extra}---\n{answer}\n"


def test_entry_has_an_id_a_description_and_a_verbatim_answer():
    e = parse_entry(ENTRY, "server-ip.md")
    assert e.id == "server-ip"
    # A colon inside the description is fine
    assert e.description == "How to join: the server address (IP) and versions."
    assert e.path == "server-ip.md" and e.draft is False
    # Word for word: inner blank lines, indentation, markdown, links and
    # trailing spaces are untouched; only the blank lines around it go
    assert e.answer == (
        "The address is `play.example.com`.\n"
        "\n"
        "  Indented line, **bold**, a link: https://example.com/x?y=1\n"
        "Trailing spaces stay.  "
    )


def test_header_details():
    e = parse_entry(
        '---\n# a comment\n\nID: a_1\nDescription: "quoted text"\ndraft: TRUE\n---\nx\n'
    )
    assert (e.id, e.description, e.draft) == ("a_1", "quoted text", True)
    # Windows line endings; a later `---` belongs to the answer
    e = parse_entry(
        "---\r\nid: a\r\ndescription: d\r\n---\r\nline 1\r\n---\r\nline 2\r\n"
    )
    assert e.answer == "line 1\n---\nline 2"
    # Placeholders are not filled in: braces are just text
    assert parse_entry(entry("a", "use {channel} and {answer}")).answer == (
        "use {channel} and {answer}"
    )


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("just some markdown\n", "must start with"),
        ("# Title\n---\nid: a\n---\nx", "must start with"),
        ("---\nid: a\ndescription: d\nanswer without closing", "isn't closed"),
        ("---\ndescription: d\n---\nx", "id ''"),
        ("---\nid: Server IP\ndescription: d\n---\nx", "id 'Server IP'"),
        ("---\nid: -a\ndescription: d\n---\nx", "id '-a'"),
        (f"---\nid: {'a' * 41}\ndescription: d\n---\nx", "must be 1-40"),
        ("---\nid: other\ndescription: d\n---\nx", "reserved"),
        ("---\nid: a\n---\nx", "description is required"),
        ("---\nid: a\ndescription:   \n---\nx", "description is required"),
        (f"---\nid: a\ndescription: {'d' * 501}\n---\nx", "over 500"),
        ("---\nid: a\ndescription: d\n---\n \n\n", "is empty"),
        (f"---\nid: a\ndescription: d\n---\n{'x' * 1801}", "limit is 1800"),
        ("---\nid: a\ndescription: d\ntitle: t\n---\nx", "unexpected header line"),
        ("---\nid: a\nid: b\ndescription: d\n---\nx", "given twice"),
        ("---\nid: a\ndescription: d\ndraft: maybe\n---\nx", "draft must be"),
        ("---\nid a\ndescription: d\n---\nx", "unexpected header line"),
    ],
)
def test_invalid_entries_say_what_is_wrong(text: str, problem: str):
    with pytest.raises(FAQError, match=problem):
        parse_entry(text)


def test_limits_are_inclusive():
    assert parse_entry(f"---\nid: {'a' * 40}\ndescription: {'d' * 500}\n---\nx")
    assert len(parse_entry(entry("a", "x" * 1800)).answer) == 1800


def test_load_skips_bad_files_and_keeps_the_rest(tmp_path: Path):
    write(tmp_path / "a.md", entry("alpha"))
    write(tmp_path / "b.md", "not an entry")
    write(tmp_path / "c.md", entry("alpha", "a second alpha"))  # duplicate id
    write(tmp_path / "README.md", "# docs, not an entry")
    write(tmp_path / "notes.txt", "ignored: not markdown")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub" / "d.md", entry("delta", extra="draft: true\n"))
    entries, problems = load_entries(tmp_path)
    assert [(e.id, e.path, e.draft) for e in entries] == [
        ("alpha", "a.md", False),
        ("delta", "sub/d.md", True),
    ]
    assert len(problems) == 2
    assert problems[0].startswith("b.md: must start with")
    assert problems[1] == "c.md: id 'alpha' is already used by a.md"
    assert load_entries(tmp_path / "missing") == (
        [],
        [f"{tmp_path / 'missing'} doesn't exist"],
    )


def test_at_most_fifty_entries(tmp_path: Path):
    for i in range(faq.MAX_ENTRIES + 2):
        write(tmp_path / f"e{i:03}.md", entry(f"e{i:03}"))
    entries, problems = load_entries(tmp_path)
    assert len(entries) == faq.MAX_ENTRIES and len(problems) == 2


def test_store_leaves_drafts_out_and_reloads_on_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    write(tmp_path / "a.md", entry("alpha", "first"), 1_000)
    write(tmp_path / "wip.md", entry("wip", extra="draft: true\n"), 1_000)
    store = FAQStore(tmp_path)
    assert [e.id for e in store.entries()] == ["alpha"]  # the draft isn't offered
    assert store.get("wip") is None
    assert store.get("alpha").answer == "first"  # type: ignore[union-attr]
    assert store.reload_if_changed() is False  # nothing changed: no re-read

    # An edit applies on the next use, with no restart
    write(tmp_path / "a.md", entry("alpha", "second"), 2_000)
    assert store.get("alpha").answer == "second"  # type: ignore[union-attr]
    # A new file, and the draft going live
    write(tmp_path / "b.md", entry("beta"), 3_000)
    write(tmp_path / "wip.md", entry("wip"), 3_000)
    assert [e.id for e in store.entries()] == ["alpha", "beta", "wip"]

    # A bad edit takes that one entry out (logged); the others keep working
    with caplog.at_level("ERROR"):
        write(tmp_path / "a.md", "oops", 4_000)
        assert [e.id for e in store.entries()] == ["beta", "wip"]
    assert any("a.md: must start with" in r.getMessage() for r in caplog.records)
    assert store.problems
    # ...and fixing it brings it back; deleting a file removes its entry
    write(tmp_path / "a.md", entry("alpha", "third"), 5_000)
    (tmp_path / "b.md").unlink()
    assert [e.id for e in store.entries()] == ["alpha", "wip"]
    assert not store.problems


def test_store_notices_an_editor_style_save(tmp_path: Path):
    """Write a temp file and rename it over the original: same time and size,
    new inode (how most editors save)."""
    f = tmp_path / "a.md"
    write(f, entry("alpha", "AAAA"), 1_000)
    store = FAQStore(tmp_path)
    assert store.get("alpha").answer == "AAAA"  # type: ignore[union-attr]
    tmp = tmp_path / "a.md.tmp"
    write(tmp, entry("alpha", "BBBB"), 1_000)
    os.replace(tmp, f)
    assert store.get("alpha").answer == "BBBB"  # type: ignore[union-attr]


def test_store_with_no_directory_has_no_entries(tmp_path: Path):
    store = FAQStore(tmp_path / "nope")
    assert store.entries() == [] and store.get("x") is None


def test_shipped_kb_is_valid_and_the_starter_is_a_placeholder_draft():
    entries, problems = load_entries(REPO_KB)
    assert problems == []
    [starter] = [e for e in entries if e.id == "server-ip"]
    assert starter.path == "server-ip.md"
    # A placeholder address must never reach players: the entry ships as a
    # draft, so it isn't matched until someone fills it in
    assert starter.draft is True
    assert "play.example.invalid" in starter.answer
    assert FAQStore(REPO_KB).entries() == []
    # The old generative knowledge-base documents are gone
    assert {p.name for p in REPO_KB.glob("*.md")} == {"README.md", "server-ip.md"}


def test_cli_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    write(tmp_path / "a.md", entry("alpha"))
    assert faq._main(["x", str(tmp_path)]) == 0
    write(tmp_path / "b.md", "bad")
    assert faq._main(["x", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "ok     a.md: alpha" in out and "ERROR  b.md:" in out
