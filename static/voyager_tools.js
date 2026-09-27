/* static/voyager_tools.js - the Atlas control panel fragment (module
 * voyager_tools; program spec section 7 and program C spec section 5.3),
 * in the design language of docs/superpowers/specs/2026-09-02-workflow-
 * page-design.md and 2026-09-02-matrix-window-design.md.
 *
 * One IIFE extending window.VoyagerTools with mount(el, opts),
 * mountPanel(el, opts) and registerPane(key, init). mount() takes the
 * <section class="voyager-tools"> root (or a container holding it), guards a
 * double mount with data-vt-mounted, injects the scoped stylesheet when the
 * page did not link it, wires the eight tools, initialises every pane a
 * later script registered (static/vt_settings.js: the settings, desktop and
 * history panes), opens opts.pane or the remembered pane, and returns a
 * handle whose unmount() stops the panes and frees the section for another
 * mount. mountPanel() is the atlas's entry (its Control panel tab): the
 * same mount plus a Back to the board button on the relocked admin line.
 * A pane script that loads before this one queues its registrations on
 * window.VoyagerTools._pendingPanes, drained here. Every tool request goes
 * to BASE + '/api/...' where BASE is data-vt-base on the root, or
 * window.VOYAGER_TOOLS_BASE, or '/voyager-tools'; a pane's request with
 * desktop: true goes to the desktop's own route (/api/settings/...) as
 * written. A 401 or 403 carrying
 * need_unlock, or a relock from window.VICADMINSESSION (the desktop's admin
 * session script, decision 8 of the manual edit chain), hides the tools
 * behind the admin line with an Unlock admin button, and the next unlock
 * brings them back; a 423 shows the lock message. Every request gives up
 * after REQUEST_TIMEOUT_MS with a sentence saying the action may still have
 * happened. Destructive actions open a role=dialog
 * confirm that names what will change; Escape closes it without bubbling,
 * because the desktop closes a window on a bubbled Escape.
 *
 * Every result list (the processing order, the Workbench folders, the
 * parameter versions, the seasons table, each manual edit project's rows, a
 * rewind plan) renders as the shared block's .rows / .row shape rather than
 * an HTML table; every choice is a .choices definition-list row with a
 * "?" glyph; every button is .btn / .btn-primary / .btn-danger / .btn-mini.
 * No route path, JSON key, or existing data-vt-* attribute changed in this
 * pass; the processing order table (T20260906-213500-1) adds four ways to
 * reorder the 116 blocks beyond one-at-a-time Up/Down clicks, all client
 * side until Save order posts the same {transects, initials} body as before:
 *   1. Drag and drop, now actually wired to the draggable rows (dragstart,
 *      dragover with a before/after insertion line, drop, dragend).
 *   2. A "#" position field on every row: type a whole number and press
 *      Enter to move that block there. A whole number outside 1..N is
 *      clamped to the nearest end; a blank or non-numeric entry refuses
 *      with a message in the result line and leaves the order unchanged
 *      (parsePositionInput, commitPosition) - the one rule for every
 *      typed-position control on this pane, single row or the selection.
 *   3. A tick box per row (click toggles, shift-click selects the run
 *      between the last click and this one) plus a "Move selected to"
 *      field and button that moves the whole selection there together,
 *      keeping the selected blocks' order among themselves.
 *   4. A drag handle per row, focusable by Tab; ArrowUp/ArrowDown while it
 *      is focused moves the block one place (the shared 2px focus ring
 *      applies with no extra CSS, since :focus-visible is already global
 *      in the shared block).
 * Ticking a box never mutates the order (harmless while locked); the other
 * three interactions check the lock and refuse with LOCK_MESSAGE, changing
 * nothing, rather than going silently inert.
 */
