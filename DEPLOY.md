# Deploying Cloudsail Support

This deploys the bot on an **Ubuntu ARM64** box (for example, an OCI Ampere instance running next to Zammad) with Docker Compose. The bot and its own Postgres run in a separate compose project from Zammad, so they share nothing with it.

The bot only makes **outbound** connections (to the Discord gateway and API). You don't need to open any inbound ports in OCI security lists or `ufw`.

---

## 1. Create the Discord application

1. Go to <https://discord.com/developers/applications>, click **New Application**, and name it (e.g. "Cloudsail Support").
2. **Bot** tab:
   - Click **Reset Token** and copy the token. It becomes `DISCORD_TOKEN`. Treat it like a password.
   - Turn **Public Bot** off.
   - Under **Privileged Gateway Intents**, turn **Message Content Intent** ON. The bot reads question text.
   - Leave **Server Members Intent** and **Presence Intent** off. They aren't needed.
3. **Installation** tab (or **OAuth2 → URL Generator**):
   - Scopes: `bot`, `applications.commands`
   - Bot permissions:

     | Permission | Why |
     |---|---|
     | View Channels | see #community-help and #tickets |
     | Send Messages | post the sticky message and the ticket panel |
     | Read Message History | react to questions; check the sticky position |
     | Add Reactions | 🤔 / ✅ on questions |
     | Embed Links | sticky, `/stats` and `/waiting` embeds |
     | Create Public Threads | one thread per #community-help question |
     | Create Private Threads | one private thread per #tickets ticket |
     | Send Messages in Threads | auto-reply, closing messages, AI answers |
     | Manage Threads | archive, unarchive, lock and unlock threads; add players to private threads; delete orphaned threads |
     | Manage Messages | remove reactions and delete the old sticky |

   - Or use this URL with your application ID (permissions integer `395137084480`):

     ```
     https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=395137084480
     ```
4. Open the URL and add the bot to the Cloudsail server.

## 2. Prepare the Discord server

1. Turn on **Developer Mode** (User Settings → Advanced) so you can right-click → **Copy ID**.
2. **#community-help** must be a regular **text channel**, not a forum. Permission overrides for `@everyone`:
   - Send Messages: ✅
   - Send Messages in Threads: ✅
   - **Create Public Threads: ❌** and **Create Private Threads: ❌**. The bot creates the threads. If players make their own, the bot can't open the ticket thread.
   - Optional: a slowmode of a few seconds cuts down on accidental multi-message questions.
3. Make sure the bot's role has the permissions above in #community-help. Either check that channel overrides don't remove them, or add an override for the bot role.
4. **Staff role:** anyone with this role can claim tickets (by replying), resolve, re-open, set categories, rate AI suggestions, and use `/faq`, `/ip`, `/resolve`, `/reopen`, `/waiting` and `/stats`. Discord shows the slash commands to everyone; the bot refuses non-staff, privately. To hide them as well, use Server Settings → Integrations → Cloudsail Support → limit commands to the Staff role.
5. Optional channels (staff-only):
   - **Staff channel** (`STAFF_CHANNEL_ID`): a ticket card per new ticket, AI shadow suggestions, and the daily summary.
   - **Log channel:** errors and "online" notices (`LOG_CHANNEL_ID`).
   - The bot needs View Channel, Send Messages and Embed Links in both.
6. Copy these IDs: the server (`GUILD_ID`), #community-help (`COMMUNITY_CHANNEL_ID`), the Staff role (`STAFF_ROLE_ID`), and optionally the staff channel, the log channel and your own user ID (`MAINTAINER_ID`).

### #tickets (private tickets, optional)

Skip this section to run #community-help only: #tickets is off while `TICKETS_CHANNEL_ID` is blank.

1. **Create a regular text channel**, e.g. `#tickets`, and set these permission overrides:

   | Who | Allow ✅ | Deny ❌ |
   |---|---|---|
   | `@everyone` | View Channel, Read Message History, Send Messages in Threads | **Send Messages** (keeps the channel to just the panel), **Create Public Threads**, **Create Private Threads** |
   | Bot role | View Channel, Send Messages, Embed Links, Read Message History, **Create Private Threads**, Send Messages in Threads, **Manage Threads**, Manage Messages | |
   | Staff role, plus the Store and Mod roles if you use them | View Channel, Read Message History, Send Messages in Threads, **Manage Threads** | |

