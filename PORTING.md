# PORTING.md: Nephthys (Slack) → Cloudsail Support (Discord)

**Status:** Draft for review, Nov 2026
**Spec:** [PRODUCT.md](PRODUCT.md)
**Scope of this doc:** the whole port, with detail for **Phase 1 (#community-help only)**. #tickets (v2) and the Zammad mirror (v3) only show up where they affect Phase 1 decisions.

---

## 0. TL;DR

- About 6.5k lines of Python (not counting migrations or transcripts). Roughly **25% is Slack-agnostic and worth keeping**: the Piccolo models, ticket state machine, stats math, stale-ticket logic, env/logging scaffolding. Roughly **45% is Slack glue** that gets rewritten. Roughly **30% is Hack Club–specific** and gets deleted.
- The core flow maps cleanly: **Slack thread in #help** → **Discord forum post**. **Slack Block Kit buttons** → **`discord.ui` persistent buttons** (`DynamicItem`). **The BTS channel used as the helper list** → **the Discord Staff role**. **App Home** → **slash commands**.
- **The schema changes enough that we should squash migrations** and start from a fresh `001_init`. There's no Slack data to carry over, and every ID column changes type (Slack `ts` text → Discord snowflake `BIGINT`).
- **Two things in PRODUCT.md don't work as written in a forum channel.** Section 4 covers both:
  1. A "sticky message at the bottom" of **#community-help** isn't possible, because a forum has no message stream. Use forum **Guidelines** plus a **pinned post** instead.
  2. `?ticket` depends on #tickets, which is v2. Defer it.

---

## 1. Module inventory

Legend: **KEEP** (as-is or with light edits) · **PORT** (the logic stays, the Slack calls get rewritten) · **REWRITE** (the concept stays, the code doesn't) · **DELETE**

### 1.1 Core logic: keep

| Module | Verdict | Notes |
|---|---|---|
| `database/tables.py` | **PORT** | `User`, `Ticket`, `CategoryTag`, `BotMessage`, `Feedback` stay. Slack `ts`/IDs become `BigInt` snowflakes. Drop `TeamTag`, `TagsOnTickets`, `UserTagSubscription`, `QuestionTag` (unused), `APIKey`. See §3. |
| `database/enums.py` | **KEEP** | `TicketStatus`, `UserType` stay as they are. `FeedbackRating` becomes a 1–5 CSAT score in v2. Add `TicketSurface` (see §3). |
| `database/postgres_enum.py` | **KEEP** | Generic Piccolo helper. |
| `database/raw_migration.py` | **KEEP** | Generic. Only the old migrations use it, but it's handy. |
| `piccolo_app.py`, `piccolo_conf.py` | **KEEP** | |
| `piccolo_migrations/*` | **DELETE → squash** | Replace with one fresh init migration (§3). |
| `utils/stats.py` | **KEEP** | Pure DB math (hang time, resolution time, leaderboard). Rename `slack_id` → `discord_id` in `as_dict`. Add a **median** time to first staff response (PRODUCT asks for the median; the code computes the mean). |
| `utils/old_tickets.py` | **KEEP** | `get_unanswered_tickets()` is exactly the "waiting > 3 days" query. |
| `utils/performance.py` | **KEEP** | Generic. |
| `utils/logging.py` | **PORT** | Keep the OTel setup. `send_heartbeat()` posts to a Discord log channel instead of Slack. |
| `utils/env.py` | **REWRITE** | Keep the `get_environ*` helpers. Replace the `Environment` fields (§6). Drop HCA config, `PROGRAM` validation, `workspace_admin_available()`, and the Slack client. |
| `errors/errors.py` | **KEEP** | `PermissionDenied`, `TicketNotClosedError`. |
| `transcripts/transcript.py` | **PORT** | Keep the pydantic `Transcript` idea, but there's only one product now. Collapse it to a single `CloudsailTranscript` (or load from a TOML/YAML file) and remove all the Hack Club fields. |
| `macros/types.py` | **PORT** | `Macro`/`ReplyMacro` are a good abstraction. Replace `chat_postEphemeral` and the Slack client with a small `MacroContext` (thread, invoker, message). |
| `macros/__init__.py` | **PORT** | Keep the alias dispatch (`run_macro`). Rewire error replies and macro-message deletion. |
| `macros/faq.py`, `resolve.py`, `reopen.py` | **PORT** | These become `?faq`, `?resolve`, `?reopen`. |
| `actions/resolve.py` | **PORT (split)** | The state transition and the 48h "who gets credit" rule are core and stay. The reactions, ephemeral messages, and backend-thread deletion are Slack-only. |
| `actions/reopen.py` | **PORT (split)** | Same split. |
| `tasks/close_stale.py` | **PORT** | Logic stays. Use `Ticket.last_msg_at` from the DB instead of `conversations_replies` (no API call, no rate-limit loop). |
| `tasks/daily_stats.py` | **PORT (v3)** | The message builder becomes an embed. Send the pie chart as a `discord.File` attachment instead of uploading it to bucky. |
| `utils/graphs/*`, `views/home/components/ticket_status_pie.py` (image part) | **KEEP (v3)** | Matplotlib → PNG bytes is library-agnostic. Move it to `utils/graphs/`. |
| `utils/starlette.py` | **REWRITE (small)** | Keep only `/health` and `/metrics`, which the Docker healthcheck and Prometheus need. Everything else is Slack or HCA. |
| `scripts/add_dummy_data.py` | **PORT** | Useful for local dev. Swap the fake Slack IDs for snowflakes. |

### 1.2 Slack-specific: rewrite against discord.py

| Module | What it does in Slack | Fate |
|---|---|---|
| `utils/slack.py` | Bolt `AsyncApp`: the router for every event, action, view and option | **DELETE.** Replaced by a `commands.Bot` subclass plus cogs. |
| `events/message_creation.py` | Top-level message → ticket. Thread reply → claim and `last_msg_*`. `?macro` dispatch. Thread-broadcast policing. AI title and category. | **REWRITE** as `on_thread_create` (new post) plus `on_message` (replies and macros). Broadcast policing has no equivalent: drop it. AI title isn't needed because forum posts have titles: drop it. AI category is deferred ("Later" in PRODUCT). |
| `events/message_deletion.py` | Delete the ticket if the OP deletes the question and nobody replied | **REWRITE** as `on_raw_thread_delete` plus starter-message delete handling (§2.6). |
| `events/message/send_backend_message.py` | Posts a "backend" card per ticket in a staff #tickets channel, with tag dropdowns | **DELETE for v1.** PRODUCT doesn't ask for a staff feed for community-help. The v2 staff alert is new code. See open question Q2. |
| `events/app_home_opened.py`, `views/home/*` | App Home dashboard: stats, assigned tickets, tag management | **DELETE.** Replaced by slash commands (`/stats`, `/waiting`), mostly in later phases. |
| `views/modals/*`, `actions/create_*`, `actions/edit_category_tag.py`, `actions/assign_*`, `options/*` | Block Kit modals and dropdowns for tag CRUD and assignment | **DELETE.** Category tags *are* forum tags now (§2.4). Manage them in Discord's channel settings, and sync them into the DB at startup. |
| `events/channel_join.py`, `channel_left.py`, `tasks/update_helpers.py` | Mirrors BTS channel membership into `User.helper` | **REWRITE** as a Staff-role check (§2.3). |
| `utils/permissions.py` | `can_resolve`: BTS member or OP | **REWRITE**: Staff role or `thread.owner_id`. No API call. |
| `utils/ticket_methods.py` | `reply_to_ticket`, `delete_bot_replies`, Slack permalinks | **REWRITE.** Same functions, Discord calls. Permalinks become `thread.jump_url`. |
| `utils/delete_thread.py` | Admin-token queue to delete whole Slack threads | **DELETE.** Not needed: we archive. |
| `utils/slack_user.py` | Profile and display-name lookups | **DELETE.** Use `Member.display_name` and `display_avatar`. |
| `macros/thread.py` | `?thread`: "not a real question", deletes bot reply, silent-closes | **PORT (optional).** Worth keeping as `?notaticket`. Not in PRODUCT, so it's your call. |
| `macros/hello_world.py`, `trigger_daily_stats.py` | Dev-only test macros | **PORT (dev only)**, or replace with a `/debug` command. |
| `macros/team_tag.py`, `actions/tag_subscribe.py` | Team tags plus DM subscriptions | **DELETE.** Discord role pings cover this. |
| `api/*` (stats, tickets, user), `api/auth.py`, `api/lobby/*`, `templates/*` | Public stats API, API keys, Hack Club Auth OAuth lobby | **DELETE for v1.** Zammad reporting and `/stats` cover this. Bring back `api/stats*` later if needed: it's all just `utils/stats.py`. |
| `manifest.yml` | Slack app manifest | **DELETE.** Replaced by a "Discord app setup" section in docs (intents and permissions, §5). |
| `public/loading.gif`, `utils/time.py` | App Home spinner; `is_day()` (astral) for App Home light/dark charts | **DELETE.** |

### 1.3 Hack Club–specific: delete outright

| What | Files |
|---|---|
| Hack Club AI / OpenRouter client (HCAI), `decisions` API | `utils/ai.py`; `openai` dependency; `HACK_CLUB_AI_API_KEY`, `AI_*_MODEL` env vars |
| Exa / search macro | `macros/search.py` |
| Flavortown and other program transcripts (24 programs) | `transcripts/transcripts/*.py`; `transcripts/__init__.py` registry; `PROGRAM` env var |
| Flavortown fulfillment reminder | `tasks/fulfillment_reminder.py`, `macros/trigger_fulfillment_reminder.py` |
| Program macros | `fraud`, `hackatime`, `identity`, `max_tokens`, `no_money`, `shipcertqueue`, `shipwrights`, `stale` (the `?stale` reply macro, not the stale *task*), `vote_quality`, `vote_queue` |
| Bucky file host | `utils/bucky.py` |
| Hack Club Auth (HCA) OAuth, sessions, lobby, API keys | `api/lobby/*`, `api/auth.py`, `templates/*`, `HCAConfig` in env, `APIKey` table; `authlib`, `itsdangerous`, `python-multipart` deps |
| Coolify / hackclub DNS deployment guide | `docs/deployment.md` (rewrite for OCI), `docs/api.md` |
| Hack Club README content | `README.md` (rewrite) |

**Dependencies after cleanup.** Remove `slack-bolt`, `blockkit`, `openai`, `authlib`, `itsdangerous`, `python-multipart`, `thefuzz` (only the deleted Slack dropdowns use it), `astral` (only `utils/time.py`, deleted with App Home). Add `discord.py>=2.4`. Keep `piccolo[postgres]`, `apscheduler`, `pydantic`, `python-dotenv`, `aiohttp`, `prometheus-client`, `opentelemetry-*`, `uvloop`, `matplotlib`/`numpy` (v3 charts; drop until then if you want a slimmer image), `starlette` and `uvicorn` (or swap both for a 20-line `aiohttp.web` health server; discord.py already ships aiohttp). `httpx2` is imported in `message_creation.py` but isn't a declared dependency, and it goes away with that file.

---

## 2. Concept mapping: Slack → discord.py

### 2.1 Surfaces and identifiers

| Slack (Nephthys) | Discord (Cloudsail) |
|---|---|
| `#help` channel (`SLACK_HELP_CHANNEL`) | **#community-help forum channel** (`discord.ForumChannel`) |
| Top-level question message | **Forum post** = `discord.Thread` whose parent is the forum. Its starter message has `message.id == thread.id`. |
| Thread replies (`thread_ts`) | Messages in that thread (`message.channel` is the `Thread`) |
| `Ticket.msg_ts` (question ts) | `Ticket.thread_id` (BIGINT snowflake, unique) |
| `Ticket.ticket_ts` (backend card in #tickets) | Removed in v1 (Q2). v2 adds `staff_alert_message_id`. |
| `BotMessage.ts` + `channel_id` | `BotMessage.message_id` + `channel_id` (BIGINT) |
| `User.slack_id` | `User.discord_id` (BIGINT, unique) |
| Slack permalink (`hackclub.slack.com/archives/...`) | `thread.jump_url` / `https://discord.com/channels/{guild}/{thread}` |
| `SLACK_HEARTBEAT_CHANNEL` | `LOG_CHANNEL_ID` (staff-only text channel) |
| `SLACK_BTS_CHANNEL` (helper roster plus stats) | `STAFF_CHANNEL_ID` (daily stats) plus `STAFF_ROLE_ID` (roster) |

### 2.2 Events

| Slack event | discord.py | Notes |
|---|---|---|
| `message` (top-level, in help channel) | `on_thread_create(thread)` where `thread.parent_id == FORUM_ID` | **Race:** the starter message may not be cached or fetchable yet. Use `thread.starter_message or await thread.fetch_message(thread.id)` with a short retry on `NotFound`. |
| `message` (in thread) | `on_message(message)` where `isinstance(message.channel, Thread)` and parent is the forum | **Skip `message.id == message.channel.id`.** That's the starter message, which also fires `on_message`. Skip bots too. |
| `message_deleted` / tombstone of question | `on_raw_message_delete` (starter id == thread id) and `on_raw_thread_delete` | §2.6 |
| `thread_broadcast` policing | n/a | Discord has no "also send to channel". Delete the policing code. |
| `member_joined_channel` / `left` on BTS | `on_member_update` (role diff) | Only needed if we cache `User.helper` (§2.3). |
| `app_home_opened` | n/a | Slash commands instead (§2.7). |

### 2.3 Staff / helper identity

- **Slack:** `User.helper` is synced from BTS channel membership (`update_helpers`). `can_resolve` calls `conversations_members` on *every* resolve.
- **Discord:** "Discord roles are the source of truth" (PRODUCT). **Decide live** with `STAFF_ROLE_ID in [r.id for r in member.roles]`. It's free with the `members` intent and the member cache.
- **Keep `User.helper` as a cached mirror**, updated on `on_member_update` and on startup (`guild.get_role(STAFF_ROLE_ID).members`). Stats queries and leaderboards still filter on it without needing the gateway.
- `User.admin` / `SLACK_MAINTAINER_ID` becomes `MAINTAINER_ID` (a Discord user id), used for "something broke" pings in the log channel.
- Per-category owner roles (Store, Mod) are **v2**. Phase 1 needs only `STAFF_ROLE_ID`.

### 2.4 Tags and status

| Slack | Discord |
|---|---|
| 🤔 `thinking_face` reaction on an open question | **`Open` forum tag** (or no status tag). Reactions still work on the starter message, but tags can be filtered in the forum UI, which is better. |
| ✅ `white_check_mark` reaction on a resolved question | **`Resolved` forum tag**, and the thread is archived |
| `CategoryTag` (AI-assigned, managed via App Home modals) | **Forum tags** (Gameplay, Bug, Claims, Economy…). `CategoryTag` gains `forum_tag_id BIGINT`, and the bot syncs `forum.available_tags` into the table at startup. Staff edit tags in Discord's UI, not ours. AI auto-tagging is "Later". |
| `TeamTag` + subscriptions | Deleted (role pings) |

Forum-tag mechanics to keep in mind:
- A post can have at most **5 applied tags**.
- Applying tags needs **Manage Threads**. Mark `Resolved`/`Open` as **moderator-only tags** in the forum settings so posters can't fake them.
- Store tag IDs in config (`RESOLVED_TAG_ID`, optional `OPEN_TAG_ID`). Don't look them up by name.

### 2.5 Interactive components (Block Kit → `discord.ui`)

| Slack | discord.py |
|---|---|
| `action_id="mark_resolved"`, `value=ts` button | `discord.ui.DynamicItem[Button]` with `custom_id` template `cs:resolve:(?P<thread_id>\d+)` |
| `action_id="reopen-button"`, `value=ticket.id` | `DynamicItem` `cs:reopen:(?P<ticket_id>\d+)` |
| `feedback-button` → `submit-feedback` modal | v2: CSAT `cs:csat:(?P<ticket_id>\d+):(?P<score>[1-5])` buttons |
| `chat_postEphemeral` (e.g. "only helpers or OP can resolve") | `interaction.response.send_message(..., ephemeral=True)`. **Only possible as an interaction response.** For macros, which are plain messages, there's no ephemeral reply. Reply and `delete_after=10`, or DM. |
| `ack()` within 3s | `interaction.response.defer()` within 3s, then `followup` |
| Modals (`views.open`) | `discord.ui.Modal` (unused in v1) |

**Why `DynamicItem`:** buttons must keep working after restarts and across thousands of old posts. Register the item classes once in `setup_hook` with `bot.add_dynamic_items(ResolveButton, ReopenButton)`. Then any message with a matching `custom_id` routes to them, so we never need to re-attach per-message views.

### 2.6 Lifecycle in #community-help (Phase 1)

| Step | Nephthys today | Cloudsail (Phase 1) |
|---|---|---|
| **New question** | Upsert `User`. Post a backend card in #tickets. Post the FAQ reply with a Resolve button in the thread. AI title and category. Insert `Ticket` and `BotMessage`. React 🤔. | Upsert `User`. Insert `Ticket(thread_id, title=thread.name, description=starter.content, status=OPEN)`. Post the auto-reply (PRODUCT copy) with a **[Mark as resolved]** button and record a `BotMessage`. Optionally apply the `Open` tag. |
| **Reply** | Update `last_msg_at`/`last_msg_by`. **Reassign to every helper who replies** (the latest one wins). Status becomes `IN_PROGRESS`. | Update `last_msg_*`. **First staff reply claims:** set `assigned_to`/`assigned_at` only if null (PRODUCT: "first staff reply claims"). Status becomes `IN_PROGRESS`. Later staff replies don't steal the claim. Non-staff replies never claim. |
| **Resolve** (button or `?resolve`) | Permission: BTS or OP. Status `CLOSED`. 48h credit rule. Post "resolved by" with Re-open (and Feedback). Swap the 🤔 reaction for ✅. Delete the backend card thread. | Permission: Staff role or `thread.owner_id`. Same state change and credit rule. Post the closing message ("marked as resolved by @X — got another question? make a new post!") with **[Re-open]**. Apply the `Resolved` tag (removing `Open`). **Archive the thread.** Keep the credit rule as-is, or simplify it given first-claim semantics: that's Q5. |
| **Re-open** (button or `?reopen`) | Permission: BTS or OP. Status `OPEN`. Clear `closed_*`. Set `reopened_*`. Repost the backend card. Swap reactions. | Same permission and DB changes. Remove `Resolved` (re-add `Open`), unarchive, post "re-opened by @X". |
| **OP posts in a resolved thread** | Thread is still open in Slack, and nothing happens | Discord **auto-unarchives** an archived (unlocked) thread when someone posts. The ticket stays `CLOSED` and keeps the `Resolved` tag. **See Q3.** |
| **Question deleted** | If there are no human replies, delete bot replies and the `Ticket` row. Otherwise keep it. | `on_raw_thread_delete` (post deleted) deletes the `Ticket` row (cascade drops `BotMessage`). If only the starter message is deleted, the thread survives in a forum. Mirror the current rule: no human replies means delete the thread and the ticket, otherwise keep both. |
| **Stale auto-close** | Hourly. Slack `conversations_replies` per ticket to find the last activity. | Hourly. Use `Ticket.last_msg_at` (already tracked), so no API calls. Resolve with the `stale` transcript line, then archive. |

### 2.7 App Home → slash commands

There's no App Home equivalent. Use `app_commands` scoped to the guild (`GUILD_ID`) so they sync instantly.

| App Home view | Slash command | Phase |
|---|---|---|
| Dashboard / stats | `/stats` (ephemeral, staff-only via `default_permissions` plus a role check) | Later (PRODUCT says "full stats later") |
| Assigned tickets | `/waiting` (unclaimed or stale posts, with jump links) | v1 nice-to-have. It's cheap: `get_unanswered_tickets()`. |
| Category tag CRUD | none (Discord forum settings) plus startup sync | v1 |
| Team tags | deleted | n/a |
| (new) `/resolve`, `/reopen` | Slash twins of the macros, which give ephemeral errors for free | Optional v1 |

### 2.8 Macros

The message prefix `?name` still works in Discord, but it needs the **Message Content** privileged intent, which we need anyway for ticket descriptions and macro text.

- Dispatch stays in `macros/__init__.run_macro`. Errors become a reply with `delete_after` instead of an ephemeral.
- **Deleting the invoking `?macro` message** (Nephthys did this with the user token) needs **Manage Messages** in the forum.
- Phase 1 macros: `?faq` (link the FAQ, then resolve, as `ReplyMacro` does today; confirm with Q6), `?ip` (post the server address, **don't** resolve), `?resolve`/`?close`, `?reopen`.
- `?ticket` (escalate to #tickets): **deferred to v2**, because it needs the #tickets private-thread flow. Phase 1 could ship a stub that locks the post and tells the poster to DM staff, but I'd rather not ship half a feature.

---

## 3. Schema

There's no production Slack data to migrate, so **squash** everything into `cloudsail_001_init`. Changes from the current schema:

```text
User
  slack_id TEXT UNIQUE   → discord_id BIGINT UNIQUE
  username TEXT          (keep: last seen display name)
  helper, admin BOOL     (keep: helper mirrors Staff role)
  app_home_last_view     → DROP
  team_tag_subscriptions → DROP

Ticket
  msg_ts TEXT UNIQUE     → thread_id BIGINT UNIQUE
  ticket_ts TEXT UNIQUE  → DROP (v2 adds staff_alert_message_id BIGINT NULL)
  + surface ticket_surface NOT NULL DEFAULT 'COMMUNITY'   -- COMMUNITY | PRIVATE (v2)
  + number INT NULL UNIQUE                                -- v2 "T-26"; NULL for community posts
  title, description, status, last_msg_at, last_msg_by,
  opened_by, assigned_to, closed_by, reopened_by, *_at  (keep)
  question_tag_id        → DROP (QuestionTag unused)
  team_tags (M2M)        → DROP
  category_tag_id        (keep)

CategoryTag
  + forum_tag_id BIGINT UNIQUE NULL
  name, slug, description (keep: description is the future AI prompt)

BotMessage
  ts TEXT                → message_id BIGINT
  channel_id TEXT        → BIGINT
  + UNIQUE(channel_id, message_id)   -- the existing TODO

Feedback                 keep the table; rating becomes SMALLINT 1–5 in v2 (CSAT)
QuestionTag, Tag, tags_on_tickets, user_tag_subscriptions, api_key → DROP
```

I'd add `surface` **now**, even though v2 is the first user of `PRIVATE`. It costs one column and saves a migration that backfills every stats query later. `number` can wait for v2.

Keep the existing camelCase `db_column_name`s, or switch to snake_case now while the DB is empty? **I recommend snake_case** for the fresh init. It's free now and annoying later.

---

## 4. Spec gaps found while mapping

1. **Sticky message in #community-help.** A forum channel has no message list, only posts, so there's no "bottom" to keep a sticky at. cloudsail-pinner's delete-and-repost trick only works in text channels. Options:
   - **(Recommended)** Put "how posting and resolving works" plus the FAQ link in the forum's **Post Guidelines** (shown above the post list and in the new-post composer), and also add a bot-owned **pinned post** ("📌 How #community-help works"). Forums allow one pinned post. No debounce or repost loop is needed.
   - The sticky stays a v2 feature for **#tickets** (a text channel). Fold cloudsail-pinner's logic into the bot then, or keep running pinner. Decide in v2.
2. **`?ticket`** needs #tickets (v2). Deferred (§2.8).
3. **Claim semantics differ from Nephthys:** "first staff reply claims" versus Nephthys's "latest helper owns it". I'm following PRODUCT. This also makes "median time to first staff response" exactly `assigned_at - created_at`.
4. **"Anyone can answer"** means non-staff replies update `last_msg_by = OTHER` but never claim. Nephthys's `get_unanswered_tickets` already treats anything not from a `HELPER` as unanswered, which matches "waiting for staff".

---

## 5. Discord app requirements

- **Gateway intents:** `guilds`, `guild_messages`, `message_content` (**privileged**), `members` (**privileged**, for role checks and the member cache). Under 100 servers, you just toggle these on in the developer portal.
- **Bot permissions in #community-help:** View Channel, Send Messages in Threads, Read Message History, Embed Links, **Manage Threads** (tags, archive, lock, pin), **Manage Messages** (delete macro invocations), Add Reactions (optional).
- **Bot permissions in the staff/log channels:** View Channel, Send Messages, Embed Links, Attach Files (stats charts).
- **Slash commands:** guild-scoped sync in `setup_hook` (instant), not global (up to an hour to propagate).

---

## 6. Configuration (`.env`)

```dotenv
ENVIRONMENT=development
DATABASE_URL=postgresql://cloudsail:...@db:5432/cloudsail_support
DISCORD_TOKEN=...
GUILD_ID=...
COMMUNITY_FORUM_ID=...        # #community-help
RESOLVED_TAG_ID=...           # forum tag
OPEN_TAG_ID=                  # optional
STAFF_ROLE_ID=...
STAFF_CHANNEL_ID=...          # daily stats (v3), alerts (v2)
LOG_CHANNEL_ID=...            # heartbeat/errors (was SLACK_HEARTBEAT_CHANNEL)
MAINTAINER_ID=...             # discord user id
FAQ_URL=https://...
SERVER_ADDRESS=play.cloudsail.uk
STALE_TICKET_DAYS=            # unset = disabled
PORT=3000                     # /health, /metrics
# unchanged: LOG_LEVEL*, OTEL_*
```

Copy (auto-reply, resolved/reopened lines, macro text) lives in the `Transcript` model, with defaults taken from PRODUCT.md. Env overrides aren't needed in v1.

---

## 7. Proposed layout

I'd **keep the package name `nephthys`** for now. It's less churn, keeps attribution obvious, and a rename is a one-commit `sed` later if we want `cloudsail_support`. I'd also avoid naming a subpackage `discord/`, which shadows the library in tooling and confuses humans.

```text
nephthys/
  __main__.py              # load env/logging, start bot + health server in one event loop
  bot.py                   # class SupportBot(commands.Bot): intents, setup_hook (DB pool, cogs,
                           #   add_dynamic_items, tag sync, guild command sync, scheduler)
  config.py                # was utils/env.py: typed settings, validation
  core/                    # NO discord imports: pure DB / domain logic, unit-testable
    tickets.py             # create_ticket, record_message (claim rules), resolve, reopen,
                           #   credit rule → return result objects; callers do the side effects
    permissions.py         # is_staff(role_ids), can_resolve(user, ticket)
    stats.py               # was utils/stats.py (+ median)
    queries.py             # was utils/old_tickets.py
    transcript.py          # single CloudsailTranscript (copy/strings)
  cogs/
    community_help.py      # on_thread_create, on_message, thread/starter deletion
    staff.py               # role sync (on_member_update, startup), /waiting, later /stats
  ui/
    buttons.py             # ResolveButton, ReopenButton (DynamicItem); CSAT in v2
    messages.py            # embed/message builders (auto-reply, resolved, reopened)
  discord_ops/
    forum.py               # apply/remove tags, archive/unarchive, jump links, fetch-starter retry
    replies.py             # reply_to_ticket (records BotMessage), delete_bot_replies
    heartbeat.py           # send_heartbeat → LOG_CHANNEL_ID
  macros/
    __init__.py            # run_macro dispatch
    types.py               # Macro, ReplyMacro, MacroContext
    faq.py  ip.py  resolve.py  reopen.py
  tasks/
    close_stale.py
    daily_stats.py         # v3
  web/
    health.py              # /health, /metrics
  database/                # tables.py, enums.py, postgres_enum.py, raw_migration.py
  piccolo_migrations/
    cloudsail_001_init.py
  utils/
    logging.py  performance.py  graphs/
tests/
  core/                    # pytest + a throwaway Postgres (docker) for core/ and stats
docker-compose.yml         # bot + postgres:17 (own DB, own volume)
Dockerfile
```

The key structural change is the **`core/` ↔ adapter split**. Today `actions/resolve.py` interleaves DB writes, Slack reactions, ephemerals and thread deletion in one function. Pulling the state machine into `core/tickets.py` lets the same `resolve()` serve the button, `?resolve`, the stale task and, in v2/v3, #tickets and the Zammad mirror. It also makes it testable: the repo has **zero tests** today.

**Process model.** A single asyncio process runs `bot.start(token)` and a tiny health/metrics server (aiohttp `web` or uvicorn `Server.serve()`) as sibling tasks. APScheduler `AsyncIOScheduler` starts in `setup_hook`. There are no HTTP inbound events from Discord: everything comes over the gateway.

---

## 8. Deployment (OCI ARM64, next to Zammad)

- The `python:3.13-slim-bookworm` and `ghcr.io/astral-sh/uv` images are both multi-arch. `asyncpg`, `numpy` and `matplotlib` ship `aarch64` manylinux wheels, so there's no compiler in the image. Drop `libatomic1` (likely a Prisma leftover). Pin the uv image tag instead of `latest`.
- `docker-compose.yml`: `bot` plus its **own** `postgres:17` service and named volume, on its own compose network. Don't share Zammad's Postgres (PRODUCT: "The bot gets its own Postgres database"), and don't publish the DB port. Port 3000 is only needed for the healthcheck and scraping: bind it to `127.0.0.1` or leave it unpublished. Zammad's stack uses its own ports and networks, so there's no conflict.
- Keep `entrypoint.sh` (it runs migrations, then the bot) and the `/health` Docker `HEALTHCHECK`. Health should report the DB pool and `bot.is_ready()`.
- CI: keep pre-commit (ruff) and pyright. Add `pytest`. Add a `docker buildx --platform linux/arm64` build job so ARM breakage shows up before deploy. Drop CodeQL, or keep it (it's free).

---

## 9. Migration plan (each step = one reviewable PR)

1. **Strip.** Delete everything in §1.3, the App Home and modals, `manifest.yml`, team tags, the API and lobby, the old migrations. Remove the Slack, AI and HCA dependencies. Rewrite README and deployment docs. *The repo won't run at this point. That's expected.*
2. **Skeleton.** `config.py`, `bot.py`, `__main__.py`, health server, `cloudsail_001_init` migration, `docker-compose.yml`, updated Dockerfile. *Done when:* `docker compose up` on arm64 connects, migrates, logs in, and `/health` is green.
3. **Core.** `core/tickets.py` (create, record_message with first-claim, resolve with credit rule, reopen), `core/permissions.py`, `core/stats.py`, plus pytest coverage of the state machine and stats.
4. **#community-help flow.** The `community_help` cog, `ResolveButton`/`ReopenButton` (`DynamicItem`), forum tag and archive helpers, the auto-reply and closing messages, staff role sync, deletion handling. *Done when:* post → auto-reply → staff reply claims → resolve (tag + archive) → re-open, all working in a test guild and surviving a bot restart.
5. **Macros.** `?faq`, `?ip`, `?resolve`/`?close`, `?reopen`, with macro-message deletion and error replies.
6. **Polish.** Stale auto-close, `/waiting`, forum Guidelines text plus the pinned "how it works" post, heartbeat/log channel, category-tag sync.
7. **Deploy.** OCI box, real guild, soak for a week alongside Mava, then cut over #community-help.

Then **v2** (#tickets, sticky, CSAT, staff alerts, `?ticket`) and **v3** (Zammad mirror, daily stats) build on `core/`.

---

## 10. Open questions for review

- **Q1. Package name:** keep `nephthys`, or rename to `cloudsail_support` now (§7)? *Recommendation: keep it for now.*
- **Q2. Staff feed for community-help:** Nephthys posts a backend card per question in a staff channel, so staff see new questions without watching the forum. PRODUCT doesn't ask for this in v1. Drop it (forum notifications plus `/waiting` are enough), or keep a lightweight "new post" feed? *Recommendation: drop it for v1.*
- **Q3. OP replies after resolve:** Discord auto-unarchives the thread. Should the bot (a) do nothing, (b) auto-reopen the ticket, or (c) re-archive and nudge "hit Re-open or make a new post"? *Recommendation: (a) for v1, and watch it.*
- **Q4. Sticky for #community-help:** OK to replace it with forum Guidelines plus a pinned post (§4.1)?
- **Q5. Resolve credit rule:** keep Nephthys's 48h "credit the active helper" rule, or always credit the claimer? (This touches PRODUCT's open question about OP-resolved posts counting for staff.) *Recommendation: credit `assigned_to` when set, otherwise the resolver if they're staff, otherwise nobody.*
- **Q6. `?faq` auto-resolves** in Nephthys (`ReplyMacro.resolve_ticket = True`). Keep that for Cloudsail, or just link the FAQ?
- **Q7. Schema column naming:** switch to snake_case in the fresh init (§3)? *Recommendation: yes.*
