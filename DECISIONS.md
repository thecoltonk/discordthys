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
