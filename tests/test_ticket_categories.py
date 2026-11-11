from pathlib import Path

import pytest

from nephthys.core import ticket_categories as tc
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
    assert {c.key: (c.role, c.priority) for c in cats.categories if c.is_leaf} == {
        "store": ("store", "medium"),
        "bans_appeals": ("mod", "medium"),
        "reports": ("mod", "medium"),
        "other": ("staff", "low"),
    }
    assert "appeals.cloudsail.uk" in (cats.get("bans_appeals") or server).blurb
    assert server.role == "staff"
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
        (BASE + 'role = "admins"\n', "role must be"),
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


def test_numeric_role_and_configure(tmp_path: Path):
    f = tmp_path / "cats.toml"
    f.write_text(BASE + 'role = "123456"\n')
    cats = tc.configure_ticket_categories(f, required=True)
    assert cats.categories[0].role == "123456"
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
