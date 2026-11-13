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
from nephthys.core.copytext import placeholders_in

REPO_COPY = Path(__file__).parents[1] / "copy" / "copy.toml"


def test_shipped_copy_matches_defaults_exactly():
    assert load_copy_file(REPO_COPY) == DEFAULT_COPY
    # Regenerate with: python -m nephthys.core.copytext > copy/copy.toml
    assert REPO_COPY.read_text(encoding="utf-8") == dump_toml()


def test_every_field_is_documented_with_its_placeholders():
    text = dump_toml()
    for section_name, section in copytext._sections(DEFAULT_COPY):
        section_cls = type(section)
        assert f"[{section_name}]" in text
        for name in section_cls.model_fields:
            assert f"\n{name} = " in text
            for p in section_cls.PLACEHOLDERS.get(name, ()):
                assert "{" + p + "}" in text


def test_partial_override_keeps_other_defaults():
    copy = parse_copy('[community]\nresolved = "done! thanks {user}"\n')
    assert copy.community.resolved == "done! thanks {user}"
    assert copy.community.reopened == DEFAULT_COPY.community.reopened
    assert copy.card == DEFAULT_COPY.card


def test_empty_file_is_all_defaults():
    assert parse_copy("") == DEFAULT_COPY


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('[community]\nresolvd = "x"\n', "resolvd"),  # typo'd key
        ('[comunity]\nresolved = "x"\n', "comunity"),  # typo'd section
        ('[community]\nresolved = "by {usr}"\n', "{usr}"),  # unknown placeholder
        ('[community]\nreopen_button = "{user}"\n', "allowed: none"),  # none allowed
        ('[community]\nresolved = "by {}"\n', "positional"),
        ('[community]\nresolved = "by {user"\n', "invalid template"),
        ("[community]\nresolved = 5\n", "resolved"),
        ("not toml =", "invalid TOML"),
    ],
)
def test_invalid_copy_is_rejected_with_a_useful_message(toml: str, expected: str):
    with pytest.raises(CopyError) as exc:
        parse_copy(toml, "copy.toml")
    assert expected in str(exc.value)


def test_literal_braces_allowed():
    copy = parse_copy('[community]\nreopen_button = "{{re-open}}"\n')
    assert copy.community.reopen_button.format() == "{re-open}"


def test_placeholders_in():
    assert placeholders_in("a {x} b {y!r} {z:>3} {{lit}}") == {"x", "y", "z"}


def write(path: Path, text: str, mtime: float) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_live_reload_and_bad_edits_are_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(copytext, "RELOAD_CHECK_SECONDS", 0)
    f = tmp_path / "copy.toml"
    write(f, '[community]\nreopen_button = "Open again"\n', 1_000)
    store = CopyStore(f)
    assert store.load(required=True).community.reopen_button == "Open again"

    write(f, '[community]\nreopen_button = "Re-open please"\n', 2_000)
    assert store.get().community.reopen_button == "Re-open please"

    write(f, '[community]\nreopen_button = "{oops}"\n', 3_000)  # invalid edit
    assert store.get().community.reopen_button == "Re-open please"

    f.unlink()  # mid-save / removed: keep current
    assert store.get().community.reopen_button == "Re-open please"


def test_reload_is_rate_limited(tmp_path: Path):
    f = tmp_path / "copy.toml"
    write(f, '[community]\nreopen_button = "A"\n', 1_000)
    store = CopyStore(f)
    store.load(required=True)
    write(f, '[community]\nreopen_button = "B"\n', 2_000)
    assert store.get().community.reopen_button == "A"  # checked < 5s ago


def test_missing_file(tmp_path: Path):
    assert CopyStore(tmp_path / "nope.toml").load(required=False) == DEFAULT_COPY
    with pytest.raises(CopyError):
        CopyStore(tmp_path / "nope.toml").load(required=True)


def test_invalid_file_fails_startup(tmp_path: Path):
    f = tmp_path / "copy.toml"
    f.write_text('[card]\ntitle = "{nope}"\n')
    with pytest.raises(CopyError):
        CopyStore(f).load(required=True)


def test_main_exits_on_invalid_copy(tmp_path: Path, caplog):
    from nephthys.__main__ import load_copy_or_exit
    from tests.helpers import make_settings

    bad = tmp_path / "copy.toml"
    bad.write_text("[sticky]\nbody = '{faq}'\n")
    with pytest.raises(SystemExit) as exc:
        load_copy_or_exit(make_settings(COPY_FILE=str(bad)))
    assert exc.value.code == 2

    good = tmp_path / "good.toml"
    good.write_text("")
    with caplog.at_level("WARNING"):
        load_copy_or_exit(make_settings(COPY_FILE=str(good), STICKY_TITLE="x"))
    assert "STICKY_TITLE is no longer used" in caplog.text


