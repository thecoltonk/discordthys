# Knowledge base

The AI answer assistant (`AI_ANSWER_MODE`) may **only** use the markdown files in this folder. It cites them by path (e.g. `faq.md`), and an answer that cites nothing, or cites a file that isn't here, is never posted.

- Write one topic per file, in plain language. Facts only; no "ask staff" filler.
- Edits apply immediately, because files are re-read for every question. In Docker, `kb/` is mounted from the host (see DEPLOY.md).
- Keep the total under about 60,000 characters. Files past that limit are skipped.
- This README is included too, so it doesn't contain any facts about the server.

**The files here are placeholders.** Replace everything marked `TODO` before you turn on `AI_ANSWER_MODE=public`.
