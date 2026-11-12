# PORTING.md: Nephthys (Slack) → Cloudsail Support (Discord)

**Status:** Revised after review, Nov 2026. This version reflects the decisions in the "Review outcome" section below and is what's built on this branch.
**Spec:** [PRODUCT.md](PRODUCT.md)
**Judgment calls made during the build:** [DECISIONS.md](DECISIONS.md)
**Scope:** Phase 1, **#community-help only**. #tickets (v2) and the Zammad mirror (v3) show up only where they affect Phase 1 decisions.

---

## Review outcome (supersedes the first draft)

| Topic | Decision |
|---|---|
| Channel type | **#community-help is a text channel, Nest-style**, not a forum. Every top-level, non-bot message makes the bot create a public thread on it and post the auto-reply with **[Mark as resolved]**. Bot messages and in-thread messages never create tickets. |
| Resolved state | ✅ reaction on the parent message, plus a closing message with **[Re-open]**, plus the thread is archived. Re-open removes ✅, unarchives, and posts a re-opened note. |
| Sticky | A "how it works" message sits at the bottom of #community-help from v1. It's deleted and reposted after new top-level messages, with a debounce. It's a bot message, so it never spawns a thread. |
| Claim | **First staff reply.** Later staff replies don't steal it. |
| Credit | **Goes to the claimer.** If the OP self-resolves and no staff ever replied, nobody gets credit. |
| Staff feed | One line per new ticket in the staff channel. |
| Poster replies after resolve | **Auto-reopen.** |
| `?faq` | Posts the link only. It doesn't resolve. |
| `?ticket` | Deferred to v2 (it needs #tickets). |
| Package name | Kept as `nephthys`. |
| Column naming | snake_case in the fresh init migration. |

### Follow-up changes (after #1)

These supersede the matching rows above and the macro / staff-feed sections below:

| Topic | Now |
|---|---|
| Staff tools | `?macros` are gone. Staff use the `/faq`, `/ip`, `/resolve` and `/reopen` slash commands, which only work inside ticket threads. `/faq` and `/ip` **don't** count as a staff reply. |
| Staff feed | Replaced by **Nephthys-style staff ticket cards**. Each card shows the question, the author with their past-ticket count, status and claimer, a category select, and a thread link. The card is edited on claim, resolve and re-open. |
| Categories | `TICKET_CATEGORIES` (default Gameplay, Bug, Claims, Economy, Store, Other). The category is stored on the ticket and shown in `/stats` and the daily summary. |
| Daily summary | Posted to `STAFF_CHANNEL_ID` at `DAILY_SUMMARY_TIME` / `DAILY_SUMMARY_TIMEZONE` (default 09:00 America/Phoenix). |
| #tickets (Phase 2) | Built. See README "#tickets" and DECISIONS entries 85–112. Staff alerts reuse the staff card (private surface); private tickets use T-<n> numbers from `TICKET_NUMBER_START`; categories live in `copy/ticket_categories.toml`. |
| AI (optional) | An OpenAI-compatible client (Hack Club AI by default). It auto-categorises new tickets, and its answers are grounded only in `kb/*.md`. Shadow mode is the default; public mode is opt-in. |

---

## 0. TL;DR

- About 6.5k lines of Python. Roughly **25% is Slack-agnostic and kept**: the Piccolo models, ticket state machine, stats math, stale-ticket logic, env/logging scaffolding. Roughly **45% is Slack glue**, rewritten. Roughly **30% is Hack Club–specific**, deleted.
- **The Slack Nephthys model maps almost 1:1 onto a Discord text channel.** Slack top-level message + thread → Discord message + a public thread created on it. 🤔/✅ reactions stay reactions. Block Kit buttons → persistent `discord.ui` buttons. BTS channel membership → the Staff role. App Home → slash commands.
- **Migrations are squashed** into a fresh `cloudsail_001_init`, because no Slack data carries over and every ID column changes type (Slack `ts` text → Discord snowflake `BIGINT`).

---

## 1. Module inventory

Legend: **KEEP** (as-is or with light edits) · **PORT** (the logic stays, the Slack calls get rewritten) · **REWRITE** (the concept stays, the code doesn't) · **DELETE**

### 1.1 Core logic: keep

| Module | Verdict | Becomes |
|---|---|---|
| `database/tables.py` | **PORT** | `User`, `Ticket`, `BotMessage` stay, with Slack IDs → `BIGINT` snowflakes. New: `StickyMessage`. Dropped: `TeamTag`, `TagsOnTickets`, `UserTagSubscription`, `QuestionTag`, `CategoryTag`, `Feedback`, `APIKey` (§3). |
| `database/enums.py` | **KEEP** | `TicketStatus`, `UserType`. New: `TicketSurface`. `FeedbackRating` is dropped (v2 brings 1–5 CSAT). |
| `database/postgres_enum.py`, `raw_migration.py` | **KEEP** | Generic Piccolo helpers. |
| `piccolo_app.py`, `piccolo_conf.py` | **KEEP** | |
| `piccolo_migrations/*` | **DELETE → squash** | `cloudsail_001_init.py` |
| `utils/stats.py` | **PORT** | `core/stats.py`. Adds the **median** time to first staff response (PRODUCT asks for the median; the old code computed the mean) and leaderboard-by-claimer. |
| `utils/old_tickets.py` | **PORT** | `core/queries.py` (`get_unanswered_tickets`) |
| `utils/performance.py` | **KEEP** | |
| `utils/logging.py` | **PORT** | Keeps the OTel setup. `send_heartbeat()` posts to the Discord log channel (`discord_ops/heartbeat.py`). |
| `utils/env.py` | **REWRITE** | `config.py`: typed `Settings`, loaded lazily so tests don't need a full env. |
| `errors/errors.py` | **KEEP** | `PermissionDenied`, `TicketNotClosedError`, plus `TicketAlreadyClosedError` |
| `transcripts/transcript.py` | **PORT** | `core/transcript.py`: a single Cloudsail copy deck. The 24-program registry is gone. |
| `macros/types.py`, `macros/__init__.py` | **PORT** | Same `Macro` / alias-dispatch idea, with a small `MacroContext` instead of the Slack client. |
| `macros/faq.py`, `resolve.py`, `reopen.py` | **PORT** | `?faq`, `?resolve`/`?close`, `?reopen`/`?unresolve`/`?open`. New: `?ip`. |
| `actions/resolve.py`, `actions/reopen.py` | **PORT (split)** | The state transitions move to `core/tickets.py` (no Discord imports). Side effects (reaction, closing message, archive) move to `discord_ops/threads.py`. |
| `tasks/close_stale.py` | **PORT** | Uses `Ticket.last_msg_at` from the DB, so it makes no API calls. |
| `tasks/daily_stats.py`, `utils/graphs/*`, pie chart | **DELETE (v3)** | The daily post is v3. `/stats` covers v1. The chart code can come back from git history. |
| `utils/starlette.py` | **REWRITE** | `web/health.py`: an aiohttp server with `/health` and `/metrics`. Starlette and uvicorn are dropped; discord.py already ships aiohttp. |
| `scripts/add_dummy_data.py` | **DELETE** | Replaced by test fixtures. |

### 1.2 Slack-specific: rewritten against discord.py

| Module | What it did in Slack | Fate |
|---|---|---|
| `utils/slack.py` | Bolt `AsyncApp` router | `bot.py` (`commands.Bot` subclass) plus cogs |
| `events/message_creation.py` | New question → ticket; reply → claim/last-msg; `?macro`; broadcast policing; AI title and category | `cogs/community_help.py` (`on_message`). Broadcast policing and AI are dropped. |
| `events/message_deletion.py` | Delete the ticket if the question is deleted with no human replies | `on_raw_message_delete` plus `on_raw_thread_delete` |
| `events/message/send_backend_message.py` | A backend card per ticket in a staff channel | Replaced by a **one-line staff feed** |
| `events/app_home_opened.py`, `views/home/*` | App Home dashboards | `/stats`, `/waiting` slash commands (`cogs/staff.py`) |
| `views/modals/*`, `actions/create_*`, `edit_*`, `assign_*`, `options/*` | Tag CRUD and assignment UI | **DELETE.** No category tags in v1 (AI tagging is "Later"). |
| `events/channel_join.py`, `channel_left.py`, `tasks/update_helpers.py` | BTS membership → `User.helper` | A live Staff-role check on every event, mirrored into `User.helper` on upsert |
| `utils/permissions.py` | BTS member or OP, via an API call per check | `core/permissions.py`: pure function of (role IDs, user ID, ticket) |
| `utils/ticket_methods.py` | Reply/delete helpers, Slack permalinks | `discord_ops/threads.py` |
| `utils/delete_thread.py`, `utils/slack_user.py` | Admin-token deletion queue; profile lookups | **DELETE** |
| `macros/thread.py`, `hello_world.py`, `trigger_*` | Misc. and dev macros | **DELETE** |
| `macros/team_tag.py`, `actions/tag_subscribe.py` | Team tags plus DM subscriptions | **DELETE** (role pings) |
| `api/*`, `api/lobby/*`, `templates/*` | Stats API, API keys, Hack Club Auth lobby | **DELETE** |
| `manifest.yml`, `public/loading.gif`, `utils/time.py` | Slack manifest, App Home assets | **DELETE** |

### 1.3 Hack Club–specific: delete outright

| What | Files |
|---|---|
| Hack Club AI / OpenRouter (HCAI) | `utils/ai.py`; `openai` dependency; `HACK_CLUB_AI_API_KEY`, `AI_*_MODEL` |
| Exa / search macro | `macros/search.py` |
| Flavortown and 23 other program transcripts | `transcripts/` |
| Flavortown fulfillment reminder | `tasks/fulfillment_reminder.py`, `macros/trigger_fulfillment_reminder.py` |
| Program macros | `fraud`, `hackatime`, `identity`, `max_tokens`, `no_money`, `shipcertqueue`, `shipwrights`, `stale`, `vote_quality`, `vote_queue` |
| Bucky file host | `utils/bucky.py` |
| Hack Club Auth, sessions, API keys | `api/`, `templates/`; `authlib`, `itsdangerous`, `python-multipart` |
| Coolify deploy guide, API docs, HC README | `docs/*`, `README.md` (rewritten) |

**Dependencies.** Removed: `slack-bolt`, `blockkit`, `openai`, `authlib`, `itsdangerous`, `python-multipart`, `thefuzz`, `astral`, `starlette`, `starlette-exporter`, `uvicorn`, `matplotlib`, `numpy`, `pytz`. Added: `discord.py`. Dev: `pytest`, `pytest-asyncio`, `ruff`.

---

## 2. Concept mapping: Slack → discord.py

### 2.1 Surfaces and identifiers

| Slack (Nephthys) | Discord (Cloudsail) |
|---|---|
| `#help` channel | **#community-help text channel** (`COMMUNITY_CHANNEL_ID`) |
| Top-level question message | Top-level message in #community-help |
| Its thread (`thread_ts`) | A **public thread created by the bot on that message**. The thread ID equals the message ID. |
| `Ticket.msg_ts` | `Ticket.message_id` and `Ticket.thread_id` (`BIGINT`, unique; equal today, but stored separately so v2 private threads fit the same table) |
| `Ticket.ticket_ts` (backend card) | Dropped. The staff feed line isn't tracked. |
| `BotMessage.ts` + `channel_id` | `BotMessage.message_id` + `channel_id` (`BIGINT`) + `kind` |
| `User.slack_id` | `User.discord_id` (`BIGINT`, unique) |
| Slack permalink | `https://discord.com/channels/{guild}/{thread}` |
| `SLACK_HEARTBEAT_CHANNEL` | `LOG_CHANNEL_ID` |
| `SLACK_BTS_CHANNEL` (helper roster) | `STAFF_ROLE_ID` |
| — | `STAFF_CHANNEL_ID` (the staff feed) |

### 2.2 Events

| Slack event | discord.py | Notes |
|---|---|---|
| `message` (top-level in help channel) | `on_message`, where `message.channel.id == COMMUNITY_CHANNEL_ID` | Only `MessageType.default` / `reply` from non-bots. Then `message.create_thread(...)`. |
| `message` (in thread) | `on_message`, where `message.channel` is a `Thread` whose `parent_id == COMMUNITY_CHANNEL_ID` | Look up the ticket by `thread_id`. Threads with no ticket are ignored. |
| `message_deleted` (question) | `on_raw_message_delete` (`message_id == Ticket.message_id`) | No human replies: delete the thread and the ticket. Otherwise keep both. |
| — | `on_raw_thread_delete` | Delete the ticket row. |
| `thread_broadcast` policing | n/a | |
| `member_joined_channel` on BTS | n/a | The role check is live. |
| `app_home_opened` | n/a | Slash commands |

### 2.3 Staff identity

- Staff means the author has `STAFF_ROLE_ID`. Guild messages and interactions carry the author's role IDs, so **the privileged members intent isn't needed**.
- `User.helper` is refreshed on every upsert. It's a cache for stats and isn't used for permission checks.

### 2.4 Status markers

| Slack | Discord |
|---|---|
| 🤔 on an open question | 🤔 on the parent message (`OPEN_REACTION`, can be disabled) |
| ✅ on a resolved question | ✅ on the parent message (`RESOLVED_REACTION`) **plus thread archived** |

### 2.5 Interactive components

| Slack | discord.py |
|---|---|
| `mark_resolved` button | `ResolveButton(DynamicItem)`, `custom_id = cs:resolve:<ticket_id>` |
| `reopen-button` | `ReopenButton(DynamicItem)`, `custom_id = cs:reopen:<ticket_id>` |
| `chat_postEphemeral` | `interaction.followup.send(..., ephemeral=True)` for buttons. Macros aren't interactions, so they get a reply that deletes itself after 10s. |

`DynamicItem`s are registered once in `setup_hook`, so buttons on every old message keep working across restarts.

### 2.6 Lifecycle

| Step | Behaviour |
|---|---|
| **New question** | Upsert the `User`. Create a public thread named after the message (or "Question from {name}"). Insert the `Ticket`. Post the auto-reply with **[Mark as resolved]** (recorded as a `BotMessage`). Add 🤔. Post one line to the staff feed. Schedule a sticky repost. |
| **Reply in thread** | Update `last_msg_at`/`last_msg_by`. If the author is staff and the ticket is unclaimed, **claim** it (`assigned_to`, `assigned_at`, status `IN_PROGRESS`). If the ticket is `CLOSED` and the author is the OP, **auto-reopen**. |
| **Resolve** (button, `?resolve`, stale task) | The OP or staff only. Status `CLOSED`, `closed_by` = resolver, credit = `assigned_to` (none if unclaimed). Then: ✅ on the parent, remove 🤔, post the closing message with **[Re-open]**, archive the thread. |
| **Re-open** (button, `?reopen`, OP reply) | The OP or staff only. Status → `IN_PROGRESS` if claimed, otherwise `OPEN`. Then: unarchive, remove ✅, re-add 🤔, post "re-opened by @X". |
| **Question deleted** | No non-bot replies in the thread: delete the thread and the ticket. Otherwise keep both. |
| **Stale** | Hourly. Tickets that aren't closed and have `last_msg_at` older than `STALE_TICKET_DAYS` are resolved by the bot with the stale copy. |

### 2.7 Sticky "how it works"

- One bot message per channel, with its ID stored in `sticky_message` so restarts don't duplicate it.
- After each new top-level human message, a repost is scheduled `STICKY_DEBOUNCE_SECONDS` later. Further messages reset the timer. The repost deletes the old message and sends a new one.
- On startup the sticky is (re)posted if it's missing or isn't the last message.
- It's a bot message, so it never creates a ticket or thread.

### 2.8 Slash commands (replacing App Home)

| Command | Who | What |
|---|---|---|
| `/waiting` | Staff | Open tickets with no staff reply since the last message, oldest first, with jump links |
| `/stats [days]` | Staff | Opened/resolved counts, median time to first staff response, unanswered for more than 3 days, top claimers |

Commands are synced to `GUILD_ID` on startup, so they appear instantly.

### 2.9 Macros (replaced by slash commands after #1; see "Follow-up changes")

Staff only, in a ticket thread, and the first word starts with `?`. The invoking message is deleted (needs Manage Messages).

| Macro | Effect |
|---|---|
| `?faq` | Posts the FAQ link. Counts as a staff reply, so it claims. |
| `?ip` | Posts the server address. Counts as a staff reply, so it claims. |
| `?resolve` / `?close` | Resolves |
| `?reopen` / `?unresolve` / `?open` | Re-opens |
| `?ticket` | **v2** |

---

## 3. Schema (`cloudsail_001_init`)

```text
user           id, discord_id BIGINT UNIQUE, username, helper, admin, created_at
ticket         id, surface ticket_surface DEFAULT 'COMMUNITY',
               channel_id BIGINT, message_id BIGINT UNIQUE, thread_id BIGINT UNIQUE,
               title, description, status ticket_status, last_msg_at, last_msg_by user_type,
               opened_by → user (RESTRICT), assigned_to/closed_by/reopened_by → user (SET NULL),
               assigned_at, closed_at, reopened_at, created_at
bot_message    id, ticket → ticket (CASCADE), channel_id BIGINT, message_id BIGINT, kind,
               UNIQUE(channel_id, message_id)
sticky_message channel_id BIGINT PK, message_id BIGINT, updated_at
```

- `assigned_to` is both "claimer" and "credit".
- `closed_by` is whoever resolved it, or NULL for the bot or stale task.
- `surface` exists now so v2's private tickets don't need a stats backfill.

---

## 4. Discord app requirements

- **Intents:** `guilds`, `guild_messages`, `guild_reactions`, **`message_content`** (privileged). The members intent isn't needed.
- **Permissions in #community-help:**
  - View Channel, Send Messages, Read Message History
  - Create Public Threads, Send Messages in Threads, **Manage Threads** (archive and unarchive, delete orphaned threads)
  - **Manage Messages** (delete macro invocations, remove reactions)
  - Add Reactions, Embed Links
- **Permissions in the staff and log channels:** View Channel, Send Messages, Embed Links.

---

## 5. Configuration

All configuration comes from env vars. They're listed with comments in [`.env.example`](.env.example).

---

## 6. Layout

```text
nephthys/
  __main__.py            # logging, then run bot + health server in one loop
  bot.py                 # SupportBot: intents, setup_hook (DB pool, cogs, dynamic items,
                         #   command sync, scheduler), on_ready sticky bootstrap
  config.py              # Settings from env
  core/                  # no discord imports: unit-tested against Postgres
    tickets.py           # create, record_message (claim/auto-reopen), resolve, reopen, delete
    permissions.py       # is_staff, can_resolve
    users.py             # upsert_user
    stats.py  queries.py  transcript.py
  cogs/
    community_help.py    # on_message, deletion handlers
    staff.py             # /waiting, /stats
  ui/buttons.py          # ResolveButton, ReopenButton
  discord_ops/
    threads.py           # side effects of create/resolve/reopen
    sticky.py            # StickyManager (debounced)
    heartbeat.py
  macros/                # dispatch + faq, ip, resolve, reopen
  tasks/close_stale.py
  web/health.py
  database/  piccolo_migrations/  utils/  errors/
tests/
```

---

## 7. Deployment

See [DEPLOY.md](DEPLOY.md). It uses a multi-arch Dockerfile built for `linux/arm64`, and `docker-compose.yml` with the bot plus its own `postgres:17` (not shared with Zammad).

---

## 8. Rollout (one commit each on this branch)

1. **Strip:** remove the Hack Club, Slack and AI code.
2. **Skeleton:** config, bot, health server, init migration.
3. **Core + tests**
4. **#community-help flow**
5. **Macros**
6. **Polish:** sticky, stale close, slash commands, heartbeat
7. **Deploy artifacts**
