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

## Follow-up: configurable copy, suppressed link previews, optional FAQ

Entry 16 (sticky text via `STICKY_TITLE`/`STICKY_BODY`) is superseded by 72–80.

**Configurable copy**

72. **All message text lives in `copy/copy.toml`.** It's grouped into sections: `community`, `sticky`, `errors`, `commands`, `card`, `stats`, `summary`, `ai`, about 100 keys in all. The built-in defaults are the previous text, word for word.
    - The shipped file is generated from those defaults (`python -m nephthys.core.copytext > copy/copy.toml`), and a test fails if the two drift apart.
    - Every key carries a comment with its purpose and the placeholders it accepts.
73. **What's in scope:** everything players or staff read.
    - That includes the auto-reply, closing, stale and re-open messages; button and select labels; the sticky; private errors; command replies; staff cards; `/stats`, `/waiting` and the daily summary; and AI embeds, labels and outcome notes.
    - **Not in scope:**
      - slash-command names and descriptions (Discord metadata, registered at startup);
      - log-channel heartbeats (maintainer diagnostics);
      - emojis inside buttons and reactions (reactions already have env vars);
      - user data and numbers;
      - the `?` used when a confidence or stale-days value is missing;
      - skip-reason codes such as `low_confidence`, which are shown as-is in the summary.
74. **It's a directory mount (`./copy:/app/copy:ro`), not a single-file mount.** Most editors save by replacing the file, and a single-file bind mount keeps pointing at the old inode, so edits would silently never reach the container. The image also bakes in `copy/copy.toml`, so it works without the mount.
75. **Live reload, like `kb/`.**
    - The file's modification time is checked at most every 5 seconds, when text is next needed.
    - A valid edit applies to new messages. An invalid edit (TOML error, unknown key or section, unknown or positional placeholder, wrong type) is logged with the exact key and reason, and **the previous text stays live**. A missing file mid-save is ignored.
    - Already-posted messages aren't rewritten.
76. **At startup, an invalid copy file is fatal**: exit code 2 with every problem listed, the same as config errors.
    - A missing file at the default path falls back to the built-in text with a warning.
    - A missing file at an explicitly set `COPY_FILE` is an error.
    - `python -m nephthys.core.copytext --check <file>` validates a file without starting the bot.
77. **Partial files are fine.** Missing keys use the built-in default, so you can trim `copy.toml` down to just what you change. Unknown keys are rejected, so typos aren't silently ignored.
78. **Placeholders are declared per key** and validated at load time.
    - Each key may only use its declared placeholders, plus `{{` and `}}` for literal braces.
    - A test builds a copy where every template uses *every* placeholder it's allowed, then runs every message path. So code that forgets to supply a declared placeholder would fail CI rather than crash in production.
79. **Text that used to be computed is now split into explicit keys,** so it can be reworded without code changes:
    - plurals: `past_tickets_one`/`_many`, `stats_title_one_day`;
    - FAQ variants: `auto_reply` / `auto_reply_no_faq`, `sticky.body` / `body_no_faq`;
    - the card status line: `status_resolved` = `{by}{credit}`, with `resolved_by`, `resolved_automatically`, `credit`, `no_credit` and `reopened_suffix` as separate keys.
80. **`STICKY_TITLE` / `STICKY_BODY` were removed.** If they're still set, startup logs a warning naming the replacement, and the values are ignored. They aren't merged into the copy, because two sources of truth for one string is worse.

**Link previews**

81. **Every bot text message is sent with `suppress_embeds=True`.** That covers channel and thread messages, public and private interaction responses, follow-ups, and heartbeats.
    - All sends go through `discord_ops/send.py`. A test sweeps every message and interaction response the full flow produces and asserts this.
82. **Rich-embed messages are the exception.** Cards, the sticky, `/stats`, `/waiting`, the daily summary and AI answers are sent *without* `suppress_embeds`, because Discord's flag would hide the bot's own embed too.
    - They're sent with no message content, so there's nothing to unfurl. Links inside an embed never unfurl.
    - The same test asserts that embed messages carry no content.

**FAQ_URL unset**

83. **The auto-reply and sticky switch to their `*_no_faq` variants,** with no FAQ sentence and no dangling "check the FAQ".
    - `/faq` replies privately to staff with `commands.faq_unconfigured` (default "no FAQ configured.") and posts nothing in the thread.
    - No other message references the FAQ.

**Fix found by the new tests**

84. **The staff card's category select now checks for staff before reading the selection.** An empty selection, which Discord shouldn't send but could, used to raise. It's now a no-op.

