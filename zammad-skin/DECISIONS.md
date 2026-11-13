# Cloudsail skin: decisions

Judgment calls made while building the skin, and what forced them. Numbered S1… so they don't collide with the bot's DECISIONS.md. How to use and deploy it is in [README.md](README.md).

Built and tested against **Zammad 7.2.3** in Docker (`zammad/zammad-docker-compose:7.2.3-0000`), through the Worker running locally in workerd (`wrangler dev`), with Playwright and Chromium. "Checked" below means checked there. What couldn't be checked is listed at the end.

## Scope

S1. **The skin targets Zammad's legacy desktop UI.** It's what 7.2 serves by default (`ui_desktop_beta_switch` is off) and what the old login Worker already styles. The new `/desktop` and `/mobile` apps are different front ends; the skin recognises them by path and does nothing there.

S2. **Only `zammad-skin/` changed**, plus the one-line pointer in DEPLOY.md. Zammad's source, settings and data are untouched. The test rig needed two changes to the *throwaway* Zammad's compose setup, neither in this repo: Elasticsearch off, and nginx's IPv6 listener removed (the build sandbox has no IPv6).

## The big one: restyle Zammad's composer, don't replace it

S3. **The chat composer is Zammad's own reply form, restyled and driven.** The alternative was a separate input that posts to `/api/v1/ticket_articles`. That would have been simpler to reason about, but it loses everything the native form does: draft autosave in the task bar, `::` text modules, `@@` mentions, knowledge-base inserts, upload limits and progress, time accounting, core workflows, and saving sidebar changes together with the reply. So:
   - Enter clicks Zammad's own **Update** button.
   - The note/visibility state is set by clicking Zammad's own controls (the type item `note`, the visibility toggle), the same clicks an agent makes.
   - Files go into Zammad's own file input.

   Read in Zammad's source and checked: the reply form's hidden fields `type` and `internal` are exactly what `TicketZoom#submit` sends, so they can be trusted as the truth.

S4. **"Never email/phone" is enforced at send time, from those hidden fields.** Before Enter triggers Update, the form must read `type=note` and `internal=<what the toggle shows>`. If it doesn't after the skin's own attempt to set it, nothing is sent and the bar says why. Checked in Zammad's API for every send in the test: `type: note`, the right `internal`, on the open ticket, sender Agent.

S5. **Zammad's default for a new note is *internal*** (`ui_ticket_zoom_article_note_new_internal`, on by default). The skin doesn't rely on that setting either way: each freshly rendered form is set to public unless the agent's toggle for that ticket is on. Checked with the setting at its default (internal).

S6. **Switching to public with an empty reply doesn't mark the ticket as changed.** Zammad's `formDiff` ignores `type` and `internal` unless there's also text or a file (read in source), so opening a ticket doesn't light up "Discard your unsaved changes". Checked: the reset button only appears once text is typed.

S7. **If "Article - visibility confirmation dialog" is on, the composer stays classic.** That setting makes Zammad ask every time a note is made public. The skin's default is public, so honouring the request would mean either a dialog on every ticket or silently bypassing an admin's choice. It does neither: bubbles apply, the composer is Zammad's. The setting is off by default. Checked with the setting on.

S8. **A restored draft that isn't a note is left alone.** Forcing an email draft to `note` makes Zammad clear its To/CC/subject (read in source: `setArticleTypePre`). A ticket that opens with such a draft gets the classic composer for that session. Not exercised live (needs an outbound email channel, see the end).

## Sending

S9. **Enter sends; Shift+Enter is Zammad's own line break.** The key handler runs in the capture phase and only handles a bare Enter inside a chat composer. It steps aside when
   - a Zammad popup owns Enter (`::` text modules, mentions: checked, Enter inserts the module and the next Enter sends it),
   - an IME is composing,
   - the composer is empty (nothing is sent),
   - an upload is still running (the bar says so).

   Cost: Enter no longer starts a new bullet in a list. Shift+Enter still breaks the line inside one. That's the trade for chat behaviour.

S10. **Ctrl/Cmd+Enter and Zammad's other shortcuts are untouched.**

S11. **A file with no text still sends.** Zammad refuses an article with attachments and no body ("Please enter a text"). In a chat that's a normal message, so the skin fills the body with `📎 <file names>` and sends. The agent sees exactly that text in the bubble.

S12. **Sending follows Zammad's "Stay on tab / Close tab" choice** in the bottom bar, because it *is* Update. An agent who has "Close tab" selected will see the ticket close after each message; "Stay on tab" is the default.

