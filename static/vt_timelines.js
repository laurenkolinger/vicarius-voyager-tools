/* static/vt_timelines.js - the Timelines pane of the Atlas control panel
 * (program C spec sections 3.9, 4.12 and 5.5, milestone C8), registered on
 * the pane seam of static/voyager_tools.js.
 *
 * The pane reads GET /voyager-tools/api/timelines once per open (and every
 * 30 s while shown, so running work moves) and draws, newest first: the
 * summary line (admin review first, as the registry window design asks);
 * the Manual edit projects table (created, finished, completed, progress,
 * transects by state, and the pull while it runs); four filters (project,
 * state, editor, since), remembered in this browser under
 * vt.timelines.filters and applied here with filterRows; the Transects
 * table (pulled, time in editing, in Metashape, in admin review, waiting for
 * upload, uploaded, total, and what is running now); Copy as CSV of the rows
 * shown; and a drawer (role dialog, Escape closes it and returns focus) with
 * the status history of every atlas row and every note of that transect,
 * from GET /voyager-tools/api/timelines/detail. The Transects table groups
 * its rows under one line per project (its id, who started it, its phase),
 * so each row carries only the transect, its state and its times.
 *
 * The pure helpers (filterRows, rowsToCsv, formatDuration, progressLine,
 * summaryLine, runningLine, pullLine, stateRole, cleanFilters, phaseWords,
 * actorWords) are exported to node through module.exports for
 * tests/test_manual_edit_pins.py.
 */
