// Cloudflare Worker: restyles the Zammad login page at support.cloudsail.uk
// Route: support.cloudsail.uk/*
//
// Serves its own CSS + logo from the same origin (so Zammad's CSP 'self'
// rules allow them) and injects a <link> into every HTML page.

// 1) Paste the OFFICIAL Cloudflare logo SVG here (from Cloudflare's press /
//    brand assets page). Keep the backticks.
const CF_LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="4 0 63 50" fill="none"><path d="M42.8 2.847A20.9 20.9 0 0 0 30.343 6.97a21.6 21.6 0 0 0-7.703 10.806l-.14.449h3.9l.09-.215a17.76 17.76 0 0 1 7.47-8.88 17.2 17.2 0 0 1 11.254-2.255 17.38 17.38 0 0 1 10.206 5.342 18.02 18.02 0 0 1 4.772 10.642c.374 3.993-.58 8-2.71 11.372a17.56 17.56 0 0 1-9.03 7.217 17.17 17.17 0 0 1-11.469-.038A17.57 17.57 0 0 1 28 34.133c-.1-.153-.19-.306-.28-.47l-.2-.357h-4.09l.24.5q.556 1.22 1.27 2.347a21.4 21.4 0 0 0 8.002 7.497 20.86 20.86 0 0 0 10.59 2.47 20.9 20.9 0 0 0 10.4-3.203 21.47 21.47 0 0 0 7.481-8.038 22 22 0 0 0 2.6-10.763 22 22 0 0 0-2.962-10.664 21.4 21.4 0 0 0-7.75-7.77A20.9 20.9 0 0 0 42.8 2.848" fill="#fff"/><path d="m43.79 21.52-7.43-7.581-2.47 2.52 4.32 4.419H6l2.08 3.56H42.6zM43.74 37.602l-2.47-2.52 4.32-4.419H11.7l-2.07-3.57h40.35l1.19 2.928z" fill="#fff"/></svg>`;

const CSS = `
/* Hide the "Powered by Zammad" footer on the login page */
.login .poweredBy {
  display: none !important;
}

/* Bigger company logo, like Hack Club's */
.login .hero-unit .company-logo {
  max-width: 260px;
  max-height: 90px;
  width: auto;
  height: auto;
}

/* Cloudflare Access button (OpenID Connect provider) */
.auth-provider--openid-connect {
  background: linear-gradient(180deg, #F48A2E 0%, #EC7A1F 100%) !important; /* Cloudflare orange */
  border: 0 !important;
  border-radius: 4px !important;
  color: #fff !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .35);
  transition: filter .15s, box-shadow .15s;
}
.auth-provider--openid-connect:hover,
.auth-provider--openid-connect:focus-visible {
  filter: brightness(1.06);
  box-shadow: 0 2px 5px rgba(0, 0, 0, .4);
}
.auth-provider--openid-connect:active {
  filter: brightness(.94);
  box-shadow: 0 1px 1px rgba(0, 0, 0, .3);
}
.auth-provider--openid-connect:focus-visible {
  outline: 2px solid #FBAD41;
  outline-offset: 2px;
}
.auth-provider--openid-connect .provider-name {
  color: #fff !important;
  font-weight: 600 !important;
}
/* Swap Zammad's generic OpenID icon for the Cloudflare logo */
.auth-provider--openid-connect .provider-icon {
  display: none !important;
}
.auth-provider--openid-connect::before {
  content: "";
  display: inline-block;
  width: 24px;
  height: 19px;
  margin-right: 8px;
  background: url("/__cloudsail/cf-logo.svg") center / contain no-repeat;
  vertical-align: middle;
}
`;

const ASSET_PREFIX = "/__cloudsail/";

// Logout handling
const HOME = "https://support.cloudsail.uk/";
const ACCESS_HOST = "cloudsail.cloudflareaccess.com";
const ACCESS_URL_RE = /https?:\\?\/\\?\/cloudsail\.cloudflareaccess\.com[^"'\s]*/g;
function isAccessLogout(u) {
  const s = u.replace(/\\\//g, "/");
  return s.includes(ACCESS_HOST) && /logout|end_session|signout/i.test(s);
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === ASSET_PREFIX + "login.css") {
      return new Response(CSS, {
        headers: {
          "content-type": "text/css; charset=utf-8",
          "cache-control": "public, max-age=300",
        },
      });
    }

    if (url.pathname === ASSET_PREFIX + "cf-logo.svg") {
      return new Response(CF_LOGO_SVG, {
        headers: {
          "content-type": "image/svg+xml",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    const response = await fetch(request);

    // --- Keep Zammad logout local -------------------------------------
    // Zammad (via OIDC/SAML) sends users to Cloudflare Access's logout,
    // which ends the session for EVERY Access app. Rewrite any such
    // logout target back to the Zammad login page.
    const loc = response.headers.get("location");
    if (loc && isAccessLogout(loc)) {
      const headers = new Headers(response.headers);
      headers.set("location", HOME);
      return new Response(response.body, { status: response.status, headers });
    }

    const type = response.headers.get("content-type") || "";
    if (url.pathname.startsWith("/api/") && type.includes("json")) {
      const body = await response.text();
      if (!body.includes(ACCESS_HOST)) {
        return new Response(body, response);
      }
      const rewritten = body.replace(ACCESS_URL_RE, (m) => (isAccessLogout(m) ? HOME : m));
      const headers = new Headers(response.headers);
      headers.delete("content-length");
      return new Response(rewritten, { status: response.status, headers });
    }
    // ------------------------------------------------------------------

    if (!type.includes("text/html")) return response;

    return new HTMLRewriter()
      .on("head", {
        element(el) {
          el.append(
            `<link rel="stylesheet" href="${ASSET_PREFIX}login.css">`,
            { html: true },
          );
        },
      })
      .transform(response);
  },
};
