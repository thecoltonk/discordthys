// Seeds a throwaway Zammad (see README "Testing") with what the e2e needs:
// an agent, a second agent, a Discord-style customer and one ticket shaped
// like the ones the bot mirrors (note articles, "<name> (Player via Discord)").
//
//   node test/seed.mjs            -> prints JSON {ticketId, ...} and writes test/.seed.json
//
// Never point this at a real instance: it creates users and tickets.
import { writeFileSync } from "node:fs";

const BASE = process.env.ZAMMAD_URL || "http://localhost:8080";
const ADMIN = process.env.ZAMMAD_ADMIN || "admin@cloudsail.test";
const PASSWORD = process.env.ZAMMAD_PASSWORD || "Cloudsail-Test-1!";

const basic = (login) => "Basic " + Buffer.from(`${login}:${PASSWORD}`).toString("base64");

async function api(method, path, body, { as = ADMIN, from } = {}) {
  const headers = { authorization: basic(as), "content-type": "application/json" };
  if (from) headers.From = String(from);
  const res = await fetch(`${BASE}/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

// The very first user of a fresh Zammad becomes its admin. That call needs a
// session cookie and CSRF token, like the browser's setup wizard.
async function ensureAdmin() {
  const probe = await fetch(`${BASE}/api/v1/users/me`, { headers: { authorization: basic(ADMIN) } });
  if (probe.ok) return;
  const page = await fetch(`${BASE}/`);
  const token = page.headers.get("csrf-token");
  const cookie = (page.headers.getSetCookie?.() || []).map((c) => c.split(";")[0]).join("; ");
  const res = await fetch(`${BASE}/api/v1/users`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": token, cookie },
    body: JSON.stringify({ firstname: "Alex", lastname: "Admin", email: ADMIN, password: PASSWORD }),
  });
  if (!res.ok) throw new Error(`creating the admin failed: ${res.status} ${await res.text()}`);
}

async function ensureUser(login, data) {
  const found = await api("GET", `/users/search?query=${encodeURIComponent(data.email)}&limit=5`);
  const hit = found.find((u) => u.login === login || u.email === data.email);
  if (hit) return hit;
  return api("POST", "/users", { login, password: PASSWORD, ...data });
}

async function ensureGroup(name) {
  const groups = await api("GET", "/groups");
  return groups.find((g) => g.name === name) || api("POST", "/groups", { name, active: true });
}

const note = (ticket_id, body, extra = {}) => ({
  ticket_id,
  body,
  content_type: "text/plain",
  type: "note",
  internal: false,
  ...extra,
});

async function main() {
  await ensureAdmin();

  const store = await ensureGroup("Store");
  const other = await ensureGroup("Other");
  const groups = { [store.id]: "full", [other.id]: "full" };
  const roles = await api("GET", "/roles");
  const role = (name) => roles.find((r) => r.name === name).id;

  const me = await api("GET", "/users/me");
  await api("PUT", `/users/${me.id}`, {
    role_ids: [role("Admin"), role("Agent")],
    group_ids: groups,
  });
  const sam = await ensureUser("sam@cloudsail.test", {
    firstname: "Sam",
    lastname: "Harbour",
    email: "sam@cloudsail.test",
    role_ids: [role("Agent")],
    group_ids: groups,
  });
  const bot = await ensureUser("bot@cloudsail.test", {
    firstname: "Cloudsail",
    lastname: "Support Bot",
    email: "bot@cloudsail.test",
    role_ids: [role("Agent")],
    group_ids: groups,
  });
  // Skip the first-login tours ("clues"), which cover the whole screen.
  for (const login of [ADMIN, sam.login, bot.login]) {
    await api("PUT", "/users/preferences", { intro: true, keyboard_shortcuts_clues: true }, { as: login });
  }
  const player = await ensureUser("discord:123456789012345678", {
    firstname: "PixelPirate",
    lastname: "",
    email: "123456789012345678@discord.invalid",
    role_ids: [role("Customer")],
  });
  const emailer = await ensureUser("jo@example.com", {
    firstname: "Jo",
    lastname: "Mailer",
    email: "jo@example.com",
    role_ids: [role("Customer")],
  });

  // A text module, to check that Enter still picks an entry from Zammad's
  // "::" popup instead of sending.
  const modules = await api("GET", "/text_modules");
  if (!modules.some((m) => m.name === "Rank fixed")) {
    await api("POST", "/text_modules", {
      name: "Rank fixed",
      keywords: "rankfixed",
      content: "Your rank has been re-applied. Please relog to see it.",
      active: true,
    });
  }

  const existing = await api("GET", "/tickets?per_page=100");
  const byTitle = (title) => existing.find((t) => t.title === title);

  // --- A Discord ticket, the way the bot writes one -------------------------
  const who = "PixelPirate (Player via Discord)";
  const ticket = byTitle("T-26 Store") || await seedDiscordTicket();
  async function seedDiscordTicket() {
    const ticket = await api("POST", "/tickets", {
      title: "T-26 Store",
      group_id: store.id,
      customer_id: player.id,
      priority: "2 normal",
      article: {
        ...note(undefined, `${who}\nOpened in #tickets · Category: Store · Priority: Medium\nDiscord ID: 123456789012345678`),
        sender: "Customer",
        origin_by_id: player.id,
      },
    }, { as: bot.login });

    const say = (body, extra, opts) => api("POST", "/ticket_articles", note(ticket.id, body, extra), opts);
    const asPlayer = { sender: "Customer", origin_by_id: player.id };
    await say(`${who}\nhey, I bought the Captain rank about an hour ago and it still hasn't shown up in game`, asPlayer, { as: bot.login });
    await say(`${who}\nIGN is PixelPirate`, asPlayer, { as: bot.login });
    await say(`${who}\nhere's the receipt`, {
      ...asPlayer,
      attachments: [{
        filename: "receipt.png",
        "mime-type": "image/png",
        data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
      }],
    }, { as: bot.login });
    await say("Hi PixelPirate! Sorry about that, let me look up the order.", { sender: "Agent" }, { as: sam.login });
    await say("Found it: the payment went through but the rank command failed on our side.", { sender: "Agent" }, { as: sam.login });
    await say("Tebex shows txn tbx-4821 as complete. Re-running the command by hand, do not refund.", { sender: "Agent", internal: true }, { as: sam.login });
    await say("Riley (Staff via Discord)\nI can confirm the rank is missing on the server side too", { sender: "Agent" }, { as: bot.login });
    await say(`${who}\nthanks! no rush`, asPlayer, { as: bot.login });
    await say(`${who}\nactually one more thing: will I keep my cosmetics?\nI had the red sails from the summer event`, asPlayer, { as: bot.login });
    return ticket;
  }

  // --- A ticket that didn't come from Discord (web form here: creating a
  // customer *email* article over the API needs an outbound email channel,
  // which a throwaway Zammad doesn't have). Used for the composer's hint.
  const mail = byTitle("Refund for order 1182") || await api("POST", "/tickets", {
    title: "Refund for order 1182",
    group_id: other.id,
    customer_id: emailer.id,
    article: {
      subject: "Refund for order 1182",
      body: "Hello, I'd like a refund for order 1182 please.",
      content_type: "text/plain",
      type: "web",
      sender: "Customer",
      internal: false,
    },
  });

  const out = {
    base: BASE,
    password: PASSWORD,
    admin: ADMIN,
    agent: sam.login,
    bot: bot.login,
    playerId: player.id,
    ticketId: ticket.id,
    ticketNumber: ticket.number,
    otherTicketId: mail.id,
  };
  writeFileSync(new URL("./.seed.json", import.meta.url), JSON.stringify(out, null, 2) + "\n");
  console.log(JSON.stringify(out, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