## Phase 2: #tickets (PRODUCT.md §2)

**Process**

85. **Step 0 (copy.toml, suppressed link previews, optional FAQ) was already its own commit (`073a34e`)** from the previous round, in PR #3, which wasn't merged yet. All work must go on the one designated branch, so Phase 2 is stacked on that branch and PR #3 now covers both. It was retitled rather than replaced with a new PR, because a branch can only have one open PR.

**Panel and creation**

86. **The panel is a sticky** that reuses `StickyManager`, which now takes a render function, so it can carry an embed *and* the Create Ticket button. It reposts after human messages in #tickets and after Discord's "X started a thread" notices, but never after its own messages.
87. **DEPLOY.md recommends denying Send Messages to `@everyone` in #tickets,** so the channel is just the panel. Players talk inside their threads.
88. **Create Ticket immediately creates a private, non-invitable thread** and adds the player.
    - The thread name defaults to `"{name} (T-{number})"`. PRODUCT says it's "named after the user"; the number is appended so that two tickets from the same player can be told apart. The format is configurable in copy.
    - The player gets a private reply with the link.
89. **Ticket numbers come from a Postgres sequence** that's aligned on every startup to the highest of `TICKET_NUMBER_START` (default 26), the largest existing number + 1, and the sequence's own next value. It never moves backwards, never reuses a number, and is race-free. A failed thread creation leaves a gap in the numbers, which is harmless.
90. **Players are limited to `TICKETS_MAX_OPEN_PER_USER` open tickets** (default 2; 0 = unlimited). Anyone at the limit gets a private reply linking their open tickets. PRODUCT doesn't specify a limit; 2 lets someone have, say, a store issue and a report open at once while stopping spam.
91. **If adding the player to the thread fails,** the thread is deleted and no ticket is created, so there's never a ticket the player can't see.

**The opener and category flow**

92. **The opener is a single message**, reposted on each pick. It contains the `Ticket T-<n>` header, the welcome, the stacked "You have selected" lines and the blurb, with the category or subcategory buttons and **Close Ticket** fixed on the last row. The first opener pings the player; reposts don't.
93. **Reposting posts the new opener first, then deletes the old one,** so there's never a moment with no buttons. Only the opener or staff can pick, and the first pick wins (atomic). Stale or double clicks get a private "already chosen".
94. **The opener is rendered from the ticket's state,** not by appending to old text, so a repost is always consistent.
95. **Categories live in `copy/ticket_categories.toml`**: categories, optional subcategories, priority, owning role, emoji and blurb.
    - The defaults match PRODUCT.md §2 exactly. A test keeps the shipped file in sync with them.
    - Roles are named (`staff`, `store`, `mod` → env IDs) or numeric. `store` and `mod` fall back to the Staff role when their IDs are unset.
    - The file is read **at startup only**, unlike copy.toml, because category keys are baked into live buttons. An invalid file stops startup with every problem listed.
    - It's limited to 20 categories and 20 subcategories, because the last button row is Close Ticket.
96. **A category with subcategories shows its blurb as the question** ("What kind of server issue is it?"). The leaf blurb (from the subcategory, or from a category with no subcategories) tells the player what to include.

**Staff side**

97. **The staff alert is the existing staff card** (private surface), posted when a leaf is reached rather than when the ticket is created, as asked.
    - Its title is `T-<n> · <category> › <subcategory>`. It shows priority, status and claimer, CSAT once rated, and escalation links.
    - It has no category select, because the player picked the category. It just has an "Open thread" link.
98. **The alert pings the category's role, plus the Staff role for High priority** (deduplicated).
    - The ping is a link-free content line on the card message ("@Role new 🔴 High ticket"). The send helper rejects any URL in that line, so the "nothing to unfurl" rule still holds.
    - DEPLOY.md explains that roles must be mentionable, or the bot needs Mention All Roles in the staff channel.
99. **The player's first message becomes the ticket description** (and title) and appears on the card. PRODUCT's alert includes the first message, which usually arrives after the category pick, so the card is edited when it does.
100. **The first staff reply claims, the same as #community-help.** It reuses the same reply tracker. Private tickets never auto-reopen: the thread is locked, so only staff can post in it.

**Close, CSAT and reopen**

101. **Close Ticket (opener or staff) and `/resolve` (staff) share one flow:** the closing message, then the CSAT prompt, then lock + archive. Credit goes to the claimer, as before.
102. **CSAT is 1–5 buttons, opener only, first click counts** (atomic). There's one rating per ticket. If a rated ticket is reopened and closed again, the prompt isn't posted again.
     - The rating replaces the prompt with a thank-you.
     - It's stored on the ticket (`csat_rating`, `csat_at`) and shown on the card.
