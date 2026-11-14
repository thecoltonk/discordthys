# Cloudsail Support — PRODUCT.md

**Status:** Draft, Nov 2026
**Replaces:** Mava
**Base:** fork of [hackclub/nephthys](https://github.com/hackclub/nephthys) (MIT), with the Slack layer swapped for Discord

---

## Goal

Give players two ways to get help on Discord, plus a real `support@cloudsail.uk` inbox:

| Surface | For | Visibility | Handled in |
|---|---|---|---|
| **#community-help** | General questions, bugs, "how do I…" | Public | Discord |
| **#tickets** | Store, bans & appeals, reports, anything personal | Private | **Zammad** (admins) ↔ Discord thread (players), synced both ways |
| **support@cloudsail.uk** | Email (store issues, people not on Discord) | Private | Zammad |

## Non-goals (v1)

- Linking Zammad agents to Discord accounts. An agent's reply shows their Zammad name and picture, nothing more.
- Syncing tickets that were created directly in Zammad (email, or by hand) into Discord.
- Syncing edits and deletions of messages, in either direction.
- Replacing appeals.cloudsail.uk. Players banned from Discord still use it.

---

## 1. #community-help (public)

Works like Nephthys and #nest-help.

- **Each post is a ticket.** Use a forum channel, so each post gets its own thread.
- **Anyone can answer.** Players are encouraged to help each other, not just staff.
- **Claiming:** the first **staff** reply claims the post. Claiming only matters for stats and the "waiting" list. It doesn't stop anyone else from replying.
- **Bot auto-reply on a new post:**
  > someone should be along to help you soon! in the meantime check out the FAQ. once your question is answered, hit the button below to mark it as resolved :D
  >
  > **[Mark as resolved]**
- **Resolving:** the person who posted or any staff member can resolve.
  - The bot posts a closing message ("marked as resolved by @X — got another question? make a new post!") with a **[Re-open]** button.
  - The bot also applies a `Resolved` forum tag and archives the thread.
- **Macros** (staff only):
  - `?faq` links the FAQ
  - `?ip` posts the server address
  - `?resolve` and `?reopen`
  - `?ticket` escalates the post: it opens a private #tickets thread for the poster with a link back, then locks the public post. Use it when someone posts something sensitive in public.
- **Categories (optional):** Jev, a decision model, picks one of the configured categories (Gameplay, Bug, Claims, Economy, Store, Other) for each new ticket. They're staff-side, for stats.
- **FAQ matcher (optional):** common questions are answered from a list of pre-written answers.
  - Each FAQ entry is a file with an id, a short description of what it covers, and the exact answer text.
  - Jev is asked which entry fits a new ticket, or "other" for none. On a confident match the bot posts that entry's answer, word for word, with **[Mark as resolved]**. Otherwise it says nothing and a human answers as usual.
  - **No model-written text ever reaches a player.** A previous Cloudsail AI bot was abused into producing NSFW output; this one can't write at all. The model only names an entry.
  - **Other channels (e.g. #general):** in channels staff choose, the bot replies only when Jev is very confident the message is a real question *and* an entry answers it (a higher bar than in #community-help). The reply is the answer plus a pointer to #community-help. Optionally it also points confident support questions with no match to #community-help. There are per-user and per-channel cooldowns, it never replies to bots, and it never creates tickets from those channels.
  - **Shadow mode first:** by default nothing is shown to players. Each match, its confidence and the text that would have been posted go to the staff channel, until staff switch it to public.

## 2. #tickets (private)

Keeps Mava's flow, which players already know, with no seat limit. Players stay in their Discord thread. Admins work the ticket in Zammad (see §2b); staff can still reply in the thread as themselves.

### Panel (sticky, see below)

> **Need help?**
> First, check the known issues and see if #community-help can sort it. If not, hit the button below to open a private thread with the team.
>
> **[Create Ticket]**

### Flow

1. **Click → a private thread is created immediately.**
   - It's named after the user, and the bot adds them.
   - No category has been picked yet.
2. **Opener message:**
   - A header with `Ticket T-26` and a **[Close Ticket]** button.
   - Welcome text.
   - Category buttons.
3. **Pick a category.**
   - The bot **deletes and reposts** the message, so the buttons always sit at the bottom.
   - Each choice adds a `You have selected: …` line, and those lines stack up.
   - Then the category blurb, plus subcategory buttons if that category has them.
4. **Pick a subcategory.** This is a leaf, so the bot shows the final blurb and removes the buttons. **[Close Ticket]** stays the whole time.
5. **The player types freely.** There are no modals. Each blurb tells them what to include.

### Categories

| Category | Subcategories → priority | Blurb asks for |
|---|---|---|
| ❗ **Server Issue** | 💥 Urgent → High · 🔧 Bug Report → Medium · 💻 Other Technical → Low | What happened, IGN, where and when, screenshots |
| 🛒 **Store** | none (Medium) | IGN, what you bought, roughly when, what's wrong |
| 🔨 **Bans & Appeals** | none (Medium) | IGN and what happened. If banned from Discord, use appeals.cloudsail.uk. |
| 🚩 **Reports** | none (Medium) | Who, what, where and when, evidence |
| ❓ **Other** | none (Low) | Description. This also covers partnerships and website issues. |

### Staff side

- **Alert:** the bot posts to the staff channel with:
  - ticket number, category, priority and the player
  - the player's first message
  - a link to the thread, and to the Zammad ticket

  It pings nobody: no category role, no Staff role on High priority. Whole-role pings were more annoying than useful; staff watch the channel, and the card shows the priority.
- **Staff reply in the thread as themselves.** The first staff reply claims the ticket.
- **Closing:** staff or the player can close.
  - The bot posts a closing message, then locks and archives the thread straight away.
  - **Closed is final.** Nobody can re-open a private ticket, from Discord or from Zammad. If there's more to sort out, the player opens a new ticket. (`/reopen` in a #tickets thread says so privately.)
  - There is no satisfaction rating (CSAT). At about 20 tickets in 9 months the numbers wouldn't mean anything, and Discord disables buttons in a locked thread, so a rating could only be collected by keeping closed threads open.
- **Zammad:** every ticket is mirrored to Zammad both ways. See §2b.

## 2b. Zammad ↔ #tickets (two-way)

Zammad is the core support platform. **Admins work tickets in Zammad; the Discord thread is the player-facing side.** The sync is off unless `ZAMMAD_URL` and `ZAMMAD_TOKEN` are set, and it never modifies Zammad itself: REST API, one webhook, one trigger, one custom attribute.

### Discord → Zammad

- **Ticket.** When a ticket reaches a leaf category, a Zammad ticket is created:
  - title `T-<n> <category>`, e.g. `T-26 Store` or `T-27 Server Issue (Urgent)`;
  - group from the category (`zammad_group` in `ticket_categories.toml`; by default the same-named groups Server Issue, Store, Bans & Appeals, Reports, Other);
  - priority mapped: Low → `1 low`, Medium → `2 normal`, High → `3 high`.
- **Customer.** One Zammad customer per Discord player, found or created: login `discord:<user_id>`, name = display name, email `<user_id>@discord.invalid`, plus a custom **Discord ID** attribute. The name follows the player's Discord display name. With the optional avatar token (`admin.user`, on a separate Zammad user), the customer also shows their Discord profile picture, refreshed when it changes, at most once a day.
- **Messages.** Every message in the thread becomes an article: type *note*, public, starting with `<name> (Player via Discord)` or `<name> (Staff via Discord)`. Attachments are uploaded when they fit; otherwise they're named in the article.
- **Escalated tickets** (`/escalate`) include the original public question and a link to the public thread.
- **Staff card** has an **Open in Zammad** button (first) next to **Open in Discord**. Until the ticket is mirrored (or with the sync off) only the Discord button shows; the Zammad one is added when the mirror lands. The footer shows the T-number.
- **No email, ever.** The bot only writes notes (never email-type articles), the customer's address can't receive mail, and Zammad is told to skip `@discord.invalid` for trigger emails.

### Zammad → Discord

- A Zammad **trigger + signed webhook** calls the bot when an agent adds a **public** article to a Discord ticket. The bot checks the signature (HMAC) before doing anything.
- **Internal notes never go to Discord.** The trigger doesn't fire for them, and the bot drops any article that isn't explicitly public and written by an agent.
- The reply is posted in the player's thread through a Discord webhook on #tickets, under the **agent's Zammad display name and avatar**, shown as `<name> (Support)` by default (`[zammad] agent_name` in copy, at most 80 characters). Plain text, no embeds. Attachments are forwarded as files.
- If the webhook can't post (missing permission, Discord refusing it for that thread), the bot posts `**<name> (Support):** <text>` itself and notes it in the log channel.
- An agent's **first public reply claims** the ticket on the Discord side. The claimer is shown as the agent's Zammad name.
- Messages the bot posts on behalf of Zammad are never mirrored back.

### Lifecycle

| Happens in | Effect on the other side |
|---|---|
| Closed in Zammad | The Discord close runs: closing message, then lock and archive |
| Closed in Discord | Zammad ticket set to closed, with an internal note saying who closed it |
| Re-opened in Zammad (any state that isn't closed) | Nothing changes in Discord: the thread stays locked and archived. The bot sets the Zammad ticket back to closed and adds an internal note saying that Discord tickets can't be re-opened and the player should open a new one |
| Agent replies publicly on a closed ticket | The reply is still posted in the thread, which is then archived again (it stays locked) |
| Group changed in Zammad | Recorded on the ticket (the card no longer shows the group) |

Tickets created directly in Zammad are out of scope and left alone.

### Reliability

- Both directions go through an **outbox table** worked by a background task, with exponential backoff (5 seconds up to 15 minutes).
- **Order is kept per ticket.** A message that fails holds back the ones behind it.
- **Idempotent.** Zammad ticket and article IDs and the mirrored Discord message IDs are stored, so retries and repeated webhook deliveries never double-post.
- **Discord never waits on Zammad.** Flows only write to the outbox. With Zammad down, tickets, replies and closing all work, and everything is delivered afterwards.
- A periodic check picks up anything a webhook failed to deliver (for example while the bot was restarting).
- Backlog and failures show in `/stats` and, rate-limited, in the log channel.

### Setup

`nephthys zammad-setup` (idempotent) creates or updates the Discord ID attribute, the webhook and the trigger, and checks that the groups exist. See DEPLOY.md §7.

## 3. support@cloudsail.uk (email)

Handled natively in Zammad at support.cloudsail.uk, which runs on cs-kvm.

**Prerequisites:**
1. Move every account signup off `support@` to another address such as `accounts@`. Otherwise newsletters turn into tickets.
2. **Inbound:** forward `support@` to a dedicated mailbox and have Zammad fetch from it (Google channel or IMAP).
3. **Outbound:** an SMTP relay with SPF and DKIM on cloudsail.uk, so replies come from `support@cloudsail.uk` and stay out of spam.

**Zammad groups mirror the categories:** Store, Bans & Appeals, Reports, Other.

---

## Sticky "how it works" messages

Both channels get a message that always sits at the bottom:
- **#community-help:** how posting and resolving works, plus the FAQ link
- **#tickets:** the ticket panel and its button

When someone posts in the channel, the bot deletes the old sticky and reposts it at the bottom, with a short debounce. **cloudsail-pinner** already does this, so either reuse it or fold its logic into the new bot.

## Roles & permissions

- **The Discord Staff role is the source of truth** for who is staff. Categories have no owner role: alerts don't ping, and routing by category happens in Zammad groups.
- **Zammad agents** are everyone who works tickets: the email inbox and, through the sync, #tickets. Their Zammad groups should match their Discord categories. Agents aren't linked to Discord accounts.
- The bot has its own Zammad agent user with access to the ticket groups.

## Stats

**Daily post to the staff channel:**
- Opened and resolved counts, per surface
- Median time to first staff response
- Tickets waiting more than 3 days with no staff reply

Full stats can come later, either in a staff-only `/stats` command or in Zammad reporting.

## Tech

- **Base:** Nephthys fork (Python 3.13, PostgreSQL, Piccolo migrations)
- **Discord library:** the Slack integration replaced with discord.py, using forum channels, threads, buttons, modals and slash commands
- **Zammad:** 7.2 via docker compose on the same box. REST API with a dedicated API token, a signed webhook and a trigger. The bot joins Zammad's Docker network; nothing is exposed publicly.
- **Hosting:** Docker, on cs-kvm next to Zammad or in the homelab. The bot gets its own Postgres database.

## Phases

1. **v1:** #community-help (auto-reply, resolve and re-open, macros, sticky message)
2. **v2:** #tickets (panel, categories, forms, private threads, routing)
3. **v3:** two-way Zammad ↔ #tickets sync (§2b), daily stats. Still to do: the `support@` email cutover.
4. **Later:** full stats in Zammad reporting

## Open questions

- Should staff stop replying in Discord threads once everyone works in Zammad? Today both work and both are mirrored.
- How long to keep closed tickets, and should old threads be deleted or only archived?
- Should OP-resolved community posts count toward staff stats?
