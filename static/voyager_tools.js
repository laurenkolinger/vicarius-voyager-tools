/* static/voyager_tools.js - the Voyager tools fragment (program spec section 7),
 * converted to the design language of docs/superpowers/specs/2026-09-02-
 * workflow-page-design.md and 2026-09-02-matrix-window-design.md.
 *
 * One IIFE exporting window.VoyagerTools = { mount(el) }. mount() takes the
 * <section class="voyager-tools"> root (or a container holding it), guards a
 * double mount with data-vt-mounted, injects the scoped stylesheet when the
 * page did not link it, and wires the eight tools. Every request goes to
 * BASE + '/api/...' where BASE is data-vt-base on the root, or
 * window.VOYAGER_TOOLS_BASE, or '/voyager-tools'. A 401 shows the admin
 * line; a 423 shows the lock message. Destructive actions open a role=dialog
 * confirm that names what will change; Escape closes it without bubbling,
 * because the desktop closes a window on a bubbled Escape.
 *
 * Every result list (the processing order, the Workbench folders, the
 * parameter versions, the seasons table, a manual edit job's rows, a
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
  var LS_TAB = "vt.tab";
  var LS_INITIALS = "vt.initials";
  var SS_INITIALS = "vic_initials";
  var INITIALS_RE = /^[A-Za-z0-9]{1,8}$/;
  var TOOLS = ["order", "workbench", "manual", "rewind", "spot", "prompts", "params", "seasons"];
  var TARGET_WORDS = { before_step1: "before step 1", before_step0: "before step 0" };
  var GB = 1e9;

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

  function mount(el) {
    var root = el && el.classList && el.classList.contains("voyager-tools") ? el
      : (el ? el.querySelector(".voyager-tools") : null);
    if (!root || root.getAttribute("data-vt-mounted") === "1") return;
    if (root.getAttribute("data-admin-required") === "1") return; // the admin body, nothing to wire
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
      var init = { method: opts.method || "GET", headers: { Accept: "application/json" }, credentials: "same-origin" };
      if (opts.body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(opts.body);
      }
      return fetch(BASE + path, init).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (r.status === 401) showAdminLine("Admin mode is required for Voyager tools. Unlock admin from the ADMIN chip and open it again.");
          else if (r.status === 423) showAdminLine(data.message || "Voyager tools is locked.");
          return { ok: r.ok, status: r.status, data: data };
        });
      }).catch(function (err) {
        return { ok: false, status: 0, data: { error: "The request did not reach VICARIUS: " + err } };
      });
    }

    function showAdminLine(text) {
      var line = q(root, "admin-line");
      if (!line) return;
      line.textContent = text;
      line.hidden = !text;
    }

    function fail(resEl, res, fallback) {
      var message = (res.data && res.data.error) || fallback || ("Request failed (" + res.status + ")");
      var html = esc(message);
      if (res.data && res.data.pullback_command) html += "<pre>" + esc(res.data.pullback_command) + "</pre>";
      if (res.data && res.data.problems && res.data.problems.length) {
        html += "<ul>" + res.data.problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
      }
      setResult(resEl, html, "error");
    }

    /* -- initials --------------------------------------------------------- */
    var initialsEl = root.querySelector("#vt-initials");
    function initialsSeed() {
      var v = readStore(LS_INITIALS);
      if (!v) { try { v = window.sessionStorage.getItem(SS_INITIALS); } catch (e) { v = null; } }
      if (!v && window.VICBOOT && typeof window.VICBOOT.getInitials === "function") { try { v = window.VICBOOT.getInitials(); } catch (e) { v = null; } }
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
        setResult(resEl, "Type your initials (one to eight letters or digits) at the top of the page first.", "error");
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

    /* -- tabs (the left list of eight tools) -------------------------------- */
    var tabs = qa(root, ".vt-tab");
    function showTool(name) {
      if (TOOLS.indexOf(name) === -1) name = TOOLS[0];
      tabs.forEach(function (t) { t.setAttribute("aria-selected", t.getAttribute("data-vt-tab") === name ? "true" : "false"); });
      qa(root, ".vt-pane").forEach(function (p) { p.hidden = p.getAttribute("data-vt-pane") !== name; });
      writeStore(LS_TAB, name);
      if (!state.loaded[name]) { state.loaded[name] = true; loaders[name](); }
    }
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
        renderManual(res.data.job);
      });
    }

    function renderManual(job) {
      var host = q(root, "me-summary");
      var cancel = q(root, "me-cancel");
      if (!job) {
        host.innerHTML = '<p class="count">No manual edit job is current.</p>';
        cancel.disabled = true;
        return;
      }
      cancel.disabled = !job.can_cancel;
      var onCancel = job.refusal ? esc(job.refusal)
        : (job.will_prune ? "the edit bench copies are removed: " + esc(job.prune_targets.join(", ") || "none on disk")
                          : "the local copies are kept (the job reached the push to the Shelf)");
      host.innerHTML =
        '<dl class="choices">' +
        '<div><dt>Job</dt><dd>' + esc(job.job_id) + " (" + esc(job.phase) + ")</dd></div>" +
        "<div><dt>Created</dt><dd>" + esc(job.created_at) + " by " + esc(job.created_by) + "</dd></div>" +
        '<div><dt>Edit bench</dt><dd><span class="id">' + esc(job.edit_bench) + "</span></dd></div>" +
        "<div><dt>On cancel</dt><dd>" + onCancel + "</dd></div>" +
        "</dl>" +
        '<ul class="rows" aria-label="The rows of this job">' + job.rows.map(function (r) {
          return '<li class="row"><span class="row-name">' + idChip(r.readable_id) + '</span><span class="row-meta">' +
            siteLabel(r.site_name, r.site) + " " + idChip(r.transect) + " back to " + esc(r.shelf_location || "?") + "</span></li>";
        }).join("") + "</ul>" +
        '<details class="src"><summary>History (' + job.history.length + ')</summary><ul>' + job.history.map(function (h) {
          return "<li>" + esc(h.at) + " " + esc(h.phase) + " by " + esc(h.by) + (h.reason ? ": " + esc(h.reason) : "") + "</li>";
        }).join("") + "</ul></details>" +
        '<details class="src"><summary>Log tail (' + job.log_tail.length + ')</summary><pre class="vt-log">' + esc(job.log_tail.join("\n") || "(empty)") + "</pre></details>";
      host.setAttribute("data-job-id", job.job_id);
      host.setAttribute("data-job-rows", String(job.rows.length));
    }

    q(root, "me-refresh").addEventListener("click", loadManual);
    q(root, "me-cancel").addEventListener("click", function () {
      var who = initials(meResult);
      if (!who) return;
      var host = q(root, "me-summary");
      var jobId = host.getAttribute("data-job-id") || "";
      var rows = host.getAttribute("data-job-rows") || "0";
      confirmDialog({
        title: "Cancel and prune the manual edit job",
        lines: ["Cancel job " + jobId + " with " + rows + " row" + (rows === "1" ? "" : "s") + ".",
                "VICARIUS restores each row's processing location to the Shelf, sets manual_edit_status back to awaiting, removes the edit bench copies when the job never reached the push, and clears the current job."],
        okLabel: "Cancel and prune"
      }).then(function (ok) {
        if (!ok) return;
        api("/api/manual-edit/cancel", { method: "POST", body: { initials: who, confirm: true } }).then(function (res) {
          if (!res.ok) { fail(meResult, res); return; }
          var d = res.data;
          var text = "Job " + esc(d.job_id) + " is " + esc(d.phase) + ": " + d.restored.length + " row" + (d.restored.length === 1 ? "" : "s") + " restored, " +
            d.pruned.length + " local cop" + (d.pruned.length === 1 ? "y" : "ies") + " removed" + (d.kept.length ? ", " + d.kept.length + " kept" : "") + ".";
          if (d.warnings.length) text += " " + esc(d.warnings.join(" "));
          if (d.problems.length) text += " " + esc(d.problems.join(" "));
          setResult(meResult, text, d.problems.length ? "warn" : "");
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
            var note = r.in_job ? " (in the manual edit job)" : (!r.local ? " (on the NAS)" : "") + (r.run_state === "running" ? " (running)" : "");
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
        if (!res.ok) { fail(pmResult, res); pmBody.innerHTML = ""; pmEditor.innerHTML = ""; return; }
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

    api("/api/state").then(function (res) {
      if (!res.ok) return;
      var stamp = q(root, "stamp");
      if (stamp) stamp.textContent = "Read " + String(res.data.now || "").replace("T", " ").slice(0, 16) + " AST";
    });
    showTool(readStore(LS_TAB) || TOOLS[0]);
  }

  function ensureStylesheet(base, version) {
    if (document.querySelector('link[data-vt-css="1"]')) return;
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = base + "/static/" + CSS_NAME + (version ? "?v=" + version : "");
    link.setAttribute("data-vt-css", "1");
    document.head.appendChild(link);
  }

  window.VoyagerTools = { mount: mount };

  // Standalone page: mount the root the template rendered.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { var r = document.getElementById("vt-root"); if (r && !r.closest(".win-body")) mount(r); });
  } else {
    var r = document.getElementById("vt-root");
    if (r && !r.closest(".win-body")) mount(r);
  }
})();
