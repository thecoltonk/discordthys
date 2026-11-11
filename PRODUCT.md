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
| **#tickets** | Store, bans & appeals, reports, anything personal | Private | Discord, mirrored to Zammad |
| **support@cloudsail.uk** | Email (store issues, people not on Discord) | Private | Zammad |

## Non-goals (v1)

- Replying from Zammad back into Discord (two-way sync)
- A web agent inbox for Discord tickets. Staff reply in Discord as themselves.
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
- **AI tagging (optional):** auto-applies a forum tag such as Gameplay, Bug, Claims or Economy. Staff can change it.

## 2. #tickets (private)

Keeps Mava's flow, which players already know, but staff reply as themselves and there's no seat limit.

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

| Category | Subcategories → priority | Pings | Blurb asks for |
|---|---|---|---|
| ❗ **Server Issue** | 💥 Urgent → High · 🔧 Bug Report → Medium · 💻 Other Technical → Low | Staff role | What happened, IGN, where and when, screenshots |
| 🛒 **Store** | none (Medium) | Store role | IGN, what you bought, roughly when, what's wrong |
| 🔨 **Bans & Appeals** | none (Medium) | Mod role | IGN and what happened. If banned from Discord, use appeals.cloudsail.uk. |
| 🚩 **Reports** | none (Medium) | Mod role | Who, what, where and when, evidence |
| ❓ **Other** | none (Low) | Staff role | Description. This also covers partnerships and website issues. |

### Staff side

- **Alert:** the bot posts to the staff channel with:
  - ticket number, category, priority and the player
  - the player's first message
  - a link to the thread (and to the Zammad ticket once v3 lands)

  It pings the role that owns the category. High priority also pings the Staff role.
- **Staff reply in the thread as themselves.** The first staff reply claims the ticket.
- **Closing:** staff or the player can close.
  - The bot posts a closing message and asks for a **CSAT** rating (1–5 buttons).
  - Then it locks and archives the thread.
- **Zammad mirror (one-way):**
  - Creating a ticket creates a Zammad ticket in the matching group.
  - Every message is copied to the Zammad ticket.
  - Closing in Discord closes the ticket in Zammad.
  - Zammad becomes the searchable record, and email and Discord tickets sit side by side.

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

- **Discord roles are the source of truth:** a Staff role, plus an owner role per category.
- **Zammad agents** are only needed for people who work the email inbox. Their Zammad groups should match their Discord categories.

## Stats

**Daily post to the staff channel:**
- Opened and resolved counts, per surface
- Median time to first staff response
- Tickets waiting more than 3 days with no staff reply
- Average CSAT for the period

Full stats can come later, either in a staff-only `/stats` command or in Zammad reporting.

## Tech

- **Base:** Nephthys fork (Python 3.13, PostgreSQL, Piccolo migrations)
- **Discord library:** the Slack integration replaced with discord.py, using forum channels, threads, buttons, modals and slash commands
- **Zammad:** REST API with a dedicated API token, used only for the mirror
- **Hosting:** Docker, on cs-kvm next to Zammad or in the homelab. The bot gets its own Postgres database.

## Phases

1. **v1:** #community-help (auto-reply, resolve and re-open, macros, sticky message)
2. **v2:** #tickets (panel, categories, forms, private threads, routing, CSAT)
3. **v3:** the Zammad mirror, the `support@` email cutover, daily stats
4. **Later:** two-way Zammad ↔ Discord replies, AI tagging, full stats

## Open questions

- Should two-way replies ever happen, or does Discord stay the only place staff answer Discord tickets?
- Who owns the Store role versus the Mod role?
- AI tagging: worth it at current volume? Which model would it use (Hack Club AI)?
- How long to keep closed tickets, and should old threads be deleted or only archived?
- Should OP-resolved community posts count toward staff stats?
