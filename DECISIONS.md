# DECISIONS.md

These are judgment calls made while building Phase 1 without review. Each one picks the option closest to PRODUCT.md and the review decisions in PORTING.md. Every entry is easy to revisit.

## Product behaviour

1. **What counts as a new ticket.** Only top-level messages of type `default` or `reply` from non-bot users in #community-help. System messages (pins, joins, boosts), webhooks and bots are ignored.
2. **Thread name.** The first line of the message, trimmed to 90 characters. If the message has no text (attachment only), it's "Question from {display name}". Discord caps thread names at 100 characters.
3. **Thread auto-archive.** Set to 1 week (`THREAD_AUTO_ARCHIVE_MINUTES=10080`, the max), so an open ticket's thread doesn't silently vanish from the sidebar after a day. Archiving because of inactivity doesn't change ticket status.
4. **Open marker.** A 🤔 reaction on the parent while the ticket is open, mirroring Nephthys. It's removed on resolve and re-added on re-open. Set `OPEN_REACTION=` (empty) to disable it.
5. **Staff resolving an unclaimed ticket gets no credit.** The rule is "credit = claimer", and only a staff *reply* claims. A staff `?resolve` or button click isn't a reply.
6. **Macros that post on staff's behalf (`?faq`, `?ip`) count as a staff reply**, so they claim an unclaimed ticket and set `last_msg_by=HELPER`. `?resolve`/`?reopen` don't claim.
7. **Re-open status.** A re-opened ticket goes back to `IN_PROGRESS` if it has a claimer, otherwise `OPEN`. The claim is kept.
8. **Auto-reopen applies only to the original poster.** Staff or other players posting in a resolved thread update `last_msg_*` but don't re-open. Discord unarchives the thread when anyone posts, and that's left alone.
9. **Who can re-open:** the same people who can resolve (the OP or staff), matching Nephthys.
10. **Deleted question.** If the parent message is deleted and the thread has no non-bot replies, the bot deletes the thread and the ticket. If people already replied, both are kept and the ticket keeps working.
11. **Thread deleted** (by a moderator): the ticket row is deleted. Stats lose it, which matches Nephthys's behaviour for deleted questions.
12. **Stale auto-close is off by default** (`STALE_TICKET_DAYS` unset), as in Nephthys. When it's on, the bot resolves with the stale copy. `closed_by` is NULL, and credit stays with the claimer, if any.
13. **The staff feed is one plain line**, sent with mentions disabled so nobody gets pinged: `🆕 #<id> <title> — <user> · <thread link>`. It isn't edited on resolve. If `STAFF_CHANNEL_ID` is unset, the feed is skipped.
14. **Macro error replies** (unknown macro, wrong state) are thread replies that delete themselves after 10 seconds, because plain messages can't be ephemeral. A non-staff `?word` is treated as an ordinary message.
15. **Sticky debounce defaults to 5 seconds.** On startup, the sticky is reposted only if it's missing or not the newest message in the channel.
16. **The sticky text is configurable** through `STICKY_TITLE` and `STICKY_BODY`. Defaults come from PRODUCT.md. `{faq_url}` is substituted.
17. **"Unanswered" / waiting** means not closed, and the last message wasn't from staff (Nephthys's definition). `/stats` reports how many have been waiting more than 3 days, per PRODUCT.
18. **The daily stats post is not built.** PRODUCT puts it in v3. `/stats` (staff-only, ephemeral) covers v1.

## Technical

19. **The package name stays `nephthys`.** That's less churn and keeps the upstream lineage obvious.
20. **Migrations are squashed** into `cloudsail_001_init`, with snake_case table and column names. There's no data to migrate.
21. **Dropped tables:** `CategoryTag` (AI tagging is "Later", and a text channel has no forum tags), `Feedback` (v2 brings CSAT 1–5), team-tag tables, `QuestionTag`, `APIKey`.
22. **No privileged members intent.** Staff status comes from the role IDs that Discord includes on message and interaction payloads. `User.helper` is refreshed on each upsert, for stats only.
23. **One process.** The discord.py client and an aiohttp `/health` + `/metrics` server share one event loop. Starlette and uvicorn are dropped.
24. **Slash commands are guild-scoped** (`GUILD_ID`) and synced on every start, so they're available immediately.
25. **Buttons use `DynamicItem`** with `custom_id`s `cs:resolve:<ticket_id>` and `cs:reopen:<ticket_id>`. They survive restarts with no per-message view state.
26. **The heartbeat/log channel is optional.** Without it, errors only go to stderr (and to OTel, if configured).
27. **Python 3.13 base image (`python:3.13-slim-bookworm`)**, pinned uv image version, non-root user. The healthcheck uses Python rather than curl, so curl isn't installed.
28. **The bot's Postgres is `postgres:17-alpine`** in the same compose project, on a private network, with no published port. Port 3000 for health is bound to `127.0.0.1` only.
29. **Tests run against a real Postgres** (`TEST_DATABASE_URL`). The Discord layer is tested with `unittest.mock` stand-ins: there's no live Discord in CI.
30. **CI.** pre-commit (ruff), pyright, pytest against a Postgres service container, and a `linux/arm64` Docker build (QEMU) so ARM breakage shows up before deploy. CodeQL was kept.
31. **pre-commit drops `reorder-python-imports`.** It fought with `ruff format` (each hook undid the other). Ruff's isort rules, configured with force-single-line, do the same job. Ruff is pinned to the same version (0.17.4) in pre-commit and in the dev dependencies.

