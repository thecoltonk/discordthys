# FAQ entries

Each `*.md` file in this folder is one FAQ entry: a question the bot may answer on its own, and the exact text it answers with. This README is the only file that isn't an entry.

```markdown
---
id: server-ip
description: How to join or connect to the Minecraft server - the server address (IP), port, supported game versions, Java or Bedrock.
---
The server address is `play.example.com` (Java Edition).

Add it under **Multiplayer → Add Server** and you're in!
```

| Part | What it's for |
|---|---|
| `id` | The entry's name: 1–40 characters of `a-z`, `0-9`, `-`, `_`. It shows up in logs and in the staff channel. `other` is reserved (it means "no match"). |
| `description` | What the entry covers, in a sentence (at most 500 characters). **Only the matcher reads this.** Say which questions it answers, including other ways people phrase them. Players never see it. |
| `draft: true` | Optional. The entry is loaded and checked but never matched. Remove the line when the answer is ready. |
| everything after the second `---` | **The answer, posted to players word for word** (at most 1800 characters). Discord markdown works. Nothing is added to it in #community-help; in other channels a short pointer to #community-help follows it. |

## How it's used

The bot asks Jev (a decision model) one thing: *which of these descriptions fits this message, or none?* Jev answers with an `id` and a confidence. It never writes text, and nothing it returns is shown to anyone. If it picks an entry confidently enough, the bot posts that entry's answer exactly as written here; otherwise it says nothing.

So an answer can only ever be something written in this folder.

## Editing

- Edits apply by themselves within seconds; no restart. In Docker, `kb/` is mounted from the host (see DEPLOY.md).
- A file that isn't a valid entry is skipped and reported in the bot's log (`FAQ entry skipped: <file>: <reason>`). The other entries keep working.
- Check the folder without starting the bot: `python -m nephthys.ai.faq kb` (in Docker: `docker compose exec bot python -m nephthys.ai.faq /app/kb`).
- At most 50 entries. Keep each one to a single, specific question: a narrow description matches more reliably than a broad one.

## The starter entry

`server-ip.md` ships as a **draft with a placeholder address**. Put the real address in, check the wording, then delete the `draft: true` line.
