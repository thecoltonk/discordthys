"""The FAQ: pre-written answers in `kb/`, one file per entry.

    ---
    id: server-ip
    description: How to join or connect: the server address (IP), port, version.
    ---
    The answer, posted to players exactly as written here.

- `id` names the entry (a-z, 0-9, `-`, `_`). It's what the model picks.
- `description` tells the model what the entry covers. Players never see it.
- Everything after the second `---` is the answer, posted word for word.
- `draft: true` (optional) keeps an unfinished entry out of matching.

The bot never edits, trims into, or adds to an answer, and nothing the model
returns is ever shown: it only names an `id`.

Files are re-read when they change, so edits apply without a restart. A file
that isn't a valid entry is skipped (and logged once per change); the other
entries keep working.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# The option that means "none of the entries fits". Not usable as an entry id.
NO_MATCH = "other"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DESCRIPTION_MAX = 500
# Discord allows 2000 characters per message; the rest is headroom for the
# pointer to #community-help that replies in other channels add.
ANSWER_MAX = 1800
# Discord allows 25 options in most places and prompts should stay small
MAX_ENTRIES = 50
SKIPPED_FILES = {"readme.md"}
KEYS = {"id", "description", "draft"}


class FAQError(ValueError):
    pass


@dataclass(frozen=True)
class FAQEntry:
    id: str
    description: str
    answer: str
    path: str
    draft: bool = False


def parse_entry(text: str, path: str = "entry.md") -> FAQEntry:
    """Parse one entry file. Raises `FAQError` saying what's wrong."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise FAQError("must start with a `---` line, then `id:` and `description:`")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FAQError("the header isn't closed with a second `---` line") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep or key not in KEYS:
            raise FAQError(
                f"unexpected header line {line.strip()!r} (allowed: "
                f"{', '.join(sorted(KEYS))})"
            )
        if key in fields:
            raise FAQError(f"`{key}` is given twice")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        fields[key] = value

    entry_id = fields.get("id", "")
    if not ID_PATTERN.match(entry_id):
        raise FAQError(
            f"id {entry_id!r} must be 1-40 characters of a-z, 0-9, - or _ "
            "(starting with a letter or digit)"
        )
    if entry_id == NO_MATCH:
        raise FAQError(f'the id "{NO_MATCH}" is reserved (it means "no match")')
    description = fields.get("description", "")
    if not description:
        raise FAQError("description is required: it tells the matcher what this covers")
    if len(description) > DESCRIPTION_MAX:
        raise FAQError(f"description is over {DESCRIPTION_MAX} characters")
    draft_raw = fields.get("draft", "false").lower()
    if draft_raw not in {"true", "false"}:
        raise FAQError("draft must be true or false")

    # Word for word: only the blank lines around the answer are dropped
    answer = "\n".join(lines[end + 1 :]).strip("\n")
    if not answer.strip():
        raise FAQError("the answer (everything after the header) is empty")
    if len(answer) > ANSWER_MAX:
        raise FAQError(
            f"the answer is {len(answer)} characters; the limit is {ANSWER_MAX}"
        )
    return FAQEntry(
        id=entry_id,
        description=description,
        answer=answer,
        path=path,
        draft=draft_raw == "true",
    )


def load_entries(kb_dir: str | Path) -> tuple[list[FAQEntry], list[str]]:
    """Read every `*.md` under `kb_dir`. Returns (valid entries, problems).
    Drafts are included in the entries; `FAQStore.entries()` leaves them out."""
    root = Path(kb_dir)
    if not root.is_dir():
        return [], [f"{root} doesn't exist"]
    entries: list[FAQEntry] = []
    problems: list[str] = []
    seen: dict[str, str] = {}
    for file in sorted(root.rglob("*.md")):
        if not file.is_file() or file.name.lower() in SKIPPED_FILES:
            continue
        rel = file.relative_to(root).as_posix()
        try:
            entry = parse_entry(file.read_text(encoding="utf-8"), rel)
        except (FAQError, OSError, UnicodeDecodeError) as e:
            problems.append(f"{rel}: {e}")
            continue
        if entry.id in seen:
            problems.append(
                f"{rel}: id {entry.id!r} is already used by {seen[entry.id]}"
            )
            continue
        if len(entries) >= MAX_ENTRIES:
            problems.append(f"{rel}: skipped, the limit is {MAX_ENTRIES} entries")
            continue
        seen[entry.id] = rel
        entries.append(entry)
    return entries, problems


class FAQStore:
    """The live FAQ. `entries()` is cheap: files are only re-read when one of
    them was added, removed or changed."""

    def __init__(self, kb_dir: str | Path):
        self.root = Path(kb_dir)
        self._signature: tuple[tuple[str, int, int, int], ...] | None = None
        self._entries: list[FAQEntry] = []
        self.problems: list[str] = []

    def signature(self) -> tuple[tuple[str, int, int, int], ...]:
        if not self.root.is_dir():
            return ()
        out = []
        for file in sorted(self.root.rglob("*.md")):
            try:
                st = file.stat()
            except OSError:
                continue
            out.append((str(file), st.st_mtime_ns, st.st_size, st.st_ino))
        return tuple(out)

    def reload_if_changed(self) -> bool:
        signature = self.signature()
        if signature == self._signature:
            return False
        first = self._signature is None
        self._signature = signature
        self._entries, self.problems = load_entries(self.root)
        live = [e.id for e in self._entries if not e.draft]
        drafts = [e.id for e in self._entries if e.draft]
        log.info(
            f"FAQ {'loaded' if first else 'reloaded'} from {self.root}: "
            f"{len(live)} entries ({', '.join(live) or 'none'})"
            + (f"; drafts not matched: {', '.join(drafts)}" if drafts else "")
        )
        for problem in self.problems:
            log.error(f"FAQ entry skipped: {problem}")
        return True

    def entries(self) -> list[FAQEntry]:
        """The entries players can be given (drafts left out)."""
        self.reload_if_changed()
        return [e for e in self._entries if not e.draft]

    def get(self, entry_id: str) -> FAQEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)


def _main(argv: list[str]) -> int:
    """`python -m nephthys.ai.faq [dir]`: check the entries without the bot."""
    entries, problems = load_entries(argv[1] if len(argv) > 1 else "kb")
    for e in entries:
        print(f"ok     {e.path}: {e.id}{' (draft, not matched)' if e.draft else ''}")
    for p in problems:
        print(f"ERROR  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
