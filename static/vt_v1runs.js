/* static/vt_v1runs.js - the Voyager 1 batch pane of the Atlas control panel,
 * registered on the pane seam of static/voyager_tools.js.
 *
 * The pane reads the driver's batch through GET /api/v1runs (the model built
 * by voyagertools/v1runs.py) and draws the batch line and one row per turn.
 * Flush opens a preview (GET /api/v1runs/flush-preview?turn=) and a form with
 * initials, a reason and two ticked choices; a last confirmation sends POST
 * /api/v1runs/flush. Move up, down and to the end send POST /api/v1runs/move
 * for pending turns. The Shelf form sends POST /api/v1runs/shelf. Every text
 * from the server goes through esc() before it reaches innerHTML.
 *
 * The pure helpers (esc, hoursWords, gigabytes, previewItems, moveTarget,
 * initialsSeed, flushBody, rowHtml, batchHtml, shelfWords, flushedLine,
 * timeoutLine) are exported to node through
 * module.exports for tests/test_v1runs_pins.py.
 */
(function () {
  "use strict";

  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var GB = 1e9;
  var SS_PANEL_INITIALS = "vt.initials";
  var SS_INITIALS = "vic_initials";
  var INITIALS_PARAM = "initials";
  var LINE_INITIALS = "Type your initials, two to four letters.";
  var LINE_INITIALS_PANEL = "Type your initials, two to four letters, in the box at the top of the panel.";
  var LINE_REASON = "Type the reason first.";
  var LINE_SHELF = "Type the Shelf folder first.";
  var LINE_NOT_PICKED = "Pick a turn to flush first.";
  // Each browser limit is the panel server's driver limit (voyagertools/driver_client.py) plus a margin, so the
  // server's own answer (a 502 sentence) arrives before the browser gives up.
  var MARGIN_MS = 30000;
  var FLUSH_TIMEOUT_MS = 900000 + MARGIN_MS;  // FLUSH_TIMEOUT_S 900
  var PREVIEW_TIMEOUT_MS = 120000 + MARGIN_MS; // PREVIEW_TIMEOUT_S 120
  var SHELF_TIMEOUT_MS = 600000 + MARGIN_MS;   // WRITE_TIMEOUT_S 600
  var STATUS_BAD_REQUEST = 400, STATUS_NOT_FOUND = 404, STATUS_CONFLICT = 409, STATUS_LOCKED = 423;
  var TRIGGER_TIME_LIMIT = "time_limit";
  var LINE_NO_CARRY_ON = "The batch did not carry on; press Resume when ready.";
  var FLAG_OVER = "over the limit";
  var FLAG_GONE = "build process gone";

  /* ---- pure helpers (exported to node) ----------------------------------------- */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : (/s$/.test(word) ? "es" : "s")); }

  // hoursWords(h): "14.2 h", or "unknown" when the driver reported none.
  function hoursWords(h) {
    return typeof h === "number" && isFinite(h) ? (Math.round(h * 10) / 10) + " h" : "unknown";
  }

  // gigabytes(bytes): "384.1 GB" in decimal gigabytes, as the Workbench figures elsewhere.
  function gigabytes(bytes) {
    return typeof bytes === "number" && isFinite(bytes) && bytes >= 0 ? (Math.round(bytes / GB * 10) / 10) + " GB" : "an unknown size";
  }

  function chip(text) { return '<span class="id code">' + esc(text) + "</span>"; }

  function stateHtml(role, words) {
    return '<span class="vt-state"><span class="chip ' + esc(role) + '" aria-hidden="true"></span>' + esc(words) + "</span>";
  }

  // initialsSeed(search, remembered): the field's first value (decision 0010):
  // a valid initials parameter of the page address, else the remembered
  // value, else empty. Upper-cased; never a default.
  function initialsSeed(search, remembered) {
    var param = "";
    var match = /[?&]initials=([^&#]*)/.exec(String(search || ""));
    if (match) {
      try { param = decodeURIComponent(match[1].replace(/\+/g, " ")).trim().toUpperCase(); } catch (e) { param = ""; }
    }
    if (INITIALS_RE.test(param)) return param;
    var kept = String(remembered || "").trim().toUpperCase();
    return INITIALS_RE.test(kept) ? kept : "";
  }

  // moveTarget(row, direction): the `to` the driver takes, or null when the
  // turn cannot move that way. "up" and "down" move one place among the
  // pending turns; "end" moves to the end.
  function moveTarget(row, direction) {
    if (!row || row.status !== "pending" || typeof row.pending_index !== "number") return null;
    var last = row.pending_count - 1;
    if (direction === "up") return row.pending_index > 0 ? row.pending_index - 1 : null;
    if (direction === "down") return row.pending_index < last ? row.pending_index + 1 : null;
    if (direction === "end") return row.pending_index < last ? "end" : null;
    return null;
  }

  // previewItems(preview): the plain sentences of the flush preview, in the
  // order a person reads them: refusal, rows, folder, finished videos, processes.
  function previewItems(preview) {
    var p = preview && typeof preview === "object" ? preview : {};
    var items = [];
    var rows = Array.isArray(p.rows) ? p.rows : [];
    if (p.can_flush === false) {
      items.push({ kind: "refusal", text: p.refusal || "The driver will not flush this turn." });
      // a refused turn the driver could not read carries no rows: the sentence alone says it
      if (!rows.length) return items;
    }
    items.push({ kind: "rows", text: plural(rows.length, "video") + " go back to their record in the Archive, with their build cells cleared.",
                 rows: rows.map(function (r) {
                   r = r && typeof r === "object" ? r : {};
                   return { id: r.id, now: r.video_location_now, after: r.video_location_after };
                 }) });
    items.push({ kind: "folder", text: p.folder
      ? "The Workbench folder " + p.folder + " (" + gigabytes(p.folder_bytes) + ") is deleted."
      : "There is no Workbench folder to delete." });
    var done = Array.isArray(p.completed_ids) ? p.completed_ids : [];
    items.push({ kind: "completed", warn: done.length > 0, text: done.length
      ? "Finished videos lost with this transect: " + done.join(", ") + "."
      : "No video of this transect had finished." });
    var live = p.live && typeof p.live === "object" ? p.live : {};
    var pids = Array.isArray(live.pids) ? live.pids : [];
    items.push({ kind: "live", warn: !!live.job_alive, text: live.job_alive
      ? "The running build is ended (" + plural(pids.length, "process") + (pids.length ? ": " + pids.join(", ") : "") + ")."
      : "No build process is running." });
    if (p.frozen) items.push({ kind: "frozen", text: "The build is frozen; the driver continues it, then ends it." });
    return items;
  }

  // idsWords([7, 8, 9]): "7, 8 and 9"; one id alone; "" for none.
  function idsWords(ids) {
    var list = ids.map(String);
    if (list.length < 2) return list.join("");
    return list.slice(0, -1).join(", ") + " and " + list[list.length - 1];
  }

  // shelfWords(answer): the sentence after a Shelf change. applies_to_turns is the list of turn ids the driver re-pointed.
  function shelfWords(answer) {
    var a = answer && typeof answer === "object" ? answer : {};
    var ids = Array.isArray(a.applies_to_turns) ? a.applies_to_turns : [];
    var tail = ids.length ? "for " + (ids.length === 1 ? "turn " : "turns ") + idsWords(ids) + "." : "for no turn yet; new turns use it.";
    return "The Shelf is now " + (a.shelf_root || "not reported") + " " + tail;
  }

  // refusedAnswer(res): true for the answers that are the driver's or the panel's own definite refusal
  // (a bad request, an unknown turn, a conflict, a lock): the flush did not start, so the sentence is shown as it is.
  function refusedAnswer(res) {
    var code = res && res.status;
    return code === STATUS_BAD_REQUEST || code === STATUS_NOT_FOUND || code === STATUS_CONFLICT || code === STATUS_LOCKED;
  }

  // causeOf(res): "timeout" (no answer within the browser limit), "unreachable" (the desktop was never reached)
  // or "error" (an answer that says the call failed, such as a 502).
  function causeOf(res) {
    if (res && res.status === 0) return res.timedOut ? "timeout" : "unreachable";
    return "error";
  }

  // timeoutLine(turnId, model, cause, detail): what the driver's status shows for the turn after the flush call
  // was not confirmed. cause is "timeout" (default), "unreachable" or "error"; detail is the sentence of an error.
  function timeoutLine(turnId, model, cause, detail) {
    var turns = model && Array.isArray(model.turns) ? model.turns : [];
    var turn = turns.filter(function (t) { return String(t.id) === String(turnId); })[0];
    var head;
    if (cause === "unreachable") head = "The desktop could not be reached, so the flush of turn " + turnId + " may not have started. ";
    else if (cause === "error") head = "The flush of turn " + turnId + " was not confirmed" + (detail ? ": " + String(detail).replace(/[.\s]+$/, "") : "") + ". ";
    else head = "The flush of turn " + turnId + " got no answer within 15 minutes. ";
    if (!turn) return head + "The driver no longer lists the turn.";
    if (turn.status === "flushed") return head + "The driver shows turn " + turnId + " as flushed.";
    return head + "The driver shows turn " + turnId + " as " + turn.status + "; the flush may still be running, so press Refresh to read it again.";
  }

  // queueWords(order, turns): "7, 8, 9, 11, 12, 13, 10": the pending turns of the driver's order, in that order.
  // turns are the pane's rows as they were before the move; a turn that was not pending is left out.
  function queueWords(order, turns) {
    var pending = {};
    (Array.isArray(turns) ? turns : []).forEach(function (t) { if (t && t.status === "pending") pending[String(t.id)] = true; });
    var ids = (Array.isArray(order) ? order : []).filter(function (id) { return pending[String(id)]; });
    return ids.length ? ids.join(", ") : "empty";
  }

  // flushDoneLines(d, turnId, carryOn): the sentences after a flush: what was flushed, and, when the batch was
  // asked to carry on and the driver says it did not, one more line (the driver's own reason when it sends one).
  function flushDoneLines(d, turnId, carryOn) {
    var a = d && typeof d === "object" ? d : {};
    var main = "Turn " + turnId + " flushed. " + plural(a.rows_restored || 0, "video") + " back in the Archive, " +
      gigabytes(a.reclaimed_bytes) + " deleted" + (a.requeued_as ? ", put back as turn " + a.requeued_as : "") + ".";
    var why = typeof a.resume_reason === "string" ? a.resume_reason.trim() : "";
    var extra = carryOn && a.resumed === false ? (why || LINE_NO_CARRY_ON) : "";
    return { text: [main, extra].filter(Boolean).join(" "), warn: !!extra };
  }

  function previewHtml(items) {
    return items.map(function (item) {
      var rows = "";
      if (item.rows && item.rows.length) {
        rows = '<ul class="vt-v1-prows">' + item.rows.map(function (r) {
          return "<li>" + chip(r.id) + ' <span class="count">' + esc(r.now || "unknown") + " to " + esc(r.after || "unknown") + "</span></li>";
        }).join("") + "</ul>";
      }
      var cls = item.kind === "refusal" ? "count is-warn" : (item.warn ? "count is-warn" : "count");
      return '<p class="' + cls + '">' + esc(item.text) + "</p>" + rows;
    }).join("");
  }

  // flushBody(form): the POST body of a flush, with confirm true (the dialog was accepted).
  function flushBody(form) {
    return { turn_id: form.turn, initials: form.initials, reason: form.reason, requeue: !!form.requeue,
             resume: !!form.resume, confirm: true };
  }

  function flagsHtml(row) {
    var flags = [];
    if (row.over_limit) flags.push(stateHtml("role-failed", FLAG_OVER));
    if (row.build_gone) flags.push(stateHtml("role-failed", FLAG_GONE));
    return flags.map(function (f) { return '<span class="vt-v1-flag">' + f + "</span>"; }).join("");
  }

  // flushedLine(f): who flushed (or that the time limit did), when, why and where it was put back, as full
  // sentences. The reason starts a sentence, so its first letter is a capital.
  function flushedLine(f) {
    if (!f) return "";
    var by = f.trigger === TRIGGER_TIME_LIMIT ? "automatically" : (f.by ? "by " + f.by : "");
    var who = by ? "Flushed " + by + (f.at ? ", " + f.at : "") + "." : (f.at ? "Flushed " + f.at + "." : "");
    var reason = f.reason ? String(f.reason).trim() : "";
    reason = reason.charAt(0).toUpperCase() + reason.slice(1);
    if (reason && !/[.!?]$/.test(reason)) reason += ".";
    var requeued = f.requeued_as ? "Put back as turn " + f.requeued_as + "." : "";
    return [who, reason, requeued].filter(Boolean).join(" ");
  }

  // flushedHtml(row): the row under a flushed turn, spanning the table, so the note has room.
  function flushedHtml(row) {
    var line = flushedLine(row.flushed);
    return line ? '<tr class="vt-v1-note-row" data-vt-v1-note="' + esc(row.id) + '"><td></td><td colspan="4" class="count">' + esc(line) + "</td></tr>" : "";
  }

  function hoursHtml(row) {
    if (!row.current_video) return row.status === "running" ? '<span class="count">none reported</span>' : "";
    var against = row.limit_hours ? " of " + hoursWords(row.limit_hours) : "";
    return chip(row.current_video) + '<span class="vt-v1-hours' + (row.over_limit ? " is-over" : "") + '">' +
      esc(hoursWords(row.current_hours) + against) + "</span>";
  }

  function actionsHtml(row) {
    var label = "turn " + row.id + (row.transect ? " (" + row.transect + ")" : "");
    var out = "";
    if (row.actions && row.actions.flush) {
      out += '<button type="button" class="btn btn-mini btn-danger" data-vt-v1-flush="' + esc(row.id) +
        '" title="' + esc("Open the preview of what flushing " + label + " would do. VICARIUS changes nothing until the flush is confirmed.") +
        '">Flush</button>';
    }
    if (row.actions && row.actions.move) {
      [["up", "Up", "Move " + label + " one place earlier in the queue. The driver keeps the other pending turns in order."],
       ["down", "Down", "Move " + label + " one place later in the queue. The driver keeps the other pending turns in order."],
       ["end", "To end", "Move " + label + " to the end of the queue. The driver keeps the other pending turns in order."]
      ].forEach(function (m) {
        if (moveTarget(row, m[0]) === null) return;
        out += '<button type="button" class="btn btn-mini" data-vt-v1-move="' + esc(row.id) + '" data-vt-v1-dir="' + m[0] +
          '" title="' + esc(m[2]) + '">' + m[1] + "</button>";
      });
    }
    return out;
  }

  function rowHtml(row) {
    var name = row.site_name ? '<span class="vt-site">' + esc(row.site_name) + "</span> " : "";
    var videos = (row.video_ids || []).map(function (v) { return '<span class="id code vt-v1-video">' + esc(v) + "</span>"; }).join(" ");
    return '<tr data-vt-v1-row="' + esc(row.id) + '">' +
      '<td class="vt-num">' + esc(row.id) + "</td>" +
      "<td>" + name + chip(row.transect || "unknown") + '<span class="vt-v1-videos">' + videos + "</span></td>" +
      '<td class="vt-v1-status">' + stateHtml(row.role, row.status) + flagsHtml(row) + "</td>" +
      "<td>" + hoursHtml(row) + "</td>" +
      '<td class="vt-v1-actions">' + actionsHtml(row) + "</td></tr>" + flushedHtml(row);
  }

  function batchHtml(batch) {
    if (!batch) return "";
    var note = batch.move_note ? '<span class="vt-v1-shelf-line">' + esc(batch.move_note) + "</span>" : "";
    return chip(batch.batch_id || "unknown batch") + " " + stateHtml(batch.role, batch.status) +
      '<span class="vt-v1-shelf-line">Shelf folder ' + chip(batch.shelf_root || "not reported") + "</span>" + note;
  }

  function limitLine(batch) {
    return batch && batch.time_limit_hours ? "Videos over " + hoursWords(batch.time_limit_hours).replace(" h", " hours") + " are flushed automatically." : "";
  }

  var helpers = { queueWords: queueWords, flushDoneLines: flushDoneLines, refusedAnswer: refusedAnswer, causeOf: causeOf, shelfWords: shelfWords, flushedLine: flushedLine, timeoutLine: timeoutLine, esc: esc, hoursWords: hoursWords, gigabytes: gigabytes, previewItems: previewItems, previewHtml: previewHtml,
                  moveTarget: moveTarget, initialsSeed: initialsSeed, flushBody: flushBody, rowHtml: rowHtml,
                  batchHtml: batchHtml, limitLine: limitLine, INITIALS_RE: INITIALS_RE };
  if (typeof module === "object" && module && module.exports) module.exports = helpers;
  if (typeof window === "undefined") return;

  /* ---- the pane ------------------------------------------------------------------ */

  function register(key, init) {
    var vt = window.VoyagerTools = window.VoyagerTools || {};
    if (typeof vt.registerPane === "function") vt.registerPane(key, init);
    else (vt._pendingPanes = vt._pendingPanes || []).push([key, init]);
  }

  register("v1runs", function (ctx) {
    var el = {
      batch: ctx.q("v1-batch"), limit: ctx.q("v1-limit"), body: ctx.q("v1-body"), empty: ctx.q("v1-empty"),
      tableBox: ctx.q("v1-table-box"),
      refresh: ctx.q("v1-refresh"), flush: ctx.q("v1-flush"), flushTitle: ctx.q("v1-flush-title"),
      preview: ctx.q("v1-preview"), initials: ctx.q("v1-initials"), reason: ctx.q("v1-reason"),
      requeue: ctx.q("v1-requeue"), resume: ctx.q("v1-resume"), go: ctx.q("v1-flush-go"), cancel: ctx.q("v1-flush-cancel"),
      shelf: ctx.q("v1-shelf"), shelfInitials: ctx.q("v1-shelf-initials"), shelfReason: ctx.q("v1-shelf-reason"),
      shelfGo: ctx.q("v1-shelf-go"), result: ctx.q("v1-result"), shelfBox: ctx.q("v1-shelf-box")
    };
    var st = { turns: [], picked: null, batch: null, flushing: false, confirming: false };
    var lockedInputs = [el.initials, el.reason, el.requeue, el.resume, el.cancel];

    function remembered() {
      var box = ctx.root.querySelector("#vt-initials");
      var fromBox = box ? box.value : "";
      var kept = "";
      try { kept = window.sessionStorage.getItem(SS_PANEL_INITIALS) || window.sessionStorage.getItem(SS_INITIALS) || ""; } catch (e) { kept = ""; }
      return fromBox || kept;
    }
    function seedInitials() {
      var seed = initialsSeed(window.location.search, remembered());
      [el.initials, el.shelfInitials].forEach(function (box) { if (!box.value) box.value = seed; });
    }
    [el.initials, el.shelfInitials].forEach(function (box) {
      box.addEventListener("input", function () { box.value = box.value.toUpperCase(); });
    });

    // signer(): the initials a move is signed with: the first valid one of the two pane
    // fields, the panel's own box, then the page address parameter.
    function signer() {
      var candidates = [el.initials.value, el.shelfInitials.value, remembered(), initialsSeed(window.location.search, "")];
      for (var i = 0; i < candidates.length; i++) {
        var v = String(candidates[i] || "").trim().toUpperCase();
        if (INITIALS_RE.test(v)) return v;
      }
      return "";
    }

    function findTurn(id) {
      return st.turns.filter(function (t) { return String(t.id) === String(id); })[0] || null;
    }

    function render(model) {
      st.turns = model.turns || [];
      st.batch = model.batch;
      el.batch.innerHTML = batchHtml(model.batch);
      el.limit.textContent = limitLine(model.batch);
      el.limit.hidden = !el.limit.textContent;
      el.body.innerHTML = st.turns.map(rowHtml).join("");
      if (st.flushing) setFlushing(true);
      el.tableBox.hidden = !st.turns.length;
      el.shelfBox.hidden = !model.batch;
      el.empty.textContent = model.line || (model.truncated ? "Only the first turns are shown." : "");
      el.empty.hidden = !el.empty.textContent;
      if (model.batch && model.batch.shelf_root && !el.shelf.value) el.shelf.value = model.batch.shelf_root;
      if (st.picked && !(findTurn(st.picked) && findTurn(st.picked).actions.flush)) closeFlush();
    }

    function load() {
      return ctx.api("/api/v1runs").then(function (res) {
        if (ctx.isRelocked()) return;
        if (!res.ok) {
          st.turns = [];
          st.batch = null;
          el.body.innerHTML = "";
          el.batch.innerHTML = "";
          el.limit.hidden = true;
          el.tableBox.hidden = true;
          el.shelfBox.hidden = true;
          el.empty.hidden = true;
          ctx.fail(el.result, res);
          return;
        }
        render(res.data);
      });
    }

    // setFlushing(on): while a flush is in flight the button, the form and the row Flush buttons take no input.
    function setFlushing(on) {
      st.flushing = on;
      el.go.disabled = true;
      lockedInputs.forEach(function (box) { box.disabled = on; });
      Array.prototype.forEach.call(el.body.querySelectorAll("[data-vt-v1-flush]"), function (b) { b.disabled = on; });
    }

    function closeFlush() {
      if (st.flushing) return;
      st.picked = null;
      el.flush.hidden = true;
      el.preview.innerHTML = "";
      el.go.disabled = true;
      el.reason.value = "";
    }

    function openFlush(id) {
      if (st.flushing) return;
      var turn = findTurn(id);
      if (!turn) { ctx.setResult(el.result, esc(LINE_NOT_PICKED), "error"); return; }
      st.picked = turn.id;
      el.flushTitle.textContent = "Flush turn " + turn.id + (turn.transect ? " (" + turn.transect + ")" : "");
      el.preview.innerHTML = '<p class="count">Reading what the flush would do.</p>';
      el.go.disabled = true;
      el.flush.hidden = false;
      seedInitials();
      ctx.setResult(el.result, "");
      ctx.api("/api/v1runs/flush-preview?turn=" + encodeURIComponent(turn.id), { timeoutMs: PREVIEW_TIMEOUT_MS }).then(function (res) {
        if (st.picked !== turn.id) return;
        if (!res.ok) { el.preview.innerHTML = ""; ctx.fail(el.result, res); return; }
        var items = previewItems(res.data);
        el.preview.innerHTML = previewHtml(items);
        el.go.disabled = res.data.can_flush === false;
        el.flush.scrollIntoView({ block: "nearest" });
        el.reason.focus();
      });
    }

    function confirmFlush() {
      var turn = findTurn(st.picked);
      if (!turn) { ctx.setResult(el.result, esc(LINE_NOT_PICKED), "error"); return; }
      var who = el.initials.value.trim().toUpperCase();
      var reason = el.reason.value.trim();
      if (!INITIALS_RE.test(who)) { ctx.setResult(el.result, esc(LINE_INITIALS), "error"); el.initials.focus(); return; }
      if (!reason) { ctx.setResult(el.result, esc(LINE_REASON), "error"); el.reason.focus(); return; }
      var lines = ["The driver ends the build of turn " + turn.id + ", gives its videos back to the Archive and deletes its Workbench folder. This cannot be undone.",
                   el.requeue.checked ? "The transect goes back at the end of the queue." : "The transect is not queued again.",
                   el.resume.checked ? "The batch carries on with the next transect." : "The batch stays paused."];
      if (st.confirming) return;
      st.confirming = true;
      el.go.disabled = true;
      ctx.confirmDialog({ title: "Flush turn " + turn.id, lines: lines, okLabel: "Flush" }).then(function (ok) {
        st.confirming = false;
        if (!ok) { el.go.disabled = false; return; }
        runFlush(turn, who, reason);
      });
    }

    // runFlush(turn, who, reason): the POST, with its own 15 minute limit; the form stays shut until the driver answers.
    function runFlush(turn, who, reason) {
      var flushCarryOn = el.resume.checked;
      var body = flushBody({ turn: turn.id, initials: who, reason: reason, requeue: el.requeue.checked, resume: flushCarryOn });
      setFlushing(true);
      ctx.setResult(el.result, esc("Flushing turn " + turn.id + ": ending the build, putting the videos back on their Archive record and deleting the Workbench copy."), "info");
      if (el.result.scrollIntoView) el.result.scrollIntoView({ block: "nearest" });
      ctx.api("/api/v1runs/flush", { method: "POST", body: body, timeoutMs: FLUSH_TIMEOUT_MS }).then(function (res) {
        setFlushing(false);
        if (!res.ok && refusedAnswer(res)) { el.go.disabled = false; ctx.fail(el.result, res); return; }
        if (!res.ok) { readAfterTimeout(turn, causeOf(res), (res.data || {}).error); return; }
        var done = flushDoneLines(res.data, turn.id, flushCarryOn);
        ctx.setResult(el.result, esc(done.text), done.warn ? "warn" : "");
        closeFlush();
        load();
      });
    }

    // readAfterTimeout(turn, cause, detail): the call was not confirmed; the driver's status says what became of the turn.
    function readAfterTimeout(turn, cause, detail) {
      closeFlush();
      ctx.api("/api/v1runs").then(function (res) {
        if (!res.ok) { ctx.fail(el.result, res); return; }
        render(res.data);
        ctx.setResult(el.result, esc(timeoutLine(turn.id, res.data, cause, detail)), "error");
      });
    }

    function move(id, direction) {
      var turn = findTurn(id);
      var to = moveTarget(turn, direction);
      var who = signer();
      if (to === null) return;
      if (!who) { ctx.setResult(el.result, esc(LINE_INITIALS_PANEL), "error"); return; }
      var before = st.turns.slice();
      ctx.api("/api/v1runs/move", { method: "POST", body: { turn_id: turn.id, to: to, initials: who } }).then(function (res) {
        if (!res.ok) { ctx.fail(el.result, res); return; }
        ctx.setResult(el.result, esc("Turn " + turn.id + " moved. The queue is now " + queueWords(res.data.order, before) + "."));
        load();
      });
    }

    function changeShelf() {
      var who = el.shelfInitials.value.trim().toUpperCase();
      var shelf = el.shelf.value.trim();
      var reason = el.shelfReason.value.trim();
      if (!shelf) { ctx.setResult(el.result, esc(LINE_SHELF), "error"); el.shelf.focus(); return; }
      if (!INITIALS_RE.test(who)) { ctx.setResult(el.result, esc(LINE_INITIALS), "error"); el.shelfInitials.focus(); return; }
      if (!reason) { ctx.setResult(el.result, esc(LINE_REASON), "error"); el.shelfReason.focus(); return; }
      if (st.confirming) return;
      st.confirming = true;
      el.shelfGo.disabled = true;
      ctx.confirmDialog({ title: "Change the Shelf", primary: true, okLabel: "Change the Shelf",
        lines: ["The batch deposits its remaining transects into " + shelf + ".", "Transects already deposited stay where they are."] }).then(function (ok) {
        st.confirming = false;
        if (!ok) { el.shelfGo.disabled = false; return; }
        ctx.busy(el.shelfGo, true);
        ctx.api("/api/v1runs/shelf", { method: "POST", body: { shelf_root: shelf, initials: who, reason: reason, confirm: true }, timeoutMs: SHELF_TIMEOUT_MS }).then(function (res) {
          ctx.busy(el.shelfGo, false);
          if (!res.ok) { ctx.fail(el.result, res); return; }
          el.shelfReason.value = "";
          ctx.setResult(el.result, esc(shelfWords(res.data)));
          load();
        });
      });
    }

    el.body.addEventListener("click", function (e) {
      var flush = e.target.closest && e.target.closest("[data-vt-v1-flush]");
      if (flush) { openFlush(flush.getAttribute("data-vt-v1-flush")); return; }
      var mv = e.target.closest && e.target.closest("[data-vt-v1-move]");
      if (mv) move(mv.getAttribute("data-vt-v1-move"), mv.getAttribute("data-vt-v1-dir"));
    });
    el.go.addEventListener("click", confirmFlush);
    el.cancel.addEventListener("click", closeFlush);
    el.shelfGo.addEventListener("click", changeShelf);
    el.refresh.addEventListener("click", load);
    seedInitials();
    return load;
  });
})();
