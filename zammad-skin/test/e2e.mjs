// End-to-end check of the skin against a real Zammad, through the real Worker.
//
//   Zammad 7.2 (Docker)  <-  wrangler dev (this Worker)  <-  Chromium (Playwright)
//
// Usage (see README "Testing"):
//   node test/seed.mjs                      # once, against the throwaway Zammad
//   npx wrangler dev --local-upstream localhost:8080 --upstream-protocol http --port 8787
//   node test/e2e.mjs                       # writes screenshots/*.png
//
// Every send is checked in Zammad's own API, not just in the page: the article
// must exist, be type "note", and have the visibility the composer showed.
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");

const here = path.dirname(fileURLToPath(import.meta.url));
const seed = JSON.parse(readFileSync(path.join(here, ".seed.json"), "utf8"));
const ZAMMAD = process.env.ZAMMAD_URL || seed.base; // straight to Zammad ("before")
const SKINNED = process.env.SKIN_URL || "http://127.0.0.1:8787"; // through the Worker
const SHOTS = path.join(here, "..", "screenshots");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "  ok  " : " FAIL "} ${name}${detail ? "  [" + detail + "]" : ""}`);
}

const basic = (login) => "Basic " + Buffer.from(`${login}:${seed.password}`).toString("base64");
async function api(method, url, body, as = seed.admin) {
  const res = await fetch(`${ZAMMAD}/api/v1${url}`, {
    method,
    headers: { authorization: basic(as), "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${url} -> ${res.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}
const articles = (ticketId) => api("GET", `/ticket_articles/by_ticket/${ticketId}`);
const plain = (html) => String(html).replace(/<br\s*\/?>/gi, "\n").replace(/<\/(div|p)>/gi, "\n").replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim();

async function waitFor(fn, what, timeout = 20000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fn();
    if (last) return last;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${what}`);
}

async function login(page, base, user = seed.agent) {
  await page.goto(`${base}/#login`);
  await page.fill("input[name=username]", user);
  await page.fill("input[name=password]", seed.password);
  await page.click("button[type=submit], .js-submit");
  await page.waitForSelector("#navigation .user-menu, #navigation .menu", { timeout: 30000 });
}

async function openTicket(page, base, id) {
  await page.goto(`${base}/#ticket/zoom/${id}`);
  await page.waitForSelector(`#content_permanent_Ticket-${id}.active .ticket-article-item`, { timeout: 30000 });
  await page.waitForTimeout(1500); // Zammad's own opening animations and scroll
}

const active = ".content.active";
const textarea = `${active} .article-new .js-textarea`;
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name) });

async function composer(page) {
  return page.evaluate(() => {
    const content = document.querySelector(".content.active");
    const form = content.querySelector("form.article-add");
    const main = content.querySelector(".main");
    const pickerEl = form && form.querySelector(".editControls .pop-select");
    const box = form && form.getBoundingClientRect();
    return {
      chat: content.classList.contains("cs-chat"),
      composer: content.classList.contains("cs-composer"),
      type: form ? form.querySelector("[name=type]").value : null,
      internal: form ? form.querySelector("[name=internal]").value === "true" : null,
      pickerVisible: !!pickerEl && getComputedStyle(pickerEl).display !== "none",
      // "At the newest message": the last bubble is fully visible, above the composer.
      atBottom: (() => {
        const items = content.querySelectorAll(".ticket-article-item");
        const last = items[items.length - 1].getBoundingClientRect();
        return last.top >= main.getBoundingClientRect().top && last.bottom <= (box ? box.top + 1 : window.innerHeight);
      })(),
      composerInView: !!box && box.bottom <= window.innerHeight && box.top >= 0,
      bubbles: content.querySelectorAll(".ticket-article-item").length,
    };
  });
}

async function typeAndSend(page, lines) {
  await page.focus(textarea);
  for (let i = 0; i < lines.length; i++) {
    if (i) await page.keyboard.press("Shift+Enter");
    await page.keyboard.type(lines[i]);
  }
  await page.keyboard.press("Enter");
}

