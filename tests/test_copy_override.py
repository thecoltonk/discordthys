"""copy.local.toml overrides, live reload, moved keys, and the example file."""

import os
from pathlib import Path

import pytest

from nephthys.core import copytext
from nephthys.core.copytext import CopyError
from nephthys.core.copytext import CopyStore
from nephthys.core.copytext import DEFAULT_COPY
from nephthys.core.copytext import dump_toml
from nephthys.core.copytext import load_copy_file
from nephthys.core.copytext import parse_copy

REPO = Path(__file__).parents[1]
EXAMPLE = REPO / "copy" / "copy.local.toml.example"


def write(path: Path, text: str, mtime: float) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "copy.toml"
    override = tmp_path / "copy.local.toml"
    write(
        base,
        '[community]\nreopen_button = "Base reopen"\nresolve_button = "Base resolve"\n',
        1_000,
    )
    return base, override


def test_override_wins_key_by_key(files):
    base, override = files
    write(
        override,
        '[community]\nreopen_button = "Local reopen"\n'
        '[embeds.sticky]\ncolor = "#FF0000"\n',
        1_000,
    )
    copy = CopyStore(base, override).load(required=True)
    assert copy.community.reopen_button == "Local reopen"  # override
    assert copy.community.resolve_button == "Base resolve"  # from copy.toml
    assert copy.community.resolved == DEFAULT_COPY.community.resolved  # default
    # Nested sections merge key by key too
    assert copy.embeds.sticky.color == "#FF0000"
    assert copy.embeds.sticky.title == DEFAULT_COPY.embeds.sticky.title


def test_missing_override_is_fine_unless_required(files):
    base, override = files
    assert (
        CopyStore(base, override).load(required=True).community.reopen_button
        == "Base reopen"
    )
    with pytest.raises(CopyError, match="COPY_OVERRIDE_FILE"):
        CopyStore(base, override).load(required=True, override_required=True)


def test_invalid_override_names_the_file(files):
    base, override = files
    write(override, '[community]\nreopen_buton = "typo"\n', 1_000)
    with pytest.raises(CopyError) as exc:
        CopyStore(base, override).load(required=True)
    assert str(override) in str(exc.value) and "reopen_buton" in str(exc.value)


def test_override_live_reload(files, monkeypatch):
    monkeypatch.setattr(copytext, "RELOAD_CHECK_SECONDS", 0)
    base, override = files
    store = CopyStore(base, override)
    store.load(required=True)
    assert store.get().community.reopen_button == "Base reopen"

    write(override, '[community]\nreopen_button = "Local"\n', 2_000)
    assert store.get().community.reopen_button == "Local"

    write(
        override, '[community]\nreopen_button = "{nope}"\n', 3_000
    )  # invalid: ignored
    assert store.get().community.reopen_button == "Local"

    override.unlink()  # removing the override falls back to copy.toml
    assert store.get().community.reopen_button == "Base reopen"

    write(
        base, '[community]\nreopen_button = "New base"\n', 4_000
    )  # base still reloads
    assert store.get().community.reopen_button == "New base"


@pytest.mark.parametrize(
    ("toml", "moved_to"),
    [
        ('[sticky]\ntitle = "x"\n', "[embeds.sticky] title"),
        ('[sticky]\nbody = "{faq_url}"\n', "[embeds.sticky] description"),
        ('[tickets]\npanel_title = "x"\n', "[embeds.tickets_panel] title"),
        ('[card]\nfooter = "x"\n', "[embeds.staff_card] footer"),
        ('[summary]\ntitle = "x"\n', "[embeds.daily_summary] title"),
        ('[stats]\nstats_title = "x"\n', "[embeds.stats] title"),
        ('[commands]\nwaiting_title = "x"\n', "[embeds.waiting] title"),
        ('[ai]\npublic_footer = "x"\n', "[embeds.ai_public] footer"),
    ],
)
def test_moved_keys_get_a_pointer(toml: str, moved_to: str):
    with pytest.raises(CopyError) as exc:
        parse_copy(toml)
    assert f"moved to {moved_to}" in str(exc.value)


