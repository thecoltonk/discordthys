/* Cloudsail skin for Zammad's agent ticket view (legacy desktop UI, 7.2).
 *
 * Served by the Worker from /__cloudsail/skin.js and injected into every HTML
 * page. It never changes Zammad's code or data model: it adds classes and a
 * few elements of its own, and drives Zammad's *own* reply form (the same
 * clicks an agent would make), so drafts, text modules, uploads, validation
 * and live updates stay Zammad's.
 *
 * Rules it lives by:
 *   - Fail safe. Every ticket is checked for the elements the skin relies on;
 *     if one is missing, that part of the skin stays off and Zammad looks and
 *     works as usual. Repeated errors switch the skin off for the session.
 *   - A reply is always a *note* on the current ticket. Before sending, the
 *     form's own hidden fields must read type=note and the visibility the
 *     agent chose; otherwise nothing is sent.
 *   - All CSS is scoped under classes this script adds, so with the script
 *     off (kill switch, per-user switch, error) the stylesheet does nothing.
 */
(function () {
  "use strict";
  if (window.CloudsailSkin) return;

  var STORAGE_KEY = "cloudsail.skin"; // "off" = this browser uses classic Zammad
  var URL_PARAM = "cloudsail-skin"; // ?cloudsail-skin=off | on
  var STATUS_URL = "/__cloudsail/status";
  var STATUS_EVERY_MS = 5 * 60 * 1000;
  var GROUP_GAP_MS = 5 * 60 * 1000; // a longer pause starts a new bubble group
  var PIN_SLACK_PX = 120; // "at the bottom" tolerance for auto-scroll
  var MAX_ERRORS = 5;
  var VIA_DISCORD = /^(.{1,80}) \((Player|Staff) via Discord\)$/;

  var killed = false; // remote kill switch or too many errors
  var errors = 0;
  var scanQueued = false;
  var internalByTicket = new Map(); // ticket id -> internal toggle (sticky per ticket)
  var classicTickets = new Set(); // tickets switched to the classic composer
  var views = new WeakMap(); // .content element -> per-view state

  // ---------------------------------------------------------------- helpers

  function storageGet() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  function storageSet(value) {
    try {
      if (value === null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* private mode: the choice just doesn't persist */
    }
  }

  function userOff() {
    return storageGet() === "off";
  }

  // Only Zammad's legacy desktop app. The new /desktop and /mobile apps are
  // different front ends; the skin leaves them alone.
  function isLegacyApp() {
    if (/^\/(desktop|mobile)(\/|$)/.test(window.location.pathname)) return false;
    return !!document.getElementById("app");
  }

  function active() {
    return !killed && !userOff() && isLegacyApp();
  }

  function zammadConfig(name) {
    try {
      return window.App.Config.get(name);
    } catch (e) {
      return undefined;
    }
  }

  function ticketIdOf(content) {
    var m = /^content_permanent_Ticket-(\d+)$/.exec(content.id || "");
    return m ? m[1] : null;
  }

  function setClass(el, name, on) {
    if (el.classList.contains(name) !== on) el.classList.toggle(name, on);
  }

  function setVar(el, name, value) {
    if (el.style.getPropertyValue(name) !== value) {
      if (value) el.style.setProperty(name, value);
      else el.style.removeProperty(name);
    }
  }

  // A CSS string literal for `content: var(--x)`.
  function cssString(text) {
    return '"' + String(text).replace(/[\\"\n\r\f]/g, " ") + '"';
  }

  function make(tag, className, text) {
    var el = document.createElement(tag);
    el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  // ------------------------------------------------------------ date labels

  var timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  var dayFormat = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
  var dayYearFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });

  function dayKey(date) {
    return date.getFullYear() + "-" + date.getMonth() + "-" + date.getDate();
  }

  function dayLabel(date) {
    var now = new Date();
    if (dayKey(date) === dayKey(now)) return "Today";
    var yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (dayKey(date) === dayKey(yesterday)) return "Yesterday";
    return date.getFullYear() === now.getFullYear() ? dayFormat.format(date) : dayYearFormat.format(date);
  }

  // --------------------------------------------------------------- bubbles

  function describe(item) {
    var side = item.classList.contains("customer") ? "customer" : item.classList.contains("agent") ? "agent" : "system";
    var avatar = item.querySelector(".js-avatar .avatar");
    var userId = avatar ? avatar.getAttribute("data-id") || "" : "";

    // "<name> (Player via Discord)" / "<name> (Staff via Discord)": the first
    // line of every message the bot mirrors. It becomes the sender label; the
    // line itself is hidden with CSS (the article's content isn't touched).
    var via = null;
    var body = item.querySelector(".textBubble-content > .richtext-content");
    var firstLine = body && body.firstChild === body.firstElementChild ? body.firstElementChild : null;
    if (firstLine && /^(DIV|SPAN|P)$/.test(firstLine.tagName)) {
      via = VIA_DISCORD.exec((firstLine.textContent || "").trim());
    }

    var name = "";
    if (via) {
      name = via[2] === "Staff" ? via[1] + " · via Discord" : via[1];
    } else {
      var from = item.querySelector(".article-meta.top .article-meta-value");
      name = from ? (from.textContent || "").replace(/\s+/g, " ").trim() : "";
    }

    var time = item.querySelector(":scope > a time[datetime], :scope > .task-subline time[datetime]");
    var date = time ? new Date(time.getAttribute("datetime")) : null;
    if (date && isNaN(date.getTime())) date = null;

    return {
      key: side + "|" + userId + "|" + (via ? via[1] : ""),
      internal: item.classList.contains("is-internal"),
      via: !!via,
      name: name,
      date: date,
    };
  }

  function groupArticles(list) {
    var items = [];
    for (var i = 0; i < list.children.length; i++) {
      if (list.children[i].classList.contains("ticket-article-item")) items.push(list.children[i]);
    }

    var infos = items.map(describe);
    var firsts = infos.map(function (info, index) {
      var prev = infos[index - 1];
      if (!prev) return true;
      if (prev.key !== info.key || prev.internal !== info.internal) return true;
      if (!prev.date || !info.date) return false;
      return dayKey(prev.date) !== dayKey(info.date) || info.date - prev.date > GROUP_GAP_MS;
    });

    items.forEach(function (item, index) {
      var info = infos[index];
      var prev = infos[index - 1];
      var newDay = !!info.date && (!prev || !prev.date || dayKey(prev.date) !== dayKey(info.date));
      setClass(item, "cs-first", firsts[index]);
      setClass(item, "cs-last", index === items.length - 1 || firsts[index + 1]);
      setClass(item, "cs-via", info.via);
      setClass(item, "cs-day", newDay);
      setVar(item, "--cs-name", info.name ? cssString(info.name) : "");
      setVar(item, "--cs-time", info.date ? cssString(timeFormat.format(info.date)) : "");
      setVar(item, "--cs-day", newDay ? cssString(dayLabel(info.date)) : "");
    });

    return items.length;
  }

  function ungroup(root) {
    root.querySelectorAll(".ticket-article-item").forEach(function (item) {
      item.classList.remove("cs-first", "cs-last", "cs-via", "cs-day");
      item.style.removeProperty("--cs-name");
      item.style.removeProperty("--cs-time");
      item.style.removeProperty("--cs-day");
    });
  }

  // -------------------------------------------------------------- composer

  // Everything the chat composer needs from Zammad's reply form. Null when
  // something is missing (read-only ticket, customer view, a Zammad update
  // that moved things): the composer then stays classic.
  function composerParts(content) {
    var form = content.querySelector(".article-new form.article-add");
    if (!form) return null;
    var parts = {
      holder: form.parentElement,
      form: form,
      type: form.querySelector('input[name="type"]'),
      internal: form.querySelector('input[name="internal"]'),
      textarea: form.querySelector('.js-textarea[contenteditable="true"]'),
      noteType: form.querySelector('.js-articleTypeItem[data-value="note"]'),
      toggle: form.querySelector(".js-toggleVisibility"),
      fileInput: form.querySelector('.article-attachment input[type="file"]'),
      submit: content.querySelector(".attributeBar .js-submit"),
    };
    for (var key in parts) if (!parts[key]) return null;
    return parts;
  }

  function readMode(parts) {
    return { type: parts.type.value, internal: parts.internal.value === "true" };
  }

  function hasContent(parts) {
    return (parts.textarea.textContent || "").trim() !== "" || !!parts.textarea.querySelector("img");
  }

  function attachmentNames(parts) {
    var names = [];
    parts.form.querySelectorAll(".attachments .attachment").forEach(function (el) {
      var name = el.querySelector(".attachment-name");
      names.push(((name || el).textContent || "").replace(/\s+/g, " ").trim() || "attachment");
    });
    return names;
  }

  function uploading(parts) {
    var upload = parts.form.querySelector(".attachmentUpload");
    return !!upload && !upload.classList.contains("hide");
  }

  // Puts Zammad's form into "note, internal or public" using its own
  // controls, then checks the form's hidden fields. True only when they read
  // exactly what was asked for.
  function ensureMode(parts, internal) {
    var mode = readMode(parts);
    if (mode.type !== "note") {
      parts.noteType.click();
      mode = readMode(parts);
    }
    if (mode.type === "note" && mode.internal !== internal) {
      parts.toggle.click();
      mode = readMode(parts);
    }
    return mode.type === "note" && mode.internal === internal;
  }

  function wantedInternal(ticketId) {
    return internalByTicket.get(ticketId) === true;
  }

  // A Zammad popup that owns the Enter key right now (text modules "::",
  // mentions "@@", knowledge base "??").
  function popupOpen(parts) {
    return !!parts.form.querySelector(".shortcut.open, .dropdown.open, .dropup.open");
  }

  function flash(parts, message) {
    var bar = parts.holder.querySelector(".cs-bar .cs-bar-note");
    if (!bar) return;
    bar.textContent = message;
    bar.classList.add("cs-bar-note--warn");
    window.clearTimeout(bar.__csTimer);
    bar.__csTimer = window.setTimeout(function () {
      bar.classList.remove("cs-bar-note--warn");
      queueScan();
    }, 4000);
  }

  function send(content) {
    var parts = composerParts(content);
    var ticketId = ticketIdOf(content);
    if (!parts || !ticketId || !content.classList.contains("cs-composer")) return;

    var names = attachmentNames(parts);
    if (!hasContent(parts) && !names.length) return; // nothing to send
    if (uploading(parts) || parts.submit.disabled) {
      flash(parts, "Still uploading or saving. Try again in a moment.");
      return;
    }
    if (!ensureMode(parts, wantedInternal(ticketId))) {
      // Never send as anything but the note the agent asked for.
      flash(parts, "Not sent: the reply form isn't a note. Use the classic composer.");
      return;
    }
    if (!hasContent(parts)) {
      // Zammad refuses an article without text. A chat message can be just a
      // file, so the file names become the text.
      parts.textarea.textContent = "📎 " + names.join(", ");
      parts.textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }
    parts.submit.click(); // Zammad's own "Update": validation, upload ids, API call
  }

  function addFiles(parts, fileList) {
    try {
      var transfer = new DataTransfer();
      for (var i = 0; i < fileList.length; i++) transfer.items.add(fileList[i]);
      if (!transfer.files.length) return false;
      parts.fileInput.files = transfer.files;
      parts.fileInput.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    } catch (e) {
      return false;
    }
  }

  function isDiscordTicket(ticketId) {
    try {
      var ticket = window.App.Ticket.find(ticketId);
      var customer = window.App.User.find(ticket.customer_id);
      return /^discord:/.test(customer.login || "") || /@discord\.invalid$/.test(customer.email || "");
    } catch (e) {
      return null; // unknown: say nothing
    }
  }

  // The line under the composer: key hint, warnings and the switches.
  function renderBar(holder, state) {
    var bar = holder.querySelector(":scope > .cs-bar");
    if (!bar) {
      bar = make("div", "cs-bar");
      bar.appendChild(make("span", "cs-bar-note"));
      bar.appendChild(make("button", "cs-bar-switch"));
      bar.lastChild.type = "button";
      holder.appendChild(bar);
    }
    var note = bar.querySelector(".cs-bar-note");
    var button = bar.querySelector(".cs-bar-switch");
    if (!note.classList.contains("cs-bar-note--warn") && note.textContent !== state.note) note.textContent = state.note;
    if (button.textContent !== state.label) button.textContent = state.label;
    if (button.getAttribute("data-cs-action") !== state.action) button.setAttribute("data-cs-action", state.action);
    if (button.title !== state.title) button.title = state.title;
    setClass(bar, "cs-bar--warn", !!state.warn);
  }

  function setupComposer(content, ticketId) {
    var parts = composerParts(content);
    var classic = classicTickets.has(ticketId);
    // With this Zammad setting on, making a note public asks for confirmation
    // every time. The skin's default *is* public, so it would have to bypass
    // the admin's choice: it leaves the composer classic instead.
    var confirmEachTime = zammadConfig("ui_ticket_zoom_article_visibility_confirmation_dialog") === true;
    var view = views.get(content);
    var usable = !!parts && !classic && !confirmEachTime && !view.composerFailed;

    if (usable && view.form !== parts.form) {
      // A form Zammad just rendered. A restored draft that isn't a note (an
      // email with recipients, say) is left alone: forcing it to a note would
      // wipe its To/CC/subject. That ticket gets the classic composer.
      if (hasContent(parts) && readMode(parts).type !== "note") {
        classicTickets.add(ticketId);
        classic = true;
        usable = false;
      }
    }
    if (usable && view.form !== parts.form) {
      // A restored note draft keeps the visibility it was written with;
      // anything else starts as a public note.
      if (!internalByTicket.has(ticketId)) {
        internalByTicket.set(ticketId, hasContent(parts) && readMode(parts).internal);
      }
      view.form = parts.form;
    }
    if (usable && !ensureMode(parts, wantedInternal(ticketId))) {
      view.composerFailed = true; // Zammad didn't follow: hands off this ticket
      usable = false;
      if (window.console) console.warn("[cloudsail-skin] reply form not as expected; using the classic composer for ticket " + ticketId);
    }

    setClass(content, "cs-composer", usable);

    if (!parts) return;
    if (!usable) {
      var button = parts.form.querySelector(":scope > .cs-send");
      if (button) button.remove();
      renderBar(parts.holder, classic
        ? { note: "Classic composer for this ticket.", label: "Chat composer", action: "ticket-chat", title: "Use the chat composer on this ticket again" }
        : { note: "", label: "Classic view", action: "user-off", title: "Use classic Zammad in this browser" });
      return;
    }

    if (!parts.form.querySelector(":scope > .cs-send")) {
      var sendButton = make("button", "cs-send");
      sendButton.type = "button";
      sendButton.title = "Send (Enter)";
      sendButton.setAttribute("aria-label", "Send");
      parts.form.appendChild(sendButton);
    }
    setClass(parts.form, "cs-empty", !hasContent(parts));
    setClass(parts.form, "cs-idle", !hasContent(parts) && !attachmentNames(parts).length);

    if (isDiscordTicket(ticketId) === false) {
      renderBar(parts.holder, {
        warn: true,
        note: "Not a Discord ticket: your reply is saved as a note and is not emailed to the customer.",
        label: "Classic composer",
        action: "ticket-classic",
        title: "Use Zammad's own composer (email, phone) on this ticket",
      });
    } else {
      renderBar(parts.holder, {
        note: "Enter to send · Shift+Enter for a new line",
        label: "Classic view",
        action: "user-off",
        title: "Use classic Zammad in this browser",
      });
    }
  }

  // ----------------------------------------------------------- auto-scroll

  function scrollToEnd(scroller) {
    scroller.scrollTop = scroller.scrollHeight;
  }

  function pinned(scroller) {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < PIN_SLACK_PX;
  }

  // Scroll to the newest message now and again shortly after. Zammad's own
  // scroll runs on a 50-300 ms delay, and for a conversation it believes fits
  // the window it scrolls to the *top*, which would leave the newest bubble
  // under the pinned composer. Skipped if the agent has scrolled away.
  function follow(scroller, view) {
    var go = function () {
      if (view.pinned && active()) scrollToEnd(scroller);
    };
    window.requestAnimationFrame(go);
    window.setTimeout(go, 180);
    window.setTimeout(go, 450);
  }

  function setupScroll(content, view, count) {
    var scroller = content.querySelector(".main");
    var zoom = content.querySelector(".ticketZoom");
    if (!scroller || !zoom) return;

    if (view.scroller !== scroller) {
      view.scroller = scroller;
      view.pinned = true;
      scroller.addEventListener("scroll", function () {
        view.pinned = pinned(scroller);
        setClass(content, "cs-unpinned", !view.pinned);
      }, { passive: true });
      // Bubbles that grow (images loading, "See more", a taller composer)
      // must not push the newest message out of view.
      if (window.ResizeObserver) {
        view.resize = new ResizeObserver(function () {
          if (view.pinned && active() && content.classList.contains("cs-chat")) scrollToEnd(scroller);
        });
        view.resize.observe(zoom);
      }
    }

    // Zammad itself scrolls to the end when a ticket opens and whenever an
    // article is added (also while you are scrolled up); that is left as it
    // is. This only makes sure the first paint with bubbles ends at the
    // newest message, unless the link points at one article
    // (#ticket/zoom/1/23), where Zammad scrolls to that article.
    var deepLink = /^#ticket\/zoom\/\d+\/\d+/.test(window.location.hash);
    if (count > 0 && view.count === 0 && !deepLink) {
      view.pinned = true;
      follow(scroller, view);
    } else if (count > view.count && view.pinned) {
      follow(scroller, view);
    }
    view.count = count;

    var holder = content.querySelector(".article-new");
    if (holder && !holder.querySelector(":scope > .cs-jump")) {
      var jump = make("button", "cs-jump");
      jump.type = "button";
      jump.title = "Jump to the newest message";
      jump.setAttribute("aria-label", "Jump to the newest message");
      holder.appendChild(jump);
    }
  }

  // ------------------------------------------------------------- main scan

  function scanContent(content) {
    var ticketId = ticketIdOf(content);
    var list = content.querySelector(".ticketZoom .ticket-article");
    if (!ticketId || !list) return;

    var view = views.get(content);
    if (!view) {
      view = { count: 0 };
      views.set(content, view);
    }

    setClass(content, "cs-chat", true);
    var count = groupArticles(list);
    setupComposer(content, ticketId);
    setupScroll(content, view, count);
  }

  function teardown() {
    document.documentElement.classList.remove("cs-skin");
    document.querySelectorAll(".cs-chat").forEach(function (content) {
      content.classList.remove("cs-chat", "cs-composer", "cs-unpinned", "cs-drop");
      ungroup(content);
      content.querySelectorAll(".cs-send, .cs-jump").forEach(function (el) { el.remove(); });
      content.querySelectorAll("form.article-add").forEach(function (form) {
        form.classList.remove("cs-empty", "cs-idle");
      });
      var view = views.get(content);
      if (view) view.form = null;
    });
  }

  // With the skin off for this browser, the only thing left is the switch
  // that turns it back on.
  function renderOffSwitches() {
    document.querySelectorAll(".content.active .article-new").forEach(function (holder) {
      if (!holder.querySelector("form.article-add")) return;
      renderBar(holder, { note: "", label: "Chat view", action: "user-on", title: "Use the Cloudsail chat view in this browser" });
    });
  }

  function scan() {
    scanQueued = false;
    try {
      if (!active()) {
        if (document.documentElement.classList.contains("cs-skin")) teardown();
        if (!killed && isLegacyApp()) renderOffSwitches();
        else document.querySelectorAll(".cs-bar").forEach(function (el) { el.remove(); });
        return;
      }
      document.documentElement.classList.add("cs-skin");
      document.querySelectorAll(".content.active").forEach(scanContent);
    } catch (error) {
      errors += 1;
      if (window.console) console.error("[cloudsail-skin]", error);
      if (errors >= MAX_ERRORS) {
        killed = true;
        try { teardown(); } catch (e) { /* nothing more to do */ }
        if (window.console) console.error("[cloudsail-skin] switched off after repeated errors; reload to retry");
      }
    }
  }

  function queueScan() {
    if (scanQueued) return;
    scanQueued = true;
    window.requestAnimationFrame(scan);
  }

  // ---------------------------------------------------------------- events

  function composerFor(target) {
    if (!target || !target.closest) return null;
    var content = target.closest(".content.cs-composer");
    if (!content) return null;
    var parts = composerParts(content);
    return parts ? { content: content, parts: parts } : null;
  }

  function guard(handler) {
    return function (event) {
      if (!active()) return;
      try {
        handler(event);
      } catch (error) {
        errors += 1;
        if (window.console) console.error("[cloudsail-skin]", error);
      }
    };
  }

  // Enter sends, Shift+Enter is Zammad's normal line break. Capture phase, so
  // this runs before Zammad's editor sees the key.
  document.addEventListener("keydown", guard(function (event) {
    if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.isComposing || event.keyCode === 229) return; // IME confirmation
    var textarea = event.target && event.target.closest ? event.target.closest(".js-textarea") : null;
    var found = textarea && composerFor(textarea);
    if (!found || found.parts.textarea !== textarea) return;
    if (popupOpen(found.parts)) return; // Enter picks the highlighted entry
    event.preventDefault();
    event.stopImmediatePropagation();
    send(found.content);
  }), true);

  document.addEventListener("click", guard(function (event) {
    var target = event.target;
    if (!target || !target.closest) return;

    var action = target.closest(".cs-bar-switch");
    if (action) {
      event.preventDefault();
      var content = action.closest(".content");
      var ticketId = content ? ticketIdOf(content) : null;
      switch (action.getAttribute("data-cs-action")) {
        case "user-off": api.disable(); break;
        case "user-on": api.enable(); break;
        case "ticket-classic": if (ticketId) classicTickets.add(ticketId); break;
        case "ticket-chat": if (ticketId) classicTickets.delete(ticketId); break;
      }
      queueScan();
      return;
    }

    if (target.closest(".cs-send")) {
      event.preventDefault();
      var forSend = target.closest(".content.cs-composer");
      if (forSend) send(forSend);
      return;
    }

    if (target.closest(".cs-jump")) {
      event.preventDefault();
      var scroller = target.closest(".main");
      if (scroller) scrollToEnd(scroller);
      return;
    }

    // The internal/public toggle is Zammad's own control. The agent's choice
    // is recorded now, before Zammad handles the click, so the next scan
    // agrees with it instead of flipping it back.
    var toggle = target.closest(".js-toggleVisibility");
    var found = toggle && composerFor(toggle);
    if (found && event.isTrusted) {
      var id = ticketIdOf(found.content);
      if (id) internalByTicket.set(id, !readMode(found.parts).internal);
      queueScan();
    }
  }), true);

  // The "skin off" switch must work while the skin is off, so it is handled
  // outside guard(), which ignores events when the skin is inactive.
  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest ? event.target.closest('.cs-bar-switch[data-cs-action="user-on"]') : null;
    if (!button || active()) return;
    event.preventDefault();
    api.enable();
  }, true);

  // Pasted files (screenshots included) become real attachments. Zammad would
  // embed a pasted image in the text, and embedded images are not part of an
  // article's attachment list, which is what the bot forwards to Discord.
  document.addEventListener("paste", guard(function (event) {
    var found = composerFor(event.target);
    var data = event.clipboardData;
    if (!found || !data || !data.files || !data.files.length) return;
    if ((data.getData("text/plain") || "").trim() !== "") return; // text with a preview image: let Zammad paste the text
    if (addFiles(found.parts, data.files)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }), true);

  // Files can be dropped anywhere on the conversation, not only on the form
  // (drops on the form itself are Zammad's).
  function dropTarget(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    if (!types || Array.prototype.indexOf.call(types, "Files") === -1) return null;
    if (!event.target || !event.target.closest || event.target.closest("form.article-add")) return null;
    if (!event.target.closest(".main")) return null;
    return composerFor(event.target);
  }

  var dropTimer = 0;
  document.addEventListener("dragover", guard(function (event) {
    var found = dropTarget(event);
    if (!found) return;
    event.preventDefault();
    found.content.classList.add("cs-drop");
    window.clearTimeout(dropTimer);
    dropTimer = window.setTimeout(function () { found.content.classList.remove("cs-drop"); }, 250);
  }), true);

  document.addEventListener("drop", guard(function (event) {
    var found = dropTarget(event);
    if (!found) return;
    event.preventDefault();
    found.content.classList.remove("cs-drop");
    addFiles(found.parts, event.dataTransfer.files);
  }), true);

  document.addEventListener("input", guard(queueScan), true);

  // ------------------------------------------------- kill switch, start-up

  // The Worker's kill switch also reaches tabs that are already open: they
  // ask now and then, and take the skin off when it says so. A 404 means the
  // skin Worker itself is gone (rolled back). Other failures change nothing.
  var lastStatusCheck = 0;
  function checkStatus() {
    if (killed || Date.now() - lastStatusCheck < 30 * 1000) return;
    lastStatusCheck = Date.now();
    try {
      window.fetch(STATUS_URL, { cache: "no-store", credentials: "same-origin" })
        .then(function (response) {
          if (response.status === 404) return { skin: false };
          return response.ok ? response.json() : null;
        })
        .then(function (status) {
          if (status && status.skin === false) {
            killed = true;
            queueScan();
          }
        })
        .catch(function () { /* offline or mid-login: keep going */ });
    } catch (e) {
      /* no fetch: no remote switch */
    }
  }

  var api = {
    enable: function () { storageSet(null); queueScan(); },
    disable: function () { storageSet("off"); queueScan(); },
    enabled: function () { return active(); },
  };
  window.CloudsailSkin = api;

  function start() {
    try {
      var param = new URLSearchParams(window.location.search).get(URL_PARAM);
      if (param === "off") storageSet("off");
      if (param === "on") storageSet(null);
    } catch (e) {
      /* old browser: no URL switch */
    }
    if (!isLegacyApp()) return;

    new MutationObserver(queueScan).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "datetime"],
    });
    window.addEventListener("hashchange", queueScan);
    window.addEventListener("storage", function (event) {
      if (event.key === STORAGE_KEY) queueScan();
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) checkStatus();
    });
    window.setInterval(checkStatus, STATUS_EVERY_MS);
    queueScan();
  }

  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);
})();
