"""The knowledge base: markdown files under KB_DIR. The answer assistant may
only use what's written here."""

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Keep prompts bounded; a few hundred KB of docs would be wasteful anyway
MAX_TOTAL_CHARS = 60_000


@dataclass(frozen=True)
class KBDoc:
    path: str  # relative to KB_DIR, POSIX style, e.g. "faq.md"
    content: str


def load_kb(kb_dir: str | Path) -> list[KBDoc]:
    """Load every `*.md` under `kb_dir` (recursively), in a stable order.

    Files are re-read on every call, so edits to kb/ apply without a restart.
    """
    root = Path(kb_dir)
    if not root.is_dir():
        log.warning(f"Knowledge base directory {root} doesn't exist")
        return []
    docs: list[KBDoc] = []
    total = 0
    for file in sorted(root.rglob("*.md")):
        if not file.is_file():
            continue
        content = file.read_text(encoding="utf-8").strip()
        if not content:
            continue
        if total + len(content) > MAX_TOTAL_CHARS:
            log.warning(
                f"Knowledge base exceeds {MAX_TOTAL_CHARS} characters; skipping {file}"
            )
            continue
        total += len(content)
        docs.append(KBDoc(path=file.relative_to(root).as_posix(), content=content))
    return docs


def normalise_source(source: str) -> str:
    """Accept "kb/faq.md", "./faq.md" or "faq.md" for the file "faq.md"."""
    s = source.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.removeprefix("kb/")
    return s