def test_empty_legacy_sticky_header_is_ignored():
    assert parse_copy("[sticky]\n") == DEFAULT_COPY


def test_example_file_lists_every_key_commented_and_parses_to_defaults():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert text == dump_toml(commented=True)
    assert load_copy_file(EXAMPLE) == DEFAULT_COPY  # nothing uncommented
    for section_name, section in copytext._sections(DEFAULT_COPY):
        assert f"\n[{section_name}]\n" in text  # headers stay live
        for name in type(section).model_fields:
            assert f"\n# {name} = " in text, f"{section_name}.{name}"


def test_uncommenting_an_example_line_overrides_it(tmp_path: Path):
    text = EXAMPLE.read_text(encoding="utf-8").replace(
        '# resolve_button = "Mark as resolved"', 'resolve_button = "Sorted!"', 1
    )
    f = tmp_path / "copy.local.toml"
    f.write_text(text, encoding="utf-8")
    copy = load_copy_file(f)
    assert copy.community.resolve_button == "Sorted!"
    assert copy.community.reopen_button == DEFAULT_COPY.community.reopen_button


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('[embeds.sticky]\ncolor = "blue"\n', "hex colour"),
        ('[embeds.staff_card]\ncolor_waiting = "#12345"\n', "hex colour"),
        ('[embeds.stats]\nthumbnail_url = "ftp://x/y.png"\n', "thumbnail_url"),
        ('[embeds.waiting]\ndescription = "{tickets}"\n', "{lines}"),
        ('[embeds.ticket_opener]\nenabled = "yes please"\n', "enabled"),
        ('[embeds.nope]\ntitle = "x"\n', "nope"),
    ],
)
def test_invalid_embed_styles(toml: str, expected: str):
    with pytest.raises(CopyError) as exc:
        parse_copy(toml)
    assert expected in str(exc.value)


def test_hex_colours_with_or_without_hash():
    copy = parse_copy('[embeds.sticky]\ncolor = "ff8800"\n[embeds.stats]\ncolor = ""\n')
    assert copytext.colour_value(copy.embeds.sticky.color) == 0xFF8800
    assert copytext.colour_value(copy.embeds.stats.color) is None


def test_cli_check_and_example(tmp_path: Path, capsys):
    local = tmp_path / "copy.local.toml"
    local.write_text('[community]\nreopen_button = "x"\n')
    assert (
        copytext._main(["x", "--check", str(REPO / "copy" / "copy.toml"), str(local)])
        == 0
    )
    local.write_text('[community]\nreopen_button = "{x}"\n')
    assert (
        copytext._main(["x", "--check", str(REPO / "copy" / "copy.toml"), str(local)])
        == 1
    )
    capsys.readouterr()
    assert copytext._main(["x", "--example"]) == 0
    assert capsys.readouterr().out == dump_toml(commented=True)


def test_main_exits_on_invalid_override(tmp_path: Path):
    from nephthys.__main__ import load_copy_or_exit
    from tests.helpers import make_settings

    bad = tmp_path / "copy.local.toml"
    bad.write_text('[embeds.sticky]\ncolor = "red"\n')
    with pytest.raises(SystemExit) as exc:
        load_copy_or_exit(make_settings(COPY_OVERRIDE_FILE=str(bad)))
    assert exc.value.code == 2
    with pytest.raises(SystemExit):  # explicitly set but missing
        load_copy_or_exit(make_settings(COPY_OVERRIDE_FILE=str(tmp_path / "nope.toml")))
    copytext.use_copy(DEFAULT_COPY)


def test_override_settings():
    from tests.helpers import make_settings

    s = make_settings()
    assert (
        s.copy_override_file == "copy/copy.local.toml"
        and not s.copy_override_file_explicit
    )
    s = make_settings(COPY_OVERRIDE_FILE="/etc/cs/local.toml")
    assert (
        s.copy_override_file == "/etc/cs/local.toml" and s.copy_override_file_explicit
    )


def test_override_file_is_gitignored_and_kept_out_of_the_image():
    assert "copy/copy.local.toml" in (REPO / ".gitignore").read_text().splitlines()
    assert "copy/copy.local.toml" in (REPO / ".dockerignore").read_text().splitlines()