def test_cli_check(tmp_path: Path, capsys):
    assert copytext._main(["x", "--check", str(REPO_COPY)]) == 0
    bad = tmp_path / "bad.toml"
    bad.write_text("[x]\n")
    assert copytext._main(["x", "--check", str(bad)]) == 1


def test_buttons_take_label_and_emoji_from_copy():
    from nephthys.core.copytext import use_copy
    from nephthys.ui.buttons import ResolveButton
    from nephthys.ui.ticket_buttons import CloseTicketButton
    from nephthys.ui.ticket_buttons import csat_view

    # Defaults: Close Ticket has no emoji (no more 🔒)
    close = CloseTicketButton(1).item
    assert (close.label, close.emoji) == ("Close Ticket", None)

    use_copy(
        parse_copy(
            "[community]\n"
            'resolve_button = "Solved"\nresolve_button_emoji = ""\n'
            "[tickets]\n"
            'close_button_emoji = "<:lock:123456789012345678>"\n'
            'csat_button_1 = "1"\ncsat_button_1_emoji = ""\n'
            'csat_button_5_emoji = "⭐"\n'
        )
    )
    resolve = ResolveButton(1).item
    assert (resolve.label, resolve.emoji) == ("Solved", None)  # "" = no emoji
    close = CloseTicketButton(1).item
    assert close.emoji is not None and close.emoji.id == 123456789012345678
    first, *_, last = [b.item for b in csat_view(1).children]  # type: ignore[attr-defined]
    assert (first.label, first.emoji) == ("1", None)
    assert str(last.emoji) == "⭐"


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ('[tickets]\nclose_button_emoji = "lock"\n', "isn't an emoji"),
        ('[community]\nreopen_button_emoji = ":lock:"\n', "isn't an emoji"),
        (
            '[tickets]\nclose_button = ""\nclose_button_emoji = ""\n',
            "both empty",
        ),
        ('[tickets]\ncsat_button_3_emoji = ""\n', "both empty"),
    ],
)
def test_bad_button_faces_are_rejected(toml: str, problem: str):
    with pytest.raises(CopyError, match=problem):
        parse_copy(toml)


def test_replaced_file_with_same_mtime_and_size_is_reloaded(tmp_path: Path, caplog):
    """Editors that save by writing a new file and renaming it over the old one
    (vim, VS Code, many others). Even with an identical timestamp and size, the
    new inode is noticed."""
    import logging

    f = tmp_path / "copy.toml"
    write(f, '[community]\nreopen_button = "Aaaa"\n', 1_000)
    store = CopyStore(f)
    store.load(required=True)

    tmp = tmp_path / "copy.toml.swp"
    write(tmp, '[community]\nreopen_button = "Bbbb"\n', 1_000)  # same mtime, size
    os.replace(tmp, f)
    with caplog.at_level(logging.INFO, logger="nephthys.core.copytext"):
        assert store.poll() == "reloaded"
    assert store.get().community.reopen_button == "Bbbb"
    assert f"Copy reloaded ({f} changed)" in caplog.text
    assert store.poll() is None  # nothing new


def test_poll_logs_reloads_and_rejections(tmp_path: Path, caplog):
    import logging

    f = tmp_path / "copy.toml"
    write(f, '[community]\nreopen_button = "A"\n', 1_000)
    store = CopyStore(f)
    store.load(required=True)
    with caplog.at_level(logging.INFO, logger="nephthys.core.copytext"):
        # poll() ignores the 5-second throttle that get() applies
        write(f, '[community]\nreopen_button = "{oops}"\n', 2_000)
        assert store.poll() == "rejected"
        write(f, '[community]\nreopen_button = "B"\n', 3_000)
        assert store.poll() == "reloaded"
    rejected = [r for r in caplog.records if "rejected" in r.getMessage()]
    assert len(rejected) == 1 and rejected[0].levelno == logging.ERROR
    assert "reopen_button" in rejected[0].getMessage()
    assert "{oops}" in rejected[0].getMessage()
    assert store.get().community.reopen_button == "B"


async def test_bot_watches_copy_while_idle(monkeypatch):
    import asyncio

    from nephthys.bot import SupportBot
    from tests.helpers import make_settings

    polls: list[int] = []
    monkeypatch.setattr(copytext, "RELOAD_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(copytext, "poll_copy", lambda: polls.append(1))
    bot = SupportBot(make_settings(), connect_db=False)
    task = asyncio.create_task(bot.watch_copy())
    await asyncio.sleep(0.1)
    task.cancel()
    assert len(polls) >= 2
    await bot.close()


def test_compose_mounts_the_copy_directory_not_single_files():
    """A single-file bind mount keeps pointing at the old inode once an editor
    replaces the file, so edits would never reach the container."""
    compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text()
    mounts = [
        line.strip().lstrip("- ").strip("\"'")
        for line in compose.splitlines()
        if "/app/copy" in line and not line.strip().startswith("#")
    ]
    assert mounts == ["./copy:/app/copy:ro"]