## Added during the build

32. **A staff member who posted the question doesn't claim it** by replying in their own thread. Their replies count as `AUTHOR`.
33. **Only `default`/`reply` messages count** as questions or replies. Pins, thread-starter stubs and other system messages are ignored everywhere.
34. **Macro names and aliases:** `?faq`; `?ip` (`?address`); `?resolve` (`?close`, `?resolved`); `?reopen` (`?unresolve`, `?open`). Running `?resolve` on a resolved ticket (or `?reopen` on an open one) gets the same "already resolved"/"not resolved" reply as the buttons. Unknown `?words` from staff get a short error and don't count as a reply.
35. **The `?macro` message is deleted before the macro runs**, because resolving archives the thread.
36. **The sticky is posted before the old one is deleted** (never zero stickies). Activity during a repost triggers exactly one more repost; nothing is cancelled mid-flight.
37. **`/waiting` lists the 15 oldest** waiting tickets. Both commands are staff-only (checked in code, so no Discord permission setup is needed) and ephemeral.
38. **The stale job first runs 2 minutes after start**, then hourly, with one instance at a time and a 1-second gap between closes.
39. **Fatal startup errors exit with a one-line reason:** bad token, Message Content intent off, Discord or the DB unreachable. Docker's `restart: unless-stopped` retries. Config errors list every missing variable and exit with code 2.
40. **Voice warnings are silenced.** The bot never uses voice, so PyNaCl and davey aren't installed.
41. **DEPLOY.md recommends turning off "Create Public/Private Threads" for `@everyone`** in #community-help. Otherwise a player can make a thread on their own message first, and the bot can't attach the ticket thread (this is logged to the log channel).
42. **The Dockerfile copies uv from `ghcr.io/astral-sh/uv:0.11.32`** (pinned) and runs as a non-root `bot` user. The health port is published on `127.0.0.1` only, via `HEALTH_PORT`.
43. **`Ticket.title` is the thread name**, i.e. the first line of the question. There's no AI title generation.

## Follow-up: slash commands, staff cards, categories, daily summary, AI

Entries 6, 14, 34 and 35 (about `?macros`) are superseded by 44–47. Entry 13 (the one-line staff feed) is superseded by 48–51. Entry 18 (no daily summary) is superseded by 56–58. Entry 21 (`CategoryTag` dropped) still holds: categories are a plain text column.

**Slash commands**

44. **`?macros` are removed entirely.** A staff message starting with `?` is now just a normal reply, and it claims like any other staff reply. There's no deprecation shim, so staff need to know about the switch.
45. **`/faq` and `/ip` reply publicly as the interaction response**, so Discord shows "<staff> used /faq" for attribution. The reply is recorded as a bot message. It never calls the reply tracker, so it doesn't claim and doesn't change `last_msg_by` or "waiting".
46. **`/resolve` and `/reopen` acknowledge privately** ("resolved ✅"). The public closing / re-opened message comes from the shared flow, exactly as with the buttons. Wrong-state errors are private.
47. **The slash commands are staff-only, enforced in code** with a private refusal. Discord still lists them for everyone. DEPLOY.md explains how to hide them through Server Settings → Integrations. Outside a ticket thread they refuse privately.

**Staff cards**

48. **One card per ticket in `STAFF_CHANNEL_ID`.** If that's unset, there are no cards. The card is an embed:
    - title `#id · <thread name>`, linking to the thread; the description is the question (up to 1000 characters);
    - fields: Asked by (mention + "N past tickets" / "first ticket"), Status, Category, and AI;
    - a category select, plus an "Open thread" link button.

    Embed mentions don't ping anyone.
49. **"N past tickets" counts tickets the same person opened *before* this one**, so it doesn't change when they open more later.
50. **Status text:** 🟡 Waiting for staff, 🔵 Claimed by @X, ✅ Resolved by @Y · credit @X / · no staff credit, or "automatically (stale)". "(re-opened)" is appended after a re-open. The card is re-rendered from the DB on claim, resolve, re-open, category change and AI updates.
51. **Cards are deleted when their ticket is deleted** (question deleted with no replies, or thread deleted). Cards for resolved tickets stay, showing ✅.

**Categories**

