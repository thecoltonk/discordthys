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

## Phase 3: Zammad two-way sync for #tickets (PRODUCT.md §2b)

PRODUCT.md's one-way "Zammad mirror" and its non-goal "replying from Zammad back into Discord" are replaced by the two-way design in §2b. Built and tested against **Zammad 7.2.3** running locally in Docker (the same image the docker-compose project uses), as well as an in-process fake.

**Things the real Zammad forced**

133. **The custom attribute is called `discord_uid`, not `discord_id`.** Zammad refuses the name: "Name can't be used because *_id and *_ids are not allowed". Agents see it labelled **Discord ID**. It's a text field on User, shown on view and edit.
134. **Applying a new attribute restarts Zammad.** `object_manager_attributes_execute_migrations` makes every Zammad process exit and restart (about a minute). `zammad-setup` only runs it when the attribute is new or pending, says so first, and waits up to 4 minutes for Zammad to come back.
135. **The bot's Zammad user needs group access, not just a role.** Without Full access to a group, Zammad answers 403 for tickets in it, even for admins. `zammad-setup` checks this per category group and names the group that's missing.
136. **Zammad only accepts a webhook address it can resolve** ("could not be resolved to a safe IP address"). So `zammad-setup` has to run while the bot container is up on Zammad's network: `docker compose exec bot …`, not `run`. The command prints that hint when it sees the error.
137. **Zammad never returns a webhook's signature token** (it reads back as `**********`), so setup can't compare it and always re-sends it.
138. **Logins like `discord:123` can't be searched for.** The search treats `:` as field syntax. Lookups search by the email address and then match the login exactly. The customer's Zammad ID is also stored on the bot's user row, so the lookup only happens once per player.
139. **A webhook is retried 5 times over about two minutes, then dropped** (read from Zammad's `TriggerWebhookJob`). That's why there's a reconcile job (entry 152).

**Zammad → Discord**

140. **One trigger, one webhook.** The trigger is limited to Discord tickets (customer login starts with `discord:`) and fires when:
     - an article is added that is public, sent by an agent, and not created by the bot's own Zammad user; or
     - the ticket's state or group changes.

     A lone internal note doesn't fire it. Tested live: an internal note saved *together with* a state change does arrive in the payload, so the trigger alone isn't enough (next entry).
141. **Internal notes are dropped in the bot as well.** An article is only queued for Discord if `internal` is exactly `false` and the sender is `Agent`. Anything else (missing, null, a string) is dropped. A test closes a ticket with an internal note attached and checks that its text appears nowhere in Discord.
142. **Three independent guards against echo loops:**
     - the trigger excludes articles created by the bot's Zammad user;
     - the bot ignores articles carrying its own marker (stored in the article's `message_id`, e.g. `<discord-msg-123@discord.invalid>`), articles created by its own user, and articles already in its message map;
     - on the Discord side, replies are posted by a webhook or by the bot itself, and the #tickets cog already ignores both kinds of message.
143. **The signature is `X-Hub-Signature: sha1=<hex HMAC-SHA1 of the raw body>`** (read from Zammad's source and confirmed on captured deliveries). It's checked in constant time before the body is parsed. Unsigned or wrong calls get 401; while the sync is off the endpoint answers 404. SHA-1 is Zammad's choice and can't be changed from outside; as an HMAC it's still sound.
144. **The webhook endpoint lives on the existing health server** (port 3000, `/zammad/webhook`), so there's still one process and one port. That port becomes reachable from Zammad's Docker network, including `/health` and `/metrics`, which hold nothing sensitive. It's not published beyond `127.0.0.1` on the host.
145. **State changes are re-read, not trusted.** A webhook only queues "sync this ticket"; the worker then reads the ticket's current state and group from Zammad and brings Discord in line. Late, repeated or out-of-order deliveries all end in the same place, and the bot's own close echoing back is a no-op.
146. **Which Zammad states count as closed:** `closed` and `merged`. Everything else (new, open, pending reminder, pending close) is open.
147. **A close or re-open made in Discord wins until it has reached Zammad.** While it's still queued, Zammad's (older) state is ignored, so a Discord close during a Zammad outage isn't undone by the reconcile job.
148. **Replies are plain text.** Zammad's HTML is converted: bold, italic, lists, quotes, and links as `text (url)`. Long replies are split at paragraph or line breaks into parts of at most 1900 characters; progress is saved after each part so a crash can't re-post one.
149. **Do webhooks work in private threads?** Discord's API docs (commit `36adecb`, 10 Nov 2026) say: "Webhooks can send messages to threads by using the `thread_id` query parameter" and "Send a message to the specified thread within a webhook's channel. The thread will automatically be unarchived." They make no exception for private threads and don't mention them either way. **Not tested against live Discord** (no access from the build environment). The fallback therefore covers every failure: if the webhook can't be created or a post fails, the bot posts `**<name> (Support):** <text>` itself and writes one line to the log channel (at most one per 30 minutes). The bot unarchives an archived thread before posting, because the docs don't promise that a webhook can unarchive a *locked* one.
150. **Webhook names.** Discord rejects usernames containing "discord" or "clyde" and a few symbols; those parts are removed, the name is cut to 80 characters, and "Support" is used if nothing is left.
151. **Avatars: a Cloudflare Access bypass for `/api/v1/users/image/*` only.**
     - Zammad serves that path without a login by design (checked in its source and on the running instance), and the address is a hash of the picture, not a user ID.
     - Rejected: re-uploading avatars to Discord and using the CDN link (those links expire, which would break avatars on old messages); a Cloudflare service token (Discord's image fetcher can't send headers); exposing the bot as an image proxy (a new public endpoint for less benefit).
     - Without the bypass, or for agents with no picture, `ZAMMAD_DEFAULT_AVATAR_URL` is used.
     - Agents are never linked to Discord accounts: only the name and picture travel.
152. **Reconcile job.** Every `ZAMMAD_RECONCILE_MINUTES` (default 5) the bot reads its open tickets, and those closed in the last 2 days, from Zammad and queues any public agent reply or state/group difference it hasn't seen. This covers webhooks lost while the bot was down. It costs two small API calls per such ticket per run.
153. **First public reply = claim.** The ticket gets `assigned_name` (the agent's Zammad name) instead of `assigned_to`, because there's no Discord user to point at. The card shows "Claimed by <name> (Zammad)". The ticket also stops counting as waiting.
     - A later reply from Discord staff doesn't steal the claim, and the other way round.
     - `/stats` counts such tickets as handled by staff, but the "top helpers" list only has Discord users.
154. **Closing from Zammad shows the agent's name in bold** in the closing message, where a Discord closer would be mentioned. The card shows "Resolved by <name> (Zammad)".
155. **A reply on a closed, archived ticket** is still delivered. The thread is unarchived for it and archived again afterwards; it stays locked.

**Discord → Zammad**

156. **Title:** `T-<n> <category>`, with the subcategory in brackets when there is one, e.g. `T-27 Server Issue (Urgent)`. The group comes from the top-level category.
157. **Group and priority mapping live in `copy/ticket_categories.toml`:** `zammad_group` per category (default: the label) and a `[zammad.priorities]` table (default `1 low`, `2 normal`, `3 high`, Zammad's stock names). The shipped file now lists them explicitly.
158. **The first article is a short header** written as the player: who opened it, category, priority, a link to the thread, the Discord ID, and for escalated tickets the original public question with a link to the public thread.
159. **Messages typed before a category is picked are kept** and mirrored, in order, once the Zammad ticket exists. If the ticket is closed without ever reaching a category, they're discarded, and no Zammad ticket is created.
160. **Staff messages from Discord are articles with sender Agent**, created by the bot's Zammad user, with the real name on the first line ("Sam (Staff via Discord)"). Player messages have sender Customer and the player as their origin.
161. **Attachments** up to `ZAMMAD_ATTACHMENT_MAX_MB` (default 10, Discord's own upload limit for bots) are copied in both directions. Larger or unavailable files are named in the text instead of failing the message. Discord attachment links expire, so a failed download is retried once with a fresh link from Discord.
162. **Close and re-open leave an internal note** in Zammad saying who did it in Discord. The CSAT rating is an internal note too.
163. **Not synced:** edits and deletions of messages (either direction), reactions, and the bot's own messages in the thread (opener, closing text, CSAT prompt, `/faq`, `/ip`).

**No email**

164. **Three layers keep email away from Discord customers:**
     - the bot only ever creates `note` articles, which Zammad doesn't send;
     - the customer's address is `<id>@discord.invalid`, a reserved domain that can't receive mail;
     - `zammad-setup` adds `.+@discord\.invalid` to Zammad's `send_no_auto_response_reg_exp` setting, which Zammad's trigger email code checks for every recipient (read from its source). It's a setting changed over the REST API, not a source change.

     Zammad's stock "auto reply" triggers also don't match: they need an email, phone or web article, not a note. Setup lists the active email triggers for information.
165. **What can't be prevented from outside Zammad:** an agent deliberately choosing the *email* reply type on a Discord ticket. The mail would go to the `.invalid` address and bounce. DEPLOY.md tells agents to use public notes.

**Reliability**

166. **One outbox table for both directions**, worked by one background task. Rows are processed per ticket and direction, oldest first, one at a time.
     - A failed row is retried after 5s, 10s, 20s … up to 15 minutes (with jitter) and holds back the rows behind it.
     - Errors that retrying can't fix (most 4xx) are given up on after 3 attempts; anything else after `ZAMMAD_OUTBOX_MAX_ATTEMPTS` (default 25, about 5 hours). A given-up row is stepped over so later messages still arrive.
     - `nephthys zammad-retry` puts given-up rows back in the queue.
167. **Idempotency.**
     - Enqueueing uses a unique key per event (Discord message ID, Zammad article ID), so duplicates are dropped at the door.
     - If Zammad performs a write but the answer is lost, the retry first looks for what's already there: the customer by login, the ticket by title and customer, the article by its marker.
     - Mirrored IDs are stored in `zammad_message_map`.
168. **Discord flows only write to the outbox.** They never call Zammad, and a failure to enqueue is logged, not raised. A test runs the whole ticket flow with Zammad down.
169. **Reporting.** `/stats` shows "Zammad sync: ✅ in sync" or the queue length, the age of the oldest item and the failed count. The log channel gets:
     - one message when items are given up on (pinging the maintainer);
     - one when the backlog is older than 10 minutes, at most every 30 minutes;
     - one when it has caught up.

     Messages waiting for a category pick aren't counted as backlog.
170. **Outbox rows are kept.** Finished rows hold the dedupe keys that make reconciliation safe, and the volume is tiny. Nothing prunes them yet.

**Config and setup**

171. **On/off.** The sync is on only when `ZAMMAD_URL` *and* `ZAMMAD_TOKEN` are set. `http://zammad-nginx:8080` is the default in `.env.example` and `docker-compose.zammad.yml`, not in code, so the token is the practical switch. With both set, `ZAMMAD_WEBHOOK_SECRET` (16+ characters) and `TICKETS_CHANNEL_ID` are required at startup.
172. **The Docker network join is a separate compose file** (`docker-compose.zammad.yml`). An external network that doesn't exist makes `docker compose up` fail, which would break deployments that don't use Zammad if it were in the main file.
173. **Two tokens.** The bot runs with a token that has only `ticket.agent`. `zammad-setup` uses `ZAMMAD_SETUP_TOKEN` (admin.object, admin.webhook, admin.trigger, admin.group, admin.channel_email) when set, so the admin token can be deleted after setup. Both sets were checked on the real instance.
174. **Setup checks groups but doesn't create them.** Groups carry access rules and email settings that are the admin's call.
175. **New bot permission: Manage Webhooks** in #tickets. Without it the fallback message is used.
176. **Migration `cloudsail_005`:** `zammad_user_id` on users; `zammad_ticket_id` (unique), `zammad_ticket_number`, `zammad_group`, `assigned_name`, `closed_by_name` on tickets; tables `zammad_outbox` and `zammad_message_map`. Tested forwards and backwards.
177. **New copy keys** (`[zammad]`, plus a few in `[card]` and `[stats]`) cover the agent name, the fallback message, the skipped-attachment note, the card's Zammad field and the `/stats` line. Notes written *into Zammad* are for agents and aren't in copy.

**Tests**

178. **The same integration tests run against two backends:** an in-process fake of Zammad's API (always, so CI needs nothing extra) and a real Zammad when `ZAMMAD_TEST_URL` is set. The fake copies behaviour observed on the real one (response shapes, the `_id` name rule, masked tokens, trigger conditions, signing, five webhook attempts, the restart after a migration, the unresolvable-host error). Running both keeps the fake honest: all of them passed against Zammad 7.2.3. Tests that need to break Zammad on purpose use the fake only.

## Follow-up: staff channel, #tickets card and ticket UI polish, Zammad profiles

One commit per numbered request, all in one PR. Entries 48–53 (community staff cards and the category select) are superseded by 179–184.

**1. Staff channel = #tickets only**

179. **#community-help tickets no longer get a staff card**, and the card's category select is gone (the `cs:cat:` component isn't registered any more). `STAFF_CHANNEL_ID` now carries #tickets alert cards, the daily summary and, only when AI is on in shadow mode, AI suggestions.
180. **AI shadow suggestions still go to the staff channel.** They used to be replies to the community card; now they're standalone messages whose title links to the thread. They only exist when `AI_API_KEY` is set and `AI_ANSWER_MODE=shadow`, there's nowhere else to rate them, and dropping them would silently break shadow mode. Easy to move to a separate channel later if they turn out to be noise.
181. **Community categories now come only from the AI** (when it's on). Staff can no longer pick one, since the select lived on the card. `TICKET_CATEGORIES` and "Opened by category" in `/stats` and the summary are unchanged.
182. **Cards already posted for community tickets are left alone and keep working**: they're still re-rendered on claim/resolve/re-open (now with just the thread link, no select) and deleted with their ticket. Old cards on tickets that never change again keep their dead select; clicking it does nothing ("interaction failed"). Not worth a migration.
183. **New "Claimed" count** in `/stats` and the daily summary, for both channels: tickets whose first staff claim (Discord reply or Zammad agent reply) happened in the window. With no cards, this is how staff see claims at a glance. Copy key `[stats] field_claimed`.
184. **Removed copy keys are ignored with a warning, not rejected.** `[card] category_placeholder` no longer exists; a `copy.local.toml` that still sets it would otherwise stop the bot at startup (unknown keys are fatal). `REMOVED_KEYS` in `copytext.py` lists such keys.

**2. #tickets staff card**

185. **The Zammad text field is replaced by link buttons:** **Open in Zammad** first, then **Open in Discord** (the old "Open thread" button, relabelled; the key is still `[card] open_thread_button`). Discord draws every link button in the same grey: a link button can't be blurple, so "primary" here means first in the row.
186. **When the Zammad button shows:** the sync is on, the ticket has a Zammad ID, and `ZAMMAD_PUBLIC_URL` is set (the bot's own `ZAMMAD_URL` is a Docker-network address browsers can't open). Otherwise only Open in Discord. The card is re-rendered when the mirror lands (it already was, for the old field), so the button appears then.
187. **The card no longer shows the Zammad group or ticket number.** The button replaces both; the group is still stored (`ticket.zammad_group`) and a group change still refreshes the card. `[card] field_zammad`, `zammad_value`, `zammad_value_no_link` and `zammad_group` are removed (ignored with a warning if set, per 184).
188. **Footer default is `Ticket {ref}`**: `Ticket T-29` on #tickets cards (`#<id>` only on old community cards). `{id}` stays allowed, so a customised footer keeps working.

**3. Discord ticket UI polish**

189. **Every button's label and emoji come from copy.** Each button key `<x>` has a sibling `<x>_emoji` (e.g. `[community] resolve_button_emoji`, `[tickets] close_button_emoji`, `[card] open_zammad_button_emoji`, `[ai] helpful_button_emoji`). `""` means no emoji; a label can be `""` too if the button has an emoji. Supersedes the "emojis inside buttons" exclusion in entry 73. #tickets category and subcategory buttons keep their emoji in `ticket_categories.toml`, where they already were.
190. **Emoji values are checked when copy loads**: a Unicode emoji, a custom one written `<:name:id>` / `<a:name:id>`, or empty. Anything with letters, spaces or colons (`lock`, `:lock:`) is rejected with the key named, as is a button with both label and emoji empty, because Discord would reject the whole message at send time otherwise. Shortcodes like `:lock:` aren't translated: Discord's API doesn't accept them.
191. **Close Ticket has no emoji by default** (was 🔒).
192. **CSAT is one row of five grey (secondary) buttons**, emoji only by default: 😡 🙁 😐 🙂 🤩 = 1–5 (`[tickets] csat_button_<n>` and `csat_button_<n>_emoji`). The prompt is shortened to "how did we do?". It was already its own message after the closing message; that's now tested explicitly.
193. **After a rating the same row stays, disabled**, and the text becomes `csat_thanks` = "thanks! you rated {rating}/5 {emoji}" (`{emoji}` is that rating's button emoji, trimmed if empty). Keeping the row shows what was offered; all buttons stay grey rather than highlighting the pick, as asked ("no blurple"). If Discord refuses the edit (thread already archived), the thanks is sent privately as before.

**4. Staff refused "Mark as resolved" after an auto-reopen**

194. **What the test found.** The ticket state machine handles resolve → poster reply (auto-reopen) → staff resolve correctly; that sequence already worked with the test stand-ins. The refusal ("only the person who asked or a staff member can do that") can only come from the clicker not being recognised as staff. Staff detection read `Member.roles`, which discord.py rebuilds from the bot's guild role cache and which **silently drops any role the cache doesn't hold** (checked in discord.py 2.7.1's source and with real `Member` objects). The role IDs Discord sends with every interaction and message (`Member._roles`) don't depend on that cache.
195. **Fix: staff detection uses the payload's role IDs** (plus any cached roles). The new test drives the reported sequence with real `discord.Member` objects, with the second click arriving while the Staff role is missing from the cache; it failed with exactly the reported message before the fix. `_roles` is a private discord.py attribute, so it's read defensively and `roles` is still used too.
196. **Why the cache was missing the role on the live bot is not proven.** The likeliest cause is a gateway reconnect: discord.py clears its guild cache on a fresh session, and interactions arriving before the guild's data comes back are given an empty placeholder guild with no roles. Both clicks in the report came soon after each other, which fits. Every refused resolve/re-open now logs the clicker's role IDs, the object type and the configured Staff role, so if it ever happens again the log says why.

**5. Webhook display name**

197. **`[zammad] agent_name` now defaults to `{name} (Support)`** (was `{name}`), `{name}` being the agent's Zammad display name. It's the webhook username on relayed replies. The key already existed; only its default and its handling of long names changed. The plain-message fallback (`fallback_reply`) already said "(Support)" and is unchanged.
198. **The 80-character limit keeps the template's own text.** If the filled-in template is too long, `{name}` alone is shortened (ending in "…") so a suffix like "(Support)" survives; only if the template itself is longer than 80 characters is the whole thing cut. Words Discord refuses in usernames ("discord", "clyde", `@ # : `` ` ``) are still removed (entry 150).
199. **An agent with no usable name is shown as just `unknown_agent`** ("Support"), not "Support (Support)". The claim still records "Support" as the claimer, as before.

**6. Log channel**

200. **`LOG_STARTUP_NOTICE` (default `false`)** controls the "🟢 Cloudsail Support is online" message. When on, it's sent once per process (not on reconnects), @silent.
201. **Only real errors notify.** `send_heartbeat` takes `error=True` for failures that need a person; everything else goes out with Discord's @silent flag (in the channel, no push/desktop notification). `ping=True` (mentions `MAINTAINER_ID`) implies an error.
     - **Errors:** unhandled event errors, failed background tasks, a failing button, a stale auto-close that failed, the Zammad sync giving up on items, and a ticket thread that couldn't be created (either channel) or a player who couldn't be added to theirs. Those last three were ⚠️ before; they're now ❌ because a player is left without a ticket.
     - **Silent:** the startup notice, the Zammad backlog warning, the webhook-fallback note (the reply was still delivered), and "caught up".
202. **Rate limiting is unchanged**: `ZammadSync.alert` still allows one message per topic per 30 minutes, and it now passes `error` through.

**7. Copy directory mount and reload logging**

203. **The compose file already mounted `./copy` as a directory** (entry 74); no single-file mount of any copy file existed in the repo. It stays that way, with a comment saying why, and a test fails if `docker-compose.yml` ever mounts something under `/app/copy` other than the whole directory. If a deployment on the host uses its own compose override with single-file mounts, that override is the problem; the repo can't see it. Checked in a real container: with an editor-style save (write a temp file, rename it over the original), the directory mount picked up the change and logged it, and the bad edit before it was logged as rejected with the exact key and reason. A single-file mount of the same file kept serving the old text.
204. **Change detection compares (mtime in ns, size, inode)** instead of the mtime alone. An editor that writes a new file and renames it over the old one changes the inode even when the timestamp and size don't, which the old check could miss.
205. **The bot checks `copy/` every 5 seconds in the background** (`copy-watcher` task), instead of only when some text is next needed. So an edit is applied, and logged, within seconds even when the bot is idle. Every reload logs `Copy reloaded (<files> changed); now using <files>` at info; every rejected edit logs `Copy edit rejected (<files> changed); keeping the previous text: <errors>` at error, once per edit. They go to the container log (`docker compose logs bot`), not the log channel.
206. **`ticket_categories.toml` is still read at startup only** (entry 95): its keys are baked into live buttons.

**8. Discord avatars and names on Zammad customers**

207. **What Zammad 7.2 offers, checked against a real Zammad 7.2.3 in Docker** (the `zammad/zammad-docker-compose:7.2.3-0000` image) and in its source:
     - `POST /api/v1/users/avatar` always sets the **current** user's avatar (`Service::Avatar::Add.with_current_user`). There's no endpoint for someone else's.
     - `image_source` on a user is stored but never fetched in 7.2; only importers use it. Setting it shows nothing.
     - The supported way is that same call made **as the customer**, with Zammad's `From` header (`X-On-Behalf-Of` is its deprecated name). Zammad allows `From` only when the token's user has **`admin.user`** (`SessionsPolicy#impersonate?`).
     - Results, live: the everyday `ticket.agent` token → 403 "Current user has no permission to use 'From'/'X-On-Behalf-Of'!". A token of a separate user whose **only** role grants `admin.user` (no Agent role, no groups) → 200; the customer's `image` points at the new picture and `/api/v1/users/image/<hash>` serves it.
     - **Zammad checks the user's roles, not the token's ticked permissions**: once the bot user had a role with `admin.user`, even its `ticket.agent`-only token could act `From` anyone. That's why the extra permission goes on a separate user (next entry).
     - Renaming a customer (`PUT /users/:id`) works with `ticket.agent`. Zammad splits "First Last" given as the first name into first and last name; the full name is unchanged.
208. **Minimum extra permission: `admin.user`, on a separate "Discord Avatars" user, via the optional `ZAMMAD_AVATAR_TOKEN`.** It's used for nothing but the avatar upload. Unset = names synced, avatars skipped. Not full `admin`, and not added to the bot user (which would hand impersonation to the everyday token). DEPLOY.md §7.1 step 5 lists the steps and warns that `admin.user` can act as any user.
209. **Rejected: a `ticket.agent`-only workaround.** Upload the picture as the bot's own avatar, point the customer's `image` at its hash (Zammad only checks that the hash exists), then restore the bot's picture. It works live, but it isn't a supported use, it briefly changes the bot's own avatar, the pictures pile up in the bot's avatar list, and deleting them there would break customers.
210. **Where the picture comes from:** Discord's `display_avatar` (the server avatar if set, else the user's, else Discord's default), as a 256 px PNG (animated ones give their first frame). It's captured from every message and button click the bot already sees, and stored as `app_user.avatar_key`/`avatar_url`; no new Discord intent or API call. CDN avatar links are content-addressed and don't expire. For `/escalate` the poster's last-seen avatar is used. An action without avatar info never erases it.
211. **When it's pushed:** on the outbox worker, never in a Discord flow. That happens when the customer is created (with the Zammad ticket) and whenever the player's own message is mirrored.
     - Name: whenever it differs from what the bot last set (`app_user.zammad_name`). A cheap PUT, no limit.
     - Avatar: only when Discord's avatar key differs from the one last uploaded, and at most once per 24 hours per player. The attempt time is recorded before trying, so a failing download or upload also waits a day.
     - The bot downloads the image (≤ 5 MB, `image/*` only) and uploads the bytes, so Zammad needs no internet access.
212. **Profile updates are best effort.** Any failure is logged and never fails or delays the ticket/article row it rode along with. A 401/403 from the avatar call turns avatars off until the next restart and posts one @silent log-channel note naming `admin.user` (rate-limited per entry 202).
213. **Migration `cloudsail_006`** adds `avatar_key`, `avatar_url`, `zammad_name`, `zammad_avatar_key`, `zammad_avatar_at` to `app_user`. Tested forwards and backwards. Existing customers get their name re-sent once (their `zammad_name` starts empty) and their avatar on their next message.
214. **Tests**: the new flow tests run against the fake and the real Zammad: avatar on creation, name change applied at once, avatar change held for a day then applied, no re-upload when unchanged, and names-only without the token. The "token without `admin.user`" test is fake-only, because the suite would need a second real Zammad user and token; that case was checked live by hand (entry 207). The fake now implements `From` with Zammad's exact refusal and `POST /users/avatar`.

## Cleanup: no CSAT, no re-opening #tickets, no role pings

Made without review (the maintainer was asleep), as PR 1 of two. Three removals, each for a stated reason:

- **CSAT.** About 20 tickets in 9 months is too few for ratings to mean anything, and Discord disables buttons in a locked thread, so the only way to collect a rating was to keep closed threads open (entries 113–120).
- **Re-opening #tickets.** It's clunky in Discord: unlock, unarchive, re-add the player, and hope they notice an old thread. Closed private tickets stay closed and the player opens a new ticket.
- **Role pings.** Whole-role pings on every new ticket are annoying.

What this supersedes: entries 101–103, 113–120, 192 and 193 (CSAT and the wait before archiving); the CSAT parts of 97, 110, 112, 130 and 162; entries 104, 116 and 118, the `/reopen` part of 105 and the re-open parts of 147 and 162 (re-opening private tickets); entry 98, the roles bullet of 95 and the last sentence of 122 (role pings); and entry 151 (the Access bypass for avatars). Entry 100 already said private tickets never auto-reopen; that still holds.

**Process**

215. **Two PRs, the second stacked on the first.** PR 1 is this cleanup on `claude/cleanup-csat-reopening-z0zi6r`. PR 2 (the Jev FAQ matcher) is a new branch cut from PR 1's head and opened against PR 1's branch, so its diff shows only its own changes; once PR 1 merges it should be retargeted to `main`. The brief asked for "branch off PR 1", which is taken as permission to push a second branch.
216. **The Zammad-login docs change rides in PR 1.** The brief didn't say which PR; it's cleanup of a removed assumption, and it touches the same DEPLOY.md sections.

**CSAT removed**

217. **Close Ticket and `/resolve` post the closing message, then lock and archive the thread in one edit,** the behaviour before entry 113. Nothing is posted after the closing message. The same flow runs for a close made in Zammad.
218. **`archived_at` is dropped too, not kept.** The brief said to keep it only if the Zammad sync needs it. Its one reader there decided whether to put a thread away again after an agent replied on a closed ticket; now that closing always archives, "closed" already means "archived", so that check uses the ticket's status. No reader is left.
219. **Migration `cloudsail_007`** drops `csat_rating`, `csat_at`, `archived_at` and the sweep's partial index. Stored ratings are deleted with the columns (there were a handful at most, and nothing shows them any more).
     - Backwards re-creates the columns (with the 1–5 CHECK) and the index, empty, and backfills `archived_at` from `closed_at` for closed private tickets exactly as `cloudsail_004` did, so the old sweep would leave them alone.
     - Tested both ways with rows in the table, including dropping a column that holds data, and run for real through `piccolo migrations forwards` / `backwards` / `forwards` on a scratch database. The older migrations aren't edited.
220. **Tickets that were closed and still waiting for a rating at upgrade time** are locked but not archived, and nothing archives them now (the sweep is gone, and after the migration the bot can't tell which they are). Discord archives them itself after `THREAD_AUTO_ARCHIVE_MINUTES` of inactivity (a week by default). At this volume that's zero or one thread; it wasn't worth a one-off job. A moderator can archive it by hand.
221. **Old rating buttons must not error.** Prompts posted before this change still sit in old threads, and their buttons have the custom ID `cs:csat:<ticket>:<rating>`. That ID stays registered on a stub (`RetiredCSATButton`): a click gets a private reply (`[tickets] csat_retired`, "we're no longer collecting ratings here — thanks anyway!") and records nothing. Without it Discord would show "This interaction failed". The bot never posts that button again, and old prompts aren't edited (that would mean unarchiving every old thread).
     - If Discord refuses the private reply, the error is swallowed and logged.
222. **`CSAT_TIMEOUT_HOURS` and `TICKETS_LOCK_ON_CLOSE` are never read,** so a `.env` that still has them starts fine whatever they contain (even values that used to be rejected). Startup logs one info line per leftover variable saying it's ignored. The same goes for `STORE_ROLE_ID` and `MOD_ROLE_ID` (entry 232). The list is `REMOVED_ENV` in `config.py`.
223. **Removed copy keys are ignored with a warning** (the rule from entry 184), so an old `copy.local.toml` doesn't stop startup: `[tickets] csat_prompt`, `csat_button_1..5` and their `_emoji`, `csat_thanks`, `csat_not_opener`, `csat_already`, `reopened`, `alert_ping`; `[card] field_csat`, `csat_value`; `[stats] field_csat`, `csat_value`, `csat_none`. New keys: `[tickets] csat_retired` and `no_reopen`.
224. **Also gone:** the CSAT field on the staff card, the CSAT average in `/stats` and the daily summary, the CSAT internal note in Zammad, `tasks/archive_closed.py` and its scheduler job, `awaiting_archive`, `rate_csat`, and `tests/flows/test_csat_archive.py`.

**No re-opening #tickets**

225. **The rule lives in the core state machine.** `tickets.reopen` raises `PrivateTicketReopenError` for a private ticket whoever asks (staff, the opener, or a sync), so no entry point can get around it. #community-help re-opening (the button, `/reopen`, the poster replying) is unchanged.
226. **`/reopen` stays registered, and refuses privately inside a #tickets thread** with `[tickets] no_reopen` ("closed tickets can't be re-opened. if there's more to sort out, the player can open a new ticket in #tickets."). Discord can't limit a slash command to threads of one channel, and #community-help still needs it. Its description now says "#community-help". The refusal is sent before anything else happens: nothing is posted, the thread isn't touched.
227. **The player is never stuck.** A closed ticket doesn't count towards `TICKETS_MAX_OPEN_PER_USER`, so they can always open a new one. A test checks this.
228. **Tickets that were re-opened before this change** keep their history: `reopened_at` stays and the card still says "(re-opened)". If such a ticket is open now, it closes normally and then stays closed.

**Re-opened in Zammad**

229. **What happened before:** setting a closed Discord ticket to any non-closed state in Zammad re-opened it in Discord (unlock, unarchive, re-add the player, post a note). That contradicts "closed stays closed".
230. **Now Zammad is brought back in line.** When the sync sees a ticket that's closed in Discord but not in Zammad, it changes nothing in Discord, sets the Zammad ticket back to closed, and adds one internal note: "This ticket is closed in Discord, and closed Discord tickets can't be re-opened, so it has been closed here again. Its Discord thread is locked and archived: the player can't see new replies there. If they need more help, ask them to open a new ticket in #tickets."
     - **Rejected: ignoring it.** The two systems would disagree for good, an agent would believe they'd re-opened something the player can't see, and the reconcile job would re-queue the difference every 5 minutes.
     - **Rejected: re-opening in Discord after all,** for the reasons at the top of this section.
     - "Not closed" means any state other than `closed` or `merged` (entry 146), so a pending state on a closed Discord ticket is undone too.
     - It settles in one round: the bot's own close echoes back as a no-op. A test checks there's no ping-pong and exactly one note.
231. **Details.**
     - An agent's public reply on a closed ticket is still posted in the thread (entry 155), which is archived again afterwards. If the agent also re-opened the ticket to write it, the reply is delivered and Zammad goes back to closed.
     - The re-close is an ordinary outbox row, so it's retried if Zammad is down.
     - The reconcile job only looks at tickets closed in the last 2 days (entry 152). A re-open of an older ticket whose webhook was also lost stays open in Zammad until its next event. Accepted.
     - Outbox rows of kind `reopen` or `note` (CSAT) left over from before the upgrade complete without doing anything, rather than failing and alerting the maintainer.

**No role pings**

232. **Staff alert cards are posted with no content line and no allowed mentions:** no category role, no Staff role on High priority, no fallback. Removed with it: the `[tickets] alert_ping` copy key, `ping_roles` on the send helper (which can no longer allow a role mention at all), `STORE_ROLE_ID`, `MOD_ROLE_ID`, `Settings.role_id_for`, and `role` in `copy/ticket_categories.toml`.
     - Nothing else used any of those. `STAFF_ROLE_ID` stays: it's how the bot knows who is staff.
     - A categories file that still has `role` loads fine; the key is dropped with a warning. Unknown keys in that file are otherwise fatal at startup, and the file lives on the host.
     - The first #tickets opener still pings the *player* (entry 92). Log-channel errors still ping `MAINTAINER_ID`, a user. Neither is a role.
233. **Staff who want a notification per ticket** set the staff channel to "All Messages". DEPLOY.md says so, and no longer asks for mentionable roles or "Mention All Roles".

**Zammad login (docs only)**

234. **`support.cloudsail.uk` is no longer behind a self-hosted Cloudflare Access application; Zammad's own Cloudflare OIDC login is the only gate.** Supersedes entry 151.
     - DEPLOY.md §7.5 is now "nothing to set up": Zammad serves `/api/v1/users/image/<hash>` without a login by design, so Discord can fetch agent avatars directly. The old bypass application does nothing and the guide says to delete it.
     - §7.5 also states what being public means: API tokens now work from anywhere rather than only behind Access, so the existing advice (delete the setup token, keep the avatar token on its own user) matters more. That's a consequence worth writing down, not a new requirement.
     - The Worker's logout rewrite stays: Zammad's OIDC logout still goes to Access's logout address, which ends the session for every Access app. No Worker code changed; `zammad-skin/README.md` and its DECISIONS (S36) explain why it's still there.
     - No bot code depended on the gate. The bot reaches Zammad over the Docker network.

## Jev FAQ matcher (replaces the generative assistant)

PR 2 of two, stacked on the cleanup above. Made without review. Supersedes entries 59–70 (the generative assistant and its gates), the AI parts of 57, 65–68, 71, 128, 131 and 180, and entry 181 where it says "the AI" (categories now come from Jev).

**Why**

235. **No model-written text ever reaches a player.** A previous Cloudsail AI bot was abused into producing NSFW output. The old assistant here tried to make that unlikely (a strict prompt, confidence and source gates, entry 64). The new one makes it impossible: Jev, TypeSafe's decision model, never writes anything. It's shown a message and a list of options, and returns the name of one option and a confidence. The bot then posts text that a human wrote in `kb/`, word for word, or nothing.
236. **How that's enforced, not just intended.**
     - The client keeps only `(choice, confidence)` from each answer, and only if the choice is one of the options it offered and the confidence is a number from 0 to 1. Everything else in the response is discarded before any other code sees it.
     - The flow then looks the choice up among the current FAQ entries (or the configured categories) again, rather than trusting the client.
     - The generative code is deleted, not disabled: the prompts, the OpenAI client, the `openai` dependency, the answer parser, the `ai_suggestion` table and its store.
     - A test drives both surfaces in both modes with a hostile "model" and asserts that every message sent outside the staff channel is one of the pre-written strings. Another asserts the old modules and the dependency are gone.

**Access**

237. **`POST {JEV_BASE_URL}/systemone`, default base `https://ai.hackclub.com/proxy/v1/jev`, model `jev-latest`, `Authorization: Bearer <AI_API_KEY>`.** The key is the existing Hack Club AI one, and it's the only setting needed to turn this on.
238. **The request and response shapes come from upstream Nephthys's own Jev client** (commit `bfffb55`, "Category tags v2 … using a decision model (Jev)"), because the endpoint couldn't be reached from the build environment (the sandbox proxy refuses it):
     - request `{"model", "state", "questions": {name: {"type": "choice", "instructions", "criteria": {option: description}}}}`;
     - response `{"answers": {name: {"type": "choice", "choice", "confidence", "probabilities"?}}}`.

     **Not verified against the live endpoint.** If the real shape differs, every answer is dropped as unusable and the bot simply stays silent (and logs it), so the failure is safe. An answer with no `confidence` falls back to `probabilities[choice]`; with neither, it's unusable.
239. **One call per message, every question in it.** A new ticket sends `faq` and `category` together; a watched-channel message sends `faq` and `kind`. A question that isn't needed (no entries, categorising off) is left out, and with nothing to ask no request is made.
240. **New variables `JEV_BASE_URL` and `JEV_MODEL` instead of reusing `AI_BASE_URL` and `AI_MODEL`.** An existing `.env` has `AI_BASE_URL=https://ai.hackclub.com/proxy/v1` (the chat endpoint) and a chat model name; reusing them would point Jev at the wrong place. The old four (`AI_MODEL`, `AI_BASE_URL`, `AI_ANSWER_MODE`, `AI_ANSWER_MIN_CONFIDENCE`) are ignored with a startup notice (entry 222's mechanism). `AI_API_KEY`, `AI_CATEGORIZE`, `AI_TIMEOUT_SECONDS` and `KB_DIR` keep their names and meaning.
241. **An old `AI_ANSWER_MODE=public` does not carry over.** The matcher starts in shadow until `FAQ_MODE=public` is set, as the brief asks. A test pins this.
242. **aiohttp, not a new HTTP library.** It's already a dependency (the health server and the Zammad client use it). `openai` is removed from `pyproject.toml` and the lock file.
243. **What's sent:** the message text (at most 2000 characters) and which kind of channel it's from. No usernames, IDs or thread history.
244. **A failed request is never retried** and never surfaces to players or the log channel: the bot says nothing and logs a warning. A ticket is created, auto-replied and reacted to before Jev is asked, in a background task (entry 60's rule, kept).

**FAQ format**

245. **One file per entry in `kb/`:** a small header between `---` lines with `id` and `description`, then the answer.
     - `id`: 1–40 characters of `a-z`, `0-9`, `-`, `_`. `other` is reserved for "no match".
     - `description` (at most 500 characters) is what Jev matches on. Players never see it.
     - The answer is everything after the header, posted word for word (only the blank lines around it are dropped). At most 1800 characters, which leaves room under Discord's 2000 for the pointer added in watched channels. Braces are plain text; nothing is substituted.
     - The header is parsed by hand (`key: value` lines). No YAML dependency for two fields.
246. **Edits apply live, like `copy/`.** The folder's files are checked (name, mtime, size, inode, entry 204's rule) whenever entries are needed, and re-read only when something changed.
     - A file that isn't a valid entry is skipped and logged once per change (`FAQ entry skipped: <file>: <reason>`); the other entries keep working. A duplicate `id` skips the later file. This differs from `copy/` (where a bad edit keeps the previous text) on purpose: there is no "previous text" worth keeping for one answer, and silently serving a stale answer is worse than not answering.
     - `python -m nephthys.ai.faq <dir>` checks a folder without the bot.
     - At most 50 entries.
247. **`draft: true` (optional) keeps an entry out of matching.** It isn't in the brief. The starter `kb/server-ip.md` has a placeholder address (`play.example.invalid`), and without this, flipping `FAQ_MODE=public` would post a made-up address to players. As a draft it's validated but never offered to Jev. Removing one line makes it live.
248. **The old `kb/faq.md`, `ip.md` and `rules.md` are deleted.** They were source documents for the generative assistant, full of `TODO`s, and aren't FAQ entries. `kb/README.md` now documents the format and is the one file that isn't an entry.

**#community-help**

249. **On each new ticket:** if Jev's top choice isn't `other`, is a current non-draft entry, and its confidence is at least `FAQ_THRESHOLD` (default 0.9, inclusive), the answer is posted in the thread as a plain message with the **Mark as resolved** button (the same button as the auto-reply). Otherwise nothing is said: no "I couldn't find an answer", no fallback.
     - It's plain text, not an embed, so it reads like a staff reply and is literally the entry's text. Links don't unfurl (entry 81).
     - There's no "This fixed it / I still need a human" pair any more. Mark as resolved covers the first; for the second the player just keeps talking.
     - The answer never claims the ticket and never clears "waiting" (entry 68's rule, kept and tested).
     - A ticket resolved while Jev was deciding gets no answer.
250. **Buttons on the old assistant's messages** (👍/👎, This fixed it, I still need a human) get a private "that button belonged to the old AI assistant, which has been removed." Same reasoning as the old CSAT buttons (entry 221).

**Other channels**

251. **`FAQ_WATCH_CHANNEL_IDS`** (comma-separated). The cog that listens is only loaded when it's set and the matcher is on. The IDs can't include #community-help, #tickets, the staff channel or the log channel: startup refuses that.
     - Only the channels themselves, not threads under them.
252. **Two questions, one bar.** Jev is asked `kind` ("is this a genuine question asking for help with the server?", `support_question` or `other`) and `faq`. The bot replies only when **both** reach `FAQ_WATCH_THRESHOLD` (default 0.95, deliberately above 0.9).
     - `kind` is a `choice` question too. Upstream's types also have a yes/no ("noul") question, but its answer has no `confidence` field and the brief says to act on `confidence`.
     - One threshold for both, not two settings: nobody needs to tune them apart yet.
253. **The reply** is `[faq] watch_reply`, by default the answer, a blank line, then small text "got another question? ask in #community-help". The answer inside it is untouched. It's sent as a reply to the message, with no mentions (the author isn't pinged).
254. **Pointer only, behind `FAQ_WATCH_POINTER` (default off):** when `kind` clears the bar and Jev's `faq` choice is `other`, the bot replies with `[faq] watch_pointer`. If Jev picked an entry but below the bar, the bot says nothing: pointing someone away when an answer probably exists helps nobody, and it's the ambiguous case.
255. **Cooldowns:** after a reply the bot stays quiet in that channel for `FAQ_CHANNEL_COOLDOWN_SECONDS` (default 120) and towards that person, in any watched channel, for `FAQ_USER_COOLDOWN_SECONDS` (default 600).
     - Checked before asking Jev (no request is spent on a message that couldn't get a reply) and again after, so two questions arriving together get one reply.
     - Only a reply starts a cooldown; a message that gets nothing doesn't.
     - In memory. A restart forgets them, which costs at most one early reply.
     - In shadow mode the staff reports follow the same cooldowns, so staff see what would really be posted.
256. **Never bots, never tickets.** Bot, webhook and system messages are dropped before anything else. The watched-channel code has no path to ticket creation; a test asserts no ticket, thread or reaction results.
257. **Messages under 8 characters are skipped** without a request. They can't be a question worth answering, and it trims cost.
258. **Cost and privacy, stated in DEPLOY.md:** watching a channel sends every message of 8+ characters there to Hack Club AI, one request each, except during a cooldown. There's no rate cap beyond that. Whether that volume is acceptable on a busy #general is the maintainer's call; it's off by default.

**Categorisation**

259. **Jev picks the #community-help category** in the same request as the FAQ question, with `TICKET_CATEGORIES` as the options (names only: they have no descriptions). It's applied as before: only if nobody set one, stored with source `ai`, shown as "(AI)". No confidence threshold, matching the old behaviour; a wrong category is staff-side and harmless.

**Shadow mode**

260. **`FAQ_MODE` is `shadow` by default** (also `off`, `public`). In shadow nothing new reaches players on either surface. Each would-be reply goes to `STAFF_CHANNEL_ID` as an embed: the entry id in the title, the exact text that would have been posted as the description, the confidence, where it was (linked), and the player's message (first 300 characters).
     - Only matches are reported, as the brief says. Non-matches are logged at info with Jev's top choice and confidence, which is what you need to tune a description.
     - No 👍/👎 buttons and no accuracy numbers in the daily summary. With a fixed set of answers, "would this have been right?" is visible at a glance in the report; a rating store would be machinery for its own sake at this volume.
     - Without a staff channel, shadow matches are only logged (startup warns).
     - `off` turns FAQ matching off and keeps categorisation.
261. **Nothing about matches is stored.** Prometheus counter `cloudsail_faq_decisions_total{surface,result}` and the log are the record.

**Removed**

262. **Migration `cloudsail_008` drops `ai_suggestion`** (model-written answers and staff ratings of them). Backwards re-creates it empty, as `cloudsail_002` defined it. Tested both ways, and run through `piccolo migrations` forwards, backwards and forwards on a scratch database.
263. **Copy:** the `[ai]` section, `[embeds.ai_shadow]`, `[embeds.ai_public]`, the card's AI line, the summary's AI block and two error strings are gone; a copy file that still has them loads with a warning per key or section (entry 184's rule, extended to whole sections). New: `[faq]` and `[embeds.faq_shadow]`.
264. **Also removed:** the AI field on staff cards, the AI block of the daily summary, `nephthys/ai/{answer,categorize,client,kb,parsing}.py`, `discord_ops/ai_flow.py`, `core/ai_store.py`, and their tests.