2. **How staff see private threads.** A private thread is visible only to the people in it, plus anyone with **Manage Threads** in the parent channel. So:
   - The player is added to their own thread automatically, and nobody else is.
   - Staff (and the Store and Mod roles) need **Manage Threads** on #tickets. Without it they can't open tickets at all, even from the alert link.
   - Staff open a ticket from the **Open thread** link on its alert card in the staff channel. In #tickets, private threads appear under the threads icon (🧵) at the top of the channel. Posting in a ticket adds you to it, so you get notifications from then on.
   - The bot creates threads as non-invitable, so players can't add other people.
3. **Alert pings.** When a player picks a category, the alert card in the staff channel pings that category's role (`STORE_ROLE_ID`, `MOD_ROLE_ID`, or the Staff role). High-priority tickets also ping the Staff role. For a ping to notify anyone, do one of these:
   - make those roles mentionable (Server Settings → Roles → the role → **Allow anyone to @mention this role**), or
   - give the bot **Mention @everyone, @here and All Roles** in the staff channel only.
4. **Set these in `.env`:**
   - `TICKETS_CHANNEL_ID`: the #tickets channel.
   - `STORE_ROLE_ID` and `MOD_ROLE_ID` (optional). Categories owned by an unset role ping the Staff role instead.
   - `TICKET_NUMBER_START`: default `26`, because Mava ended at T-25.
   - `TICKETS_MAX_OPEN_PER_USER`: default `2`; `0` means no limit.
5. **Categories, subcategories, priorities, owning roles and blurbs** are in `copy/ticket_categories.toml`. The defaults are PRODUCT.md §2. Edit the file, check it with `docker compose exec bot python -m nephthys.core.ticket_categories --check /app/copy/ticket_categories.toml`, then run `docker compose restart bot`. This file is read at startup only. Don't rename the `key` of a category that open tickets use; add a new category instead.
6. All #tickets text (the panel, welcome, closing, CSAT, escalation) is in the `[tickets]` section of `copy/copy.toml`, and reloads live like the rest of the copy.

## 3. Install Docker on the box (Ubuntu 22.04/24.04, arm64)

Skip this if Docker is already installed for Zammad. Check with `docker compose version`.

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

```bash
sudo usermod -aG docker "$USER"
```

Log out and back in so the group change applies.

## 4. Get the code and configure it

```bash
sudo mkdir -p /opt/cloudsail-support && sudo chown "$USER" /opt/cloudsail-support
```

```bash
git clone https://github.com/thecoltonk/discordthys.git /opt/cloudsail-support
```

```bash
cd /opt/cloudsail-support && cp .env.example .env && chmod 600 .env
```

Edit `.env` and set at least these values:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | the bot token from step 1 |
| `GUILD_ID`, `COMMUNITY_CHANNEL_ID`, `STAFF_ROLE_ID` | the IDs from step 2 |
| `POSTGRES_PASSWORD` | a long random password, e.g. from `openssl rand -hex 24` |
| `FAQ_URL`, `SERVER_ADDRESS` | your FAQ link and the Minecraft server address |
| `STAFF_CHANNEL_ID`, `LOG_CHANNEL_ID`, `MAINTAINER_ID` | optional, but recommended |

Every variable is documented in `.env.example`. In Docker, `DATABASE_URL` is built from the `POSTGRES_*` values automatically, so you don't need to set it.

