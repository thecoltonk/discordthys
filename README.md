# Cloudsail Support

A Discord support bot for **The Cloudsail Project**'s `#community-help` channel. It's a fork of [hackclub/nephthys](https://github.com/hackclub/nephthys) (MIT) with the Slack layer replaced by [discord.py](https://discordpy.readthedocs.io/).

- **Spec:** [PRODUCT.md](PRODUCT.md)
- **Port plan:** [PORTING.md](PORTING.md)
- **Judgment calls:** [DECISIONS.md](DECISIONS.md)
- **Deploying:** [DEPLOY.md](DEPLOY.md)

## How it works (Phase 1)

- Every top-level message in `#community-help` becomes a ticket. The bot opens a thread on it and posts an auto-reply with a **Mark as resolved** button.
- Anyone can help. The **first staff reply claims** the ticket.
- The original poster or any staff member can resolve. Resolving adds ✅ to the question, posts a closing message with **Re-open**, and archives the thread. If the poster replies after it's resolved, the ticket re-opens.
- Staff slash commands:
  - inside ticket threads: `/faq`, `/ip`, `/resolve`, `/reopen`, and `/escalate` (#community-help threads only);
  - anywhere: `/waiting`, `/stats`.
- Staff ticket cards in the staff channel (status, claimer, category picker), plus a daily summary.
- Optional AI, off unless `AI_API_KEY` is set:
  - auto-categorises new tickets;
  - suggests answers grounded only in [`kb/`](kb/). It runs in shadow mode by default: suggestions go to staff only, with 👍/👎 buttons.
- A sticky "how it works" message stays at the bottom of the channel.

## #tickets (Phase 2, optional)

A private, Mava-style ticket system, enabled by setting `TICKETS_CHANNEL_ID`:

- A sticky **Create Ticket** panel opens a private thread named after the player.
- The player picks a category, then a subcategory. Each pick reposts the opener so the buttons stay at the bottom.
- At a leaf category, the staff channel gets an alert card that pings the owning role (plus Staff for High priority).
- The first staff reply claims the ticket.
- **Close Ticket** (by the player or staff) asks the player for a 1–5 CSAT rating, then locks the thread. Staff can `/reopen`.
- `/escalate` moves a sensitive #community-help post into a private ticket.
- Categories live in [`copy/ticket_categories.toml`](copy/ticket_categories.toml). Setup is in DEPLOY.md.

## Changing what the bot says

Every message players and staff see lives in [`copy/copy.toml`](copy/copy.toml), with comments listing each key's `{placeholders}`. Edit it and save: changes apply within a few seconds, with no restart. Invalid edits are logged and ignored. See DEPLOY.md §6.

## Development

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL.

```bash
uv sync
cp .env.example .env   # fill in the Discord IDs and token
uv run piccolo migrations forwards nephthys
uv run nephthys
```

Checks (CI runs the same ones):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/cs_test uv run pytest
```

The tests wipe the database in `TEST_DATABASE_URL`, so point it at a throwaway database.

## License

MIT, as upstream. See [LICENSE](LICENSE).
