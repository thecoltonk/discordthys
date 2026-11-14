from pathlib import Path

import pytest

from nephthys.core import ticket_categories as tc
from nephthys.core.ticket_categories import Category
from nephthys.core.ticket_categories import DEFAULT_TICKET_CATEGORIES
from nephthys.core.ticket_categories import dump_toml
from nephthys.core.ticket_categories import parse_ticket_categories
from nephthys.core.ticket_categories import TicketCategoriesError

REPO_FILE = Path(__file__).parents[1] / "copy" / "ticket_categories.toml"


def test_defaults_match_product_md():
    cats = DEFAULT_TICKET_CATEGORIES
    assert [c.label for c in cats.categories] == [
        "Server Issue",
        "Store",
        "Bans & Appeals",
        "Reports",
        "Other",
    ]
    server = cats.get("server_issue")
    assert server and not server.is_leaf
    assert [(s.label, s.priority) for s in server.subcategories] == [
        ("Urgent", "high"),
        ("Bug Report", "medium"),
        ("Other Technical", "low"),
    ]
    assert {c.key: c.priority for c in cats.categories if c.is_leaf} == {
        "store": "medium",
        "bans_appeals": "medium",
        "reports": "medium",
        "other": "low",
    }
    assert "role" not in Category.model_fields  # nothing is pinged any more
    assert (cats.get("bans_appeals") or server).blurb == (
        "Please tell us your **IGN** and **what happened**. If you're banned "
        "from Discord, use https://appeals.cloudsail.uk instead."
    )
    assert cats.label_for("store") == "🛒 Store"
    assert cats.label_for("gone") == "gone"


def test_shipped_file_matches_defaults():
    assert REPO_FILE.read_text(encoding="utf-8") == dump_toml()
    assert parse_ticket_categories(REPO_FILE.read_text()) == DEFAULT_TICKET_CATEGORIES


BASE = '[[categories]]\nkey = "a"\nlabel = "A"\nblurb = "b"\n'


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ("", "categories"),
        (BASE + BASE, "duplicate category keys"),
        ('[[categories]]\nkey = "Bad Key"\nlabel = "A"\nblurb = "b"\n', "must match"),
        (BASE + 'priority = "urgent"\n', "priority"),
        (BASE + 'colour = "red"\n', "colour"),
        (
            BASE + "[[categories.subcategories]]\nkey='s'\nlabel='S'\nblurb='b'\n"
            "[[categories.subcategories]]\nkey='s'\nlabel='S'\nblurb='b'\n",
            "duplicate subcategory",
        ),
        ("".join(BASE.replace('"a"', f'"c{i}"') for i in range(21)), "at most 20"),
        ("not = toml =", "invalid TOML"),
    ],
)
def test_invalid_files(toml: str, expected: str):
    with pytest.raises(TicketCategoriesError) as exc:
        parse_ticket_categories(toml)
    assert expected in str(exc.value)


def test_leftover_role_is_ignored_and_configure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """`role` named who an alert pinged. A file written before pings were
    removed still loads (whatever the value); the key is dropped with a warning."""
    f = tmp_path / "cats.toml"
    f.write_text(BASE + 'role = "123456"\n' + BASE.replace('"a"', '"b"') + "role = 5\n")
    with caplog.at_level("WARNING"):
        cats = tc.configure_ticket_categories(f, required=True)
    assert [c.key for c in cats.categories] == ["a", "b"]
    warned = [
        r.getMessage() for r in caplog.records if "ignoring `role`" in r.getMessage()
    ]
    assert len(warned) == 2
    assert tc.get_ticket_categories() is cats
    assert (
        tc.configure_ticket_categories(tmp_path / "nope", required=False)
        == DEFAULT_TICKET_CATEGORIES
    )
    with pytest.raises(TicketCategoriesError):
        tc.configure_ticket_categories(tmp_path / "nope", required=True)
    tc.use_ticket_categories(DEFAULT_TICKET_CATEGORIES)


def test_cli_check(tmp_path: Path):
    assert tc._main(["x", "--check", str(REPO_FILE)]) == 0
    bad = tmp_path / "bad.toml"
    bad.write_text("[[categories]]\n")
    assert tc._main(["x", "--check", str(bad)]) == 1