If something else on the box already uses host port 3000 (Zammad's published port is normally 8080), set `HEALTH_PORT=3001` in `.env`.

## 5. First run

```bash
cd /opt/cloudsail-support && docker compose up -d --build
```

The first build takes a few minutes. Every dependency ships an arm64 wheel, so nothing is compiled. On start, the container runs the database migrations, then connects to Discord.

```bash
docker compose ps
```

```bash
docker compose logs -f bot
```

You should see `Synced 7 application commands to guild`, `AI: off` (or `AI: on (...)`) and `Logged in as …`. If you set a log channel, "🟢 Cloudsail Support is online" is posted there.

```bash
curl -s http://127.0.0.1:3000/health
```

This should return `{"healthy": true, ...}`. `docker compose ps` shows the bot as `healthy` about a minute after start.

### Smoke test in Discord

1. The sticky "How #community-help works" message appears at the bottom of #community-help.
2. As a non-staff account, post a question. The bot opens a thread, posts the auto-reply with **Mark as resolved**, reacts 🤔, posts one line in the staff feed, and moves the sticky back to the bottom after about 5 seconds.
3. Reply in the thread as staff. `/waiting` no longer lists the ticket, and `/stats` shows it as in progress.
4. Click **Mark as resolved** as the poster. You should see the closing message with **Re-open**, ✅ on the question, and the thread archived.
5. Reply in the archived thread as the poster. The ticket re-opens automatically (🤔 is back, along with a "re-opened by" message).
6. As staff, run `/faq`, `/ip`, `/resolve` and `/reopen` inside a ticket thread. `/faq` and `/ip` post publicly. Afterwards, `/waiting` still lists the ticket, because these commands don't count as a reply. Running them outside a ticket thread gives a private error.
7. Check the **staff card** in the staff channel. It flips from 🟡 Waiting to 🔵 Claimed to ✅ Resolved as you go. Pick a category from its menu.
8. Optional: temporarily set `DAILY_SUMMARY_TIME` a couple of minutes ahead and restart the bot to see the summary.

**#tickets** (if enabled):

9. The "Need help?" panel with **Create Ticket** sits at the bottom of #tickets.
10. As a non-staff account, click **Create Ticket**.
    - You get a private reply with a link.
    - The thread is named `<you> (T-26)` and only you (plus staff) can see it.
    - The opener shows `Ticket T-26`, the welcome text, five category buttons, and **Close Ticket**.
11. Pick **Server Issue**. The opener is reposted at the bottom with "You have selected: ❗ Server Issue", the question, and three subcategory buttons.
12. Pick **Urgent**. The category buttons disappear, though **Close Ticket** stays.
    - The staff channel gets an alert card pinging the Staff role, showing 🔴 High.
    - Type a message as the player: it becomes the card's description.
13. Reply as staff: the card shows 🔵 Claimed.
14. Click **Close Ticket** as the player.
    - You should see the closing message and 1–5 rating buttons. The thread locks, so the player can't post, but it stays open so they can rate.
    - Rate it: the buttons are replaced by a thank-you, the card shows CSAT, and the thread archives.
    - Unrated tickets archive on their own after `CSAT_TIMEOUT_HOURS` (default 24). Staff can `/reopen` at any point before or after that.
15. As staff, open the thread from the card and run `/reopen`. The thread unlocks and the player is back in it.
16. In a #community-help thread, run `/escalate` as staff.
    - The poster gets a private #tickets thread linking back.
    - The public thread gets a note linking to it, then locks.
## 5b. Optional: AI auto-categorisation and answers

Everything AI is off while `AI_API_KEY` is blank. To turn it on:

1. Get an API key for an OpenAI-compatible provider. The default `AI_BASE_URL` is Hack Club AI (`https://ai.hackclub.com/proxy/v1`).
2. In `.env`, set `AI_API_KEY` and `AI_MODEL` (a model ID your provider serves). Leave `AI_ANSWER_MODE=shadow` for now.
3. **Fill in `kb/`.** The assistant may only answer from the markdown files there, and it must cite them. The shipped `faq.md`, `ip.md` and `rules.md` are placeholders marked `TODO`. Edits apply immediately, with no restart, because `./kb` is mounted into the container.
4. Restart: `docker compose up -d`. The log should say `AI: on (model=…, answers=shadow, categorise=True)`.

What happens next:

- **Categories:** new tickets get an AI category on their staff card, shown as "Bug (AI)". When staff pick a category from the menu, that choice wins, and the AI never overwrites it.
- **Shadow mode:**
  - When the model is confident (`AI_ANSWER_MIN_CONFIDENCE`, default 0.8) and cites real `kb/` files, it posts a suggestion as a reply to the staff card, with 👍/👎 buttons. Players never see these.
  - Rate as many as you can. The daily summary reports shadow accuracy for the last 24h and 7 days.
- **Going public:** once accuracy has been consistently high, set `AI_ANSWER_MODE=public`. A reasonable bar is 90% or better over at least 30 ratings.
  - Answers then appear in the thread, labelled as automated, with **This fixed it** (resolves the ticket) and **I still need a human** buttons.
  - AI answers never claim a ticket and never take it off `/waiting`.
- **Privacy:** the question title and text are sent to the AI provider. Don't enable AI if that isn't acceptable for your community.

## 6. Operations

**Change what the bot says.** Every player- and staff-facing message is in `copy/copy.toml`. That includes the auto-reply, closing and re-open messages, the sticky, staff card labels, command replies, `/stats` and `/waiting`, the daily summary, and AI answer labels. It's mounted read-only into the container from `./copy`.

1. Edit `/opt/cloudsail-support/copy/copy.toml` on the host and save.
2. Within about 5 seconds the bot logs `Reloaded copy from copy/copy.toml`, and new messages use the new text.
   - Messages already posted (e.g. old closing messages) aren't rewritten.
   - Staff cards pick up the change the next time they update.
   - The sticky updates on its next repost.
3. If your edit is invalid, the bot logs `Ignoring invalid copy edit …` with the exact key and problem, and keeps the previous text. Examples: a typo'd key, or a `{placeholder}` that key doesn't accept.

   To check an edit before saving it into place:

   ```bash
   docker compose exec bot python -m nephthys.core.copytext --check /app/copy/copy.toml
   ```

4. Each key's comment lists the placeholders it accepts. Use `{{` / `}}` for literal braces. Delete a key to go back to its built-in default.
5. If `FAQ_URL` is blank, the auto-reply and sticky use their `*_no_faq` variants (no FAQ sentence), and `/faq` privately tells staff "no FAQ configured."

If you set `STICKY_TITLE` / `STICKY_BODY` in an older `.env`, they're ignored now (the bot logs a warning). Move that text into `[sticky]` in `copy.toml`.

All text messages are sent with link previews suppressed, so links in copy never unfurl into big embeds.


**Update to the latest code:**

```bash
cd /opt/cloudsail-support && git pull && docker compose up -d --build
```

Migrations run automatically on start.

**Back up the database:**

```bash
cd /opt/cloudsail-support && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "cloudsail-support-$(date +%F).sql.gz"
```

**Restore** (into a fresh volume):

```bash
gunzip -c BACKUP.sql.gz | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

**Restart / stop:**

```bash
docker compose restart bot
```

```bash
docker compose down
```

`docker compose down` keeps the `pgdata` volume. Only `down -v` deletes it.

**Metrics:** Prometheus can scrape `http://127.0.0.1:3000/metrics` on the host.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Log says "The Message Content intent is disabled" | Turn on **Message Content Intent** (step 1.2) and restart: `docker compose restart bot`. |
| Log says "Discord rejected DISCORD_TOKEN" | Reset the token in the developer portal, update `.env`, then run `docker compose up -d`. |
| `Invalid configuration: …` | A required variable is missing or isn't a numeric ID. The message lists every problem. |
| No thread appears for a question; the log channel says "Couldn't open a thread" | The bot is missing **Create Public Threads** in #community-help, or a player created a thread on the message first (turn off thread creation for `@everyone`). |
| `/waiting` or `/stats` don't show up | The bot was invited without the `applications.commands` scope. Re-invite it with the URL from step 1.3. `GUILD_ID` must also be correct. |
| No staff cards or daily summary | `STAFF_CHANNEL_ID` is unset, or the bot can't Send Messages / Embed Links there. |
| `AI_MODEL is required when AI_API_KEY is set` | Set `AI_MODEL` to a model your provider serves. |
| AI suggestions never appear | Check the logs for `AI answer … not posted: <reason>`. The common reasons are `low_confidence` and `no_answer` (the knowledge base doesn't cover the question). Add the missing facts to `kb/`. |
| Threads don't archive | Give the bot **Manage Threads**. |
| Create Ticket says "couldn't open a ticket" | The bot is missing **Create Private Threads** or **Manage Threads** in #tickets. The log channel has the exact error. |
| Staff can't open a #tickets thread from the alert | Give that role **Manage Threads** in #tickets: private threads are only visible to members and people with Manage Threads. |
| Alert cards don't notify the role | Make the role mentionable, or give the bot **Mention All Roles** in the staff channel. |
| `copy/ticket_categories.toml: invalid categories` at startup | Fix the reported key. Check the file with `python -m nephthys.core.ticket_categories --check`. |
