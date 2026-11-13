# Deploying Cloudsail Support

This deploys the bot on an **Ubuntu ARM64** box (for example, an OCI Ampere instance running next to Zammad) with Docker Compose. The bot and its own Postgres run in a separate compose project from Zammad, so they share nothing with it.

The bot only makes **outbound** connections (to the Discord gateway and API). You don't need to open any inbound ports in OCI security lists or `ufw`. The optional Zammad sync (§7) adds one private link between the two compose projects over Zammad's Docker network; it still opens nothing on the host or to the internet.

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
     | Manage Webhooks | only for the Zammad sync (§7): one webhook on #tickets posts agent replies under the agent's name |

   - Or use this URL with your application ID (permissions integer `395137084480`, or `395673955392` with Manage Webhooks):

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
4. **Staff role:** anyone with this role can claim tickets (by replying), resolve, re-open, rate AI suggestions, and use `/faq`, `/ip`, `/resolve`, `/reopen`, `/waiting` and `/stats`. Discord shows the slash commands to everyone; the bot refuses non-staff, privately. To hide them as well, use Server Settings → Integrations → Cloudsail Support → limit commands to the Staff role.
5. Optional channels (staff-only):
   - **Staff channel** (`STAFF_CHANNEL_ID`): #tickets alert cards, AI shadow suggestions (if AI is on), and the daily summary. #community-help tickets don't get cards here; they show up in `/stats`, `/waiting` and the summary.
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
2. As a non-staff account, post a question. The bot opens a thread, posts the auto-reply with **Mark as resolved**, reacts 🤔, and moves the sticky back to the bottom after about 5 seconds.
3. Reply in the thread as staff. `/waiting` no longer lists the ticket, and `/stats` shows it as in progress.
4. Click **Mark as resolved** as the poster. You should see the closing message with **Re-open**, ✅ on the question, and the thread archived.
5. Reply in the archived thread as the poster. The ticket re-opens automatically (🤔 is back, along with a "re-opened by" message).
6. As staff, run `/faq`, `/ip`, `/resolve` and `/reopen` inside a ticket thread. `/faq` and `/ip` post publicly. Afterwards, `/waiting` still lists the ticket, because these commands don't count as a reply. Running them outside a ticket thread gives a private error.
7. Check that nothing was posted to the staff channel for these questions, and that `/stats` counts them (opened, claimed, resolved, top helpers).
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
    - Rate it: the buttons are disabled and the message thanks the player ("thanks! you rated 4/5 🙂"), the card shows CSAT, and the thread archives.
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

- **Categories:** new #community-help tickets get an AI category, counted in `/stats` and the daily summary ("Opened by category").
- **Shadow mode:**
  - When the model is confident (`AI_ANSWER_MIN_CONFIDENCE`, default 0.8) and cites real `kb/` files, it posts the suggestion to the staff channel (linking to the thread), with 👍/👎 buttons. Players never see these.
  - Rate as many as you can. The daily summary reports shadow accuracy for the last 24h and 7 days.
- **Going public:** once accuracy has been consistently high, set `AI_ANSWER_MODE=public`. A reasonable bar is 90% or better over at least 30 ratings.
  - Answers then appear in the thread, labelled as automated, with **This fixed it** (resolves the ticket) and **I still need a human** buttons.
  - AI answers never claim a ticket and never take it off `/waiting`.
- **Privacy:** the question title and text are sent to the AI provider. Don't enable AI if that isn't acceptable for your community.

## 6. Operations

**Change what the bot says.** Every player- and staff-facing message is in `copy/copy.toml`, mounted read-only into the container from `./copy`. That includes:

- the auto-reply, closing and re-open messages, the sticky, staff card labels, command replies, `/stats` and `/waiting`, the daily summary, and AI answer labels;
- **how each embed looks.** The `[embeds.*]` sections set the title, description, colour (hex), footer and an optional thumbnail URL for the sticky, the ticket panel, staff cards (one colour per status), `/stats`, `/waiting`, the daily summary and the AI answers. The #tickets opener and closing message are plain text by default; set `enabled = true` under `[embeds.ticket_opener]` or `[embeds.ticket_closed]` to send them as embeds.