S13. **Sidebar changes travel with the next message.** Change the state to closed, type "all done", press Enter: one update, as with Update. Checked that a sidebar change alone still saves through Update without creating an article.

## Internal notes

S14. **The toggle is Zammad's own visibility control**, restyled as a labelled pill, so there's one source of truth. On: yellow composer with "Internal note · only staff can see this", yellow pill, yellow send button; the sent bubble is yellow with "Internal note · <time>".
   - **It stays on for that ticket until switched off** (in memory, until reload). A toggle that flips itself back after one message is the dangerous kind: the second "internal" thought would go to the player. The opposite mistake (a reply left internal) is visible in yellow and fixed with Zammad's "set to public".
   - A note draft restored by Zammad keeps the visibility it was written with.

## Bubbles

S15. **Colours are placeholders for the Cloudsail palette.** The repo defines no brand colours and the build sandbox couldn't reach cloudsail.uk, so the tokens at the top of `skin.css` are an ocean blue (`#0f7a9c`) picked to be distinct from Signal's and Zammad's blues. One block to change, light and dark.

S16. **Bubbles are tints with Zammad's normal text colour, not solid brand colour with white text.** Player grey, agent light blue, internal yellow. A solid outgoing bubble looks more like Signal, but article bodies are arbitrary HTML (emails will arrive with their own colours and links once `support@` is live), and tints keep all of it legible in both themes.

S17. **Nothing inside an article's content is modified.** Grouping, names, times and day dividers are classes and CSS variables on the article element, drawn with pseudo-elements. Zammad's highlighter stores positions inside the content, and its article list finds elements by `data-id`; neither notices. The only elements the skin adds are the send button, the "jump to newest" button and the bar under the composer.

S18. **The bot's first line becomes the sender label.** `PixelPirate (Player via Discord)` → name "PixelPirate"; `Riley (Staff via Discord)` → "Riley · via Discord" (those are written by the bot's Zammad user, so without this they'd all group as the bot). The line is hidden with CSS, only when it is the first line and matches exactly; the text is still in the article. Other articles use Zammad's "From".

S19. **Grouping:** consecutive messages from the same sender with the same visibility, on the same day, less than 5 minutes apart. Name above the first, avatar and time at the last.

S20. **Times are absolute, in the browser's locale** ("02:58 PM" / "14:58"), with "Today / Yesterday / date" dividers. Zammad's relative time ("3 minutes ago") is still there when a bubble is clicked.

S21. **"Hide the clutter" means fold, not remove.** From/Channel were already folded until a click. The action row (set to internal, split, delete) and relative time now fold the same way. Per-message reply/forward actions (email and other channels) are hidden while the chat composer is on, because they'd start a draft the composer turns back into a note; the selector comes from Zammad's source (`emailReply`, `emailReplyAll`, `emailForward`, …) and wasn't exercised on a real email article.

## Attachments

S22. **A pasted image becomes an attachment, not an inline image.** Zammad embeds pasted images in the body, and the article's attachment list (what the bot forwards to Discord) leaves embedded images out. As a file, the player actually receives the screenshot. Pasted text with a preview image (from Word, a browser) is left to Zammad and pastes as text.

S23. **Drop anywhere on the conversation.** Zammad only accepts drops on the form. Drops elsewhere in the ticket are handed to the same file input; drops on the form are still Zammad's.

## Scrolling and live updates

S24. **Live updates are Zammad's.** New articles arrive over its WebSocket and render through its own code; the skin restyles them as they appear. Checked: a message posted over the API shows up without a reload, grouped and labelled. The WebSocket upgrade passes through the Worker (checked locally: `101` through workerd).

S25. **Zammad already scrolls to the end when a ticket opens and whenever an article is added, even if you had scrolled up.** That's kept. The skin adds:
   - staying at the bottom while content grows (images loading, a taller composer);
   - a fix for short conversations: Zammad decides they "fit" and scrolls to the *top*, which left the newest bubble under the pinned composer (found in the test);
   - a "jump to newest" button when scrolled up.

   Not changed: being pulled down by a new message while reading history. Stopping that means fighting Zammad's own scroll call.

## Tickets that aren't from Discord

