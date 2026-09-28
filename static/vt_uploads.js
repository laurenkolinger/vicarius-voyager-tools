/* static/vt_uploads.js - the Uploads pane of the Atlas control panel
 * (program C spec sections 3.7, 3.8 and 5.5, milestone C7), registered on
 * the pane seam of static/voyager_tools.js.
 *
 * The pane lists, across every unfinished manual edit project (program B's
 * GET /manual-edit/api/jobs?active=1, one card per project), the transects
 * marked for re-upload (tickable), those uploading (with Watch) and those in
 * admin review (read only), and every upload set of the last 7 days (GET
 * /manual-edit/api/uploads?limit=50) with Watch. Upload ticked checks the
 * panel initials, reads the Shelf copy setting (GET /api/settings/shelf),
 * asks for a confirmation that names each transect and what happens to its
 * Shelf copy, and starts one upload set through POST /manual-edit/api/uploads
 * {items, initials}. The progress window is the editor's own component,
 * window.ManualEditUpload.mount (static/manual_edit_upload.js of the manual
 * edit module, loaded once with its stylesheet when the page lacks it), in a
 * dialog appended to the page body: a relock hides the panel but leaves the
 * window open, the upload keeps running on the server, and the component
 * shows Stop only while its own reading says admin (chain decision 8).
 *
 * The same progress window serves the review content's Start the upload now
 * (static/vt_review.js): it is exported as window.VoyagerTools.openUploadWindow.
 *
 * The pure helpers (readyItems, runningItems, reviewItems, recentUploads,
 * uploadPlanLines, statusRole) are exported to node through module.exports
 * for tests/test_manual_edit_pins.py.
 */