**Prefer a local override file for your changes.** That way `copy.toml` stays the shipped defaults and `git pull` never conflicts with your text:

```bash
cd /opt/cloudsail-support && cp copy/copy.local.toml.example copy/copy.local.toml
```

Every key is listed there, commented out, with its default value. Uncomment and edit only what you want to change. Any key in `copy/copy.local.toml` (`COPY_OVERRIDE_FILE`) overrides `copy.toml`, and the file is gitignored.

1. Edit `/opt/cloudsail-support/copy/copy.local.toml` (or `copy.toml`) on the host and save.
2. Within about 5 seconds the bot logs `Reloaded copy from copy/copy.toml`, and new messages use the new text.
   - Messages already posted (e.g. old closing messages) aren't rewritten.
   - Staff cards pick up the change the next time they update.
   - The sticky updates on its next repost.
3. If your edit is invalid, the bot logs `Ignoring invalid copy edit …` with the exact key and problem, and keeps the previous text. Examples: a typo'd key, or a `{placeholder}` that key doesn't accept.

   To check an edit before saving it into place:

   ```bash
   docker compose exec bot python -m nephthys.core.copytext --check /app/copy/copy.toml /app/copy/copy.local.toml
   ```

4. Each key's comment lists the placeholders it accepts. Use `{{` / `}}` for literal braces. Delete a key to go back to its built-in default.
5. If `FAQ_URL` is blank, the auto-reply and sticky use their `*_no_faq` variants (no FAQ sentence), and `/faq` privately tells staff "no FAQ configured."

If you set `STICKY_TITLE` / `STICKY_BODY` in an older `.env`, they're ignored now (the bot logs a warning). Move that text into `[embeds.sticky]`.

Some keys have moved into `[embeds.*]`: the sticky, the panel and card titles, the summary title and period, the `/stats` and `/waiting` titles, and the AI titles and footers. A copy file that still uses an old key fails validation with a message naming its new location, e.g. `[sticky] body moved to [embeds.sticky] description`.

The daily summary is posted as an **@silent** message: it shows up in the staff channel but doesn't send push notifications.

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

## 7. Zammad two-way sync for #tickets (optional)

Admins work #tickets tickets in Zammad; players stay in their Discord thread. The sync is **off** unless `ZAMMAD_URL` and `ZAMMAD_TOKEN` are both set, and it needs #tickets (`TICKETS_CHANNEL_ID`). Zammad itself is never modified: everything goes through its REST API, one webhook, one trigger and one custom attribute.

This was built and tested against **Zammad 7.2.3**.

### 7.1 Zammad: groups, a bot user and two tokens

1. **Groups.** Admin → Groups: make sure there is a group per #tickets category. By default they have the same names: `Server Issue`, `Store`, `Bans & Appeals`, `Reports`, `Other`. To use other groups, set `zammad_group` per category in `copy/ticket_categories.toml`.
2. **A user for the bot.** Admin → Users → New user, e.g. "Discord Bot" with any address you own as its email.
   - Role: **Agent** only.
   - Group permissions: **Full** on every group from step 1. Without it Zammad answers 403 when the bot creates or reads tickets there.
   - Agents then see mirrored Discord staff messages as written by "Discord Bot", with the real name on the first line.
3. **The bot's everyday token** (`ZAMMAD_TOKEN`). Log in to Zammad **as the bot user**, then avatar → Profile → Token Access → Create:
   - Name: `cloudsail-support-bot`
   - Permissions: tick **`ticket.agent`** and nothing else.
   - Copy the token. It's shown once.
   - If Token Access is missing, turn it on under Admin → System → API → "Enable Token Access".
4. **A setup token** (`ZAMMAD_SETUP_TOKEN`), used once by `zammad-setup`. Log in as **yourself (an admin)**, Profile → Token Access → Create, and tick exactly:
   - `admin.object` (the Discord ID attribute)
   - `admin.webhook`
   - `admin.trigger`
   - `admin.group` (to check the groups)
   - `admin.channel_email` (to block trigger emails to Discord customers)

   Delete this token again after §7.4. The bot never needs it at runtime.

