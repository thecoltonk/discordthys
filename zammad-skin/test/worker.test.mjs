// Worker behaviour against a stub origin: asset routes, injection, the kill
// switch, the logout rewrite and pass-through. Runs the real Worker in
// workerd via `wrangler dev`, so HTMLRewriter and friends are the real ones.
//
//   npm test
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const ORIGIN_PORT = 18080;
const ACCESS_LOGOUT = "https://cloudsail.cloudflareaccess.com/cdn-cgi/access/logout";

// A stand-in for Zammad: just enough routes to exercise each branch.
const origin = createServer((req, res) => {
  const send = (status, headers, body) => { res.writeHead(status, headers); res.end(body); };
  switch (req.url.split("?")[0]) {
    case "/":
      return send(200, { "content-type": "text/html; charset=utf-8" }, "<!doctype html><html><head><title>Zammad</title></head><body><div id=app></div></body></html>");
    case "/logout":
      return send(302, { location: ACCESS_LOGOUT });
    case "/elsewhere":
      return send(302, { location: "https://cloudsail.cloudflareaccess.com/cdn-cgi/access/login" });
    case "/api/v1/signshow":
      return send(200, { "content-type": "application/json" }, // "escaped" is written with JSON's optional \/ escape, as some serializers emit it.
        `{"config":{"logout_url":"${ACCESS_LOGOUT}","escaped":"${ACCESS_LOGOUT.replace(/\//g, "\\/")}","login_url":"https://cloudsail.cloudflareaccess.com/login"}}`);
    case "/api/v1/tickets":
      return send(200, { "content-type": "application/json" }, JSON.stringify([{ id: 1, title: "T-26 Store" }]));
    case "/api/v1/empty":
      return send(204, { "content-type": "application/json" });
    case "/assets/app.js":
      return send(200, { "content-type": "text/javascript" }, "console.log('zammad')");
    case "/__cloudsail/unknown":
      return send(404, { "content-type": "text/plain" }, "from origin");
    default:
      return send(404, { "content-type": "text/plain" }, "not found");
  }
});

function startWorker(port, vars = []) {
  const child = spawn("npx", ["wrangler", "dev", "--local-upstream", `127.0.0.1:${ORIGIN_PORT}`, "--upstream-protocol", "http", "--port", String(port), "--inspector-port", String(port + 1000), "--ip", "127.0.0.1", ...vars.flatMap((v) => ["--var", v])], { cwd: root, stdio: "ignore", detached: true });
  const base = `http://127.0.0.1:${port}`;
  const ready = (async () => {
    for (let i = 0; i < 120; i++) {
      try {
        if ((await fetch(`${base}/__cloudsail/status`)).ok) return;
      } catch { /* not up yet */ }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("wrangler dev did not start");
  })();
  return { base, ready, stop: () => { try { process.kill(-child.pid); } catch { /* already gone */ } } };
}

let on;
let off;
before(async () => {
  await new Promise((r) => origin.listen(ORIGIN_PORT, "127.0.0.1", r));
  // One after the other: two wrangler processes starting at once trip over
  // each other's local state.
  on = startWorker(18787);
  await on.ready;
  off = startWorker(18788, ["SKIN_ENABLED:false"]);
  await off.ready;
});
after(() => {
  on?.stop();
  off?.stop();
  origin.close();
});

test("serves the skin and login assets from /__cloudsail/", async () => {
  for (const [name, type] of [["skin.css", "text/css"], ["skin.js", "text/javascript"], ["login.css", "text/css"], ["cf-logo.svg", "image/svg+xml"]]) {
    const res = await fetch(`${on.base}/__cloudsail/${name}`);
    assert.equal(res.status, 200, name);
    assert.ok(res.headers.get("content-type").startsWith(type), name);
    assert.ok((await res.text()).length > 100, name);
  }
});

test("versioned asset URLs are cacheable forever, unversioned ones briefly", async () => {
  const { version } = await (await fetch(`${on.base}/__cloudsail/status`)).json();
  const pinned = await fetch(`${on.base}/__cloudsail/skin.js?v=${version}`);
  assert.match(pinned.headers.get("cache-control"), /immutable/);
  const loose = await fetch(`${on.base}/__cloudsail/skin.js`);
  assert.doesNotMatch(loose.headers.get("cache-control"), /immutable/);
});

test("injects login.css, skin.css and skin.js into HTML", async () => {
  const res = await fetch(`${on.base}/`);
  const html = await res.text();
  const { version } = await (await fetch(`${on.base}/__cloudsail/status`)).json();
  assert.match(html, /<link rel="stylesheet" href="\/__cloudsail\/login\.css">/);
  assert.ok(html.includes(`<link rel="stylesheet" href="/__cloudsail/skin.css?v=${version}">`));
  assert.ok(html.includes(`<script src="/__cloudsail/skin.js?v=${version}" defer></script>`));
  assert.ok(html.indexOf("skin.js") < html.indexOf("</head>"), "inside <head>");
  assert.match(res.headers.get("x-cloudsail-skin"), /^on/);
});

test("kill switch: SKIN_ENABLED=false keeps the login styling, drops the skin", async () => {
  const res = await fetch(`${off.base}/`);
  const html = await res.text();
  assert.match(html, /\/__cloudsail\/login\.css/);
  assert.doesNotMatch(html, /skin\.(css|js)/);
  assert.equal(res.headers.get("x-cloudsail-skin"), "off");
  assert.deepEqual((await (await fetch(`${off.base}/__cloudsail/status`)).json()).skin, false);
  assert.deepEqual((await (await fetch(`${on.base}/__cloudsail/status`)).json()).skin, true);
});

test("a redirect to Cloudflare Access logout goes to the Zammad home page instead", async () => {
  const res = await fetch(`${on.base}/logout`, { redirect: "manual" });
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://support.cloudsail.uk/");
});

test("other Access redirects are left alone", async () => {
  const res = await fetch(`${on.base}/elsewhere`, { redirect: "manual" });
  assert.equal(res.headers.get("location"), "https://cloudsail.cloudflareaccess.com/cdn-cgi/access/login");
});

test("Access logout URLs inside API JSON are rewritten, escaped or not", async () => {
  const { config } = await (await fetch(`${on.base}/api/v1/signshow`)).json();
  assert.equal(config.logout_url, "https://support.cloudsail.uk/");
  assert.equal(config.escaped, "https://support.cloudsail.uk/");
  assert.equal(config.login_url, "https://cloudsail.cloudflareaccess.com/login");
});

test("everything else passes through untouched", async () => {
  assert.deepEqual(await (await fetch(`${on.base}/api/v1/tickets`)).json(), [{ id: 1, title: "T-26 Store" }]);
  assert.equal(await (await fetch(`${on.base}/assets/app.js`)).text(), "console.log('zammad')");
  assert.equal((await fetch(`${on.base}/api/v1/empty`)).status, 204);
  const unknown = await fetch(`${on.base}/__cloudsail/unknown`);
  assert.equal(unknown.status, 404);
  assert.equal(await unknown.text(), "from origin");
});
