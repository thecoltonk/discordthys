// Cloudsail skin Worker for Zammad. Route: support.cloudsail.uk/*
//
// One Worker in front of Zammad that
//   1. serves its own CSS/JS/logo from /__cloudsail/* (same origin, so
//      Zammad's CSP `'self'` rules allow them),
//   2. injects them into every HTML page: the login styling always, the chat
//      skin unless the kill switch (SKIN_ENABLED) is off,
//   3. keeps a Zammad logout local instead of ending the whole Cloudflare
//      Access session (moved unchanged from legacy-login-worker.js).
//
// Zammad itself is never modified. Everything else passes through untouched,
// and if this Worker throws, Cloudflare serves the request from Zammad
// directly (passThroughOnException).

import SKIN_CSS from "./assets/skin.css";
import SKIN_JS from "./assets/skin.js";
import LOGIN_CSS from "./assets/login.css";
import CF_LOGO_SVG from "./assets/cf-logo.svg";

// Note: a Worker's entry module may only export handlers, so nothing else in
// this file is exported; test/worker.test.mjs exercises it over HTTP.

const ASSET_PREFIX = "/__cloudsail/";

// Short content hash: the injected URLs change with every deploy, so browsers
// pick up a new skin on the next page load and can cache each version forever.
function contentHash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

const VERSION = contentHash(SKIN_CSS + SKIN_JS);

const ASSETS = {
  "login.css": { body: LOGIN_CSS, type: "text/css; charset=utf-8", maxAge: 300 },
  "cf-logo.svg": { body: CF_LOGO_SVG, type: "image/svg+xml", maxAge: 86400 },
  "skin.css": { body: SKIN_CSS, type: "text/css; charset=utf-8", versioned: true },
  "skin.js": { body: SKIN_JS, type: "text/javascript; charset=utf-8", versioned: true },
};

// The kill switch. Anything but an explicit "off" value leaves the skin on, so
// a typo can't silently switch it off; the variable is set in wrangler.toml.
function skinEnabled(env) {
  const value = String(env?.SKIN_ENABLED ?? "true").trim().toLowerCase();
  return !["false", "0", "off", "no"].includes(value);
}

function serveAsset(name, url) {
  const asset = ASSETS[name];
  const immutable = asset.versioned && url.searchParams.get("v") === VERSION;
  return new Response(asset.body, {
    headers: {
      "content-type": asset.type,
      "cache-control": immutable
        ? "public, max-age=31536000, immutable"
        : `public, max-age=${asset.maxAge ?? 60}`,
      "x-content-type-options": "nosniff",
    },
  });
}

// --- Logout handling (from legacy-login-worker.js) --------------------------
// Zammad (via OIDC/SAML) sends users to Cloudflare Access's logout, which ends
// the session for EVERY Access app. Any such target is rewritten to the Zammad
// home page instead.
const HOME = "https://support.cloudsail.uk/";
const ACCESS_HOST = "cloudsail.cloudflareaccess.com";
const ACCESS_URL_RE = /https?:\\?\/\\?\/cloudsail\.cloudflareaccess\.com[^"'\s]*/g;

function isAccessLogout(u) {
  const s = u.replace(/\\\//g, "/");
  return s.includes(ACCESS_HOST) && /logout|end_session|signout/i.test(s);
}

function rewriteAccessLogout(body) {
  return body.replace(ACCESS_URL_RE, (m) => (isAccessLogout(m) ? HOME : m));
}

// Statuses that can't carry a body: such a response is never rebuilt.
const NULL_BODY = new Set([101, 204, 205, 304]);

async function handle(request, env) {
  const url = new URL(request.url);

  if (url.pathname.startsWith(ASSET_PREFIX)) {
    const name = url.pathname.slice(ASSET_PREFIX.length);
    if (Object.hasOwn(ASSETS, name)) return serveAsset(name, url);
    // Polled by skin.js, so the kill switch also reaches tabs that are
    // already open (they switch the skin off without a reload).
    if (name === "status") {
      return Response.json(
        { skin: skinEnabled(env), version: VERSION },
        { headers: { "cache-control": "no-store" } },
      );
    }
  }

  const response = await fetch(request);

  const loc = response.headers.get("location");
  if (loc && isAccessLogout(loc)) {
    const headers = new Headers(response.headers);
    headers.set("location", HOME);
    return new Response(response.body, { status: response.status, headers });
  }

  const type = response.headers.get("content-type") || "";
  if (NULL_BODY.has(response.status) || response.webSocket) return response;

  if (url.pathname.startsWith("/api/") && type.includes("json")) {
    const body = await response.text();
    if (!body.includes(ACCESS_HOST)) {
      return new Response(body, response);
    }
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    return new Response(rewriteAccessLogout(body), { status: response.status, headers });
  }

  if (!type.includes("text/html")) return response;

  const enabled = skinEnabled(env);
  let tags = `<link rel="stylesheet" href="${ASSET_PREFIX}login.css">`;
  if (enabled) {
    tags +=
      `<link rel="stylesheet" href="${ASSET_PREFIX}skin.css?v=${VERSION}">` +
      `<script src="${ASSET_PREFIX}skin.js?v=${VERSION}" defer></script>`;
  }

  const transformed = new HTMLRewriter()
    .on("head", {
      element(el) {
        el.append(tags, { html: true });
      },
    })
    .transform(response);

  const headers = new Headers(transformed.headers);
  headers.set("x-cloudsail-skin", enabled ? `on; v=${VERSION}` : "off");
  return new Response(transformed.body, {
    status: transformed.status,
    statusText: transformed.statusText,
    headers,
  });
}

export default {
  async fetch(request, env, ctx) {
    // Fail open: an exception here must never take Zammad down with it.
    ctx.passThroughOnException();
    return handle(request, env);
  },
};