### 7.2 Network: join the bot to Zammad's Docker network

Zammad's nginx only listens on the host's `127.0.0.1:8090`, which a container can't reach, so the bot joins Zammad's Docker network instead. That also lets Zammad call the bot back.

1. Find the network name. With Zammad in `/opt/zammad-docker-compose` it's `zammad-docker-compose_default`:

   ```bash
   docker network ls | grep zammad
   ```
2. In the bot's `.env`:

   ```ini
   COMPOSE_FILE=docker-compose.yml:docker-compose.zammad.yml
   ZAMMAD_DOCKER_NETWORK=zammad-docker-compose_default
   ZAMMAD_URL=http://zammad-nginx:8080
   ```

   `docker-compose.zammad.yml` attaches the bot to that network under the name `cloudsail-support-bot`. Zammad's compose project isn't touched.
3. Port 3000 of the bot (`/health`, `/metrics` and `/zammad/webhook`) becomes reachable from Zammad's containers, and from nowhere else. The webhook only accepts calls signed with `ZAMMAD_WEBHOOK_SECRET`.

### 7.3 Environment variables

| Variable | Value |
|---|---|
| `ZAMMAD_URL` | `http://zammad-nginx:8080` |
| `ZAMMAD_TOKEN` | the bot user's token (§7.1 step 3) |
| `ZAMMAD_SETUP_TOKEN` | the admin token (§7.1 step 4); remove after setup |
| `ZAMMAD_WEBHOOK_SECRET` | a random secret: `openssl rand -hex 32` |
| `ZAMMAD_PUBLIC_URL` | `https://support.cloudsail.uk` (the staff card's **Open in Zammad** button, agent avatars; without it the button isn't shown) |
| `ZAMMAD_DEFAULT_AVATAR_URL` | a public image URL used when an agent has no picture or it can't be shown |
| `ZAMMAD_WEBHOOK_URL` | leave at `http://cloudsail-support-bot:3000/zammad/webhook` |

The rest (`ZAMMAD_ATTACHMENT_MAX_MB`, `ZAMMAD_TIMEOUT_SECONDS`, `ZAMMAD_OUTBOX_MAX_ATTEMPTS`, `ZAMMAD_RECONCILE_MINUTES`) have sensible defaults, described in `.env.example`.

Also give the bot **Manage Webhooks** in #tickets (channel permissions → the bot's role). It creates one webhook there, named "Cloudsail Support (Zammad replies)", the first time an agent replies.

### 7.4 Start the bot, then run the setup command

```bash
docker compose up -d --build          # the bot must be running on Zammad's network first
docker compose exec bot nephthys zammad-setup
```

Run it with `exec` in the running container, not `run`: Zammad refuses a webhook whose address it can't resolve, and only the running bot answers to `cloudsail-support-bot`.

The command is idempotent. Run it again after changing the secret, the webhook address, the categories or the bot's Zammad user. It:

- checks every category's group exists and the bot user can work in it;
- creates the User attribute **Discord ID** (internal name `discord_uid`);
- creates or updates the webhook "Cloudsail Support bot (Discord #tickets)", signed with `ZAMMAD_WEBHOOK_SECRET`;
- creates or updates the trigger "Cloudsail Support bot: notify Discord";
- adds `@discord.invalid` to Zammad's "no auto response" address filter, so no trigger can email a Discord customer.

**The first run restarts Zammad.** Applying a new attribute makes Zammad restart its processes; it's unavailable for a minute or two and the command waits for it. Later runs don't.

It prints a ✓ or ✗ per step and exits non-zero if anything needs attention. Afterwards, delete the setup token in Zammad and remove `ZAMMAD_SETUP_TOKEN` from `.env`.

### 7.5 Avatars: a narrow Cloudflare Access bypass