(function () {
  "use strict";

  var DEFAULT_BASE = "/voyager-tools";
  var CSS_NAME = "voyager_tools.css";
  // The shared design block and the state-role sheet: the standalone page and
  // the atlas link both; the desktop links neither, so the panel injects the
  // role sheet as a link and the shared block scoped under its root class.
  var SHARED_CSS = "vicarius_workflow.css";
  var ROLES_CSS = "vicarius_roles.css";
  var ROOT_CLASS = "voyager-tools";
  var LS_TAB = "vt.tab";
  var LS_INITIALS = "vt.initials";
  var SS_INITIALS = "vic_initials";
  // Two to four letters after trimming and upper-casing: the registry's
  // MANUAL_EDIT_INITIALS_PATTERN, the one initials rule of the chain.
  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var PANE_KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;
  var TARGET_WORDS = { before_step1: "before step 1", before_step0: "before step 0" };
  var GB = 1e9;
  var REQUEST_TIMEOUT_MS = 30000;
  var LINE_ADMIN_OFF = "Admin mode is off. Unlock admin to use the Atlas control panel.";
  var LINE_INITIALS = "Type your initials, two to four letters, at the top of the panel.";
  var LINE_LOCKED = "The Atlas control panel is locked.";
  var LINE_PANE_MISSING = "The script of this pane did not load; reload the window.";

  /* ---- the pane seam (program C spec 5.3) ------------------------------- */
  // PANES: key -> init(ctx), filled by registerPane from later scripts.
  // instances: every mounted panel, so a pane registered after a mount is
  // initialised in it at once.
  var PANES = {};
  var instances = [];

  // registerPane(key, init): make a pane available to every panel. init(ctx)
  // runs once per mount and returns the pane's loader (called when the pane
  // is first shown, and again after a relock ends).
  //
  // The pane context (ctx) a pane receives: root, pane, base, key; api(path,
  // options) for this blueprint and desktopApi(path, options) for the
  // desktop's own routes, both answering {ok, status, data}; initials(resEl),
  // confirmDialog(spec), setResult, busy, fail, esc, q, qa, idChip,
  // stampWords; onStop(fn), where fn stops the pane's timers and outside
  // listeners (it plays the part of spec 5.3's onLocked); isRelocked();
  // isShown(). Register onStop inside init: each fn runs at every relock and
  // at unmount, stays registered for the next relock, and must be safe to
  // run more than once.
  function registerPane(key, init) {
    if (typeof key !== "string" || !PANE_KEY_RE.test(key)) throw new Error("registerPane: the pane key must be a short lowercase name, got " + key);
    if (typeof init !== "function") throw new Error("registerPane: the initialiser of pane " + key + " must be a function");
    PANES[key] = init;
    pruneDetached();
    instances.forEach(function (inst) { inst.initPane(key); });
  }

  // pruneDetached(): forget panels whose section was on the page and left
  // it without an unmount (a desktop window closed by its frame), so their
  // closures are freed and a later registration never initialises a pane in
  // them. A panel mounted before its host was attached is kept.
  function pruneDetached() {
    for (var i = instances.length - 1; i >= 0; i--) {
      var inst = instances[i];
      var attached = !!document.body && document.body.contains(inst.root);
      if (attached) inst.wasAttached = true;
      else if (inst.wasAttached) instances.splice(i, 1);
    }
  }
  var LINE_TIMEOUT = "No answer from the desktop after 30 s; the action may still have happened. Reload the pane before trying again.";

  function adminSession() { return window.VICADMINSESSION || null; }

  // isLockedAnswer(status, body): 401 or 403 whose body carries need_unlock.
  function isLockedAnswer(status, body) {
    var session = adminSession();
    if (session) return session.isLockedResponse(status, body);
    return (status === 401 || status === 403) && !!body && body.need_unlock === true;
  }

  /* ---- small helpers -------------------------------------------------- */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function q(root, name) { return root.querySelector('[data-vt="' + name + '"]'); }
  function qa(root, sel) { return Array.prototype.slice.call(root.querySelectorAll(sel)); }

  function readStore(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function writeStore(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ } }

  function gb(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n <= 0) return "0.0 GB";
    return (n / GB).toFixed(n < GB ? 3 : 1) + " GB";
  }

  function idChip(id) { return '<span class="id code">' + esc(id) + "</span>"; }

  function siteLabel(name, code) {
    return name ? '<span class="vt-site">' + esc(name) + "</span> " + idChip(code) : idChip(code);
  }

  // roleClass: the Workbench comparison status on the eight-role vocabulary
  // of docs/superpowers/specs/2026-09-02-matrix-window-design.md section 2.
  function roleClass(status) {
    if (status === "identical") return "role-person";
    if (status === "processing right now") return "role-in-progress";
    if (status === "differs" || status === "error") return "role-failed";
    return "role-attention"; // "no registry row", "not on the Shelf"
  }

  // stampWords: "2026-09-04T07:22:00-04:00" as "2026-09-04 07:22 AST" for a row.
  function stampWords(iso) {
    var t = String(iso || "");
    if (t.length < 16) return t;
    return t.slice(0, 10) + " " + t.slice(11, 16) + " AST";
  }

  function seasonWords(year, token) {
    return esc(year) + " " + (token === "_pbl" ? "spring" : "annual");
  }

  function setResult(el, text, kind) {
    if (!el) return;
    el.classList.remove("result-error", "is-warn");
    if (kind === "error") el.classList.add("result-error");
    else if (kind === "warn") el.classList.add("is-warn");
    el.innerHTML = text || "";
  }

  function busy(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-busy", !!on);
    btn.disabled = !!on;
  }

  var emptyRow = function (text) { return '<li class="row"><span class="row-meta">' + esc(text) + "</span></li>"; };

  /* ---- mount ------------------------------------------------------------ */

  var NO_HANDLE = { unmount: function () { /* nothing was mounted */ } };

  // mount(el, opts): wire the panel in el. opts.pane opens that pane first
  // (else the remembered one, else Atlas edit); opts.onClose, when given,
  // adds Back to the board to the relocked admin line. Returns {unmount()}.
  function mount(el, opts) {
    var options = opts || {};
    var root = el && el.classList && el.classList.contains("voyager-tools") ? el
      : (el ? el.querySelector(".voyager-tools") : null);
    if (!root || root.getAttribute("data-vt-mounted") === "1") return NO_HANDLE;
    if (root.getAttribute("data-admin-required") === "1") return NO_HANDLE; // the admin body, nothing to wire
    root.setAttribute("data-vt-mounted", "1");
    var BASE = root.getAttribute("data-vt-base") || window.VOYAGER_TOOLS_BASE || DEFAULT_BASE;
    ensureStylesheet(BASE, root.getAttribute("data-vt-version") || "");

    var state = {
      base: BASE, root: root, loaded: {}, order: null, rewind: null, spot: null, params: null,
      selected: {}, selectAnchor: null // the order table's multi-select, by block key
    };

    /* -- fetch ------------------------------------------------------------ */
    function api(path, options) {
      var opts = options || {};
      var url = opts.desktop ? path : BASE + path;
      var init = { method: opts.method || "GET", headers: { Accept: "application/json" }, credentials: "same-origin" };
      if (opts.body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(opts.body);
      }
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timer = null;
      if (controller) {
        init.signal = controller.signal;
        timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
      }
      return fetch(url, init).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (isLockedAnswer(r.status, data)) {
            var session = adminSession();
            if (session) session.noteResponse(r.status, data);
            showRelocked();
          } else if (r.status === 423) {
            showAdminLine(data.message || LINE_LOCKED);
          }
          return { ok: r.ok, status: r.status, data: data };
        });
      }).catch(function (err) {
        if (err && err.name === "AbortError") return { ok: false, status: 0, data: { error: LINE_TIMEOUT } };
        return { ok: false, status: 0, data: { error: "The request did not reach VICARIUS: " + err } };
      }).then(function (res) {
        if (timer !== null) clearTimeout(timer);
        return res;
      });
    }

    function showAdminLine(text) {
      var line = q(root, "admin-line");
      if (!line) return;
      line.textContent = text;
      line.hidden = !text;
    }

    /* -- the admin lock ------------------------------------------------------ */
    // showRelocked(): admin mode ended; the tools hide behind the admin line
    // and its Unlock admin button until the next unlock. The server refuses
    // every tool request meanwhile, so nothing half-done is lost.
    var relocked = false;
    function showRelocked() {
      var line = q(root, "admin-line");
      var stages = root.querySelector(".vt-stages");
      if (stages) stages.hidden = true;
      if (!relocked) stopPanes();
      relocked = true;
      if (!line) return;
      line.textContent = "";
      var words = document.createElement("span");
      words.textContent = LINE_ADMIN_OFF + " ";
      line.appendChild(words);
      if (adminSession()) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-mini";
        btn.setAttribute("data-vt", "admin-unlock");
        btn.textContent = "Unlock admin";
        btn.addEventListener("click", function () { adminSession().promptUnlock(); });
        line.appendChild(btn);
      }
      if (typeof options.onClose === "function") {
        var back = document.createElement("button");
        back.type = "button";
        back.className = "btn btn-mini";
        back.setAttribute("data-vt", "admin-back");
        back.textContent = "Back to the board";
        back.addEventListener("click", function () { options.onClose(); });
        line.appendChild(back);
      }
      line.hidden = false;
    }

    // showUnlocked(): admin mode is back; the tools reappear and the current
    // pane loads again from the server.
    function showUnlocked() {
      if (!relocked) return;
      relocked = false;
      var stages = root.querySelector(".vt-stages");
      if (stages) stages.hidden = false;
      showAdminLine("");
      state.loaded = {};
      showTool(readStore(LS_TAB) || toolKeys()[0]);
    }

    var stopWatching = null;
    if (adminSession()) {
      stopWatching = adminSession().onChange(function (change) {
        if (!document.body.contains(root)) { stopWatching(); return; }
        if (!change.unlocked) showRelocked();
        else if (relocked) showUnlocked();
      });
    }

    // fail(resEl, res, fallback): the refusal line of a request. The server's
    // own error or message wins, then the caller's fallback; a body with
    // neither (a 5xx from a proxy, a crash page) names the status.
    function fail(resEl, res, fallback) {
      var data = res.data || {};
      var message = data.error || data.message || fallback || ("The desktop answered " + res.status + "; nothing on this pane changed.");
      var html = esc(message);
      if (data.pullback_command) html += "<pre>" + esc(data.pullback_command) + "</pre>";
      if (data.problems && data.problems.length) {
        html += "<ul>" + data.problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
      }
      setResult(resEl, html, "error");
    }

    /* -- initials --------------------------------------------------------- */
    // The field is seeded only from what a person typed: this panel's own
    // key, then the desktop's session key. Nothing else fills it, so a save
    // is never signed with initials nobody typed (every settings history
    // line and registry event carries them).
    var initialsEl = root.querySelector("#vt-initials");
    function initialsSeed() {
      var v = readStore(LS_INITIALS);
      if (!v) { try { v = window.sessionStorage.getItem(SS_INITIALS); } catch (e) { v = null; } }
      return (v || "").trim().toUpperCase();
    }
    if (initialsEl) {
      initialsEl.value = initialsSeed();
      initialsEl.addEventListener("change", function () {
        initialsEl.value = initialsEl.value.trim().toUpperCase();
        writeStore(LS_INITIALS, initialsEl.value);
      });
    }
    function initials(resEl) {
      var v = initialsEl ? initialsEl.value.trim().toUpperCase() : "";
      if (!INITIALS_RE.test(v)) {
        setResult(resEl, LINE_INITIALS, "error");
        if (initialsEl) initialsEl.focus();
        return null;
      }
      writeStore(LS_INITIALS, v);
      return v;
    }

    /* -- confirm dialog --------------------------------------------------- */
    function confirmDialog(spec) {
      return new Promise(function (resolve) {
        var backdrop = document.createElement("div");
        backdrop.className = "vt-dialog-backdrop";
        var titleId = "vt-dialog-title-" + Date.now();
        var fieldHtml = spec.field
          ? '<label for="vt-dialog-field">' + esc(spec.field.label) + '</label>' +
            '<input type="text" id="vt-dialog-field" class="vt-field" maxlength="2000" title="' + esc(spec.field.tip || "") + '" />'
          : "";
        backdrop.innerHTML =
          '<div class="vt-dialog" role="dialog" aria-modal="true" aria-labelledby="' + titleId + '">' +
          '<h3 id="' + titleId + '">' + esc(spec.title) + "</h3>" +
          (spec.lines || []).map(function (l) { return "<p>" + esc(l) + "</p>"; }).join("") +
          fieldHtml +
          '<div class="actions">' +
          '<button type="button" class="btn" data-vt-dialog="cancel">Cancel</button>' +
          '<button type="button" class="btn ' + (spec.primary ? "btn-primary" : "btn-danger") + '" data-vt-dialog="ok">' + esc(spec.okLabel || "Confirm") + "</button>" +
          "</div></div>";
        root.appendChild(backdrop);
        var okBtn = backdrop.querySelector('[data-vt-dialog="ok"]');
        var cancelBtn = backdrop.querySelector('[data-vt-dialog="cancel"]');
        var field = backdrop.querySelector("#vt-dialog-field");
        var previous = document.activeElement;
        function close(value) {
          backdrop.removeEventListener("keydown", onKey, true);
          if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
          if (previous && typeof previous.focus === "function") { try { previous.focus(); } catch (e) { /* gone */ } }
          resolve(value);
        }
        function onKey(e) {
          if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(null); return; }
          if (e.key === "Tab") {
            var focusables = [field, cancelBtn, okBtn].filter(Boolean);
            var idx = focusables.indexOf(document.activeElement);
            var next = e.shiftKey ? (idx <= 0 ? focusables.length - 1 : idx - 1) : (idx >= focusables.length - 1 ? 0 : idx + 1);
            e.preventDefault();
            focusables[next].focus();
          }
          if (e.key === "Enter" && field && document.activeElement === field) { e.preventDefault(); okBtn.click(); }
        }
        backdrop.addEventListener("keydown", onKey, true);
        cancelBtn.addEventListener("click", function () { close(null); });
        okBtn.addEventListener("click", function () {
          if (field && spec.field && spec.field.required && !field.value.trim()) { field.focus(); return; }
          close(field ? field.value.trim() : true);
        });
        backdrop.addEventListener("click", function (e) { if (e.target === backdrop) close(null); });
        (field || cancelBtn).focus();
      });
    }

    /* -- tabs (the grouped pane list) ---------------------------------------- */
    var tabs = qa(root, ".vt-tab");
    var current = null;
    // toolKeys(): every pane key the list offers, in list order.
    function toolKeys() { return tabs.map(function (t) { return t.getAttribute("data-vt-tab"); }); }
    function showTool(name) {
      var keys = toolKeys();
      if (keys.indexOf(name) === -1) name = keys[0];
      current = name;
      tabs.forEach(function (t) { t.setAttribute("aria-selected", t.getAttribute("data-vt-tab") === name ? "true" : "false"); });
      qa(root, ".vt-pane").forEach(function (p) { p.hidden = p.getAttribute("data-vt-pane") !== name; });
      writeStore(LS_TAB, name);
      loadPane(name);
    }
    // loadPane(name): run a pane's loader the first time it is shown; a
    // registered pane whose script has not arrived yet waits for it.
    function loadPane(name) {
      if (state.loaded[name]) return;
      var loader = loaders[name] || paneLoaders[name];
      if (!loader) {
        if (!PANES[name]) {
          var line = root.querySelector('#vt-' + name + ' .result');
          if (line) setResult(line, LINE_PANE_MISSING, "error");
        }
        return;
      }
      state.loaded[name] = true;
      loader();
    }

    /* -- registered panes ------------------------------------------------------ */
    var paneLoaders = {};
    var stopFns = [];
    // stopPanes(forget): run every stop function a pane registered (its
    // timers and listeners outside the section), on a relock and at unmount;
    // they stay registered for the next relock unless forget is true.
    function stopPanes(forget) {
      var fns = forget ? stopFns.splice(0, stopFns.length) : stopFns.slice();
      fns.forEach(function (fn) { try { fn(); } catch (e) { /* a pane's own cleanup failed; the rest still run */ } });
    }
    function paneContext(key) {
      return {
        root: root, pane: root.querySelector("#vt-" + key), base: BASE, key: key,
        api: api,
        desktopApi: function (path, options) { return api(path, Object.assign({}, options || {}, { desktop: true })); },
        initials: initials, confirmDialog: confirmDialog, setResult: setResult, busy: busy, fail: fail,
        esc: esc, q: function (name) { return q(root, name); }, qa: function (sel) { return qa(root, sel); },
        idChip: idChip, stampWords: stampWords,
        onStop: function (fn) { if (typeof fn === "function") stopFns.push(fn); },
        isRelocked: function () { return relocked; },
        isShown: function () { return current === key && !relocked; }
      };
    }
    var instance = {
      root: root,
      wasAttached: !!document.body && document.body.contains(root),
      initPane: function (key) {
        if (paneLoaders[key] || loaders[key] || !PANES[key] || !root.querySelector("#vt-" + key)) return;
        var made = PANES[key](paneContext(key));
        paneLoaders[key] = typeof made === "function" ? made : (made && typeof made.load === "function" ? made.load : function () {});
        var line = root.querySelector("#vt-" + key + " .result");
        if (line && line.textContent === LINE_PANE_MISSING) setResult(line, "");
        if (current === key) loadPane(key);
      }
    };
    tabs.forEach(function (t) {
      t.addEventListener("click", function () { showTool(t.getAttribute("data-vt-tab")); });
      t.addEventListener("keydown", function (e) {
        var idx = tabs.indexOf(t);
        if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); tabs[(idx + 1) % tabs.length].focus(); }
        if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); tabs[(idx - 1 + tabs.length) % tabs.length].focus(); }
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showTool(t.getAttribute("data-vt-tab")); }
      });
    });

    /* ==================================================================== */
    /* 1. the processing order                                              */
    /* ==================================================================== */
    var orderBody = q(root, "order-body");
    var orderResult = q(root, "order-result");

    function loadOrder() {
      return api("/api/order").then(function (res) {
        if (!res.ok) { fail(orderResult, res); return; }
        state.order = res.data;
        state.order.keys = res.data.blocks.map(function (b) { return b.key; });
        renderOrder();
      });
    }

    // LOCK_MESSAGE: the one refusal every one of the four new order
    // interactions (drag, typed position, move-selected, arrow keys) shows
    // while the order is locked, instead of silently doing nothing.
    // Ticking a select box is exempt: it does not move anything by itself.
    var LOCK_MESSAGE = "The processing order is locked; unlock it first to change positions, drag rows, or move a selection.";
    function isLocked() { return !!(state.order && state.order.lock); }

    // parsePositionInput: the one rule for every typed-position field on
    // this pane. A whole number (with an optional sign) parses; anything
    // else - blank, letters, a decimal - is not a position and returns
    // null so the caller refuses with a message instead of guessing.
    function parsePositionInput(raw) {
      var trimmed = String(raw == null ? "" : raw).trim();
      if (!/^[+-]?\d+$/.test(trimmed)) return null;
      return parseInt(trimmed, 10);
    }

    // focusTarget/restoreFocus: renderOrder() rebuilds the row list from
    // scratch on every move, which would otherwise drop keyboard focus to
    // <body> after a single arrow key press or Up/Down click - unusable
    // for working through 116 rows. Remember what kind of control the
    // block's own key had focus and refocus its replacement after render.
    function focusTarget() {
      var active = document.activeElement;
      if (!active || !orderBody.contains(active)) return null;
      if (active.hasAttribute("data-vt-handle")) return { kind: "handle", key: active.getAttribute("data-vt-handle") };
      if (active.hasAttribute("data-vt-move")) return { kind: "move", key: active.getAttribute("data-key"), dir: active.getAttribute("data-vt-move") };
      if (active.hasAttribute("data-vt-pos")) return { kind: "pos", key: active.getAttribute("data-vt-pos") };
      return null;
    }
    function restoreFocus(target) {
      if (!target) return;
      var sel = target.kind === "handle" ? "[data-vt-handle]" : target.kind === "move" ? '[data-vt-move="' + target.dir + '"]' : "[data-vt-pos]";
      var attr = target.kind === "handle" ? "data-vt-handle" : target.kind === "move" ? "data-key" : "data-vt-pos";
      var els = qa(orderBody, sel);
      for (var i = 0; i < els.length; i++) {
        if (els[i].getAttribute(attr) === target.key) { els[i].focus(); return; }
      }
    }

    // pruneSelection: drop a ticked key the moment it is no longer part of
    // the waiting blocks (a fresh loadOrder after Save, for example), so
    // the count line and the Move selected range never lie about a block
    // that is not on screen any more.
    function pruneSelection() {
      var valid = {};
      state.order.keys.forEach(function (k) { if (state.selected[k]) valid[k] = true; });
      state.selected = valid;
      if (state.selectAnchor && !valid[state.selectAnchor]) state.selectAnchor = null;
    }

    function renderOrder() {
      var data = state.order;
      if (!data) return;
      pruneSelection();
      var lock = q(root, "order-lock");
      if (data.lock) {
        lock.textContent = "Locked in by " + data.lock.locked_by + " at " + data.lock.locked_at + ": " + data.count + " rows, " + data.first + " to " + data.last + ".";
      } else {
        lock.textContent = data.count ? data.count + " rows in the order, " + data.first + " to " + data.last + "; unlocked." : "No processing order saved yet; the atlas sorts by date until one is saved.";
      }
      var byKey = {};
      data.blocks.forEach(function (b) { byKey[b.key] = b; });
      var locked = !!data.lock;
      var position = data.started.length;
      var total = state.order.keys.length;
      orderBody.innerHTML = state.order.keys.map(function (key, i) {
        var b = byKey[key];
        var first = position + 1;
        position += b.rows.length;
        var ids = b.rows.map(function (r) { return idChip(r.readable_id); }).join(" ");
        var ordinal = i + 1;
        var name = (b.site_name || b.site) + " " + b.transect;
        var selected = !!state.selected[key];
        return '<li class="row vt-order-row' + (selected ? " is-selected" : "") + '" draggable="true" data-key="' + esc(key) + '">' +
          '<span class="vt-order-lead">' +
          '<button type="button" class="vt-order-handle" data-vt-handle="' + esc(key) + '" aria-label="Reorder ' + esc(name) + ", block " + ordinal + " of " + total + '" title="Press arrow up or arrow down while focused to move this block one place; dragging does the same. VICARIUS applies moves when you save the order.">⋮⋮</button>' +
          '<label class="opt vt-order-select" title="Tick to include this block in Move selected. VICARIUS keeps the tick until you clear the selection or save the order.">' +
          '<input type="checkbox" data-vt-select="' + esc(key) + '"' + (selected ? " checked" : "") + ' aria-label="Select ' + esc(name) + ' for a multi-select move"><span>Select</span></label>' +
          '<span class="vt-order-pos-wrap"><label for="vt-order-pos-' + i + '">Block</label>' +
          '<input type="number" class="vt-field vt-order-pos" id="vt-order-pos-' + i + '" data-vt-pos="' + esc(key) + '" min="1" max="' + total + '" step="1" value="' + ordinal + '" title="Type a whole number for the position of this block among the ' + total + ' waiting blocks and press Enter to move it there. VICARIUS clamps whole numbers outside the range to the nearest end and shows an error for anything else.">' +
          "</span></span>" +
          '<span class="row-name">' + siteLabel(b.site_name, b.site) + " " + idChip(b.transect) + "</span>" +
          '<span class="row-act">' +
          '<button type="button" class="btn btn-mini" data-vt-move="-1" data-key="' + esc(key) + '"' + (locked || i === 0 ? " disabled" : "") + ' title="Move this transect block one place earlier in the order; Save order makes it count.">Up</button>' +
          '<button type="button" class="btn btn-mini" data-vt-move="1" data-key="' + esc(key) + '"' + (locked || i === total - 1 ? " disabled" : "") + ' title="Move this transect block one place later in the order; Save order makes it count.">Down</button>' +
          "</span>" +
          '<span class="row-meta"><span class="count">position ' + first + (b.rows.length > 1 ? " to " + position : "") + '</span> ' + ids +
          ' <span class="count">' + b.rows.length + " timepoint" + (b.rows.length === 1 ? "" : "s") + "</span></span>" +
          "</li>";
      }).join("") || emptyRow("No transect is waiting to be processed.");
      var started = q(root, "order-started");
      started.innerHTML = data.started.map(function (r) {
        return "<li>" + idChip(r.readable_id) + " " + esc(r.step1_status || r.stage) + (r.position ? " (position " + r.position + ")" : "") + "</li>";
      }).join("") || "<li>none</li>";
      q(root, "order-started-summary").textContent = "Rows already started (" + data.started.length + ")";
      var skipped = q(root, "order-skipped");
      skipped.hidden = !data.skipped.length;
      skipped.textContent = data.skipped.length ? "Not orderable (blank site or transect): " + data.skipped.join(", ") : "";
      q(root, "order-save").disabled = locked;
      q(root, "order-lock-btn").disabled = locked || !data.count;
      q(root, "order-unlock-btn").disabled = !locked;
      q(root, "order-select-count").textContent = Object.keys(state.selected).length + " selected";
    }

    function moveKey(key, dir) {
      var keys = state.order.keys;
      var i = keys.indexOf(key);
      var j = i + dir;
      if (i < 0 || j < 0 || j >= keys.length) return;
      keys[i] = keys[j]; keys[j] = key;
      var target = focusTarget();
      renderOrder();
      restoreFocus(target);
    }

    // syncPosInput: put a row's position field back to its true ordinal
    // after a refused Enter (blank, non-numeric) so the box never keeps
    // showing text that did not move anything.
    function syncPosInput(key) {
      var els = qa(orderBody, "[data-vt-pos]");
      for (var i = 0; i < els.length; i++) {
        if (els[i].getAttribute("data-vt-pos") === key) { els[i].value = state.order.keys.indexOf(key) + 1; return; }
      }
    }

    function commitPosition(key, raw) {
      if (isLocked()) { setResult(orderResult, LOCK_MESSAGE, "error"); syncPosInput(key); return; }
      var count = state.order.keys.length;
      var n = parsePositionInput(raw);
      if (n === null) {
        setResult(orderResult, "Type a whole number from 1 to " + count + " in the position field of the block before pressing Enter; VICARIUS did not move it.", "error");
        syncPosInput(key);
        return;
      }
      n = Math.max(1, Math.min(count, n));
      var keys = state.order.keys.filter(function (k) { return k !== key; });
      keys.splice(n - 1, 0, key);
      state.order.keys = keys;
      renderOrder();
      restoreFocus({ kind: "pos", key: key });
    }

    // handleSelect: a plain click toggles one block; a shift-click selects
    // every block between the last-clicked block (the anchor) and this one,
    // inclusive, matching the click-then-shift-click run Lauren asked for.
    function handleSelect(cb, shift) {
      var key = cb.getAttribute("data-vt-select");
      var keys = state.order.keys;
      if (shift && state.selectAnchor && keys.indexOf(state.selectAnchor) !== -1) {
        var a = keys.indexOf(state.selectAnchor), b = keys.indexOf(key);
        var lo = Math.min(a, b), hi = Math.max(a, b);
        for (var i = lo; i <= hi; i++) state.selected[keys[i]] = true;
      } else {
        if (cb.checked) state.selected[key] = true; else delete state.selected[key];
        state.selectAnchor = key;
      }
      renderOrder();
    }

    orderBody.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-move]");
      if (btn) { moveKey(btn.getAttribute("data-key"), Number(btn.getAttribute("data-vt-move"))); return; }
      var cb = e.target.closest("[data-vt-select]");
      if (cb) handleSelect(cb, e.shiftKey);
    });

    // Enter on a position field commits it; ArrowUp/ArrowDown on a drag
    // handle moves that block one place, mirroring Up/Down for keyboard
    // users who never touch the mouse.
    orderBody.addEventListener("keydown", function (e) {
      var posInput = e.target.closest && e.target.closest("[data-vt-pos]");
      if (posInput) {
        if (e.key !== "Enter") return;
        e.preventDefault();
        commitPosition(posInput.getAttribute("data-vt-pos"), posInput.value);
        return;
      }
      var handle = e.target.closest && e.target.closest("[data-vt-handle]");
      if (handle) {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        if (isLocked()) { setResult(orderResult, LOCK_MESSAGE, "error"); return; }
        moveKey(handle.getAttribute("data-vt-handle"), e.key === "ArrowUp" ? -1 : 1);
      }
    });
    // A position field left mid-edit without Enter (a Tab or a click away)
    // reverts to the true value; blur does not bubble, so this listens in
    // the capture phase.
    orderBody.addEventListener("blur", function (e) {
      var posInput = e.target.closest && e.target.closest("[data-vt-pos]");
      if (posInput) syncPosInput(posInput.getAttribute("data-vt-pos"));
    }, true);

    q(root, "order-move-btn").addEventListener("click", function () {
      if (isLocked()) { setResult(orderResult, LOCK_MESSAGE, "error"); return; }
      if (!state.order) return;
      var selectedKeys = state.order.keys.filter(function (k) { return state.selected[k]; });
      if (!selectedKeys.length) {
        setResult(orderResult, "Tick at least one block before using Move selected.", "error");
        return;
      }
      var remaining = state.order.keys.filter(function (k) { return !state.selected[k]; });
      var n = 1;
      if (remaining.length) {
        n = parsePositionInput(q(root, "order-move-to").value);
        if (n === null) {
          setResult(orderResult, "Type a whole number from 1 to " + remaining.length + " in Move selected to before moving the selection.", "error");
          return;
        }
        n = Math.max(1, Math.min(remaining.length, n));
      }
      var newKeys = remaining.slice(0, n - 1).concat(selectedKeys, remaining.slice(n - 1));
      state.order.keys = newKeys;
      renderOrder();
      setResult(orderResult, "Moved " + selectedKeys.length + " block" + (selectedKeys.length === 1 ? "" : "s") + " to position " + n + " among the blocks not selected.");
    });
    q(root, "order-clear-btn").addEventListener("click", function () {
      state.selected = {};
      state.selectAnchor = null;
      renderOrder();
    });

    var dragKey = null;
    orderBody.addEventListener("dragstart", function (e) {
      var row = e.target.closest(".row[draggable='true']");
      if (!row) return;
      if (isLocked()) { e.preventDefault(); setResult(orderResult, LOCK_MESSAGE, "error"); return; }
      dragKey = row.getAttribute("data-key");
      row.classList.add("is-dragging");
      try { e.dataTransfer.setData("text/plain", dragKey); e.dataTransfer.effectAllowed = "move"; } catch (err) { /* older engines */ }
    });
    orderBody.addEventListener("dragover", function (e) {
      var row = e.target.closest(".row[data-key]");
      if (!row || !dragKey) return;
      e.preventDefault();
      var rect = row.getBoundingClientRect();
      var before = e.clientY < rect.top + rect.height / 2;
      qa(orderBody, ".row").forEach(function (r) { r.classList.remove("is-drop-before", "is-drop-after"); });
      row.classList.add(before ? "is-drop-before" : "is-drop-after");
    });
    orderBody.addEventListener("dragleave", function (e) {
      var row = e.target.closest && e.target.closest(".row[data-key]");
      if (row) row.classList.remove("is-drop-before", "is-drop-after");
    });
    orderBody.addEventListener("drop", function (e) {
      var row = e.target.closest(".row[data-key]");
      if (!row || !dragKey) return;
      e.preventDefault();
      var targetKey = row.getAttribute("data-key");
      var before = row.classList.contains("is-drop-before");
      var keys = state.order.keys.filter(function (k) { return k !== dragKey; });
      var at = keys.indexOf(targetKey) + (before ? 0 : 1);
      keys.splice(at, 0, dragKey);
      state.order.keys = keys;
      dragKey = null;
      renderOrder();
    });
    orderBody.addEventListener("dragend", function () {
      dragKey = null;
      qa(orderBody, ".row").forEach(function (r) { r.classList.remove("is-dragging", "is-drop-before", "is-drop-after"); });
    });

    q(root, "order-save").addEventListener("click", function () {
      var who = initials(orderResult);
      if (!who || !state.order) return;
      var btn = q(root, "order-save");
      busy(btn, true);
      api("/api/order", { method: "POST", body: { transects: state.order.keys, initials: who } }).then(function (res) {
        busy(btn, false);
        if (!res.ok) { fail(orderResult, res); return; }
        setResult(orderResult, "Order saved: " + res.data.count + " rows, " + esc(res.data.first) + " to " + esc(res.data.last) + ".");
        loadOrder();
      });
    });

    q(root, "order-lock-btn").addEventListener("click", function () {
      var who = initials(orderResult);
      if (!who || !state.order) return;
      var d = state.order;
      confirmDialog({
        title: "Lock the processing order in",
        lines: ["Lock " + d.count + " rows in the order shown, from " + d.first + " to " + d.last + ".",
                "Voyager 1 and the atlas follow this order until it is unlocked here; unsaved moves in the table are not part of it."],
        okLabel: "Lock order in", primary: true
      }).then(function (ok) {
        if (!ok) return;
        api("/api/order/lock", { method: "POST", body: { initials: who, confirm: true } }).then(function (res) {
          if (!res.ok) { fail(orderResult, res); return; }
          setResult(orderResult, "Order locked: " + res.data.count + " rows, " + esc(res.data.first) + " to " + esc(res.data.last) + ", by " + esc(res.data.lock.locked_by) + " at " + esc(res.data.lock.locked_at) + ".");
          loadOrder();
        });
      });
    });

    q(root, "order-unlock-btn").addEventListener("click", function () {
      var who = initials(orderResult);
      if (!who) return;
      api("/api/order/unlock", { method: "POST", body: { initials: who } }).then(function (res) {
        if (!res.ok) { fail(orderResult, res); return; }
        setResult(orderResult, "Order unlocked; it can be changed again.");
        loadOrder();
      });
    });

    /* ==================================================================== */
    /* 2. clean workbench                                                    */
    /* ==================================================================== */
    var wbBody = q(root, "wb-body");
    var wbResult = q(root, "wb-result");

    function loadWorkbench() {
      var btn = q(root, "wb-scan");
      busy(btn, true);
      wbBody.innerHTML = emptyRow("Scanning the Workbench and asking the Shelf for the listing of each folder...");
      return api("/api/workbench").then(function (res) {
        busy(btn, false);
        if (!res.ok) { fail(wbResult, res); wbBody.innerHTML = ""; return; }
        renderWorkbench(res.data);
      });
    }

    function renderWorkbench(data) {
      q(root, "wb-roots").textContent = data.roots.length ? "Workbench roots: " + data.roots.join(", ") : "No Workbench root configured.";
      q(root, "wb-problems").innerHTML = (data.problems || []).map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("");
      wbBody.innerHTML = data.folders.map(function (f) {
        var expander = "";
        if (f.mismatches && f.mismatches.length) {
          expander = ' <details class="src"><summary>' + f.mismatches.length + " difference" + (f.mismatches.length === 1 ? "" : "s") + "</summary><ul>" +
            f.mismatches.slice(0, 20).map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul></details>";
        }
        var meta = (f.files ? '<span class="count">' + f.files + " files, " + gb(f.bytes) + "</span> " : "") +
          (f.readable_ids.length ? f.readable_ids.map(idChip).join(" ") : '<span class="count">no registry row</span>') +
          ' <span class="count">shelf: ' + (f.shelf_path ? esc(f.shelf_path) : "not on the Shelf") + "</span>";
        return '<li class="row">' +
          '<span class="row-name">' + esc(f.path) + "</span>" +
          (f.deletable
            ? '<span class="row-act"><button type="button" class="btn btn-mini btn-danger" data-vt-delete="' + esc(f.path) + '" data-shelf="' + esc(f.shelf_path || "") + '" title="Delete this local copy after the confirmation. VICARIUS checks the two copies again, removes the folder from inside the Workbench root only, and writes one processing_log row.">Delete</button></span>'
            : "<span class=\"row-act\"></span>") +
          '<span class="row-state"><span class="chip ' + roleClass(f.status) + '" aria-hidden="true"></span> ' + esc(f.status) + (f.detail ? ": " + esc(f.detail) : "") + expander + "</span>" +
          '<span class="row-meta">' + meta + "</span>" +
          "</li>";
      }).join("") || emptyRow("No processing folder under the Workbench roots.");
    }

    q(root, "wb-scan").addEventListener("click", loadWorkbench);
    wbBody.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-delete]");
      if (!btn) return;
      var who = initials(wbResult);
      if (!who) return;
      var path = btn.getAttribute("data-vt-delete");
      var shelf = btn.getAttribute("data-shelf") || "";
      confirmDialog({
        title: "Delete this Workbench copy",
        lines: ["Delete " + path + " from the Workbench.", "The Shelf copy at " + shelf + " matched it file for file by name and size; VICARIUS checks again before deleting and writes one processing_log row."],
        okLabel: "Delete the local copy"
      }).then(function (ok) {
        if (!ok) return;
        busy(btn, true);
        api("/api/workbench/delete", { method: "POST", body: { path: path, initials: who, confirm: true } }).then(function (res) {
          busy(btn, false);
          if (!res.ok) { fail(wbResult, res); return; }
          setResult(wbResult, "Deleted " + esc(res.data.deleted) + " (" + gb(res.data.bytes) + ")." + (res.data.problems.length ? " " + esc(res.data.problems.join(" ")) : ""), res.data.problems.length ? "warn" : "");
          loadWorkbench();
        });
      });
    });

    /* ==================================================================== */
    /* 3. clean manual edit run                                              */
    /* ==================================================================== */
    var meResult = q(root, "me-result");

    function loadManual() {
      return api("/api/manual-edit").then(function (res) {
        if (!res.ok) { fail(meResult, res); return; }
        renderManual(res.data.jobs);
      });
    }

    var CANCEL_TIP = "Cancel this project after the confirmation. VICARIUS marks it cancelled, returns each row still at editing to awaiting with its Shelf location, and removes the edit bench copies when the project never reached the push to the Shelf.";

    function projectHtml(job) {
      var onCancel = job.refusal ? esc(job.refusal)
        : (job.will_prune ? "the edit bench copies are removed: " + esc(job.prune_targets.join(", ") || "none on disk")
                          : "the local copies are kept (the project reached the push to the Shelf)");
      return '<div class="vt-me-project" data-job-id="' + esc(job.job_id) + '" data-job-rows="' + job.rows.length + '">' +
        '<dl class="choices">' +
        '<div><dt>Project</dt><dd>' + esc(job.job_id) + " (" + esc(job.phase) + ")</dd></div>" +
        "<div><dt>Created</dt><dd>" + esc(stampWords(job.created_at)) + " by " + esc(job.created_by) + "</dd></div>" +
        '<div><dt>Edit bench</dt><dd><span class="id">' + esc(job.edit_bench) + "</span></dd></div>" +
        "<div><dt>On cancel</dt><dd>" + onCancel + "</dd></div>" +
        "</dl>" +
        '<ul class="rows" aria-label="The rows of project ' + esc(job.job_id) + '">' + job.rows.map(function (r) {
          return '<li class="row"><span class="row-name">' + idChip(r.readable_id) + '</span><span class="row-meta">' +
            siteLabel(r.site_name, r.site) + " " + idChip(r.transect) + " back to " + esc(r.shelf_location || "?") + "</span></li>";
        }).join("") + "</ul>" +
        '<details class="src"><summary>History (' + job.history.length + ')</summary><ul>' + job.history.map(function (h) {
          return "<li>" + esc(stampWords(h.at)) + " " + esc(h.phase) + " by " + esc(h.by) + (h.reason ? ": " + esc(h.reason) : "") + "</li>";
        }).join("") + "</ul></details>" +
        '<details class="src"><summary>Log tail (' + job.log_tail.length + ')</summary><pre class="vt-log">' + esc(job.log_tail.join("\n") || "(empty)") + "</pre></details>" +
        '<div class="actions"><button type="button" class="btn btn-danger" data-vt-cancel="' + esc(job.job_id) + '"' +
        (job.can_cancel ? "" : " disabled") + ' title="' + esc(CANCEL_TIP) + '">Cancel and prune</button></div>' +
        "</div>";
    }

    function renderManual(jobs) {
      var host = q(root, "me-summary");
      host.innerHTML = jobs.length ? jobs.map(projectHtml).join("")
        : '<p class="count">No manual edit project is unfinished.</p>';
    }

    function cancelResultText(d) {
      var reset = d.restored.filter(function (r) { return r.status_reset; }).length;
      var kept = d.restored.filter(function (r) { return r.status_kept !== null; });
      var text = "Project " + esc(d.job_id) + " is " + esc(d.phase) + ": " + reset + " row" + (reset === 1 ? "" : "s") + " back to awaiting, " +
        d.pruned.length + " local cop" + (d.pruned.length === 1 ? "y" : "ies") + " removed" + (d.kept.length ? ", " + d.kept.length + " kept" : "") + ".";
      if (kept.length) {
        text += " Status left as it was: " + kept.map(function (r) { return esc(r.readable_id) + " (" + esc(r.status_kept || "blank") + ")"; }).join(", ") + ".";
      }
      if (d.warnings.length) text += " " + esc(d.warnings.join(" "));
      if (d.problems.length) text += " " + esc(d.problems.join(" "));
      return text;
    }

    q(root, "me-refresh").addEventListener("click", loadManual);
    q(root, "me-summary").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-cancel]");
      if (!btn || btn.disabled) return;
      var who = initials(meResult);
      if (!who) return;
      var block = btn.closest(".vt-me-project");
      var jobId = block.getAttribute("data-job-id") || "";
      var rows = block.getAttribute("data-job-rows") || "0";
      confirmDialog({
        title: "Cancel and prune the manual edit project",
        lines: ["Cancel project " + jobId + " with " + rows + " row" + (rows === "1" ? "" : "s") + ".",
                "VICARIUS returns each row still at editing to awaiting with its Shelf location, leaves a row at done as it is, and removes the edit bench copies when the project never reached the push."],
        okLabel: "Cancel and prune"
      }).then(function (ok) {
        if (!ok) return;
        api("/api/manual-edit/cancel", { method: "POST", body: { job_id: jobId, initials: who, confirm: true } }).then(function (res) {
          if (!res.ok) { fail(meResult, res); return; }
          setResult(meResult, cancelResultText(res.data), res.data.problems.length ? "warn" : "");
          loadManual();
        });
      });
    });

    /* ==================================================================== */
    /* 4 and 5. rewind and spot redo                                         */
    /* ==================================================================== */
    function rewindTool(prefix, spot) {
      var select = q(root, prefix + "-row");
      var planEl = q(root, prefix + "-plan");
      var resultEl = q(root, prefix + "-result");
      var dryBtn = q(root, prefix + "-dry");
      var applyBtn = q(root, prefix + "-apply");
      var current = null;

      function target() {
        if (spot) return "before_step1";
        var checked = root.querySelector('input[name="vt-rw-target"]:checked');
        return checked ? checked.value : "before_step1";
      }

      function load() {
        return api("/api/rewind/rows").then(function (res) {
          if (!res.ok) { fail(resultEl, res); return; }
          var rows = res.data.rows;
          select.innerHTML = rows.map(function (r) {
            var note = r.in_job ? " (in a manual edit project)" : r.manual_edit_under_way ? " (manual edit under way)" : (!r.local ? " (on the NAS)" : "") + (r.run_state === "running" ? " (running)" : "");
            var label = r.readable_id + "  " + (r.site_name || r.site) + " " + r.transect + " " + r.year + (r.season_token === "_pbl" ? " spring" : " annual") + "  " + (r.step1_status || r.stage || "started") + note;
            return '<option value="' + esc(r.readable_id) + '">' + esc(label) + "</option>";
          }).join("") || '<option value="">No row has been touched by Voyager 1 yet</option>';
          current = null;
          planEl.innerHTML = "";
          applyBtn.disabled = true;
        });
      }

      function renderPlan(plan) {
        var moves = plan.moves.map(function (m) {
          return '<li class="row"><span class="row-name">' + esc(m.kind) + '</span><span class="row-meta">' + esc(m.from) + "</span></li>";
        }).join("") || emptyRow("none");
        var cellKeys = Object.keys(plan.registry_resets);
        var cells = cellKeys.map(function (c) {
          return '<li class="row"><span class="row-name">' + esc(c) + '</span><span class="row-meta">' + esc(plan.registry_resets[c]) + "</span></li>";
        }).join("") || emptyRow("none");
        var chunkLine = plan.chunk ? (plan.chunk.present ? "remove chunk " + esc(plan.readable_id) + " from " + esc(plan.chunk.psx) : "psx not on disk: " + esc(plan.chunk.psx)) : "no psx recorded";
        var html = "<h3>" + esc(plan.readable_id) + " " + esc(plan.target_words) + "</h3>" +
          '<p class="count">Rewind folder: ' + esc(plan.rewind_dir) + "</p>" +
          "<h3>Artifacts to move (" + plan.moves.length + ")</h3><ul class=\"rows\">" + moves + "</ul>" +
          "<h3>Chunk</h3><p class=\"count\">" + chunkLine + "</p>" +
          "<h3>Registry cells to reset (" + cellKeys.length + ")</h3><ul class=\"rows\">" + cells + "</ul>" +
          "<h3>status.csv cells</h3><p class=\"count\">" + esc(plan.status_resets.join(", ") || "none") + "</p>" +
          (plan.warnings.length ? "<h3>Warnings</h3><ul class=\"rows\">" + plan.warnings.map(function (w) {
            return '<li class="row"><span class="row-meta is-warn">' + esc(w) + "</span></li>";
          }).join("") + "</ul>" : "");
        planEl.innerHTML = html;
      }

      dryBtn.addEventListener("click", function () {
        var rid = select.value;
        if (!rid) { setResult(resultEl, "Choose a timepoint first.", "error"); return; }
        busy(dryBtn, true);
        api("/api/rewind/plan", { method: "POST", body: { readable_id: rid, target: target() } }).then(function (res) {
          busy(dryBtn, false);
          if (!res.ok) { fail(resultEl, res); planEl.innerHTML = ""; applyBtn.disabled = true; return; }
          current = res.data.plan;
          renderPlan(current);
          setResult(resultEl, "Dry run: nothing changed. " + current.moves.length + " artifact" + (current.moves.length === 1 ? "" : "s") + " would move and " + Object.keys(current.registry_resets).length + " registry cells would reset.");
          applyBtn.disabled = false;
        });
      });

      applyBtn.addEventListener("click", function () {
        var who = initials(resultEl);
        if (!who || !current) return;
        var cells = Object.keys(current.registry_resets).length;
        confirmDialog({
          title: spot ? "Redo this timepoint" : "Apply the rewind",
          lines: [(spot ? "Rewind " : "Rewind ") + current.readable_id + " " + current.target_words + ": " + current.moves.length + " artifact" + (current.moves.length === 1 ? "" : "s") + " move into " + current.rewind_dir + ", " +
                  (current.chunk && current.chunk.present ? "its chunk leaves " + current.chunk.psx + ", " : "") + cells + " registry cells reset.",
                  spot ? "VICARIUS then records the spot_redo fact so Voyager 1 setup offers the timepoint again." : "Nothing is deleted; the moved files stay under _rewind."],
          okLabel: spot ? "Redo this timepoint" : "Apply rewind"
        }).then(function (ok) {
          if (!ok) return;
          busy(applyBtn, true);
          var path = spot ? "/api/spot-redo" : "/api/rewind/apply";
          api(path, { method: "POST", body: { readable_id: current.readable_id, target: current.target, initials: who, confirm: true } }).then(function (res) {
            busy(applyBtn, false);
            if (!res.ok) { fail(resultEl, res); return; }
            var m = res.data.manifest;
            setResult(resultEl, "Rewound " + esc(m.readable_id) + " " + esc(m.target_words) + ": " + m.moves.length + " artifact" + (m.moves.length === 1 ? "" : "s") + " moved into " + esc(m.rewind_dir) +
              (m.chunk ? ", " + (m.chunk.removed || 0) + " chunk removed" : "") + ", " + Object.keys(m.registry_resets).length + " registry cells reset" + (m.spot_redo ? ", spot_redo fact recorded" : "") + ".");
            load();
          });
        });
      });

      return { load: load };
    }
    var rewindUi = rewindTool("rw", false);
    var spotUi = rewindTool("sr", true);

    /* ==================================================================== */
    /* 6. the prompts doc                                                    */
    /* ==================================================================== */
    var prResult = q(root, "pr-result");
    function loadPrompts() {
      return api("/api/prompts").then(function (res) {
        if (!res.ok) { fail(prResult, res); return; }
        q(root, "pr-path").textContent = res.data.path + (res.data.updated_at ? "  (updated " + res.data.updated_at + ")" : "");
        q(root, "pr-doc").innerHTML = res.data.html || "";
        setResult(prResult, res.data.exists ? "" : esc(res.data.error || "The prompts document is missing."), res.data.exists ? "" : "error");
      });
    }
    q(root, "pr-reload").addEventListener("click", loadPrompts);

    /* ==================================================================== */
    /* 7. the parameter defaults                                             */
    /* ==================================================================== */
    var pmResult = q(root, "pm-result");
    var pmBody = q(root, "pm-body");
    var pmEditor = q(root, "pm-editor");
    var pmPhase = q(root, "pm-phase");

    function loadParams() {
      var phase = pmPhase.value;
      return api("/api/params?phase=" + encodeURIComponent(phase)).then(function (res) {
        var schemaChip = q(root, "pm-schema-chip");
        if (!res.ok) { fail(pmResult, res); pmBody.innerHTML = ""; pmEditor.innerHTML = ""; if (schemaChip) schemaChip.hidden = true; return; }
        // A phase with no editor schema yet (Voyager 2 until program D ships
        // params_schema_voyager2.yaml) is edited through the YAML box only.
        if (schemaChip) schemaChip.hidden = !!res.data.schema;
        state.params = res.data;
        renderParamsList(res.data);
        loadRef(res.data.default.ref, res.data.default);
      });
    }

    function renderParamsList(data) {
      pmBody.innerHTML = data.entries.map(function (e) {
        var kind = e.kind + (e.is_default ? " (default)" : "");
        return '<li class="row">' +
          '<span class="row-name"><a class="id code" href="' + esc(e.url) + '" target="_blank" rel="noopener">' + esc(e.ref) + "</a></span>" +
          '<span class="row-act"><button type="button" class="btn btn-mini" data-vt-load="' + esc(e.ref) + '" title="Load the values of this file into the editor as the starting point of a save.">Load</button></span>' +
          '<span class="row-meta"><span class="count">' + esc(kind) + '</span> <span class="count">' + esc(e.version || "") + '</span> <span class="count">' +
          esc(stampWords(e.saved_at)) + (e.saved_by ? " by " + esc(e.saved_by) : "") + "</span> " + esc(e.description) + "</span>" +
          "</li>";
      }).join("") || emptyRow("No parameter file for this phase.");
    }

    function loadRef(ref, preloaded) {
      var phase = pmPhase.value;
      var apply = function (file, diff) {
        state.params.loaded = file;
        q(root, "pm-loaded").textContent = "Editing " + file.ref + (file.version ? " (" + file.version + ")" : "") + (file.base_ref ? ", based on " + file.base_ref : "") + ".";
        renderEditor(state.params.schema, file.values);
        q(root, "pm-yaml").value = "";
        renderDiff(diff || []);
      };
      if (preloaded) { apply(preloaded, []); return Promise.resolve(); }
      return api("/api/params/load?phase=" + encodeURIComponent(phase) + "&ref=" + encodeURIComponent(ref)).then(function (res) {
        if (!res.ok) { fail(pmResult, res); return; }
        apply(res.data.file, res.data.diff);
        setResult(pmResult, "Loaded " + esc(ref) + ".");
      });
    }

    pmBody.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-load]");
      if (btn) loadRef(btn.getAttribute("data-vt-load"));
    });
    pmPhase.addEventListener("change", loadParams);

    function getPath(values, path) {
      return path.split(".").reduce(function (node, key) { return node && typeof node === "object" ? node[key] : undefined; }, values);
    }
    function setPath(values, path, value) {
      var parts = path.split(".");
      var node = values;
      for (var i = 0; i < parts.length - 1; i++) {
        if (!node[parts[i]] || typeof node[parts[i]] !== "object") node[parts[i]] = {};
        node = node[parts[i]];
      }
      node[parts[parts.length - 1]] = value;
    }

    function controlHtml(key, value, id) {
      var attrs = ' id="' + id + '" data-vt-key="' + esc(key.path) + '" data-vt-type="' + esc(key.type) + '"';
      if (key.type === "boolean") {
        return '<label class="opt"><input type="checkbox"' + attrs + (value ? " checked" : "") + ' /> <span>' + esc(key.label) + "</span></label>";
      }
      if (key.choices) {
        return '<select class="vt-field"' + attrs + ">" + key.choices.map(function (c) {
          return '<option value="' + esc(c) + '"' + (String(c) === String(value) ? " selected" : "") + ">" + esc(c) + "</option>";
        }).join("") + "</select>";
      }
      if (key.type === "integer" || key.type === "number") {
        var step = key.type === "integer" ? "1" : "any";
        var range = (key.min !== undefined ? ' min="' + esc(key.min) + '"' : "") + (key.max !== undefined ? ' max="' + esc(key.max) + '"' : "");
        return '<input type="number" class="vt-field" step="' + step + '"' + range + attrs + ' value="' + esc(value === undefined || value === null ? "" : value) + '" />' + (key.unit ? ' <span class="count">' + esc(key.unit) + "</span>" : "");
      }
      if (key.type === "list") {
        return '<textarea class="vt-field" rows="3"' + attrs + ">" + esc(JSON.stringify(value === undefined ? [] : value)) + "</textarea>";
      }
      return '<input type="text" class="vt-field"' + attrs + ' value="' + esc(value === undefined || value === null ? "" : value) + '" />';
    }

    function renderEditor(schema, values) {
      if (!schema) {
        pmEditor.innerHTML = '<p class="count">No editor schema for this phase; edit the values through the YAML box.</p>';
        return;
      }
      pmEditor.innerHTML = schema.sections.map(function (section) {
        var keys = schema.keys.filter(function (k) { return k.section === section.id; });
        if (!keys.length) return "";
        return "<fieldset><legend>" + esc(section.label) +
          '<button type="button" class="tip" aria-label="About ' + esc(section.label) + '" title="' + esc(section.tooltip) + '"><span>i</span></button></legend>' +
          '<dl class="choices">' + keys.map(function (key) {
            var id = "vt-pm-" + key.path.replace(/\./g, "-");
            var label = key.type === "boolean" ? "" : '<label for="' + id + '">' + esc(key.label) + "</label>";
            return "<div><dt>" + (label || '<span>' + esc(key.label) + "</span>") +
              '<button type="button" class="tip" aria-label="About ' + esc(key.label) + '" title="' + esc(key.tooltip) + '"><span>?</span></button></dt>' +
              "<dd>" + controlHtml(key, getPath(values, key.path), id) + "</dd></div>";
          }).join("") + "</dl></fieldset>";
      }).join("");
    }

    function readEditor() {
      var values = JSON.parse(JSON.stringify((state.params && state.params.loaded && state.params.loaded.values) || {}));
      var problems = [];
      qa(pmEditor, "[data-vt-key]").forEach(function (input) {
        var path = input.getAttribute("data-vt-key");
        var type = input.getAttribute("data-vt-type");
        var value;
        if (type === "boolean") value = !!input.checked;
        else if (type === "integer" || type === "number") {
          // A blank number keeps whatever the loaded file holds for the key
          // (a file may lack a key the schema knows); nothing is invented.
          if (input.value.trim() === "") return;
          value = Number(input.value);
          if (!isFinite(value) || (type === "integer" && !Number.isInteger(value))) { problems.push(path + ": not a " + type); return; }
        } else if (type === "list") {
          try { value = JSON.parse(input.value || "[]"); } catch (e) { problems.push(path + ": the list must be JSON, for example [\"a\", \"b\"]"); return; }
        } else value = input.value;
        setPath(values, path, value);
      });
      return { values: values, problems: problems };
    }

    function renderDiff(diff) {
      var host = q(root, "pm-diff");
      host.innerHTML = diff.length ? "<ul>" + diff.map(function (d) {
        var cls = d.kind === "added" ? "is-added" : (d.kind === "removed" ? "is-removed" : "is-changed");
        return '<li class="' + cls + '">' + esc(d.kind) + " " + esc(d.path) + ": " + esc(JSON.stringify(d.a)) + " to " + esc(JSON.stringify(d.b)) + "</li>";
      }).join("") + "</ul>" : "";
    }

    function saveBody(extra) {
      var read = readEditor();
      if (read.problems.length) { setResult(pmResult, "The editor holds values that do not parse: " + esc(read.problems.join("; ")), "error"); return null; }
      var body = { phase: pmPhase.value, values: read.values, yaml_text: q(root, "pm-yaml").value };
      Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
      return body;
    }

    q(root, "pm-validate").addEventListener("click", function () {
      var body = saveBody();
      if (!body) return;
      api("/api/params/validate", { method: "POST", body: body }).then(function (res) {
        if (!res.ok) { fail(pmResult, res); return; }
        if (!res.data.ok) { fail(pmResult, res, "The values do not pass the schema."); renderDiff([]); return; }
        renderDiff(res.data.diff);
        setResult(pmResult, res.data.diff.length ? "Valid: " + res.data.diff.length + " difference" + (res.data.diff.length === 1 ? "" : "s") + " from the default (" + esc(res.data.level) + " bump if saved as default)." : "Valid and identical to the default.");
      });
    });

    function gitLine(git) {
      q(root, "pm-git").textContent = git ? (git.committed ? "Commit " + git.commit + ": " + git.detail : "Not committed: " + git.detail) : "";
    }

    q(root, "pm-branch").addEventListener("click", function () {
      var who = initials(pmResult);
      if (!who) return;
      var body = saveBody({
        initials: who, slug: q(root, "pm-slug").value.trim(), description: q(root, "pm-desc").value.trim(),
        why: q(root, "pm-why").value.trim(), base_ref: state.params && state.params.loaded ? state.params.loaded.ref : "default"
      });
      if (!body) return;
      var btn = q(root, "pm-branch");
      busy(btn, true);
      api("/api/params/branch", { method: "POST", body: body }).then(function (res) {
        busy(btn, false);
        if (!res.ok) { fail(pmResult, res); return; }
        setResult(pmResult, "Saved branch file " + esc(res.data.file.ref) + " (" + esc(res.data.file.version) + ").", res.data.git.committed ? "" : "warn");
        gitLine(res.data.git);
        loadParams();
      });
    });

    q(root, "pm-default").addEventListener("click", function () {
      var who = initials(pmResult);
      if (!who) return;
      var loaded = state.params && state.params.loaded ? state.params.loaded : null;
      confirmDialog({
        title: "Save as the new default",
        lines: ["The default is the parameter set every Voyager 1 run starts from; saving it writes the next numbered version under " + pmPhase.value + "/versions/, points DEFAULT at it, and commits and pushes to the public voyagerparams repository.",
                "Changed values bump MINOR; a key added, removed or renamed bumps MAJOR. The current default is " + (state.params ? state.params.default_version : "?") + (loaded ? "; the editor holds " + loaded.ref : "") + "."],
        field: { label: "Description of this default", tip: "Describe what changed and why in one line; VICARIUS writes it into the file header and the commit message.", required: true },
        okLabel: "Save as default"
      }).then(function (description) {
        if (!description) return;
        var body = saveBody({ initials: who, description: description, confirm: true });
        if (!body) return;
        var btn = q(root, "pm-default");
        busy(btn, true);
        api("/api/params/default", { method: "POST", body: body }).then(function (res) {
          busy(btn, false);
          if (!res.ok) { fail(pmResult, res); return; }
          setResult(pmResult, "New default " + esc(res.data.file.version) + " (" + esc(res.data.level) + " bump).", res.data.git.committed ? "" : "warn");
          gitLine(res.data.git);
          loadParams();
        });
      });
    });

    /* ==================================================================== */
    /* 8. the camera model list                                              */
    /* ==================================================================== */
    var seBody = q(root, "se-body");
    var seResult = q(root, "se-result");

    function loadSeasons() {
      return api("/api/seasons").then(function (res) {
        if (!res.ok) { fail(seResult, res); return; }
        seBody.innerHTML = res.data.seasons.map(function (s) {
          var label = s.label ? esc(s.label) : seasonWords(s.year, s.season_token);
          var recorded = s.set_at ? esc(stampWords(s.set_at)) + " by " + esc(s.set_by) : "never";
          return '<li class="row" data-season="' + esc(s.season_key) + '">' +
            '<span class="row-name">' + label + " " + idChip(s.season_key) + "</span>" +
            '<span class="row-act"><button type="button" class="btn btn-mini btn-primary" data-vt-season-save="' + esc(s.season_key) + '" title="Save the cells of this season. VICARIUS keeps the other cells, stamps the time and your initials, and writes one season event.">Save</button></span>' +
            '<span class="row-state">' + recorded + "</span>" +
            '<span class="row-meta"><span class="count">' + s.row_count + " row" + (s.row_count === 1 ? "" : "s") + "</span></span>" +
            '<div class="row-fields">' +
            '<label class="opt-field"><span class="vt-field-label">Camera model</span><input type="text" class="vt-field" data-vt-field="camera_model" value="' + esc(s.camera_model) + '" title="Type the camera model used for every video of this season, for example GoPro HERO12."></label>' +
            '<label class="opt-field"><span class="vt-field-label">Preprocessing</span><input type="text" class="vt-field" data-vt-field="preprocessing" value="' + esc(s.preprocessing) + '" title="Type the preprocessing the videos of this season went through before Voyager 1, for example proxy encode or stabilisation."></label>' +
            '<label class="opt-field"><span class="vt-field-label">Notes</span><input type="text" class="vt-field" data-vt-field="notes" value="' + esc(s.notes) + '" title="Type anything else about the filming of this season that a reader of the atlas should know."></label>' +
            "</div></li>";
        }).join("") || emptyRow("No season in the registry yet.");
      });
    }

    seBody.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-season-save]");
      if (!btn) return;
      var who = initials(seResult);
      if (!who) return;
      var row = btn.closest(".row");
      var body = { season_key: btn.getAttribute("data-vt-season-save"), initials: who };
      qa(row, "[data-vt-field]").forEach(function (input) { body[input.getAttribute("data-vt-field")] = input.value; });
      busy(btn, true);
      api("/api/seasons", { method: "POST", body: body }).then(function (res) {
        busy(btn, false);
        if (!res.ok) { fail(seResult, res); return; }
        setResult(seResult, (res.data.changed ? "Saved " : "No change for ") + esc(res.data.label) + ".");
        loadSeasons();
      });
    });

    /* ==================================================================== */
    /* boot                                                                  */
    /* ==================================================================== */
    var loaders = {
      order: loadOrder, workbench: function () { /* on demand: the scan asks the NAS */ q(root, "wb-roots").textContent = "Press Scan the Workbench to list the folders and compare them to the Shelf."; },
      manual: loadManual, rewind: rewindUi.load, spot: spotUi.load, prompts: loadPrompts, params: loadParams, seasons: loadSeasons
    };

    pruneDetached();
    instances.push(instance);
    Object.keys(PANES).forEach(instance.initPane);

    api("/api/state").then(function (res) {
      if (!res.ok) return;
      var stamp = q(root, "stamp");
      if (stamp) stamp.textContent = "Read " + String(res.data.now || "").replace("T", " ").slice(0, 16) + " AST";
    });
    showTool(options.pane || readStore(LS_TAB) || toolKeys()[0]);

    // unmount(): stop every pane, drop the admin subscription, and swap the
    // section for a clean copy with no listeners, so the container can be
    // mounted again. A caller keeps the container, not the section.
    return {
      unmount: function () {
        stopPanes(true);
        if (stopWatching) { stopWatching(); stopWatching = null; }
        var at = instances.indexOf(instance);
        if (at !== -1) instances.splice(at, 1);
        var fresh = root.cloneNode(true);
        fresh.removeAttribute("data-vt-mounted");
        qa(fresh, ".vt-dialog-backdrop").forEach(function (d) { d.parentNode.removeChild(d); });
        var stages = fresh.querySelector(".vt-stages");
        if (stages) stages.hidden = false;
        var line = q(fresh, "admin-line");
        if (line) { line.textContent = ""; line.hidden = true; }
        if (root.parentNode) root.parentNode.replaceChild(fresh, root);
      }
    };
  }

  // mountPanel(el, opts): the atlas's entry (opts = {onClose, pane}); the
  // panel mount with Back to the board on the relocked admin line.
  function mountPanel(el, opts) {
    var options = opts || {};
    return mount(el, { pane: options.pane, onClose: options.onClose });
  }

  function hasStylesheet(name) { return !!document.querySelector('link[href*="' + name + '"]'); }

  // ensureStylesheet(base, version): the panel's own sheet, then the role
  // sheet and the scoped shared block before it, each once per document.
  function ensureStylesheet(base, version) {
    var suffix = version ? "?v=" + version : "";
    var link = document.querySelector('link[data-vt-css="1"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = base + "/static/" + CSS_NAME + suffix;
      link.setAttribute("data-vt-css", "1");
      document.head.appendChild(link);
    }
    if (!hasStylesheet(ROLES_CSS)) {
      var roles = document.createElement("link");
      roles.rel = "stylesheet";
      roles.href = base + "/static/" + ROLES_CSS + suffix;
      document.head.insertBefore(roles, link);
    }
    if (hasStylesheet(SHARED_CSS) || document.querySelector('style[data-scoped-shared="voyager-tools"]')) return;
    var style = document.createElement("style");
    style.setAttribute("data-scoped-shared", ROOT_CLASS);
    document.head.insertBefore(style, link);
    fetch(base + "/static/" + SHARED_CSS + suffix, { credentials: "same-origin" })
      .then(function (response) { return response.ok ? response.text() : ""; })
      .then(function (text) { style.textContent = scopeCss(text, "." + ROOT_CLASS); },
            function () { /* the panel's own sheet carries the token fallbacks */ });
  }

  // scopeCss(text, scope): every selector of a stylesheet prefixed with
  // scope. ":root" becomes the scope itself, html and body rules stay with the
  // host page and are dropped, "*" becomes "scope *", ".bubble" (appended to
  // body by the tooltip script) stays global, at-rules holding rules (media,
  // supports, container, layer) are recursed and every other at-rule passes
  // through. The same rule as window.ManualEdit.scopeCss.
  function scopeCss(text, scope) {
    return scopeRules(String(text || "").replace(/\/\*[\s\S]*?\*\//g, ""), scope);
  }
  function scopeRules(src, scope) {
    var out = [];
    var pos = 0;
    while (pos < src.length) {
      var open = src.indexOf("{", pos);
      if (open < 0) break;
      var head = src.slice(pos, open).trim();
      var close = matchingBrace(src, open);
      var body = src.slice(open + 1, close);
      if (head.charAt(0) === "@") {
        if (/^@(media|supports|container|layer)\b/.test(head)) out.push(head + "{" + scopeRules(body, scope) + "}");
        else out.push(head + "{" + body + "}");
      } else if (head) {
        var selectors = head.split(",").map(function (sel) { return scopeSelector(sel.trim(), scope); }).filter(Boolean);
        if (selectors.length) out.push(selectors.join(",") + "{" + body + "}");
      }
      pos = close + 1;
    }
    return out.join("\n");
  }
  function matchingBrace(src, open) {
    var depth = 0;
    for (var i = open; i < src.length; i++) {
      if (src.charAt(i) === "{") depth += 1;
      else if (src.charAt(i) === "}") { depth -= 1; if (depth === 0) return i; }
    }
    return src.length;
  }
  function scopeSelector(selector, scope) {
    if (!selector) return "";
    if (selector === ":root") return scope;
    if (selector === "html" || selector === "body") return "";
    if (selector === "*") return scope + " *";
    if (selector.indexOf(".bubble") === 0) return selector;
    return scope + " " + selector;
  }

  var pending = (window.VoyagerTools && window.VoyagerTools._pendingPanes) || [];
  window.VoyagerTools = Object.assign(window.VoyagerTools || {}, {
    mount: mount, mountPanel: mountPanel, registerPane: registerPane, scopeCss: scopeCss, _pendingPanes: []
  });
  pending.forEach(function (entry) { registerPane(entry[0], entry[1]); });

  // Standalone page: mount the root the template rendered.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { var r = document.getElementById("vt-root"); if (r && !r.closest(".win-body")) mount(r); });
  } else {
    var r = document.getElementById("vt-root");
    if (r && !r.closest(".win-body")) mount(r);
  }
})();