52. **Categories are `TICKET_CATEGORIES`** (comma-separated, deduplicated, at most 25 because that's Discord's select limit). They're stored by name in `ticket.category`, with `category_source` set to `ai` or `staff`. Renaming a category in config leaves old tickets with the old name: stats show it as-is.
53. **Only staff can change a category**, through the card's select menu (the change is applied by re-rendering the card in place). A staff choice always wins: the AI only sets a category when none is set, using an atomic `WHERE category IS NULL`.
54. **`/stats` and the daily summary count tickets *opened* in the window, by category.** Uncategorised tickets show as "Uncategorised".
55. **Players don't see categories.** They stay staff-side for now: there's no player-facing tag in a text channel.

**Daily summary**

56. **The daily summary covers the last 24 hours** at `DAILY_SUMMARY_TIME` in `DAILY_SUMMARY_TIMEZONE` (default 09:00 America/Phoenix, which has no DST). It's on by default whenever `STAFF_CHANNEL_ID` is set. Missed runs (e.g. the bot was down) still post if the bot comes back within an hour.
57. **The summary contains:**
    - opened; resolved (and how many without staff); median first staff response; tickets by category;
    - tickets waiting more than 3 days (up to 10 links, then "…and N more (see /waiting)"); top helpers by claimer;
    - when AI answers are on, shadow accuracy for 24h and 7d (👍 ÷ rated; unrated suggestions are excluded), the public "fixed" rate, and attempt and skip counts by reason.
58. **`TIMEZONE` still controls the stale job.** The summary has its own timezone setting so that changing one doesn't move the other.

**AI**

59. **AI is off unless `AI_API_KEY` is set.** `AI_MODEL` is then required: no model is guessed, because model availability on Hack Club AI changes. `AI_BASE_URL` defaults to `https://ai.hackclub.com/proxy/v1`. The client uses `temperature=0` and requests JSON mode; if the provider rejects `response_format`, it falls back to a plain request once and remembers that.
60. **AI runs in the background after the ticket, auto-reply, reaction and card exist**, so a slow or failing provider never delays or breaks ticket creation. Errors are logged (and AI failures recorded as `skip_reason=error`). Order: categorise, then answer.
61. **The knowledge base is every `*.md` under `KB_DIR`**, recursively, re-read for every question (edits apply live; Docker mounts `./kb` read-only). The total is capped at 60,000 characters, and later files beyond that are skipped with a warning. `kb/README.md` is included but contains no server facts.
62. **The answer JSON is validated strictly.** It must be exactly the three keys `answer`, `confidence`, `kb_sources`:
    - `answer` is a string or null; `confidence` is a number from 0 to 1 (booleans are rejected); `kb_sources` is a list of strings;
    - a ```json code fence is tolerated; anything else is `invalid_json`.

    Source paths are normalised (`kb/faq.md`, `./faq.md` → `faq.md`).
63. **Posting gates, in order:** a non-empty answer (`no_answer`), confidence ≥ `AI_ANSWER_MIN_CONFIDENCE` (default 0.8, inclusive; `low_confidence`), at least one source (`no_sources`), every source a real KB file (`unknown_sources`), and at most 1500 characters (`too_long`). Every attempt is stored in `ai_suggestion`, posted or not, with its reason.
64. **The prompt puts the KB in the system message and the question in the user message**, wrapped in tags and marked as untrusted. That's basic prompt-injection hygiene; the strict source and confidence gates are the real safety net.
65. **Shadow mode** posts the suggestion as a reply to the ticket's card in the staff channel (it isn't posted if there's no staff channel: `no_staff_channel`). Only staff can rate it, and only the first rating counts. Rating removes the buttons and notes who rated it.
66. **Public mode** posts an embed in the thread, titled "🤖 Automated answer", with a footer saying it's a bot that can be wrong and listing the sources. There's no shadow copy, but the card's AI field shows the outcome.
    - **This fixed it** (the poster or staff) records `fixed` and resolves the ticket through the normal flow. Credit still goes only to a claimer, so an AI fix of an unclaimed ticket gives nobody credit.
    - **I still need a human** records `needs_human` and posts a short reassurance. The ticket stays waiting.
    - Only the first click counts.
67. **Answers aren't posted in public mode if the ticket was resolved while the AI was thinking** (`ticket_closed`). If a send fails, the attempt is marked `send_failed`.
68. **AI messages never claim and never clear "waiting".** They're bot messages, which the reply tracker ignores, and the AI flow never calls it. Tests check this.
69. **AI categorisation is on by default when AI is on** (`AI_CATEGORIZE`), and the answer assistant can be turned off separately (`AI_ANSWER_MODE=off`).
70. **Privacy:** the question title and text are sent to the AI provider. DEPLOY.md says so.
71. **New tables and columns:**
    - an `ai_suggestion` table;
    - on `ticket`: `category`, `category_source`, `staff_card_channel_id`, `staff_card_message_id`.

    These come from migration `cloudsail_002`, which was tested forwards and backwards. Outcome and mode values are `TEXT` with `CHECK` constraints rather than Postgres enums, which are easier to extend.