103. **Clicking a CSAT button in the locked, archived thread is expected to work,** because interactions don't need send permission. This is listed as untested-live in the PR.
104. **`/reopen` (staff only for private tickets)** unarchives and unlocks the thread, re-adds the player, posts a note, and refreshes the card. Players can't re-open private tickets.
105. **`/faq`, `/ip`, `/resolve` and `/reopen` now work in #tickets threads too.**
106. **Stale auto-close skips #tickets.** Private tickets are closed by people, with CSAT.

**`/escalate`**

107. **`/escalate` (staff, in a #community-help ticket thread)** does the following:
     - creates a private ticket for the *original poster*, named and numbered as usual, with the community title and description copied and `escalated_from` set;
     - posts a link back in the private opener;
     - posts a note in the public thread linking to the private one;
     - resolves the community ticket, swaps 🤔 for ✅, then locks and archives the public thread;
     - shows "Escalated to" and "Escalated from" on both cards.
108. **`delete_original` (optional, default off)** first copies the question text into the private thread (before the opener, so the buttons stay last), then deletes the public message. The deletion handler skips escalated tickets, so the community ticket's record survives.
109. **A second `/escalate` on the same post** replies privately with the existing link. Escalating needs #tickets to be enabled.

**Stats**

110. **`/stats` and the daily summary each send one embed per channel.** The #tickets embed only appears when #tickets is enabled.
     - #tickets adds the CSAT average (ratings given in the window), counts by priority (High, Medium, Low, then not picked yet) and by category (labels from the categories file).
     - `/waiting` and the summary's waiting list cover both channels, shown as `#id` or `T-n`.
     - "Uncategorised" now always sorts last.
111. **AI never runs on #tickets:** these are private, and PRODUCT doesn't ask for it.
112. **New schema (migration `cloudsail_003`, tested forwards and backwards):**
     - new `ticket` columns: `number` (unique), `subcategory`, `priority` (CHECK), `opener_message_id`, `escalated_from` (FK), `csat_rating` (1–5), `csat_at`;
     - `message_id` is now nullable;
     - `category_source` accepts `player`;
     - a new `ticket_number_seq` sequence.

## Follow-up: closed #tickets stay open for CSAT

Entries 101–103 (lock and archive on close) are superseded by 113–120.

113. **Closing no longer archives straight away.** Close (button or `/resolve`) posts the closing message and the CSAT buttons, then **locks** the thread (`TICKETS_LOCK_ON_CLOSE`, default on). The thread stays open: the player can read it and click a rating but can't post, and staff (Manage Threads) can still post.
    - The lock is on by default because the player posting in a closed ticket would otherwise go unnoticed. Private tickets don't auto-reopen.
114. **The thread is archived when the opener rates or after `CSAT_TIMEOUT_HOURS` (default 24), whichever comes first.**
    - Archiving locks and archives the thread and sets the new `ticket.archived_at`.
    - A ticket closed when it already has a rating, because it was rated before a reopen, has nothing to wait for and archives immediately.
115. **The timeout is a scheduler sweep every 10 minutes,** not a per-ticket timer. It finds closed private tickets with `archived_at` unset whose `closed_at` is older than the timeout. That way it survives restarts and deploys with no job state, and an archive lands at most about 10 minutes late.
    - The sweep is registered only when #tickets is enabled.
    - A partial index keeps the query cheap.
116. **`/reopen` works at any time,** before or after the archive. It clears `archived_at`, so a pending timeout no longer applies to a re-opened ticket, and it unlocks, unarchives and re-adds the player as before. Closing it again starts a new wait.
117. **The CSAT handler tolerates archived threads.** The rating is always recorded first. If Discord refuses to edit the prompt (for example because the timeout already archived the thread), the player gets the same thank-you privately, and nothing is re-archived.
118. **A rating clicked on an old prompt after a reopen is recorded** (first click still counts) but **doesn't archive the open ticket.**
119. **Migration `cloudsail_004`** adds `ticket.archived_at` and backfills it from `closed_at` for private tickets that were already closed, since they were archived on close under the old behaviour. The sweep therefore never touches them. Tested forwards and backwards, with a backfill check on real rows.
120. **New env vars:** `CSAT_TIMEOUT_HOURS` (a positive number, so fractions like `0.5` work) and `TICKETS_LOCK_ON_CLOSE`. Both are in `.env.example`.

## Follow-up: silent summary, copy overrides, configurable embeds

**Process**

121. **This work is on the same branch as the CSAT change** and goes into PR #4: the session can only push to one designated branch, and #4 wasn't merged yet. PR #4 is retitled to cover both changes.

**Silent summary**

122. **The daily summary is sent with Discord's @silent flag** (`silent=True`). It appears in the staff channel but sends no push or desktop notifications. Nothing else is silent: #tickets alerts still ping their roles.

**Copy override file**

123. **`COPY_OVERRIDE_FILE` (default `copy/copy.local.toml`) sits on top of `COPY_FILE`:** defaults ← `copy.toml` ← `copy.local.toml`.
    - The merge is deep, key by key, including nested `[embeds.*]` sections: any key in the override wins, and anything missing falls back.
    - The file is gitignored and excluded from the Docker image (it's host-local). It's served through the existing read-only `./copy` directory mount, so no new mount is needed.
124. **Each file is validated on its own first, so errors name the file they're in.** The merged result is validated too.
    - Live reload watches both files' modification times. An invalid edit to either is logged and the previous text stays live.
    - Deleting the override falls back to `copy.toml`. The base file disappearing briefly (mid-save) is ignored.
125. **Missing files.** A missing override at the default path is normal and logged at info level. An explicitly set `COPY_OVERRIDE_FILE` that doesn't exist is fatal at startup, matching `COPY_FILE`.
126. **`copy/copy.local.toml.example` is generated** from the defaults (`python -m nephthys.core.copytext --example`), and a test keeps it in sync.
    - Every key is listed *commented out* with its default value, its description and its placeholders, grouped under live `[section]` headers.
    - Uncommenting a line overrides exactly that key. Copying the file as-is changes nothing, so defaults can still evolve.
127. **`--check` accepts several files** (base, then override) and validates each file and their merge.

**Configurable embeds**

128. **Every embed the bot sends has an `[embeds.<name>]` section** with `title`, `description`, `color` (hex, `""` = none), `footer` and `thumbnail_url` (`""`, or an http(s) URL). The sections are `sticky`, `tickets_panel`, `ticket_opener`, `ticket_closed`, `staff_card`, `daily_summary`, `stats`, `waiting`, `ai_shadow` and `ai_public`.
    - Titles, descriptions and footers are templates with per-key placeholders, e.g. `{question}` for staff cards, `{lines}` for `/waiting`, `{answer}` for AI, `{body}` and `{number}` for the opener.
    - **The defaults reproduce the previous look exactly** (same text, colours and footers; no thumbnails), and a test checks this.
    - All embeds are now built through one helper (`discord_ops/embeds.py`), which also enforces Discord's length limits.
129. **Some embeds need extra keys:**
    - Staff cards have one colour per status (`color_waiting`, `color_claimed`, `color_resolved`; gold, blurple and green as before) and a separate `private_title` for #tickets.
    - `/stats` has a `title_one_day`.
    - The sticky has a `description_no_faq`.
130. **The #tickets opener and closing message were plain text, not embeds.** To keep "defaults = current look", their sections have `enabled = false` by default; set `enabled = true` to send them as embeds.
    - In embed mode, "Ticket T-<n>" becomes the embed title and the welcome, picks and blurb become the description, with the buttons unchanged.
    - The first opener still pings the player through a mention-only content line, because mentions inside embeds don't ping. The line contains no link, so nothing can unfurl. Reposts don't ping.
    - The CSAT prompt stays text.
131. **Keys moved, so each text has one home:**
    - `[sticky] title/body/body_no_faq` → `[embeds.sticky]` (`[sticky]` is gone);
    - `[tickets] panel_title/panel_body` → `[embeds.tickets_panel]`;
    - `[card] title/private_title/footer` → `[embeds.staff_card]`;
    - `[summary] title/period` → `[embeds.daily_summary]`;
    - `[stats] stats_title*` → `[embeds.stats]`;
    - `[commands] waiting_title` → `[embeds.waiting]`;
    - `[ai] shadow_title/shadow_footer/public_title/public_footer` → `[embeds.ai_*]`.

    A file that still uses an old key fails with a message naming the new location, rather than a generic "extra inputs" error. An empty leftover `[sticky]` header is ignored.
132. **The every-placeholder stress test now runs twice:** with the opener and closing as text, and as embeds. Every declared placeholder in both modes is proven to be supplied by the code. The link-preview sweep still holds, because embed messages only ever carry mention-only content.
