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
- **Close Ticket** (by the player or staff) locks the thread and asks the player for a 1–5 CSAT rating. The thread archives when they rate, or after `CSAT_TIMEOUT_HOURS` (default 24). Staff can `/reopen` at any time.
- `/escalate` moves a sensitive #community-help post into a private ticket.
- Categories live in [`copy/ticket_categories.toml`](copy/ticket_categories.toml). Setup is in DEPLOY.md.

## Zammad two-way sync (Phase 3, optional)

Admins work #tickets tickets in Zammad; players stay in their Discord thread. Enabled by setting `ZAMMAD_URL` and `ZAMMAD_TOKEN`:

- A leaf category creates a Zammad ticket (`T-<n> <category>`) in the category's group, with one Zammad customer per Discord player.
- Every thread message becomes a public note article; attachments are copied.
- An agent's **public** article is posted into the thread under their Zammad name and avatar. Internal notes never leave Zammad.
- Close, re-open, CSAT and group changes sync both ways.
- Everything goes through an outbox with retries, so Discord never waits on Zammad and nothing is posted twice.
- `nephthys zammad-setup` configures Zammad (attribute, webhook, trigger); `nephthys zammad-retry` re-queues failed items.

Design: PRODUCT.md §2b. Setup: DEPLOY.md §7.

## Changing what the bot says

Every message players and staff see lives in [`copy/copy.toml`](copy/copy.toml), with comments listing each key's `{placeholders}`. That includes each embed's title, description, colour, footer and thumbnail (`[embeds.*]`).

- To change just a few strings, copy [`copy/copy.local.toml.example`](copy/copy.local.toml.example) to `copy/copy.local.toml` (gitignored) and uncomment the keys you want to change. They override `copy.toml` key by key.
- Saved changes apply within a few seconds, with no restart.
- Invalid edits are logged and ignored.

See DEPLOY.md §6.

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

The Zammad sync tests run against an in-process fake of Zammad's API. To also run them against a real, throwaway Zammad, set `ZAMMAD_TEST_URL`, `ZAMMAD_TEST_ADMIN_TOKEN` and `ZAMMAD_TEST_CALLBACK_HOST` (see `tests/zammad/conftest.py`).

## License

MIT, as upstream. See [LICENSE](LICENSE).