(function () {
  "use strict";

  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var JOB_RE = /^ME-\d{8}-\d{6}-[A-Z]{2,4}$/;
  var STATE_RE = /^[a-z_]{1,32}$/;
  var DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
  var FORMULA_START_RE = /^[=+\-@]/;
  var NUMBER_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;
  var MINUTE_S = 60;
  var HOUR_S = 3600;
  var HOURS_SHOWN_BELOW_S = 172800;  // under two days a duration reads in hours, from two days in days
  var DAY_S = 86400;
  var PERCENT = 100;
  var POLL_MS = 30000;
  var LS_FILTERS = "vt.timelines.filters";
  var API_TIMELINES = "/api/timelines";
  var API_DETAIL = "/api/timelines/detail";
  var HTTP_UNAVAILABLE = 503;
  var FILTER_KEYS = ["job", "state", "editor", "since"];
  // The summary's phrase per transect state, admin review first (attention before the rest).
  var SUMMARY_PHRASES = [["review", "in admin review"], ["restarting", "starting over"], ["upload", "marked for re-upload"],
                         ["uploading", "uploading"], ["editing", "editing"], ["todo", "not started"], ["done", "uploaded"]];
  // The chip of each transect state: the atlas matrix colour of the registry status it writes (the shared
  // stage chips of static/vicarius_roles.css); static/vt_review.js uses the same map.
  var STATE_ROLES = { todo: "stage-bench", editing: "stage-bench", review: "stage-review", upload: "stage-upload",
                      uploading: "stage-upload", restarting: "stage-bench", done: "stage-edited" };
  // The upload item states of the editor's progress window (manual_edit_upload.js ITEM_WORDS).
  var ITEM_WORDS = { waiting: "Waiting to upload", checking_hold: "Checking Metashape", exporting_report: "Exporting the report",
                     rendering_series: "Rendering the time series", checking_space: "Checking space",
                     archiving: "Archiving the Shelf copy", copying: "Copying", verifying: "Verifying",
                     verified: "Verified", removing: "Removing from the bench", uploaded: "Uploaded", failed: "Failed",
                     not_started: "Not started" };
  var ITEM_WAITING = "waiting";
  // The registry manual edit statuses as the drawer names them.
  var STATUS_WORDS = { "": "no manual edit", awaiting: "awaiting edit", editing: "editing", review: "in admin review",
                       upload: "marked for upload", done: "uploaded and verified" };
  // The registry statuses in the atlas matrix colours: awaiting is the matrix's amber (role-attention).
  var STATUS_ROLES = { "": "role-empty", awaiting: "role-attention", editing: "stage-bench", review: "stage-review",
                       upload: "stage-upload", done: "stage-edited" };
  var NOTE_WORDS = { editor_explanation: "Editor's explanation", check_note: "Check note", admin_review: "Admin review",
                     return_instructions: "Instructions to the editor", restart: "Start over", rescale: "Rescale" };
  var DECISION_WORDS = { upload: "marked for upload", "return": "sent back", restart: "started over" };
  var PHASE_WORDS = { draft: "draft", pulling: "pulling", editing: "editing", done: "complete", cancelled: "cancelled" };
  var FAILED_PREFIX = "failed_";  // manualedit.jobstore.FAILED_PREFIX: failed_pulling is a pull that failed
  var ACTOR_SEPARATOR = ":";      // a registry actor is <source>:<initials>, for example manual_edit:LO
  var CSV_COLUMNS = [
    { key: "job_id", label: "Project" }, { key: "psx", label: "Metashape project" }, { key: "site", label: "Site" },
    { key: "site_name", label: "Site name" }, { key: "transect", label: "Transect" }, { key: "editor", label: "Editor" },
    { key: "round", label: "Round" }, { key: "state", label: "State" }, { key: "pulled_at", label: "Pulled" },
    { key: "editing_s", label: "Editing (s)" }, { key: "metashape_s", label: "Metashape (s)" },
    { key: "unknown_opens", label: "Opens with no close" }, { key: "review_s", label: "Admin review (s)" },
    { key: "upload_wait_s", label: "Upload wait (s)" }, { key: "uploaded_at", label: "Uploaded" },
    { key: "total_s", label: "Total (s)" }, { key: "last_note", label: "Last note" }];
  var LINE_NOT_MOUNTED = "The manual edit module is not mounted here.";
  var LINE_NO_ROWS = "No transect matches these filters.";
  var TRANSECT_COLUMNS = 7;  // the Transects table's columns, for the project lines and the empty line
  var LINE_NO_PROJECTS = "No manual edit project recorded yet.";
  var LINE_BAD_DAY = "Type the first day as YYYY-MM-DD, for example 2026-09-27.";
  var TIP_OPEN_ROW = "Show the status history and the notes of this transect.";
  var TIP_UNKNOWN_OPENS = "VICARIUS found {n} with no recorded close, so the Metashape time leaves them out.";

  /* ---- pure helpers (exported to node) --------------------------------------- */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // filterRows(rows, filters): the rows matching every filter set
  // ({job, state, editor, since}); since compares the project's creation day.
  function filterRows(rows, filters) {
    var f = filters || {};
    return (Array.isArray(rows) ? rows : []).filter(function (r) {
      return (!f.job || r.job_id === f.job) && (!f.state || r.state === f.state) &&
        (!f.editor || r.editor === f.editor) && (!f.since || String(r.created_at || "").slice(0, 10) >= f.since);
    });
  }

  function csvCell(value) {
    var text = String(value == null ? "" : value);
    if (FORMULA_START_RE.test(text) && !NUMBER_RE.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }

  // rowsToCsv(rows, columns): a header of labels and one line per row, every
  // cell quoted, a cell that would open a spreadsheet formula prefixed with an apostrophe.
  function rowsToCsv(rows, columns) {
    var lines = [columns.map(function (c) { return csvCell(c.label); }).join(",")];
    (rows || []).forEach(function (row) {
      lines.push(columns.map(function (c) { return csvCell(row[c.key]); }).join(","));
    });
    return lines.join("\r\n");
  }

  // formatDuration(seconds): "45 s", "12 min", "5.2 h" (under two days),
  // "3.9 d"; "" for anything that is not a number from 0.
  function formatDuration(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) return "";
    var s = Math.round(seconds);
    if (s < MINUTE_S) return s + " s";
    if (s < HOUR_S) return Math.round(s / MINUTE_S) + " min";
    if (s < HOURS_SHOWN_BELOW_S) return (s / HOUR_S).toFixed(1) + " h";
    return (s / DAY_S).toFixed(1) + " d";
  }

  // progressLine(project): "3 of 4 uploaded".
  function progressLine(project) {
    var total = project && typeof project.transects === "number" ? project.transects : 0;
    if (!total) return "No transects";
    return (project.uploaded || 0) + " of " + total + " uploaded";
  }

  // summaryLine(summary): the line above the tables, admin review first.
  function summaryLine(summary) {
    var s = summary || {};
    if (!s.projects) return LINE_NO_PROJECTS;
    var byState = s.by_state || {};
    var parts = SUMMARY_PHRASES.filter(function (p) { return byState[p[0]] > 0; }).map(function (p) {
      return byState[p[0]] + " " + p[1];
    });
    var line = plural(s.projects, "manual edit project") + ", " + plural(s.transects || 0, "transect") +
      (parts.length ? ": " + parts.join(", ") : "");
    if (typeof s.median_metashape_s === "number") line += "; median time in Metashape " + formatDuration(s.median_metashape_s);
    return line;
  }

  function percentOf(done, total) {
    if (typeof done !== "number" || typeof total !== "number" || total <= 0) return null;
    return Math.min(PERCENT, Math.floor(done / total * PERCENT));
  }

  // runningLine(row): what is running for a transect now: its upload item, or an open checks window;
  // an upload set that cannot be read says so, with the reason.
  function runningLine(row) {
    var r = row || {};
    if (r.upload && r.upload.error) {
      return "Upload set " + r.upload.upload_id + " could not be read: " + String(r.upload.error).replace(/\.$/, "");
    }
    if (r.upload) {
      var words = ITEM_WORDS[r.upload.item_state || ITEM_WAITING] || String(r.upload.item_state);
      var pct = percentOf(r.upload.bytes_done, r.upload.bytes_total);
      return pct === null ? words : words + ", " + pct + " percent";
    }
    return r.checks_open ? "Checks window open" : "";
  }

  // pullLine(pull): the pull of a project while it copies to the edit bench.
  function pullLine(pull) {
    var pct = pull ? percentOf(pull.bytes_done, pull.bytes_total) : null;
    return pct === null ? "" : "Pulling to the edit bench, " + pct + " percent";
  }

  // stateRole(state): a transect state in the atlas matrix's stage colours.
  function stateRole(state) { return STATE_ROLES[state] || "role-empty"; }

  // phaseWords(phase): a project phase in words; failed_pulling reads "failed while pulling".
  function phaseWords(phase) {
    var text = String(phase || "");
    if (text.indexOf(FAILED_PREFIX) === 0) return "failed while " + text.slice(FAILED_PREFIX.length).replace(/_/g, " ");
    return PHASE_WORDS[text] || text;
  }

  // actorWords(actor): a registry actor as initials with its source in words,
  // "manual_edit:LO" -> "LO, manual edit"; an actor with no source stays as it is.
  function actorWords(actor) {
    var text = String(actor || "");
    var at = text.lastIndexOf(ACTOR_SEPARATOR);
    if (at <= 0 || at === text.length - 1) return text;
    return text.slice(at + 1) + ", " + text.slice(0, at).replace(/_/g, " ");
  }

  // cleanFilters(stored): the remembered filters, each dropped when it does not parse.
  function cleanFilters(stored) {
    var f = stored && typeof stored === "object" ? stored : {};
    var editor = typeof f.editor === "string" ? f.editor.trim().toUpperCase() : "";
    return { job: typeof f.job === "string" && JOB_RE.test(f.job) ? f.job : "",
             state: typeof f.state === "string" && STATE_RE.test(f.state) ? f.state : "",
             editor: INITIALS_RE.test(editor) ? editor : "",
             since: typeof f.since === "string" && DAY_RE.test(f.since) ? f.since : "" };
  }

  var helpers = { filterRows: filterRows, rowsToCsv: rowsToCsv, formatDuration: formatDuration, progressLine: progressLine,
                  summaryLine: summaryLine, runningLine: runningLine, pullLine: pullLine, stateRole: stateRole,
                  cleanFilters: cleanFilters, phaseWords: phaseWords, actorWords: actorWords, CSV_COLUMNS: CSV_COLUMNS };
  if (typeof module === "object" && module && module.exports) module.exports = helpers;
  if (typeof window === "undefined") return;

  /* ---- markup ------------------------------------------------------------------ */

  function chip(text) { return '<span class="id code">' + esc(text) + "</span>"; }

  function stateHtml(role, words) {
    return '<span class="vt-state"><span class="chip ' + esc(role) + '" aria-hidden="true"></span>' + esc(words) + "</span>";
  }

  function stampWords(iso) {
    var t = String(iso || "");
    return t.length < 16 ? t : t.slice(0, 10) + " " + t.slice(11, 16) + " AST";
  }

  // whenHtml(iso): a stamp as a day over a time, so a table column stays narrow.
  function whenHtml(iso) {
    if (!iso) return '<span class="count">none yet</span>';
    var words = stampWords(iso);
    var split = words.indexOf(" ");
    return '<span class="vt-when-day">' + esc(words.slice(0, split)) + '</span><span class="vt-when-time">' +
      esc(words.slice(split + 1)) + "</span>";
  }

  // summaryHtml(line): the summary line with every figure in the bold mono of
  // the registry window's summary (design 6.1), the words around it dim.
  function summaryHtml(line) {
    return String(line).split(/(\d+(?:\.\d+)?)/).map(function (part, i) {
      return i % 2 ? "<b>" + esc(part) + "</b>" : esc(part);
    }).join("");
  }

  function siteHtml(row) {
    return row.site_name ? '<span class="vt-site">' + esc(row.site_name) + "</span> " + chip(row.site)
      : '<span title="' + esc("The site table has no name for " + row.site + ".") + '">' + chip(row.site) + "</span>";
  }

  function projectRowHtml(p, stateWords) {
    var byState = p.by_state || {};
    var chips = Object.keys(byState).map(function (state) {
      return '<span class="vt-tl-chip">' + stateHtml(stateRole(state), byState[state] + " " + (stateWords[state] || state)) + "</span>";
    }).join("");
    var pull = pullLine(p.pull);
    return "<tr><td>" + chip(p.job_id) + '<span class="count vt-by">by ' + esc(p.created_by) + ", " + esc(phaseWords(p.phase)) + "</span>" +
      (pull ? '<span class="count vt-running">' + esc(pull) + "</span>" : "") + "</td>" +
      '<td class="vt-mono vt-when">' + whenHtml(p.created_at) + '</td><td class="vt-mono vt-when">' + whenHtml(p.finished_at) +
      '</td><td class="vt-mono vt-when">' + whenHtml(p.completed_at) + '</td><td class="vt-mono">' + esc(progressLine(p)) +
      "</td><td>" + chips + "</td></tr>";
  }

  function durationCell(seconds) {
    var words = formatDuration(seconds);
    return '<td class="vt-mono vt-num">' + (words ? esc(words) : '<span class="count">none</span>') + "</td>";
  }

  // unknownOpensHtml(n): "+5 open" as a count chip, the full sentence in its tooltip.
  function unknownOpensHtml(n) {
    if (!(n > 0)) return "";
    var tip = TIP_UNKNOWN_OPENS.replace("{n}", plural(n, "open"));
    return '<span class="id code vt-tl-opens" tabindex="0" title="' + esc(tip) + '">+' + esc(n) + " open</span>";
  }

  // totalCell(row): the total, with the upload's day and time under it once uploaded.
  function totalCell(row) {
    var words = formatDuration(row.total_s);
    var uploaded = row.uploaded_at ? stampWords(row.uploaded_at) : "";
    var split = uploaded.indexOf(" ");
    return '<td class="vt-mono vt-num"' + (uploaded ? ' title="' + esc("Uploaded " + uploaded + ".") + '"' : "") + ">" +
      (words ? esc(words) : '<span class="count">none</span>') +
      (uploaded ? '<span class="count vt-by">' + esc(uploaded.slice(0, split)) + '</span><span class="count vt-by">' +
        esc(uploaded.slice(split + 1)) + "</span>" : "") + "</td>";
  }

  // groupRowHtml(row, project): the line that opens a project's rows: its id,
  // who started it and its phase, from the project's summary when there is one.
  function groupRowHtml(row, project) {
    var p = project || {};
    return '<tr class="vt-tl-group"><th scope="colgroup" colspan="' + TRANSECT_COLUMNS + '">' + chip(row.job_id) +
      '<span class="count">started by ' + esc(p.created_by || row.editor) + ", " + esc(phaseWords(p.phase || row.phase)) +
      "</span></th></tr>";
  }

  function transectRowHtml(row, index, stateWords) {
    var running = runningLine(row);
    var failed = !!(row.upload && row.upload.error);
    var round = row.round > 0 ? '<span class="count vt-by">edit round ' + esc(row.round + 1) + "</span>" : "";
    return '<tr data-vt-tl-row="' + index + '"><td><button type="button" class="vt-tl-open" data-vt-tl-open="' + index +
      '" title="' + esc(TIP_OPEN_ROW) + '">' + siteHtml(row) + " " + esc(row.transect) + "</button>" + chip(row.psx) +
      '<span class="vt-tl-state">' + stateHtml(stateRole(row.state), stateWords[row.state] || row.state) + "</span>" + round +
      (running ? '<span class="count vt-running' + (failed ? " is-error" : "") + '">' + esc(running) + "</span>" : "") + "</td>" +
      '<td class="vt-mono vt-when">' + whenHtml(row.pulled_at) + "</td>" + durationCell(row.editing_s) +
      '<td class="vt-mono vt-num">' + esc(formatDuration(row.metashape_s)) + unknownOpensHtml(row.unknown_opens) + "</td>" +
      durationCell(row.review_s) + durationCell(row.upload_wait_s) + totalCell(row) + "</tr>";
  }

  function intervalRowsHtml(spells) {
    if (!spells.length) return '<tr><td colspan="6" class="count">No manual edit status change inside this project.</td></tr>';
    return spells.map(function (s) {
      return "<tr><td>" + stateHtml(STATUS_ROLES[s.value] || "role-empty", STATUS_WORDS[s.value] || s.value) +
        (s.clock_anomaly ? '<span class="count vt-by">the clock ran backwards here</span>' : "") + "</td>" +
        '<td class="vt-mono vt-when">' + whenHtml(s.start) + '</td><td class="vt-mono vt-when">' +
        (s.open ? '<span class="count">now</span>' : whenHtml(s.end)) + "</td>" +
        '<td class="vt-mono vt-num">' + esc(formatDuration(s.seconds)) + "</td><td>" + esc(actorWords(s.actor)) +
        "</td><td>" + esc(s.open ? "" : actorWords(s.end_actor)) + "</td></tr>";
    }).join("");
  }

  function noteRowHtml(n) {
    var decision = n.decision ? ", " + (DECISION_WORDS[n.decision] || n.decision) : "";
    return '<li class="row"><span class="row-name vt-note-kind">' + esc((NOTE_WORDS[n.kind] || n.kind) + decision) + "</span>" +
      '<span class="row-meta"><span class="count">' + esc(stampWords(n.at)) + '</span><span class="count">by ' + esc(n.by) +
      '</span><span class="count">edit round ' + esc(Number(n.round || 0) + 1) + "</span>" +
      (n.rule ? '<span class="count">' + esc(n.rule) + "</span>" : "") + "</span>" +
      '<span class="vt-note-text">' + esc(n.text) + "</span></li>";
  }

  // spellGroups(intervals): readable ids whose spells match, together, in
  // first-seen order: the manual edit writes one status for every readable
  // id of a transect at once, so one table usually covers them all.
  function spellGroups(intervals) {
    var groups = [];
    Object.keys(intervals || {}).forEach(function (rid) {
      var spells = intervals[rid] || [];
      var key = JSON.stringify(spells.map(function (s) { return [s.value, s.start, s.end, s.actor, s.end_actor]; }));
      var hit = groups.filter(function (g) { return g.key === key; })[0];
      if (hit) hit.ids.push(rid);
      else groups.push({ key: key, ids: [rid], spells: spells });
    });
    return groups;
  }

  function drawerBodyHtml(data) {
    var spells = spellGroups(data.intervals).map(function (group) {
      return '<h4 class="vt-drawer-sub">' + group.ids.map(chip).join(" ") + "</h4>" +
        '<div class="vt-table-wrap vt-keep"><table class="vt-table vt-tl-table"><thead><tr><th scope="col">Status</th><th scope="col">From</th>' +
        '<th scope="col">To</th><th scope="col" class="vt-num">Time</th><th scope="col">Entered by</th><th scope="col">Left by</th>' +
        "</tr></thead><tbody>" + intervalRowsHtml(group.spells) + "</tbody></table></div>";
    }).join("");
    var notes = data.notes || [];
    return '<h4 class="vt-drawer-sub">Status history</h4>' + spells +
      '<h4 class="vt-drawer-sub">Notes</h4>' +
      (notes.length ? '<ul class="rows vt-striped">' + notes.map(noteRowHtml).join("") + "</ul>"
        : '<p class="count">No note recorded for this transect in this project.</p>');
  }

  /* ---- the pane ------------------------------------------------------------------ */

  function readFilters() {
    try { var raw = window.localStorage.getItem(LS_FILTERS); return cleanFilters(raw ? JSON.parse(raw) : {}); }
    catch (e) { return cleanFilters({}); }
  }
  function writeFilters(f) { try { window.localStorage.setItem(LS_FILTERS, JSON.stringify(f)); } catch (e) { /* private mode */ } }

  function register(key, init) {
    var vt = window.VoyagerTools = window.VoyagerTools || {};
    if (typeof vt.registerPane === "function") vt.registerPane(key, init);
    else (vt._pendingPanes = vt._pendingPanes || []).push([key, init]);
  }

  register("timelines", function (ctx) {
    var el = {
      summary: ctx.q("tl-summary"), projects: ctx.q("tl-projects"), job: ctx.q("tl-job"), state: ctx.q("tl-state"),
      editor: ctx.q("tl-editor"), since: ctx.q("tl-since"), clear: ctx.q("tl-clear"), rows: ctx.q("tl-rows"),
      count: ctx.q("tl-count"), csv: ctx.q("tl-csv"), result: ctx.q("tl-result"), drawer: ctx.q("tl-drawer"),
      drawerTitle: ctx.q("tl-drawer-title"), drawerSub: ctx.q("tl-drawer-sub"), drawerBody: ctx.q("tl-drawer-body")
    };
    var st = { data: null, shown: [], filters: readFilters(), timer: null, opener: null, detailFor: null };

    function options(select, values, words, everyLabel, current) {
      var list = values.slice();
      if (current && list.indexOf(current) === -1) list.unshift(current);
      select.innerHTML = '<option value="">' + esc(everyLabel) + "</option>" + list.map(function (v) {
        return '<option value="' + esc(v) + '">' + esc(words(v)) + "</option>";
      }).join("");
      select.value = current || "";
    }

    function renderFilters() {
      var d = st.data;
      var rows = d.rows || [];
      var unique = function (key) {
        var seen = [];
        rows.forEach(function (r) { if (r[key] && seen.indexOf(r[key]) === -1) seen.push(r[key]); });
        return seen;
      };
      var words = d.state_words || {};
      options(el.job, (d.projects || []).map(function (p) { return p.job_id; }), String, "Every project", st.filters.job);
      options(el.state, (d.states || []).filter(function (s) { return unique("state").indexOf(s) !== -1; }),
              function (s) { return words[s] || s; }, "Every state", st.filters.state);
      options(el.editor, unique("editor").sort(), String, "Every editor", st.filters.editor);
      el.since.value = st.filters.since;
      el.clear.hidden = !FILTER_KEYS.some(function (k) { return st.filters[k]; });
    }

    function renderRows() {
      var words = st.data.state_words || {};
      var projects = {};
      (st.data.projects || []).forEach(function (p) { projects[p.job_id] = p; });
      st.shown = filterRows(st.data.rows, st.filters);
      el.rows.innerHTML = st.shown.map(function (r, i) {
        var opens = i === 0 || st.shown[i - 1].job_id !== r.job_id;
        return (opens ? groupRowHtml(r, projects[r.job_id]) : "") + transectRowHtml(r, i, words);
      }).join("") || '<tr><td colspan="' + TRANSECT_COLUMNS + '" class="count">' + esc(LINE_NO_ROWS) + "</td></tr>";
      var all = (st.data.rows || []).length;
      el.count.textContent = st.shown.length === all ? plural(all, "transect") : st.shown.length + " of " + all + " transects";
      el.csv.disabled = st.shown.length === 0;
    }

    function render(data) {
      st.data = data;
      var s = data.summary || {};
      el.summary.innerHTML = summaryHtml(summaryLine(s)) +
        ' <span class="count vt-stamp">read ' + esc(stampWords(data.generated_at)) + "</span>";
      var problems = (s.skipped || []).map(function (k) { return k.job_id + " could not be read: " + k.error; });
      if (problems.length) ctx.setResult(el.result, problems.map(esc).join("<br>"), "warn");
      var words = data.state_words || {};
      el.projects.innerHTML = (data.projects || []).map(function (p) { return projectRowHtml(p, words); }).join("") ||
        '<tr><td colspan="6" class="count">' + esc(LINE_NO_PROJECTS) + "</td></tr>";
      renderFilters();
      renderRows();
    }

    function load() {
      schedule();
      return ctx.api(API_TIMELINES).then(function (res) {
        if (!res.ok) {
          if (res.status === HTTP_UNAVAILABLE && res.data && res.data.not_mounted) {
            el.summary.textContent = "";
            ctx.setResult(el.result, esc(LINE_NOT_MOUNTED), "error");
          } else if (!ctx.isRelocked()) ctx.fail(el.result, res);
          return;
        }
        ctx.setResult(el.result, "");
        render(res.data);
      });
    }

    // schedule(): read again every POLL_MS while the pane is shown and no
    // drawer is open, so running work moves; stopped at a relock or unmount.
    function schedule() {
      if (st.timer !== null) return;
      st.timer = setInterval(function () { if (ctx.isShown() && el.drawer.hidden) load(); }, POLL_MS);
    }
    ctx.onStop(function () { if (st.timer !== null) { clearInterval(st.timer); st.timer = null; } });

    function setFilter(key, value) {
      st.filters[key] = value;
      writeFilters(st.filters);
      el.clear.hidden = !FILTER_KEYS.some(function (k) { return st.filters[k]; });
      if (st.data) renderRows();
    }

    function closeDrawer() {
      el.drawer.hidden = true;
      el.drawerBody.innerHTML = "";
      st.detailFor = null;
      if (st.opener && st.opener.isConnected) st.opener.focus();
      st.opener = null;
    }

    function openDrawer(row, opener) {
      st.opener = opener;
      st.detailFor = row.job_id + "|" + row.psx;
      var forKey = st.detailFor;
      el.drawerTitle.innerHTML = (row.site_name ? '<span class="vt-site">' + esc(row.site_name) + "</span> " : "") + chip(row.site) +
        " " + esc(row.transect) + " " + chip(row.psx);
      el.drawerSub.textContent = "Project " + row.job_id + ", editor " + row.editor + ". Atlas rows " +
        (row.readable_ids || []).join(", ") + ".";
      el.drawerBody.innerHTML = '<p class="count">Reading the status history and notes.</p>';
      el.drawer.hidden = false;
      el.drawer.querySelector('[data-vt="tl-drawer-close"]').focus();
      var query = "?job=" + encodeURIComponent(row.job_id) + "&psx=" + encodeURIComponent(row.psx);
      ctx.api(API_DETAIL + query).then(function (res) {
        if (st.detailFor !== forKey) return;
        if (!res.ok) {
          var data = res.data || {};
          el.drawerBody.innerHTML = '<p class="result result-error">' + esc(data.error || data.message ||
            "The desktop answered " + res.status + "; nothing on this pane changed.") + "</p>";
          return;
        }
        el.drawerBody.innerHTML = drawerBodyHtml(res.data);
      });
    }

    el.job.addEventListener("change", function () { setFilter("job", el.job.value); });
    el.state.addEventListener("change", function () { setFilter("state", el.state.value); });
    el.editor.addEventListener("change", function () { setFilter("editor", el.editor.value); });
    el.since.addEventListener("change", function () {
      var value = el.since.value.trim();
      if (value && !DAY_RE.test(value)) { ctx.setResult(el.result, esc(LINE_BAD_DAY), "error"); el.since.focus(); return; }
      ctx.setResult(el.result, "");
      setFilter("since", value);
    });
    el.clear.addEventListener("click", function () {
      st.filters = cleanFilters({});
      writeFilters(st.filters);
      if (st.data) { renderFilters(); renderRows(); }
    });
    el.rows.addEventListener("click", function (e) {
      var button = e.target.closest && e.target.closest("[data-vt-tl-open]");
      var tr = e.target.closest && e.target.closest("[data-vt-tl-row]");
      var index = button ? button.getAttribute("data-vt-tl-open") : (tr ? tr.getAttribute("data-vt-tl-row") : null);
      var row = index === null ? null : st.shown[Number(index)];
      if (!row) return;
      openDrawer(row, button || (tr && tr.querySelector("[data-vt-tl-open]")));
    });
    ctx.q("tl-drawer-close").addEventListener("click", closeDrawer);
    // Escape closes the drawer without bubbling: the desktop closes a window on a bubbled Escape.
    el.drawer.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); closeDrawer(); }
    });
    ctx.onStop(function () { if (!el.drawer.hidden) closeDrawer(); });
    el.csv.addEventListener("click", function () {
      var text = rowsToCsv(st.shown, CSV_COLUMNS);
      var done = function () { ctx.setResult(el.result, esc("Copied " + plural(st.shown.length, "transect") + " as CSV.")); };
      var failed = function () { ctx.setResult(el.result, esc("The browser refused the clipboard; nothing was copied."), "error"); };
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") navigator.clipboard.writeText(text).then(done, failed);
      else failed();
    });
    ctx.q("tl-refresh").addEventListener("click", load);
    return load;
  });
})();