async function newestArticle(ticketId, marker) {
  return waitFor(async () => (await articles(ticketId)).find((a) => plain(a.body).includes(marker)), `article "${marker}"`);
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const consoleErrors = [];

  // A fresh Discord-style ticket for the sends, so the seeded one stays as it
  // is for the before/after pictures.
  const player = await api("GET", `/users/${seed.playerId}`);
  const who = "PixelPirate (Player via Discord)";
  const work = await api("POST", "/tickets", {
    title: `T-${Math.floor(Date.now() / 1000) % 100000} Server Issue (Bug Report)`,
    group: "Store",
    customer_id: player.id,
    article: { body: `${who}\nthe boat shop NPC at spawn won't open its menu`, content_type: "text/plain", type: "note", internal: false, sender: "Customer", origin_by_id: player.id },
  }, seed.bot);
  const say = (body) => api("POST", "/ticket_articles", { ticket_id: work.id, body: `${who}\n${body}`, content_type: "text/plain", type: "note", internal: false, sender: "Customer", origin_by_id: player.id }, seed.bot);
  await say("tried relogging, same thing");

  // ---------------------------------------------------------------- before
  console.log("\nBefore: stock Zammad, no Worker");
  const before = await context.newPage();
  await login(before, ZAMMAD);
  await openTicket(before, ZAMMAD, seed.ticketId);
  await shot(before, "01-before-stock-zammad.png");
  check("stock Zammad has no skin classes", !(await before.evaluate(() => document.documentElement.classList.contains("cs-skin"))));
  await before.close();

  // ----------------------------------------------------------------- after
  console.log("\nAfter: the same ticket through the Worker");
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  await page.goto(`${SKINNED}/#login`);
  await page.waitForSelector("input[name=username]");
  const loginPage = await page.evaluate(() => ({
    css: !!document.querySelector('link[href="/__cloudsail/login.css"]'),
    footerHidden: !document.querySelector(".login .poweredBy") || getComputedStyle(document.querySelector(".login .poweredBy")).display === "none",
  }));
  check("login page: the Worker's login styling is applied", loginPage.css && loginPage.footerHidden, JSON.stringify(loginPage));
  await login(page, SKINNED);
  await openTicket(page, SKINNED, seed.ticketId);
  await shot(page, "02-after-cloudsail-skin.png");

  let state = await composer(page);
  check("skin is on: bubbles and chat composer", state.chat && state.composer);
  check("composer defaults to a public note", state.type === "note" && state.internal === false, `type=${state.type} internal=${state.internal}`);
  check("article-type picker is hidden", !state.pickerVisible);
  check("opens scrolled to the newest message", state.atBottom);
  check("composer is pinned in view", state.composerInView);
  const sides = await page.evaluate(() => {
    const mid = (el) => { const r = el.querySelector(".textBubble").getBoundingClientRect(); return (r.left + r.right) / 2; };
    const list = document.querySelector(".content.active .ticket-article").getBoundingClientRect();
    const centre = (list.left + list.right) / 2;
    const items = [...document.querySelectorAll(".content.active .ticket-article-item")];
    return {
      playerLeft: items.filter((i) => i.classList.contains("customer")).every((i) => mid(i) < centre),
      agentRight: items.filter((i) => i.classList.contains("agent")).every((i) => mid(i) > centre),
      groups: items.filter((i) => i.classList.contains("cs-first")).length,
      total: items.length,
      viaHidden: items.filter((i) => i.classList.contains("cs-via")).every((i) => getComputedStyle(i.querySelector(".richtext-content > :first-child")).display === "none"),
      actionsHidden: items.every((i) => getComputedStyle(i.querySelector(".js-article-actions")).display === "none"),
      stamp: getComputedStyle(items[items.length - 1], "::after").content,
    };
  });
  check("player bubbles left, agent bubbles right", sides.playerLeft && sides.agentRight);
  check("messages are grouped by sender", sides.groups < sides.total, `${sides.total} messages in ${sides.groups} groups`);
  check("each group ends with a timestamp", /\d/.test(sides.stamp), sides.stamp);
  check('"(Player via Discord)" line shown as the sender name instead', sides.viaHidden);
  check("per-message action rows are folded away", sides.actionsHidden);

  // A bubble still unfolds Zammad's own details and actions on click.
  await page.click(`${active} .ticket-article-item.agent .textBubble`);
  await page.waitForTimeout(700);
  const unfolded = await page.evaluate(() => {
    const item = document.querySelector(".content.active .ticket-article-item.state--folde-out");
    return !!item && getComputedStyle(item.querySelector(".js-article-actions")).display !== "none";
  });
  check("clicking a bubble shows Zammad's details and actions", unfolded);
  await shot(page, "07-bubble-details-on-click.png");
  await page.click(`${active} .ticket-article-item.state--folde-out .textBubble`);
  await page.waitForTimeout(500);

  // ----------------------------------------------------- Enter = public note
  console.log("\nSending");
  await openTicket(page, SKINNED, work.id);
  const startCount = (await articles(work.id)).length;

  const m1 = `enter-sends-${Date.now()}`;
  await typeAndSend(page, [`Thanks for the report! ${m1}`]);
  const a1 = await newestArticle(work.id, m1);
  check("Enter creates an article", !!a1);
  check("... of type note (never email/phone)", a1.type === "note", `type=${a1.type}`);
  check("... that is public", a1.internal === false, `internal=${a1.internal}`);
  check("... on the current ticket, from the agent", a1.ticket_id === work.id && a1.sender === "Agent");
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a1.id}"]`);
  await page.waitForTimeout(600);
  check("composer is empty again and still a public note", await page.evaluate((sel) => document.querySelector(sel).textContent.trim() === "", textarea) && (await composer(page)).internal === false);
  check("stays scrolled to the newest message after sending", (await composer(page)).atBottom);
  await shot(page, "03-enter-sent-public-note.png");

  // ------------------------------------------------- Shift+Enter = newline
  const m2 = `shift-enter-${Date.now()}`;
  await page.focus(textarea);
  await page.keyboard.type(`first line ${m2}`);
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("second line");
  await page.waitForTimeout(300);
  const midway = (await articles(work.id)).length;
  check("Shift+Enter does not send", midway === startCount + 1, `${midway - startCount - 1} extra articles`);
  check("Shift+Enter adds a line break in the composer", await page.evaluate((sel) => { const el = document.querySelector(sel); return el.querySelectorAll("br, div").length > 0 && /first line[\s\S]*second line/.test(el.innerText); }, textarea));
  await shot(page, "04-shift-enter-newline.png");
  await page.keyboard.press("Enter");
  const a2 = await newestArticle(work.id, m2);
  const lines = plain(a2.body).split(/\n+/).map((l) => l.trim()).filter(Boolean);
  check("both lines arrive in one public note", lines.length === 2 && lines[1] === "second line" && a2.type === "note" && a2.internal === false, JSON.stringify(lines));

  // ------------------------------------------------------- internal toggle
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a2.id}"]`);
  await page.click(`${active} .article-new .js-toggleVisibility`);
  await page.waitForTimeout(400);
  state = await composer(page);
  check("the toggle switches the composer to internal", state.internal === true && state.type === "note");
  const yellow = await page.evaluate(() => getComputedStyle(document.querySelector(".content.active .article-add .textBubble")).backgroundColor);
  check("internal composer is yellow", yellow === "rgb(255, 241, 184)", yellow);
  const m3 = `internal-${Date.now()}`;
  await page.focus(textarea);
  await page.keyboard.type(`Checked the NPC config, the shop id is wrong. ${m3}`);
  await page.waitForTimeout(300);
  await shot(page, "05-internal-toggle-on.png");
  await page.keyboard.press("Enter");
  const a3 = await newestArticle(work.id, m3);
  check("with the toggle on, Enter creates an internal note", a3.type === "note" && a3.internal === true, `type=${a3.type} internal=${a3.internal}`);
  await page.waitForSelector(`${active} .ticket-article-item.is-internal[data-id="${a3.id}"]`);
  await page.waitForTimeout(600);
  const bubble = await page.evaluate((id) => getComputedStyle(document.querySelector(`.content.active .ticket-article-item[data-id="${id}"] .textBubble`)).backgroundColor, a3.id);
  check("the internal bubble is yellow", bubble === "rgb(255, 241, 184)", bubble);
  check("the toggle stays on until switched off", (await composer(page)).internal === true);
  await shot(page, "06-internal-note-sent.png");
  await page.click(`${active} .article-new .js-toggleVisibility`);
  await page.waitForTimeout(400);
  const m4 = `public-again-${Date.now()}`;
  await typeAndSend(page, [`Fixed, try the shop again. ${m4}`]);
  const a4 = await newestArticle(work.id, m4);
  check("switched off again, the next reply is public", a4.type === "note" && a4.internal === false);

  // ----------------------------------------- Zammad's "::" text modules
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a4.id}"]`);
  const beforeModule = (await articles(work.id)).length;
  await page.focus(textarea);
  await page.keyboard.type("::rankfixed", { delay: 60 });
  const popup = await page.waitForSelector(`${active} .article-add .shortcut.open`, { state: "attached", timeout: 8000 }).catch(() => null);
  check('typing "::" opens Zammad\'s text-module popup', !!popup);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1200);
  const inserted = await page.evaluate((sel) => document.querySelector(sel).innerText, textarea);
  check("Enter picks the text module instead of sending", /re-applied/.test(inserted) && (await articles(work.id)).length === beforeModule, inserted.trim().slice(0, 60));
  await page.keyboard.press("Enter");
  const a4b = await newestArticle(work.id, "re-applied");
  check("... and the next Enter sends it as a public note", a4b.type === "note" && a4b.internal === false);
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a4b.id}"]`);

  // ------------------------------------------------------------ live update
  console.log("\nLive updates and scrolling");
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a4.id}"]`);
  const live = await say(`it works now, thank you! live-${Date.now()}`);
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${live.id}"]`, { timeout: 30000 });
  await page.waitForTimeout(800);
  state = await composer(page);
  check("a new message from the player appears without a reload", true);
  check("... and the view follows it", state.atBottom, JSON.stringify(await page.evaluate(() => { const c = document.querySelector(".content.active"); const m = c.querySelector(".main"); const items = c.querySelectorAll(".ticket-article-item"); const l = items[items.length - 1].getBoundingClientRect(); const f = c.querySelector("form.article-add").getBoundingClientRect(); return { top: m.scrollTop, h: m.scrollHeight, ch: m.clientHeight, lastTop: Math.round(l.top), lastBottom: Math.round(l.bottom), formTop: Math.round(f.top), mainTop: Math.round(m.getBoundingClientRect().top) }; })));
  check("... grouped and labelled like the rest", await page.evaluate((id) => document.querySelector(`.ticket-article-item[data-id="${id}"]`).classList.contains("cs-via"), live.id));

  // Scrolled up: the jump button appears; a new message brings the view to
  // the newest one (Zammad's own behaviour, which the skin leaves alone).
  for (let i = 0; i < 8; i++) await say(`padding the thread so it scrolls (${i + 1}/8)`);
  const pad = await say("last padding message");
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${pad.id}"]`, { timeout: 30000 });
  await page.waitForTimeout(800);
  await page.evaluate(() => { document.querySelector(".content.active .main").scrollTop = 0; });
  await page.waitForTimeout(400);
  check('scrolled up: a "jump to newest" button appears', await page.isVisible(`${active} .cs-jump`));
  await page.click(`${active} .cs-jump`);
  await page.waitForTimeout(500);
  check("... and returns to the newest message", (await composer(page)).atBottom);
  await page.evaluate(() => { document.querySelector(".content.active .main").scrollTop = 0; });
  await page.waitForTimeout(400);
  const live2 = await say(`one more thing live-${Date.now()}`);
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${live2.id}"]`, { timeout: 30000 });
  await page.waitForTimeout(1000);
  check("a new message while scrolled up scrolls to it (as stock Zammad does)", (await composer(page)).atBottom);

  // ------------------------------------------------------------ attachments
  console.log("\nAttachments");
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
  const attached = () => page.evaluate(() => [...document.querySelectorAll(".content.active .article-add .attachments .attachment")].map((a) => a.textContent.replace(/\s+/g, " ").trim()));
  const waitAttached = (n) => waitFor(async () => (await attached()).length === n, `${n} attachment(s) in the composer`);

  await page.setInputFiles(`${active} .article-add .article-attachment input[type=file]`, { name: "button.png", mimeType: "image/png", buffer: png });
  await waitAttached(1);
  check("attach with the paperclip button", true);

  // Paste and drop are synthetic events carrying a real File, which is what
  // the browser hands the page for a pasted screenshot or a dropped file.
  await page.focus(textarea);
  await page.evaluate(({ sel, b64 }) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const data = new DataTransfer();
    data.items.add(new File([bytes], "pasted.png", { type: "image/png" }));
    document.querySelector(sel).dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, { sel: textarea, b64: png.toString("base64") });
  await waitAttached(2);
  check("paste a screenshot: it becomes an attachment, not inline", await page.evaluate((sel) => !document.querySelector(sel).querySelector("img"), textarea));

  await page.evaluate(({ b64 }) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const data = new DataTransfer();
    data.items.add(new File([bytes], "dropped.png", { type: "image/png" }));
    const target = document.querySelector(".content.active .ticket-article-item .textBubble");
    for (const type of ["dragenter", "dragover", "drop"]) {
      target.dispatchEvent(new DragEvent(type, { dataTransfer: data, bubbles: true, cancelable: true }));
    }
  }, { b64: png.toString("base64") });
  await waitAttached(3);
  check("drop a file anywhere on the conversation", true);
  await page.waitForTimeout(400);
  await shot(page, "08-attachments-in-composer.png");

  const m5 = `files-${Date.now()}`;
  await typeAndSend(page, [`Here are the screenshots. ${m5}`]);
  const a5 = await newestArticle(work.id, m5);
  const names = (a5.attachments || []).map((f) => f.filename).sort();
  check("all three arrive as attachments on a public note", a5.type === "note" && a5.internal === false && names.join() === "button.png,dropped.png,pasted.png", names.join());

  // A file with no text still sends (Zammad itself would refuse it).
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a5.id}"]`);
  await page.setInputFiles(`${active} .article-add .article-attachment input[type=file]`, { name: "only-a-file.png", mimeType: "image/png", buffer: png });
  await waitAttached(1);
  await page.focus(textarea);
  await page.keyboard.press("Enter");
  const a6 = await newestArticle(work.id, "only-a-file.png");
  check("a file without text sends, named in the message", a6.type === "note" && (a6.attachments || []).length === 1, plain(a6.body));

  // Enter on an empty composer does nothing.
  await page.waitForSelector(`${active} .ticket-article-item[data-id="${a6.id}"]`);
  const beforeEmpty = (await articles(work.id)).length;
  await page.focus(textarea);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1500);
  check("Enter on an empty composer sends nothing", (await articles(work.id)).length === beforeEmpty);

  // ------------------------------------------------- sidebar still works
  console.log("\nSidebar");
  await page.selectOption(`${active} .sidebar[data-tab="ticket"] select[name="priority_id"]`, { label: "3 high" });
  await page.click(`${active} .attributeBar .js-submit`);
  await waitFor(async () => (await api("GET", `/tickets/${work.id}`)).priority_id === 3, "priority change saved");
  check("sidebar fields and Update still save (priority -> 3 high)", true);
  check("... without creating an article", (await articles(work.id)).length === beforeEmpty);

  // ------------------------------------------------------ non-Discord ticket
  console.log("\nNon-Discord ticket");
  await openTicket(page, SKINNED, seed.otherTicketId);
  const hint = await page.evaluate(() => document.querySelector(".content.active .cs-bar")?.textContent || "");
  check("a ticket that isn't from Discord warns that notes aren't emailed", /not emailed/.test(hint), hint.trim());
  await shot(page, "09-non-discord-ticket-warning.png");
  await page.click(`${active} .cs-bar-switch`);
  await page.focus(textarea); // stock Zammad shows its controls once the form is focused
  await page.waitForTimeout(900);
  state = await composer(page);
  check('"Classic composer" gives that ticket Zammad\'s own form back, type picker included', state.chat && !state.composer && state.pickerVisible);
  await shot(page, "10-classic-composer-for-one-ticket.png");
  await page.click(`${active} .cs-bar-switch`);
  await page.waitForTimeout(500);
  check("... and back", (await composer(page)).composer);

  // --------------------------------------------------------------- fail safe
  console.log("\nFail safe");
  await openTicket(page, SKINNED, work.id);

  // 1. An element the composer relies on is missing: hands off.
  await page.evaluate(() => {
    const toggle = document.querySelector(".content.active .article-add .js-toggleVisibility");
    window.__toggleParent = toggle.parentElement;
    window.__toggle = toggle;
    toggle.remove();
  });
  await page.focus(textarea);
  await page.waitForTimeout(900);
  state = await composer(page);
  check("expected element missing: the chat composer steps aside", !state.composer && state.pickerVisible);
  const countBefore = (await articles(work.id)).length;
  await page.keyboard.type("not sent");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1500);
  check("... and Enter is Zammad's again (a new line, nothing sent)", (await articles(work.id)).length === countBefore);
  await page.evaluate((sel) => { window.__toggleParent.appendChild(window.__toggle); document.querySelector(sel).innerHTML = ""; document.querySelector(sel).dispatchEvent(new Event("input", { bubbles: true })); }, textarea);
  await page.waitForTimeout(500);
  check("... and it comes back when the element does", (await composer(page)).composer);

  // 2. Per-user switch (localStorage).
  await page.click(`${active} .cs-bar-switch`);
  await page.waitForTimeout(600);
  const off = await page.evaluate(() => ({
    html: document.documentElement.classList.contains("cs-skin"),
    left: document.querySelectorAll(".cs-chat, .cs-composer, .cs-first, .cs-send, .cs-jump").length,
    stored: localStorage.getItem("cloudsail.skin"),
    button: document.querySelector(".content.active .cs-bar-switch")?.textContent,
  }));
  check('"Classic view" turns the skin off for this browser', !off.html && off.left === 0 && off.stored === "off", JSON.stringify(off));
  await shot(page, "11-per-user-off-classic-view.png");
  await page.reload();
  await page.waitForSelector(`${active} .ticket-article-item`, { timeout: 30000 });
  await page.waitForTimeout(1500);
  check("... and it stays off after a reload", !(await page.evaluate(() => document.documentElement.classList.contains("cs-skin"))));
  await page.click(`${active} .cs-bar-switch`);
  await page.waitForTimeout(600);
  check('"Chat view" turns it back on', (await composer(page)).composer);

  // 3. Kill switch reaching an open tab: the Worker's status says "off".
  await page.route("**/__cloudsail/status", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ skin: false }) }));
  await page.evaluate(() => { Date.now = ((now) => () => now() + 10 * 60 * 1000)(Date.now.bind(Date)); document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => page.evaluate(() => !document.documentElement.classList.contains("cs-skin")), "skin off after the kill switch");
  const killed = await page.evaluate(() => document.querySelectorAll(".cs-chat, .cs-composer, .cs-first, .cs-send, .cs-jump, .cs-bar").length);
  check("kill switch: an open tab drops the skin without a reload", killed === 0, `${killed} skin elements left`);
  await page.unroute("**/__cloudsail/status");

  // 4. Zammad set to confirm every "make public": the composer stays classic.
  const settings = await api("GET", "/settings");
  const confirm = settings.find((x) => x.name === "ui_ticket_zoom_article_visibility_confirmation_dialog");
  await api("PUT", `/settings/${confirm.id}`, { state_current: { value: true } });
  try {
    const strict = await context.newPage();
    await openTicket(strict, SKINNED, work.id);
    const s4 = await composer(strict);
    check('Zammad\'s "visibility confirmation dialog" setting on: bubbles only, classic composer', s4.chat && !s4.composer, JSON.stringify({ chat: s4.chat, composer: s4.composer }));
    await strict.close();
  } finally {
    await api("PUT", `/settings/${confirm.id}`, { state_current: { value: false } });
  }

  // ------------------------------------------------------------ dark theme
  await api("PUT", "/users/preferences", { theme: "dark" }, seed.agent);
  const dark = await context.newPage();
  await openTicket(dark, SKINNED, seed.ticketId);
  await shot(dark, "12-dark-theme.png");
  check("dark theme: the skin follows Zammad's theme", await dark.evaluate(() => document.documentElement.getAttribute("data-theme") === "dark" && document.querySelector(".content.active").classList.contains("cs-composer")));
  await dark.close();
  await api("PUT", "/users/preferences", { theme: "light" }, seed.agent);

  const skinErrors = consoleErrors.filter((e) => /cloudsail|skin/i.test(e));
  check("no errors from the skin in the browser console", skinErrors.length === 0, skinErrors.join(" | ").slice(0, 200));

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  writeFileSync(path.join(SHOTS, "e2e-results.txt"), results.map((r) => `${r.ok ? "ok  " : "FAIL"}  ${r.name}${r.detail ? "  [" + r.detail + "]" : ""}`).join("\n") + "\n");
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