Agent replies appear in Discord with the agent's Zammad name and picture. Discord's servers download the picture, so they need to reach it, and `support.cloudsail.uk` is behind Cloudflare Access.

Zammad serves profile pictures at `/api/v1/users/image/<hash>` **without a login** (that's how its own login page shows them), and the address contains a hash of the image, not a user ID. So it's enough to let that one path through:

1. Cloudflare Zero Trust → Access → Applications → **Add an application** → Self-hosted.
2. Application domain: `support.cloudsail.uk`, path: `api/v1/users/image/*`. Name it e.g. "Zammad avatars (public)".
3. Add one policy: Action **Bypass**, Include **Everyone**.
4. Save. Cloudflare applies the most specific path first, so this takes precedence over the application protecting the rest of `support.cloudsail.uk`.

Check it from anywhere outside your network:

```bash
curl -sI https://support.cloudsail.uk/api/v1/users/image/0 | head -1   # 200, a small grey placeholder
curl -sI https://support.cloudsail.uk/api/v1/users/me | head -1        # still 302/403 from Access
```

What this exposes: profile pictures of Zammad users to anyone who knows a picture's hash. Nothing else on the path. If you'd rather expose nothing, skip the bypass and leave `ZAMMAD_PUBLIC_URL` set for links only: Discord then shows a broken or default avatar, so set `ZAMMAD_DEFAULT_AVATAR_URL` and consider leaving agents without pictures in Zammad. Options that were considered and rejected are in DECISIONS.md.

### 7.6 How agents use it

- A ticket appears in Zammad when the player picks a (sub)category, titled `T-<n> <category>`, in the category's group, with the player as customer (`discord:<id>`).
- Every message in the Discord thread appears as an article starting with `<name> (Player via Discord)` or `<name> (Staff via Discord)`.
- **To reply to the player, write a note and set its visibility to public.** Zammad makes new notes *internal* by default (the lock icon in the reply box). Internal notes never go to Discord. To make public the default for everyone: Admin → Settings → Ticket → "Note - default visibility".
- Don't use the email reply type on these tickets: the customer's address is a placeholder (`<id>@discord.invalid`) that can't receive mail.
- An agent's first public reply claims the ticket on the Discord side. Closing or re-opening in Zammad closes or re-opens the Discord ticket, and the other way round. Moving the ticket to another group updates the staff card.
- Tickets created directly in Zammad (email, or by hand) are never touched by the bot.

### 7.7 Smoke test

1. In Discord, **Create Ticket** → pick **Store**. Within a few seconds the staff card gains an **Open in Zammad** button (before **Open in Discord**).
2. Type "test 1" in the thread and attach an image. In Zammad the ticket shows the article "… (Player via Discord)" with the image.
3. In Zammad, add a **public** note "hello from Zammad" with an attachment. It appears in the thread under your Zammad name and picture, with the file. The staff card says "Claimed by <you> (Zammad)".
4. Add an **internal** note. Nothing appears in Discord.
5. Move the ticket to another group in Zammad. Nothing changes in Discord (the card doesn't show the group); the bot just records it.
6. Close the ticket in Zammad. The thread gets the closing message and the 1–5 rating. Click a rating: Zammad gets an internal note "CSAT: … 4/5".
7. In Discord run `/reopen` in the thread. The Zammad ticket goes back to open. Close it with **Close Ticket**: Zammad shows closed.
8. `/stats` shows **Zammad sync: ✅ in sync** on the #tickets embed.
9. Check the Zammad customer (Customers → search the Discord name): login `discord:<id>`, the Discord ID field filled, and no email-type articles on the ticket.

### 7.8 Operating it

- **Zammad down or restarting:** Discord keeps working. Messages queue in the bot's database and are delivered in order when Zammad is back (retries back off from 5 seconds to 15 minutes). After 10 minutes of backlog the log channel gets one warning (at most one per 30 minutes), and a "caught up" note afterwards.
- **Bot down:** Zammad retries a webhook for about two minutes, then drops it. Every `ZAMMAD_RECONCILE_MINUTES` the bot checks its open and recently closed tickets in Zammad and picks up anything it missed.
- **Failed items:** an item Zammad rejects outright (for example a deleted group) is given up on after 3 attempts, and anything else after `ZAMMAD_OUTBOX_MAX_ATTEMPTS`. The log channel says so once, with the error, and `/stats` shows the count. Fix the cause, then re-queue:

  ```bash
  docker compose exec bot nephthys zammad-retry
  ```
- **Turning it off:** blank `ZAMMAD_TOKEN` and restart. Queued items stay in the database and are sent if you turn it back on. To also stop Zammad calling the bot, disable the trigger "Cloudsail Support bot: notify Discord" in Zammad.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Log says "The Message Content intent is disabled" | Turn on **Message Content Intent** (step 1.2) and restart: `docker compose restart bot`. |
| Log says "Discord rejected DISCORD_TOKEN" | Reset the token in the developer portal, update `.env`, then run `docker compose up -d`. |
| `Invalid configuration: …` | A required variable is missing or isn't a numeric ID. The message lists every problem. |
| No thread appears for a question; the log channel says "Couldn't open a thread" | The bot is missing **Create Public Threads** in #community-help, or a player created a thread on the message first (turn off thread creation for `@everyone`). |
| `/waiting` or `/stats` don't show up | The bot was invited without the `applications.commands` scope. Re-invite it with the URL from step 1.3. `GUILD_ID` must also be correct. |
| No #tickets alert cards or daily summary | `STAFF_CHANNEL_ID` is unset, or the bot can't Send Messages / Embed Links there. |
| `AI_MODEL is required when AI_API_KEY is set` | Set `AI_MODEL` to a model your provider serves. |
| AI suggestions never appear | Check the logs for `AI answer … not posted: <reason>`. The common reasons are `low_confidence` and `no_answer` (the knowledge base doesn't cover the question). Add the missing facts to `kb/`. |
| Threads don't archive | Give the bot **Manage Threads**. |
| Create Ticket says "couldn't open a ticket" | The bot is missing **Create Private Threads** or **Manage Threads** in #tickets. The log channel has the exact error. |
| Staff can't open a #tickets thread from the alert | Give that role **Manage Threads** in #tickets: private threads are only visible to members and people with Manage Threads. |
| Alert cards don't notify the role | Make the role mentionable, or give the bot **Mention All Roles** in the staff channel. |
| `copy/ticket_categories.toml: invalid categories` at startup | Fix the reported key. Check the file with `python -m nephthys.core.ticket_categories --check`. |
| `zammad-setup`: "could not be resolved to a safe IP address" | Zammad can't resolve `cloudsail-support-bot`. The bot must be running with `docker-compose.zammad.yml` (§7.2), and the command must be run with `docker compose exec bot …`. |
| `zammad-setup`: `network … declared as external, but could not be found` on `docker compose up` | `ZAMMAD_DOCKER_NETWORK` is wrong. Check `docker network ls \| grep zammad`. |
| `zammad-setup`: 403 "Token authorization failed" | The setup token is missing one of the admin permissions in §7.1 step 4. |
| Log channel: "Zammad sync is behind … HTTP 403: Not authorized" | The bot's Zammad user has no Full access to that ticket's group (§7.1 step 2). |
| Log channel: "No lookup value found for 'group'" | A category's group doesn't exist in Zammad. Create it or set `zammad_group`, then `nephthys zammad-retry`. |
| Agent replies show as "**Name (Support):** …" from the bot | The webhook post failed; the log channel has the reason. Usually the bot lacks **Manage Webhooks** in #tickets. |
| An agent's reply doesn't reach Discord | It was an internal note (the default in Zammad): set the note to public. Otherwise check Zammad → Admin → Webhooks → the webhook's recent logs, and the bot's log for "bad signature" (the secret changed: run `zammad-setup` again). |
| Agent avatars are missing in Discord | The Cloudflare Access bypass (§7.5) isn't in place, or `ZAMMAD_PUBLIC_URL` is unset. |