(function () {
  "use strict";

  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var STATE_UPLOAD = "upload";
  var STATE_UPLOADING = "uploading";
  var STATE_REVIEW = "review";
  var RECENT_DAYS = 7;
  var DAY_MS = 86400000;
  var RECENT_LIMIT = 50;
  var POLL_MS = 30000;
  var TICKS_SHOWN_ABOVE = 2;  // Tick all and Untick all appear for more than two choices (design 5.2)
  var HTTP_NOT_FOUND = 404;
  var ARCHIVE_SUFFIX = "_voy1_archive";
  var MANUAL_EDIT_BASE = "/manual-edit";
  var UPLOAD_SCRIPT = MANUAL_EDIT_BASE + "/static/manual_edit_upload.js";
  var UPLOAD_CSS = MANUAL_EDIT_BASE + "/static/manual_edit.css";
  var UPLOAD_CSS_NAME = "manual_edit.css";
  var JOBS_ROUTE = MANUAL_EDIT_BASE + "/api/jobs?active=1";
  var UPLOADS_ROUTE = MANUAL_EDIT_BASE + "/api/uploads";
  var SHELF_ROUTE = "/api/settings/shelf";
  // A done set is strictly verified, the role the Timelines pane gives an uploaded transect.
  var ROLE_REVIEW = "stage-review";  // the atlas matrix's admin review colour (static/vicarius_roles.css)
  var STATUS_ROLES = { queued: "role-planned", running: "role-in-progress", done: "role-verified", failed: "role-failed",
                       stopped: "role-attention", interrupted: "role-attention" };
  var STATUS_WORDS = { queued: "queued", running: "running", done: "done", failed: "failed", stopped: "stopped",
                       interrupted: "interrupted" };
  var ORIGIN_WORDS = { finish: "from Finish project", admin: "from the Atlas control panel" };
  var LINE_NOT_MOUNTED = "The manual edit module is not mounted here.";
  var LINE_NONE_READY = "No transect is marked for re-upload.";
  var LINE_NONE_RECENT = "No upload set was started in the last 7 days.";
  var LINE_TICK_FIRST = "Tick at least one transect to upload.";
  var LINE_NO_COMPONENT = "The upload progress window could not load; the upload continues on the server.";
  var TIP_WATCH = "Open the progress window of this upload set. VICARIUS shows Stop only in admin mode.";
  // The transect state words of the project cards (manualedit.jobstore.STATE_WORDS), for the rows drawn here.
  var WORDS_UPLOADING = "Uploading in";
  var WORDS_REVIEW = "Marked for admin review";

  /* ---- pure helpers (exported to node) --------------------------------------- */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // transectsOf(cards, keep): every transect of every card that keep(t)
  // accepts, flattened with its project's id and creator.
  function transectsOf(cards, keep) {
    var out = [];
    (Array.isArray(cards) ? cards : []).forEach(function (c) {
      if (!c || typeof c !== "object" || !Array.isArray(c.transects)) return;
      c.transects.forEach(function (t) {
        if (!t || typeof t !== "object" || typeof t.psx !== "string" || !t.psx || !keep(t)) return;
        out.push({ job_id: c.job_id, editor: c.created_by || "", psx: t.psx, processing_folder: t.processing_folder || "",
                   site: t.site || "", site_name: t.site_name || "", transect: t.transect || "",
                   round: typeof t.round === "number" ? t.round : 0, state: t.state, upload_id: t.upload_id || "",
                   shelf_mode: t.shelf_mode === "archive" || t.shelf_mode === "overwrite" ? t.shelf_mode : "",
                   pre_edit_archive: t.pre_edit_archive || "" });
      });
    });
    return out;
  }

  // readyItems(cards): transects marked for re-upload that no upload set holds.
  function readyItems(cards) {
    return transectsOf(cards, function (t) { return t.state === STATE_UPLOAD && !t.upload_id; });
  }

  // runningItems(cards): transects an upload set holds now.
  function runningItems(cards) {
    return transectsOf(cards, function (t) { return t.state === STATE_UPLOADING && !!t.upload_id; });
  }

  // reviewItems(cards): transects marked for admin review.
  function reviewItems(cards) {
    return transectsOf(cards, function (t) { return t.state === STATE_REVIEW; });
  }

  // recentUploads(uploads, nowMs, days): the sets created within `days` of
  // nowMs, newest first; a set file that could not be read stays listed so
  // the problem shows.
  function recentUploads(uploads, nowMs, days) {
    var floor = nowMs - days * DAY_MS;
    return (Array.isArray(uploads) ? uploads : []).filter(function (u) {
      if (!u || typeof u !== "object") return false;
      if (u.corrupt) return true;
      var at = Date.parse(u.created_at);
      return isFinite(at) && at >= floor && at <= nowMs + DAY_MS;
    }).sort(function (a, b) { return String(b.upload_id).localeCompare(String(a.upload_id)); });
  }

  // transectWords(item): "Lang Bank Red Hind FSA (LBH) T3", or "MRS T2" when
  // the site table has no name for the code.
  function transectWords(item) {
    var site = item.site_name ? item.site_name + " (" + item.site + ")" : item.site;
    return site + " " + item.transect;
  }

  // baseName(path): the last part of a Shelf path.
  function baseName(path) { return String(path).replace(/\/+$/, "").split("/").pop(); }

  // pinnedLine(item): the line of a transect whose first upload attempt already
  // changed the Shelf, which keeps that attempt's mode whatever the setting says;
  // "" for a transect with no such attempt.
  function pinnedLine(item) {
    var folder = item.processing_folder;
    if (item.shelf_mode === "archive") {
      var archive = item.pre_edit_archive ? baseName(item.pre_edit_archive) : folder + ARCHIVE_SUFFIX;
      return "its first upload attempt kept the Voyager 1 copy as " + archive + ", so the edit goes up as " +
        folder + " in archive mode.";
    }
    if (item.shelf_mode === "overwrite") {
      return "its first upload attempt began to overwrite the Shelf folder " + folder +
        ", so the edit replaces it in overwrite mode and files the edit removed are deleted there.";
    }
    return "";
  }

  // uploadPlanLines(items, archive): the confirmation's lines; archive is the
  // Shelf copy setting (true archive, false overwrite, null when unread), which
  // a transect pinned by its first upload attempt does not follow.
  function uploadPlanLines(items, archive) {
    var lines = ["Upload " + plural(items.length, "transect") + " to the Shelf, one after another:"];
    items.forEach(function (item) {
      var head = transectWords(item) + " (" + item.psx + "): ";
      var folder = item.processing_folder;
      var pinned = pinnedLine(item);
      if (pinned) {
        lines.push(head + pinned);
      } else if (archive === true) {
        lines.push(head + "the Shelf folder " + folder + " is renamed to " + folder + ARCHIVE_SUFFIX +
                   " (or a name with the edit round when an earlier archive holds that one) and kept, then the " +
                   "edit goes up as " + folder + ".");
      } else if (archive === false) {
        lines.push(head + "the edit replaces the Shelf folder " + folder + ", and files the edit removed are deleted there.");
      } else {
        lines.push(head + "the Shelf copy is archived or overwritten as the Shelf copy setting says when the upload starts.");
      }
    });
    return lines;
  }

  // statusRole(status): an upload set's status on the matrix design's roles.
  function statusRole(status) { return STATUS_ROLES[status] || "role-empty"; }

  var helpers = { readyItems: readyItems, runningItems: runningItems, reviewItems: reviewItems,
                  recentUploads: recentUploads, uploadPlanLines: uploadPlanLines, statusRole: statusRole };
  if (typeof module === "object" && module && module.exports) module.exports = helpers;
  if (typeof window === "undefined") return;

  /* ---- markup ------------------------------------------------------------------ */

  function chip(text) { return '<span class="id code">' + esc(text) + "</span>"; }

  // nameHtml(item): the site's full name beside its code chip and the transect;
  // a code the site table cannot resolve stays bare with a tooltip saying so.
  function nameHtml(item) {
    var site = item.site_name ? '<span class="vt-site">' + esc(item.site_name) + "</span> " + chip(item.site)
      : '<span title="' + esc("The site table has no name for " + item.site + ".") + '">' + chip(item.site) + "</span>";
    return site + " " + esc(item.transect);
  }

  function stateHtml(role, words) {
    return '<span class="vt-state"><span class="chip ' + esc(role) + '" aria-hidden="true"></span>' + esc(words) + "</span>";
  }

  // projectMetaHtml(item, extra): the project as a chip, who started it, and
  // any extra count line (the edit round), each its own span in the meta row.
  function projectMetaHtml(item, extra) {
    return '<span class="row-meta"><span class="count">project ' + chip(item.job_id) + "</span>" +
      '<span class="count">started by ' + esc(item.editor) + "</span>" + (extra || "") + "</span>";
  }

  function readyRowHtml(item, index, ticked) {
    var label = transectWords(item);
    return '<li class="row vt-up-row"><span class="row-name"><label class="opt vt-up-pick">' +
      '<input type="checkbox" data-vt-up-tick="' + index + '"' + (ticked ? " checked" : "") +
      ' aria-label="' + esc("Tick " + label + " for upload") + '"> ' + nameHtml(item) + " " + chip(item.psx) +
      "</label></span>" + projectMetaHtml(item) + "</li>";
  }

  function watchButtonHtml(uploadId) {
    return '<button type="button" class="btn btn-mini" data-vt-watch="' + esc(uploadId) + '" title="' + esc(TIP_WATCH) + '">Watch</button>';
  }

  function runningRowHtml(item) {
    return '<li class="row"><span class="row-name">' + nameHtml(item) + " " + chip(item.psx) + "</span>" +
      '<span class="row-act">' + watchButtonHtml(item.upload_id) + "</span>" +
      '<span class="row-state">' + stateHtml("role-in-progress", WORDS_UPLOADING) + " " + chip(item.upload_id) + "</span>" +
      projectMetaHtml(item) + "</li>";
  }

  function reviewRowHtml(item) {
    var round = item.round > 0 ? '<span class="count">edit round ' + esc(item.round + 1) + "</span>" : "";
    return '<li class="row"><span class="row-name">' + nameHtml(item) + " " + chip(item.psx) + "</span>" +
      '<span class="row-state">' + stateHtml(ROLE_REVIEW, WORDS_REVIEW) + "</span>" +
      projectMetaHtml(item, round) + "</li>";
  }

  function stampWords(iso) {
    var t = String(iso || "");
    return t.length < 16 ? t : t.slice(0, 10) + " " + t.slice(11, 16) + " AST";
  }

  function recentRowHtml(upload) {
    if (upload.corrupt) {
      return '<li class="row"><span class="row-name">' + chip(upload.upload_id) + "</span>" +
        '<span class="row-meta vt-error-text">' + esc(upload.error) + "</span></li>";
    }
    var items = Array.isArray(upload.items) ? upload.items : [];
    var facts = ["started by " + (upload.created_by || "") + " at " + stampWords(upload.created_at),
                 plural(items.length, "transect"), ORIGIN_WORDS[upload.origin] || ""].filter(Boolean);
    return '<li class="row"><span class="row-name">' + chip(upload.upload_id) + "</span>" +
      '<span class="row-act">' + watchButtonHtml(upload.upload_id) + "</span>" +
      '<span class="row-state">' + stateHtml(statusRole(upload.status), STATUS_WORDS[upload.status] || String(upload.status || "")) + "</span>" +
      '<span class="row-meta">' + facts.map(function (f) { return '<span class="count">' + esc(f) + "</span>"; }).join("") +
      "</span></li>";
  }

  function emptyRowHtml(text) { return '<li class="row"><span class="row-meta">' + esc(text) + "</span></li>"; }

  /* ---- the upload progress window -------------------------------------------- */

  var componentLoad = null;

  // ensureComponent(version): load the editor's upload component and its
  // stylesheet once per page, the panel's asset version as the cache bust;
  // resolves true when window.ManualEditUpload is ready.
  function ensureComponent(version) {
    var suffix = version ? "?v=" + encodeURIComponent(version) : "";
    if (!document.querySelector('link[href*="' + UPLOAD_CSS_NAME + '"]')) {
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = UPLOAD_CSS + suffix;
      document.head.appendChild(link);
    }
    if (window.ManualEditUpload && typeof window.ManualEditUpload.mount === "function") return Promise.resolve(true);
    if (!componentLoad) {
      componentLoad = new Promise(function (resolve) {
        var script = document.createElement("script");
        script.src = UPLOAD_SCRIPT + suffix;
        script.onload = function () { resolve(!!(window.ManualEditUpload && window.ManualEditUpload.mount)); };
        script.onerror = function () { componentLoad = null; resolve(false); };
        document.head.appendChild(script);
      });
    }
    return componentLoad;
  }

  var openWindow = null;

  // openUploadWindow(uploadId, opts): the progress window as a dialog on the
  // page body (one at a time). opts = {version, readInitials(), onClosed(),
  // onNewUpload(id)}; every callback is optional.
  function openUploadWindow(uploadId, opts) {
    if (openWindow) openWindow.close();
    var opener = document.activeElement;
    var layer = document.createElement("div");
    layer.className = "voyager-tools vt-upload-layer";
    var dialog = document.createElement("section");
    dialog.className = "vt-upload-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "me-upload-title-" + String(uploadId).replace(/[^A-Za-z0-9-]/g, ""));
    dialog.tabIndex = -1;
    layer.appendChild(dialog);
    document.body.appendChild(layer);
    var handle = null;
    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      layer.removeEventListener("keydown", onKey, true);
      if (handle) handle.unmount();
      if (layer.parentNode) layer.parentNode.removeChild(layer);
      if (openWindow && openWindow.layer === layer) openWindow = null;
      if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
      if (typeof opts.onClosed === "function") opts.onClosed();
    }
    // Escape closes the window without bubbling: the desktop closes a window on a bubbled Escape.
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } }
    layer.addEventListener("keydown", onKey, true);
    layer.addEventListener("click", function (e) {
      if (e.target === layer) close();
      if (e.target.closest && e.target.closest("[data-vt-upload-close]")) close();
    });
    dialog.focus();
    openWindow = { layer: layer, close: close };
    ensureComponent(opts.version).then(function (ready) {
      if (closed) return;
      if (!ready) {
        dialog.innerHTML = '<p class="result result-error">' + esc(LINE_NO_COMPONENT) + " " + chip(uploadId) + "</p>" +
          '<div class="actions"><button type="button" class="btn" data-vt-upload-close>Close</button></div>';
        return;
      }
      handle = window.ManualEditUpload.mount(dialog, { uploadId: uploadId, base: MANUAL_EDIT_BASE, onClose: close,
                                                       getInitials: opts.readInitials || function () { return ""; },
                                                       onNewUpload: opts.onNewUpload });
    });
    return openWindow;
  }

  // The review content opens its Start the upload now in the same window.
  window.VoyagerTools = Object.assign(window.VoyagerTools || {}, { openUploadWindow: openUploadWindow });

  /* ---- the pane ------------------------------------------------------------------ */

  function register(key, init) {
    var vt = window.VoyagerTools = window.VoyagerTools || {};
    if (typeof vt.registerPane === "function") vt.registerPane(key, init);
    else (vt._pendingPanes = vt._pendingPanes || []).push([key, init]);
  }

  register("uploads", function (ctx) {
    var el = {
      meta: ctx.q("uploads-meta"), ready: ctx.q("uploads-ready"), ticks: ctx.q("uploads-ticks"),
      start: ctx.q("uploads-start"), result: ctx.q("uploads-result"), running: ctx.q("uploads-running"),
      runningWrap: ctx.q("uploads-running-wrap"), review: ctx.q("uploads-review"),
      reviewWrap: ctx.q("uploads-review-wrap"), recent: ctx.q("uploads-recent")
    };
    var st = { ready: [], ticked: {}, busy: false, mounted: true, timer: null };

    function keyOf(item) { return item.job_id + "|" + item.psx; }
    function tickedItems() { return st.ready.filter(function (item) { return st.ticked[keyOf(item)]; }); }
    function refreshButton() { el.start.disabled = st.busy || !st.mounted || tickedItems().length === 0; }

    function readInitials() {
      var box = ctx.root.querySelector("#vt-initials");
      var clean = box ? box.value.trim().toUpperCase() : "";
      return INITIALS_RE.test(clean) ? clean : "";
    }

    function showNotMounted() {
      st.mounted = false;
      st.ready = [];
      [el.ready, el.running, el.review, el.recent].forEach(function (list) { list.innerHTML = ""; });
      el.ticks.hidden = true;
      el.runningWrap.hidden = true;
      el.reviewWrap.hidden = true;
      el.meta.textContent = "";
      el.start.hidden = true;
      ctx.setResult(el.result, esc(LINE_NOT_MOUNTED), "error");
    }

    function renderCards(cards) {
      st.ready = readyItems(cards);
      var live = {};
      st.ready.forEach(function (item) { if (st.ticked[keyOf(item)]) live[keyOf(item)] = true; });
      st.ticked = live;
      var running = runningItems(cards);
      var review = reviewItems(cards);
      el.ready.innerHTML = st.ready.map(function (item, i) { return readyRowHtml(item, i, !!st.ticked[keyOf(item)]); }).join("") ||
        emptyRowHtml(LINE_NONE_READY);
      el.ticks.hidden = st.ready.length <= TICKS_SHOWN_ABOVE;
      el.running.innerHTML = running.map(runningRowHtml).join("");
      el.runningWrap.hidden = running.length === 0;
      el.review.innerHTML = review.map(reviewRowHtml).join("");
      el.reviewWrap.hidden = review.length === 0;
      el.meta.textContent = st.ready.length + " marked for re-upload, " + running.length + " uploading, " +
        review.length + " in admin review.";
      refreshButton();
    }

    // load(): the upload sets first, then the cards: reading the sets lets the
    // manual edit module mark a set a desktop restart stranded, and return its
    // transects to marked for re-upload, before the cards are read.
    function load() {
      schedule();
      var uploads = null;
      return ctx.desktopApi(UPLOADS_ROUTE + "?limit=" + RECENT_LIMIT)
        .then(function (answer) { uploads = answer; return ctx.desktopApi(JOBS_ROUTE); })
        .then(function (jobs) {
          if (jobs.status === HTTP_NOT_FOUND) { showNotMounted(); return; }
          st.mounted = true;
          el.start.hidden = false;
          if (!jobs.ok) { if (!ctx.isRelocked()) ctx.fail(el.result, jobs); }
          else renderCards(jobs.data.jobs);
          if (!uploads.ok) { el.recent.innerHTML = ""; if (!ctx.isRelocked()) ctx.fail(el.result, uploads); return; }
          var recent = recentUploads(uploads.data.uploads, Date.now(), RECENT_DAYS);
          el.recent.innerHTML = recent.map(recentRowHtml).join("") || emptyRowHtml(LINE_NONE_RECENT);
        });
    }

    // schedule(): read the lists again every POLL_MS while the pane is shown,
    // so a running set's state stays current; stopped at a relock or unmount.
    function schedule() {
      if (st.timer !== null) return;
      st.timer = setInterval(function () { if (ctx.isShown() && !st.busy) load(); }, POLL_MS);
    }
    ctx.onStop(function () { if (st.timer !== null) { clearInterval(st.timer); st.timer = null; } });

    function watch(uploadId) {
      openUploadWindow(uploadId, { version: ctx.root.getAttribute("data-vt-version") || "", readInitials: readInitials,
                                   onClosed: load, onNewUpload: load });
    }

    function done(answer) {
      st.busy = false;
      ctx.busy(el.start, false);
      refreshButton();
      return answer;
    }

    function start() {
      var chosen = tickedItems();
      if (!chosen.length) { ctx.setResult(el.result, esc(LINE_TICK_FIRST), "error"); return; }
      var who = ctx.initials(el.result);
      if (!who) return;
      st.busy = true;
      ctx.busy(el.start, true);
      ctx.setResult(el.result, "");
      ctx.desktopApi(SHELF_ROUTE).then(function (res) {
        if (ctx.isRelocked()) return null;
        var values = res.ok && res.data && res.data.values ? res.data.values : {};
        var archive = typeof values.archive_shelf_copy === "boolean" ? values.archive_shelf_copy : null;
        return ctx.confirmDialog({ title: "Upload to the Shelf", lines: uploadPlanLines(chosen, archive),
                                   okLabel: "Upload", primary: true });
      }).then(function (ok) {
        if (!ok) return done(null);
        var body = { items: chosen.map(function (item) { return { job_id: item.job_id, psx: item.psx }; }), initials: who };
        return ctx.desktopApi(UPLOADS_ROUTE, { method: "POST", body: body }).then(done).then(function (res) {
          if (!res.ok) { if (!ctx.isRelocked()) ctx.fail(el.result, res); load(); return; }
          st.ticked = {};
          ctx.setResult(el.result, esc("Upload set " + res.data.upload_id + " started with " + plural(chosen.length, "transect") + "."));
          watch(res.data.upload_id);
          load();
        });
      });
    }

    el.ready.addEventListener("change", function (e) {
      var index = e.target.getAttribute && e.target.getAttribute("data-vt-up-tick");
      if (index === null || index === undefined || !st.ready[Number(index)]) return;
      var key = keyOf(st.ready[Number(index)]);
      if (e.target.checked) st.ticked[key] = true;
      else delete st.ticked[key];
      refreshButton();
    });
    ctx.q("uploads-tick-all").addEventListener("click", function () {
      st.ready.forEach(function (item) { st.ticked[keyOf(item)] = true; });
      ctx.qa('[data-vt="uploads-ready"] [data-vt-up-tick]').forEach(function (box) { box.checked = true; });
      refreshButton();
    });
    ctx.q("uploads-untick-all").addEventListener("click", function () {
      st.ticked = {};
      ctx.qa('[data-vt="uploads-ready"] [data-vt-up-tick]').forEach(function (box) { box.checked = false; });
      refreshButton();
    });
    ctx.pane.addEventListener("click", function (e) {
      var button = e.target.closest && e.target.closest("[data-vt-watch]");
      if (button && ctx.pane.contains(button)) watch(button.getAttribute("data-vt-watch"));
    });
    el.start.addEventListener("click", start);
    ctx.q("uploads-refresh").addEventListener("click", load);
    return load;
  });
})();