S26. **The brief says every reply is a public note, and that's what the composer does on every ticket.** But a public note on an email ticket is never emailed: the customer hears nothing. `support@cloudsail.uk` isn't cut over yet (PRODUCT.md §3), so today this affects nobody, but it would be a silent failure later. So on a ticket whose customer isn't a Discord customer (login `discord:…` or `…@discord.invalid`), the bar under the composer says "Not a Discord ticket: your reply is saved as a note and is not emailed to the customer" and offers **Classic composer**, which gives that one ticket Zammad's form back (type picker included). Nothing is sent as email unless an agent deliberately does that.
   - This reads the ticket's customer from Zammad's in-page models (`App.Ticket`, `App.User`). If that fails, no warning is shown and nothing else changes.
   - **This will need a decision before the email cutover**: keep the warning, or make email tickets classic by default.

## Switches and failure

S27. **Per-user switch is per browser** (`localStorage`), as asked. It's reachable from the composer ("Classic view" / "Chat view"), from a link (`?cloudsail-skin=off`), and from the console. It isn't a Zammad user preference: that would mean writing to Zammad.

S28. **The kill switch reaches open tabs.** `SKIN_ENABLED=false` stops the injection, but Zammad is a single-page app that agents keep open for days. So `skin.js` asks `/__cloudsail/status` every 5 minutes and when the tab gets focus, and takes the skin off when told. A 404 (the skin Worker replaced by the legacy one) counts as off; any other failure changes nothing. Checked with the status answer faked in the browser, and the Worker's answer checked for both values.

S29. **Anything but an explicit off value leaves the skin on** (`false`, `0`, `off`, `no`). A typo in the variable can't silently disable it; it's set in `wrangler.toml`.

S30. **CSS can't act alone.** Every rule is scoped under classes `skin.js` adds (`html.cs-skin`, `.cs-chat`, `.cs-composer`), and inside `@media screen`, so printing is stock. The only unscoped rules style the small bar the script itself creates.

S31. **Labels the skin adds are English** ("Message", "Internal note", "Enter to send …"). Zammad's own strings stay in the agent's language.

## Worker

S32. **CSS and JS are bundled into the Worker as text modules**, not Workers Static Assets: one script, one deploy, nothing that could be served stale separately. Injected URLs carry a content hash and are cached as immutable; the HTML is `no-store`, so a deploy reaches agents on their next page load.

S33. **The logout rewrite is the old code, moved as is**, including reading every `/api/*` JSON body to look for the Access host. Two guards were added: responses that can't have a body (101, 204, 205, 304) and WebSocket upgrades are returned untouched instead of rebuilt.

S34. **New Worker name (`cloudsail-support-skin`), old Worker kept.** The old Worker's name isn't in the repo. A new name leaves it intact as a rollback target; the README also covers reusing its name. `wrangler.legacy.toml` deploys the old code to the new Worker, which is the quickest full rollback from a terminal.

S35. **A Worker's entry module can only export handlers** (found when `wrangler dev` refused the first version; the dry-run build hadn't). So the Worker is tested over HTTP against a stub origin rather than by importing its functions.

## Not verified live

- **The deploy itself.** No Cloudflare account from the build environment: `wrangler deploy`, the route on the `cloudsail.uk` zone, moving the route off the old Worker, `wrangler rollback`, and the dashboard variable were not run. Both configs build (`wrangler deploy --dry-run`), and the Worker runs in workerd locally.
- **Behind Cloudflare Access.** The login page was checked against a plain Zammad login (stylesheet applied, footer hidden), not with the real OIDC button. The logout rewrite was checked against a stub origin, not real Access redirects. Whether Access lets `/__cloudsail/*` through for logged-in agents wasn't checked (the old Worker's `/__cloudsail/login.css` suggests it does).
- **The production Zammad.** Its exact version, settings, custom attributes, core workflows, time accounting, checklists and any admin-added CSS. The skin was checked on a stock 7.2.3.
- **Cloudsail's real colours** (S15).
- **The Discord round trip.** That a note sent from the skin reaches the player's thread, and that its attachments are forwarded: the bot wasn't running. The articles are the same shape as the ones Zammad's own Update creates (type note, public, sender Agent).
- **Real email tickets.** An email article needs an outbound email channel, which the throwaway Zammad doesn't have; a web-form ticket stood in for "not from Discord". S8 and the hidden reply/forward actions (S21) come from reading Zammad's source.
- **Browsers other than Chromium.** Firefox and Safari weren't run. The CSS uses nothing newer than `mask`, `inset` and `min()`.
- **Real paste and drag from the desktop.** The test dispatches paste and drop events carrying real `File` objects; the operating system's clipboard and drag weren't involved. Large uploads and the progress strip weren't exercised.
- **Narrow windows, right-to-left languages, and a non-English Zammad.**
- **Long-running tabs.** The status poll and the 5-error cut-out were triggered by hand, not by days of use.
