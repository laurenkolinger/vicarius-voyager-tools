/* static/vt_review.js - the review content of one transect and the Review
 * queue pane of the Atlas control panel (program C spec sections 3.5, 3.6,
 * 4.10, 4.11 and 5.5, milestone C6; decision 15 and ruling O3 of the manual
 * edit chain decisions).
 *
 * window.VoyagerTools.mountReview(el, opts) fills the review fragment's
 * section (templates/voyager_tools/review.html) with
 * opts = {jobId, psx, onDecision, onClose, initialsInput} and returns
 * {unmount()}. The atlas's review sidecar mounts it with the first four;
 * the Review queue pane adds initialsInput, the panel's own initials field,
 * so the review signs with the initials typed at the top of the panel.
 * It reads program B's review record of the transect and program A's
 * status intervals,
 *   GET  /manual-edit/api/jobs/<job>/projects/<psx>/dossier      {dossier}
 *   GET  /voyager-tools/api/timelines/detail?job=&psx=            {intervals}
 * and shows the state and round, the admin's initials, Open in Metashape,
 * the checks with the editor's explanations and the limits they were judged
 * under, scale and alignment per chunk, the timings (time in Metashape by
 * round, opens, admin inspection time, time in editing, time in review so
 * far), the certification, the notes of the current round with every
 * earlier round collapsed, and the bench copy. Its controls post program B's
 * admin routes:
 *   POST .../open            {initials}
 *   POST .../review          {decision, report, instructions, initials, start_upload}
 *   POST .../back-to-review  {initials}
 * The decision block holds the admin report, three radios (Mark for upload,
 * Send back with instructions, Start over), Start the upload now (only with
 * Mark for upload), the instructions (only for the other two) and one
 * Record decision button; a stopped start over offers only Resume start
 * over, which posts the report and instructions the first Start over wrote
 * to the notes; a transect marked for upload that no upload set holds offers
 * Back to admin review (ruling O3). Start over asks first, naming the bench
 * folder that moves aside and the Shelf folder the fresh copy comes from
 * (the section's data-shelf-location). Before posting a decision the record is read
 * again and nothing is posted when the round or the state changed. A
 * recorded decision shows one result line, moves the state chip, closes
 * the form and Open in Metashape, calls opts.onDecision({decision, job_id,
 * psx, upload_id}), and opens an upload it started in the panel's upload
 * window (VoyagerTools.openUploadWindow, static/vt_uploads.js, cache-busted
 * with the section's data-manual-edit-version). State chips use the atlas
 * matrix's stage colours (static/vicarius_roles.css). A refusal
 * carrying need_unlock goes to the admin session and shows the relock line
 * with Unlock; the typed report and instructions are kept for this page
 * load and come back when the record is read again.
 *
 * The Review queue pane (registered on the pane seam of
 * static/voyager_tools.js) lists, from GET /voyager-tools/api/review/queue,
 * every transect in admin review across the unfinished projects, every
 * start over that stopped, every transect marked for upload that can still
 * go back to admin review, and one line per registry row in review that no
 * project holds; Review on a row loads the review fragment of that
 * transect under the list and mounts it here.
 *
 * Every value from the server is escaped with esc() before it is written.
 * The pure helpers are exported to node through module.exports for
 * tests/test_review_pins.py.
 */
(function () {
  "use strict";

  var MANUAL_EDIT_JOBS = "/manual-edit/api/jobs/";
  var DEFAULT_BASE = "/voyager-tools";
  var QUEUE_ROUTE = "/api/review/queue";
  var DETAIL_ROUTE = "/api/timelines/detail?job=";
  var FRAGMENT_ROUTE = "/fragment?tool=review&job=";
  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var REPORT_MAX_CHARS = 4000;          // program B's REVIEW_REPORT_MAX_CHARS
  var INSTRUCTIONS_MAX_CHARS = 2000;    // program B's REVIEW_INSTRUCTIONS_MAX_CHARS
  var SS_REVIEW_INITIALS = "vt.initials"; // the panel's own session key, so the sidecar and the panel agree
  var SS_INITIALS = "vic_initials";     // the desktop login's typed initials
  var LINE_NO_EDITOR = "no editor on record";
  var AST_ZONE = "America/Puerto_Rico"; // Atlantic Standard Time all year, no daylight saving
  var AST_OFFSET = "-04:00";
  var DECISIONS = ["upload", "return", "restart"];
  var DECISION_BACK = "back_to_review";
  var DECISION_RESTART = "restart";
  var STATE_RESTARTING = "restarting";
  var STATE_UPLOADING = "uploading";
  var STATUS_EDITING = "editing";       // registry manual_edit_status values in program A's intervals
  var STATUS_REVIEW = "review";
  var KIND_ADMIN_REVIEW = "admin_review";
  var KIND_RETURN_INSTRUCTIONS = "return_instructions";
  // The note kinds an admin writes, tinted with the admin review stage colour as the atlas drawer tints them.
  var ADMIN_KINDS = ["admin_review", "return_instructions", "restart"];
  var SECONDS_PER_MINUTE = 60;
  var SECONDS_PER_HOUR = 3600;
  var SECONDS_PER_DAY = 86400;
  var REQUEST_TIMEOUT_MS = 30000;
  var QUEUE_POLL_MS = 30000;
  var HTTP_OK = 200;
  var HTTP_ACCEPTED = 202;
  var HTTP_CONFLICT = 409;
  var HTTP_SERVER_ERROR = 500;
  var HTTP_UNAVAILABLE = 503;
  // B's start over moves the bench folder aside as <folder>_restart_<YYYYMMDD-HHMMSS> (jobstore.RESTART_ASIDE_MARK).
  var RESTART_ASIDE_MARK = "_restart_";

  var DECISION_LABELS = { upload: "Mark for upload", return: "Send back with instructions", restart: "Start over" };
  var RESUME_LABEL = "Resume start over";
  var RESULT_WORDS = { upload: "Marked for upload", return: "Sent back to the editor", restart: "Start over recorded",
                       back_to_review: "Moved back to admin review" };
  // The transect state each recorded decision moves to (spec B 4.3); an upload started at once is uploading.
  var STATE_AFTER = { upload: "upload", return: "editing", restart: "restarting" };
  var RESTART_STAGE_WORDS = { space: "the free space check", aside: "the move aside",
                              pulling: "the pull of the fresh copy", verified: "the verify of the fresh copy" };
  var LEVEL_WORDS = { ok: "passed", warn: "warned", stop: "failed" };
  // A passed check is green, never the admin review violet (static/voyager_tools.css .chip.check-passed).
  var LEVEL_ROLES = { ok: "check-passed", warn: "role-attention", stop: "role-failed" };
  var STATE_WORDS = { todo: "not started", editing: "on the edit bench", review: "admin review",
                      upload: "marked for upload", uploading: "uploading to the Shelf",
                      restarting: "starting over", done: "uploaded" };
  // The chip of each transect state: the atlas matrix colour of the registry status that state
  // writes (manualedit.jobstore.REGISTRY_STATUS_OF_STATE), from the shared stage chips of
  // static/vicarius_roles.css, so the review reads in the colours of the matrix beside it.
  // static/vt_timelines.js uses the same map (tests/test_review_pins.py compares them).
  var STATE_ROLES = { todo: "stage-bench", editing: "stage-bench", review: "stage-review", upload: "stage-upload",
                      uploading: "stage-upload", restarting: "stage-bench", done: "stage-edited" };
  var ROLE_STOPPED = "role-failed";     // a start over that stopped, as the queue's Start over stopped row shows it
  var RULE_WORDS = { chunks: "Chunks present", transform: "Transforms", faces: "Crop", scale: "Scale",
                     align: "Alignment", steps: "Checklist" };
  var KIND_WORDS = { editor_explanation: "Editor explanation", check_note: "Check note", admin_review: "Admin review",
                     return_instructions: "Instructions to the editor", restart: "Start over", rescale: "Rescale" };
  var NOTE_DECISION_WORDS = { upload: "Marked for upload", return: "Sent back to the editor", restart: "Start over" };

  var LINES = {
    decision: "Choose Mark for upload, Send back with instructions or Start over.",
    initials: "Type your initials, two to four letters, for example LO.",
    initialsPanel: "Type your initials, two to four letters, at the top of the panel.",
    noReport: "Write the admin report before recording a decision.",
    longReport: "Keep the admin report to 4,000 characters.",
    nul: "Remove the NUL character from the text before recording.",
    needInstructions: "Write the instructions to the editor for a send back or a start over.",
    longInstructions: "Keep the instructions to the editor to 2,000 characters.",
    instructionsOnUpload: "Clear the instructions to the editor, or choose Send back with instructions.",
    startUpload: "Untick Start the upload now, which goes only with Mark for upload.",
    stale: "This transect changed since the review opened; reload it before deciding.",
    relock: "Admin mode ended. Unlock it to go on reviewing.",
    timeout: "No answer from the desktop after 30 s; the action may still have happened. Reload before trying again.",
    noAnswer: "The request did not reach the desktop; nothing was changed.",
    notMounted: "The manual edit module is not mounted here.",
    loading: "Reading the review record...",
    recording: "Recording the decision...",
    opening: "Opening Metashape...",
    moving: "Moving the transect back to admin review...",
    noneInReview: "No transect is in admin review.",
    resumeKept: "VICARIUS resumes with the report and instructions written at the first Start over.",
    resumeRewrite: "The first Start over did not reach the notes; write the report and instructions again.",
  };

  var TIPS = {
    open: "Open this transect in Metashape on the edit bench to inspect the edit. " +
      "VICARIUS checks the GPU right of way first and records the open with your initials.",
    checks: "VICARIUS ran these checks at the editor's last Confirm and shows the editor's explanation under " +
      "each check that warned or failed.",
    fixed: "The check failed or warned on an earlier pass; the editor wrote an explanation, changed the project, " +
      "and it passed on the next pass.",
    scale: "The checks read each chunk's scale error and marker spread from the project; " +
      "a chunk with one scale bar has no cross-check.",
    timings: "VICARIUS counts time in Metashape from each open to its close, and time in editing and in review " +
      "from the atlas status changes, so time in editing includes days with Metashape closed.",
    notes: "VICARIUS keeps every explanation, check note, admin report and instruction written about this " +
      "transect, grouped by edit round.",
    decision: "An admin records one of three decisions for the transect. VICARIUS writes the decision and the " +
      "report to the atlas record, and the matrix changes colour.",
    initials: "Type your initials, two to four letters. VICARIUS signs the open in Metashape and the decision " +
      "with them.",
    report: "Write what the review found. VICARIUS keeps the report in the transect's notes, where every " +
      "atlas viewer can read it.",
    choice: "Choose what happens next: Mark for upload queues the transect for the Shelf, Send back returns it to " +
      "the editor, Start over pulls a fresh copy from the Shelf. VICARIUS raises the edit round for the last two.",
    instructions: "Write what the editor must change, for a send back or a start over. " +
      "The walkthrough shows these instructions to the editor when the transect returns.",
    startUpload: "Tick this to start uploading the transect to the Shelf as soon as it is marked for upload. " +
      "VICARIUS refuses while Metashape still holds the project.",
    record: "Record the chosen decision with the report. VICARIUS reads the review record again first and records " +
      "nothing when the transect changed meanwhile.",
    resume: "Resume the start over from the stage where it stopped. VICARIUS keeps the report and instructions " +
      "of the first Start over and raises no round.",
    back: "Move this transect back to admin review before its upload starts. VICARIUS then asks for a new decision " +
      "and keeps the earlier report in the notes.",
    restartConfirm: "Move the edited copy aside and pull a fresh copy from the Shelf now.",
    resumeConfirm: "Resume the stopped start over now, from the stage it reached.",
    reload: "Read the review record again, keeping the typed report and instructions.",
    unlock: "Unlock admin mode with the admin PIN. VICARIUS then reads the review record again, keeping the typed text.",
    queueOpen: "Open this transect's checks, notes and decision. " +
      "VICARIUS keeps the list in place and refreshes it after a decision.",
  };

  // The typed report, instructions and decision per "<job>/<psx>", kept across
  // a reload, a relock or a remount for this page load, cleared once a decision records.
  var drafts = {};

  /* ---- pure helpers (exported to node) ------------------------------------ */

  // Text made safe to write as HTML: & < > " and ' escaped.
  function esc(value) {
    return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function isObject(value) { return !!value && typeof value === "object" && !Array.isArray(value); }
  function listOf(value) { return Array.isArray(value) ? value.filter(isObject) : []; }
  function plural(n, word, many) { return n + " " + (n === 1 ? word : (many || word + "s")); }

  // A check level as a word: passed, warned, failed; an unknown level as itself.
  function levelWord(level) { return LEVEL_WORDS[level] || String(level || ""); }

  // The one way the review names a round: B counts rounds from 0, the review from 1 ("Edit round 1").
  function roundLabel(round) {
    var n = Number(round);
    if (round == null || round === "" || !isFinite(n) || n < 0 || Math.floor(n) !== n) return "";
    return "Edit round " + (n + 1);
  }

  // "YYYY-MM-DDTHH:MM:SS-04:00" for a Date, in Atlantic Standard Time.
  function astStamp(date) {
    var parts = {};
    new Intl.DateTimeFormat("en-CA", { timeZone: AST_ZONE, hour12: false, year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(date).forEach(function (p) { parts[p.type] = p.value; });
    var hour = parts.hour === "24" ? "00" : parts.hour;
    return parts.year + "-" + parts.month + "-" + parts.day + "T" + hour + ":" + parts.minute + ":" +
      parts.second + AST_OFFSET;
  }

  // "YYYY-MM-DD HH:MM AST" from an ISO stamp. A stamp written with the AST
  // offset (or none) is read by position, so the browser's zone never moves
  // it; any other offset is converted. A blank or unreadable value is "-".
  function astText(iso) {
    var text = String(iso || "");
    var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/.exec(text);
    if (!m) return text ? text : "-";
    if (!m[3] || m[3] === AST_OFFSET) return m[1] + " " + m[2] + " AST";
    var at = new Date(text);
    return isNaN(at.getTime()) ? text : astStamp(at).slice(0, 16).replace("T", " ") + " AST";
  }

  // A span of seconds as "2 d 4 h", "1 h 30 min", "12 min" or "under a minute"; "-" when unknown.
  function spanText(seconds) {
    var s = Number(seconds);
    if (seconds == null || seconds === "" || !isFinite(s) || s < 0) return "-";
    if (s < SECONDS_PER_MINUTE) return "under a minute";
    if (s >= SECONDS_PER_DAY) {
      return Math.floor(s / SECONDS_PER_DAY) + " d " + Math.floor((s % SECONDS_PER_DAY) / SECONDS_PER_HOUR) + " h";
    }
    var minutes = Math.floor((s % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE);
    var hours = Math.floor(s / SECONDS_PER_HOUR);
    return hours ? hours + " h " + (minutes < 10 ? "0" : "") + minutes + " min" : minutes + " min";
  }

  // The one line a recorded decision shows: "Marked for upload by LO at 2026-09-27 10:31 AST."
  function resultLine(result) {
    var r = result || {};
    return (RESULT_WORDS[r.decision] || String(r.decision || "")) + " by " + String(r.by || "") +
      " at " + astText(r.at) + ".";
  }

  // The sentence of a refusal (answer = {status, body, timedOut}): the
  // route's message or error; else, for a 5xx, the desktop's status and that
  // nothing changed (spec C section 8); else lead and the status.
  function refusalText(answer, lead) {
    var a = answer || {};
    if (a.timedOut) return LINES.timeout;
    var b = isObject(a.body) ? a.body : {};
    var said = typeof b.message === "string" && b.message ? b.message : (typeof b.error === "string" ? b.error : "");
    if (said) return said;
    if (!a.status) return LINES.noAnswer;
    if (a.status >= HTTP_SERVER_ERROR) return "The desktop answered " + a.status + "; nothing on this pane changed.";
    return String(lead || "") + "the desktop answered " + a.status + ".";
  }

  // Check a decision form against program B's review body rules (spec B 5.4).
  // form: {decision, report, instructions, initials, startUpload}. Returns
  // {ok: true, body} with the body to post, or {ok: false, line}.
  function readyToRecord(form) {
    var f = form || {};
    var decision = String(f.decision || "");
    if (DECISIONS.indexOf(decision) === -1) return { ok: false, line: LINES.decision };
    var initials = String(f.initials || "").trim().toUpperCase();
    if (!INITIALS_RE.test(initials)) return { ok: false, line: LINES.initials };
    var report = String(f.report || "").trim();
    var instructions = String(f.instructions || "").trim();
    if (!report) return { ok: false, line: LINES.noReport };
    if (report.length > REPORT_MAX_CHARS) return { ok: false, line: LINES.longReport };
    if (report.indexOf("\u0000") !== -1 || instructions.indexOf("\u0000") !== -1) return { ok: false, line: LINES.nul };
    if (decision === "upload" && instructions) return { ok: false, line: LINES.instructionsOnUpload };
    if (decision !== "upload" && !instructions) return { ok: false, line: LINES.needInstructions };
    if (instructions.length > INSTRUCTIONS_MAX_CHARS) return { ok: false, line: LINES.longInstructions };
    var startUpload = f.startUpload === true;
    if (startUpload && decision !== "upload") return { ok: false, line: LINES.startUpload };
    return { ok: true, body: { decision: decision, report: report, instructions: instructions,
                               initials: initials, start_upload: startUpload } };
  }

  // The URL of one of program B's routes for this transect: leaf "/dossier", "/open", "/review" or "/back-to-review".
  function projectUrl(jobId, psx, leaf) {
    return MANUAL_EDIT_JOBS + encodeURIComponent(jobId) + "/projects/" + encodeURIComponent(psx) + leaf;
  }

  function numberText(value, digits) {
    var n = Number(value);
    return value == null || value === "" || !isFinite(n) ? "" : n.toFixed(digits);
  }

  // The limits the checks were judged under, with when and by whom they were saved; "" when none are recorded.
  function limitsLine(settings) {
    var s = isObject(settings) ? settings : {};
    var mm = numberText(s.scale_error_max_mm, 2), pct = numberText(s.scale_error_max_percent, 2);
    var spread = numberText(s.align_spread_max_mm, 2);
    if (!mm && !pct && !spread) return "";
    var saved = s.saved_at ? ", saved " + astText(s.saved_at) + (s.saved_by ? " by " + s.saved_by : "")
      : (s.saved_by === "shipped defaults" ? ", the shipped defaults (no settings file)" : "");
    return "Judged under " + (mm ? mm + " mm" : "-") + " or " + (pct ? pct + " %" : "-") +
      " scale error and " + (spread ? spread + " mm" : "-") + " marker spread" + saved + ".";
  }

  // The sentence of the Start over confirmation: the bench folder moving
  // aside and the Shelf folder the fresh copy comes from (shelf, the job
  // row's shelf_location; "the Shelf" when unknown), or, for a stopped start
  // over, the stage it resumes from and where the edited copy already sits.
  function restartConfirmText(d, resume, shelf) {
    var folder = String(d.processing_folder || "");
    var restart = isObject(d.restart) ? d.restart : {};
    if (resume) {
      var aside = String(restart.aside || "");
      return "VICARIUS resumes the start over of " + folder + " from " +
        (RESTART_STAGE_WORDS[restart.stage] || "the stage it reached") +
        (aside ? "; the edited copy already sits aside as " + aside : "") + ".";
    }
    var benchPath = String((isObject(d.on_bench) && d.on_bench.path) || folder);
    var source = String(shelf || "").trim();
    return "VICARIUS moves " + benchPath + " aside as " + folder + RESTART_ASIDE_MARK +
      " followed by the date and time of the move, and pulls a fresh copy from " + (source || "the Shelf") + ".";
  }

  // Where a start over stopped, then its recorded error as a sentence of its own.
  function resumeLine(d) {
    var restart = isObject(d.restart) ? d.restart : {};
    var why = String(restart.error || "").trim();
    if (why && !/[.!?]$/.test(why)) why += ".";
    return "The start over stopped at " + (RESTART_STAGE_WORDS[restart.stage] || "a stage not recorded") + "." +
      (why ? " " + why.charAt(0).toUpperCase() + why.slice(1) : "");
  }

  // The report and instructions the first Start over wrote to the notes (the
  // latest admin review note deciding restart, and the instructions note of
  // the same decision), which a resume posts again; null when either is
  // missing (the first decision's registry write failed), so the admin types them.
  function resumeTexts(notes) {
    var list = listOf(notes);
    var report = list.filter(function (n) {
      return n.kind === KIND_ADMIN_REVIEW && n.decision === DECISION_RESTART && typeof n.text === "string" && n.text.trim();
    }).pop();
    if (!report) return null;
    var instructions = list.filter(function (n) {
      return n.kind === KIND_RETURN_INSTRUCTIONS && n.source_id && n.source_id === report.source_id &&
        typeof n.text === "string" && n.text.trim();
    }).pop();
    return instructions ? { report: report.text, instructions: instructions.text } : null;
  }

  // The state chip and words of the review head: a start over that stopped
  // reads as stopped (with its stage), as its Start over stopped row does.
  function stateBadge(d) {
    var r = isObject(d) ? d : {};
    var restart = isObject(r.restart) ? r.restart : {};
    if (r.state === STATE_RESTARTING && String(restart.error || "").trim()) {
      return { role: ROLE_STOPPED, words: "start over stopped at " + (RESTART_STAGE_WORDS[restart.stage] ||
        "a stage not recorded") };
    }
    return { role: STATE_ROLES[r.state] || "role-empty", words: STATE_WORDS[r.state] || r.state || "state not recorded" };
  }

  // Time in editing (every editing interval summed) and time in review so
  // far (the open review interval) from program A's intervals of the
  // transect's first readable id whose list is not empty; null where
  // unknown, and editing_s null when no editing interval exists. Program B
  // writes one status for every readable id of a transect at once.
  function stateTimes(intervalsById, readableIds) {
    var byId = isObject(intervalsById) ? intervalsById : {};
    var ids = Array.isArray(readableIds) ? readableIds : [];
    var spells = null;
    for (var i = 0; i < ids.length && !spells; i++) {
      var listed = Array.isArray(byId[ids[i]]) ? byId[ids[i]].filter(isObject) : [];
      if (listed.length) spells = listed;
    }
    if (!spells) return { editing_s: null, review_open_s: null };
    var editingSpells = spells.filter(function (s) { return s.value === STATUS_EDITING; });
    var editing = editingSpells.length ? editingSpells.reduce(function (sum, s) {
      var n = Number(s.seconds);
      return isFinite(n) && n > 0 ? sum + n : sum;
    }, 0) : null;
    var last = spells[spells.length - 1];
    var open = last && last.open === true && last.value === STATUS_REVIEW && isFinite(Number(last.seconds))
      ? Number(last.seconds) : null;
    return { editing_s: editing, review_open_s: open };
  }

  // The notes of the current round, and every earlier round newest first
  // with its outcome (the admin review decision recorded in that round).
  // A note whose round is not a whole number counts as round 0.
  function roundGroups(notes, currentRound) {
    var list = listOf(notes);
    var current = Number(currentRound) || 0;
    var rounds = {};
    var out = { current: [], earlier: [] };
    list.forEach(function (n) {
      var r = roundLabel(n.round) ? Number(n.round) : 0;
      if (r === current) out.current.push(n);
      else (rounds[r] = rounds[r] || []).push(n);
    });
    out.earlier = Object.keys(rounds).map(Number).sort(function (a, b) { return b - a; }).map(function (r) {
      var decided = rounds[r].filter(function (n) { return n.kind === KIND_ADMIN_REVIEW && n.decision; }).pop();
      return { round: r, outcome: decided ? (NOTE_DECISION_WORDS[decided.decision] || String(decided.decision)) : "",
               notes: rounds[r] };
    });
    return out;
  }

  // The Review queue's count line.
  function queueMeta(payload) {
    var p = isObject(payload) ? payload : {};
    var count = function (key) { return Array.isArray(p[key]) ? p[key].length : 0; };
    return plural(count("items"), "transect") + " in admin review, " +
      plural(count("stopped_restarts"), "start over", "start overs") + " stopped, " +
      count("marked_for_upload") + " marked for upload.";
  }

  // "Lang Bank Red Hind FSA (LBH) T3", or "MRS T2" when the site table has no name for the code.
  function transectWords(item) {
    var it = isObject(item) ? item : {};
    var site = it.site_name ? it.site_name + " (" + it.site + ")" : String(it.site || "");
    return site + " " + String(it.transect || "");
  }

  var helpers = { esc: esc, levelWord: levelWord, roundLabel: roundLabel, astText: astText, spanText: spanText,
                  resultLine: resultLine, readyToRecord: readyToRecord, projectUrl: projectUrl, limitsLine: limitsLine,
                  restartConfirmText: restartConfirmText, stateTimes: stateTimes, roundGroups: roundGroups,
                  queueMeta: queueMeta, transectWords: transectWords, resumeLine: resumeLine, resumeTexts: resumeTexts,
                  stateBadge: stateBadge, refusalText: refusalText, STATE_ROLES: STATE_ROLES, LEVEL_ROLES: LEVEL_ROLES,
                  TIPS: TIPS, LINES: LINES };
  if (typeof module === "object" && module && module.exports) module.exports = helpers;
  if (typeof window === "undefined") return;

  /* ---- markup ------------------------------------------------------------ */

  function chip(text) { return '<span class="id code">' + esc(text) + "</span>"; }

  function stateHtml(role, words) {
    return '<span class="vt-state"><span class="chip ' + esc(role) + '" aria-hidden="true"></span>' + esc(words) + "</span>";
  }

  function glyph(symbol, about, tip) {
    return '<button type="button" class="tip" aria-label="About ' + esc(about) + '" title="' + esc(tip) + '"><span>' +
      symbol + "</span></button>";
  }

  // A section heading with its "i" glyph; about names the glyph ("About notes") when the
  // visible heading carries more than the plain word (a count).
  function sectionHead(text, tip, about) {
    return '<div class="vt-list-head"><h3 class="vt-list-title">' + esc(text) + "</h3>" +
      glyph("i", about || text.toLowerCase(), tip) + "</div>";
  }

  function dimLine(text, extraClass) {
    return '<p class="vt-review-dim' + (extraClass ? " " + extraClass : "") + '">' + esc(text) + "</p>";
  }

  // Who the review names as the editor: whoever certified the edit, else the
  // last person the open route recorded (the dossier's editor), never the
  // project's creator; "" when nobody is on record.
  function editorOf(d) {
    var c = isObject(d.certification) ? d.certification : {};
    return String(c.by || d.editor || "");
  }

  // The state chip, the round and the editor on one line; a record that
  // names nobody says so rather than leaving the reader to guess.
  function factsHtml(d) {
    var round = roundLabel(d.round);
    var badge = stateBadge(d);
    var editor = editorOf(d);
    return '<p class="vt-review-facts"><span data-review-part="state">' + stateHtml(badge.role, badge.words) +
      "</span>" + (round ? '<span class="count">' + esc(round) + "</span>" : "") +
      '<span class="count">' + esc(editor ? "edited by " + editor : LINE_NO_EDITOR) + "</span></p>";
  }

  // One definition-list row: the label with its "?" glyph, the control beside it.
  function rowHtml(id, label, tip, control, attrs) {
    return "<div" + (attrs || "") + '><dt><label for="' + id + '">' + esc(label) + "</label>" +
      glyph("?", label.toLowerCase(), tip) + "</dt><dd>" + control + "</dd></div>";
  }

  function initialsHtml(ids, initials) {
    return '<dl class="choices vt-review-choices">' + rowHtml(ids.initials, "Your initials", TIPS.initials,
      '<input type="text" id="' + ids.initials + '" class="w-initials" data-review-field="initials" maxlength="4" ' +
      'autocomplete="off" spellcheck="false" value="' + esc(initials) + '">') + "</dl>";
  }

  function openHtml() {
    return '<div class="vt-review-open"><button type="button" class="btn btn-mini" data-review-action="open" title="' +
      esc(TIPS.open) + '">Open in Metashape</button>' +
      '<span class="count vt-review-open-line" data-review-part="open-line" role="status"></span></div>';
  }

  function checkItemHtml(item) {
    var level = String(item.level || "");
    var note = isObject(item.note) ? item.note : null;
    return '<li class="vt-review-check"><span class="vt-review-check-head">' +
      stateHtml(LEVEL_ROLES[level] || "role-empty", levelWord(level)) +
      (item.fixed ? ' <span class="count vt-review-fixed" title="' + esc(TIPS.fixed) + '" tabindex="0">fixed</span>' : "") +
      ' <span class="vt-review-rule">' + esc(RULE_WORDS[item.rule] || item.rule || "") + "</span></span>" +
      '<span class="vt-review-check-line">' + esc(item.line || "") + "</span>" +
      (note && note.text ? '<pre class="vt-review-text">' + esc(note.text) + "</pre>" +
        dimLine([note.by, note.at ? astText(note.at) : ""].filter(Boolean).join(", ")) : "") + "</li>";
  }

  function checksHtml(d) {
    var checks = isObject(d.checks) ? d.checks : null;
    var items = checks ? listOf(checks.items) : [];
    var limits = limitsLine((checks && checks.settings) || d.settings);
    return sectionHead("Checks", TIPS.checks) +
      (items.length ? '<ul class="vt-review-checks">' + items.map(checkItemHtml).join("") + "</ul>"
        : dimLine("No checks are recorded for this round.")) + (limits ? dimLine(limits) : "");
  }

  // "Rescaled by LO at ...: MRS_T2_2023ann 3.1 mm to 1.2 mm." or "".
  function rescaleLine(rescale) {
    if (!isObject(rescale) || !listOf(rescale.chunks).length) return "";
    var parts = listOf(rescale.chunks).map(function (c) {
      return String(c.label || "") + " " + (numberText(c.error_before_mm, 1) || "-") + " mm to " +
        (numberText(c.error_after_mm, 1) || "-") + " mm";
    });
    return "Rescaled" + (rescale.by ? " by " + rescale.by : "") + (rescale.at ? " at " + astText(rescale.at) : "") +
      ": " + parts.join("; ") + ".";
  }

  // One table row per chunk: bars and scale error from the scale check, spread from the alignment.
  function scaleAlignHtml(d) {
    var scale = isObject(d.scale_check) ? listOf(d.scale_check.chunks) : [];
    var align = isObject(d.align) ? d.align : {};
    var spreads = {};
    listOf(align.chunks).forEach(function (c) { if (c.label) spreads[c.label] = c; });
    var byLabel = {};
    var labels = [];
    scale.forEach(function (c) { if (c.label) { byLabel[c.label] = c; labels.push(c.label); } });
    Object.keys(spreads).forEach(function (l) { if (labels.indexOf(l) === -1) labels.push(l); });
    if (align.reference && labels.indexOf(align.reference) === -1) labels.push(align.reference);
    var rows = labels.map(function (label) {
      var s = byLabel[label] || {};
      var err = numberText(s.error_mm, 1), pct = numberText(s.error_pct, 2);
      var spreadMm = numberText((spreads[label] || {}).spread_mm, 1);
      var spread = label === align.reference ? "reference" : (spreadMm ? spreadMm + " mm" : "-");
      return "<tr><td>" + chip(label) + '</td><td class="vt-num">' + esc(s.bars == null ? "-" : s.bars) +
        '</td><td class="vt-num">' + esc(err ? err + " mm" + (pct ? ", " + pct + " %" : "") : "-") +
        '</td><td class="vt-num">' + esc(spread) + "</td></tr>";
    }).join("");
    var rescale = rescaleLine(d.rescale);
    if (!rows && !rescale) return "";
    return sectionHead("Scale and alignment", TIPS.scale) +
      (rows ? '<div class="vt-table-wrap"><table class="vt-table vt-review-chunks"><thead><tr><th scope="col">Chunk</th>' +
        '<th scope="col" class="vt-num">Bars</th><th scope="col" class="vt-num">Scale error</th>' +
        '<th scope="col" class="vt-num">Spread</th></tr></thead><tbody>' + rows + "</tbody></table></div>" : "") +
      (rescale ? dimLine(rescale) : "");
  }

  function kvRow(label, value) { return "<dt>" + esc(label) + "</dt><dd>" + esc(value) + "</dd>"; }

  function timingsHtml(d, times) {
    var t = isObject(d.timings) ? d.timings : {};
    var unknown = Number(t.unknown_opens) || 0;
    var edit = spanText(t.edit_seconds) + (unknown ? " and " + plural(unknown, "open") + " with no recorded close" : "");
    var rounds = listOf(t.rounds).map(function (r) {
      return (roundLabel(r.round) || "Round not recorded") + ": " + spanText(r.edit_seconds);
    }).join("; ");
    var adminOpens = Number(t.admin_opens) || 0;
    var s = isObject(times) ? times : {};
    return sectionHead("Timings", TIPS.timings) + '<dl class="vt-review-kv">' +
      kvRow("Time in Metashape", edit) + (rounds ? kvRow("By round", rounds) : "") +
      kvRow("Opens", t.opens == null ? "-" : String(t.opens)) +
      (adminOpens ? kvRow("Admin inspection", spanText(t.admin_seconds) + " over " + plural(adminOpens, "open")) : "") +
      kvRow("Time in editing", spanText(s.editing_s)) +
      kvRow("Time in review so far", s.review_open_s == null ? "-" : spanText(s.review_open_s)) +
      kvRow("First open", astText(t.first_open_at)) + kvRow("Last close", astText(t.last_close_at)) +
      kvRow("Confirmed", astText(t.confirmed_at)) + "</dl>";
  }

  function certificationHtml(d) {
    var c = isObject(d.certification) ? d.certification : null;
    return c ? dimLine("Certified by " + (c.by || "-") + " at " + astText(c.at) + ": " + (c.statement || "")) : "";
  }

  function noteHtml(n) {
    var qualifier = NOTE_DECISION_WORDS[n.decision] || RULE_WORDS[n.rule] || "";
    var admin = ADMIN_KINDS.indexOf(n.kind) !== -1 ? " is-admin" : "";
    return '<div class="vt-review-note"><p class="vt-review-note-head"><span class="vt-review-kind' + admin + '">' +
      esc(KIND_WORDS[n.kind] || n.kind || "note") + "</span>" +
      (qualifier ? '<span class="count">' + esc(qualifier) + "</span>" : "") +
      '<span class="count">' + esc(n.by || "") + "</span>" + '<span class="count">' + esc(astText(n.at)) + "</span></p>" +
      '<pre class="vt-review-text">' + esc(n.text) + "</pre></div>";
  }

  // One round of notes under its "Edit round N" heading, with the outcome of an earlier round beside it.
  function roundHtml(round, outcome, notes) {
    return '<div class="vt-review-round"><h4 class="vt-review-round-head">' + esc(roundLabel(round) || "Round not recorded") +
      (outcome ? '<span class="count">' + esc(outcome) + "</span>" : "") + "</h4>" + notes.map(noteHtml).join("") + "</div>";
  }

  function notesHtml(d) {
    var groups = roundGroups(d.notes, d.round);
    var count = groups.current.length + groups.earlier.reduce(function (sum, g) { return sum + g.notes.length; }, 0);
    var earlier = groups.earlier.map(function (g) { return roundHtml(g.round, g.outcome, g.notes); }).join("");
    return sectionHead("Notes (" + count + ")", TIPS.notes, "notes") +
      (d.notes_error ? '<p class="result result-error">' + esc(d.notes_error) + "</p>" : "") +
      (groups.current.length ? roundHtml(Number(d.round) || 0, "", groups.current)
        : dimLine("No notes are recorded for " + (roundLabel(d.round) || "this round").toLowerCase() + ".")) +
      (groups.earlier.length ? '<details class="src vt-review-earlier"><summary>Earlier rounds (' +
        groups.earlier.length + ")</summary>" + earlier + "</details>" : "");
  }

  function benchHtml(d) {
    var bench = isObject(d.on_bench) ? d.on_bench : {};
    var lines = [];
    if (bench.path) lines.push("On the edit bench: " + bench.path + (bench.exists === false ? " (missing)" : ""));
    if (d.admin_opened_after_checks) lines.push("Opened in Metashape by an admin after the checks.");
    return lines.map(function (l) { return dimLine(l, "vt-review-path"); }).join("");
  }

  function offeredDecisions(d) { return Array.isArray(d.decisions) ? d.decisions : []; }

  function isResume(d) {
    var offered = offeredDecisions(d);
    return d.state === STATE_RESTARTING && offered.length === 1 && offered[0] === DECISION_RESTART;
  }

  function textareaHtml(id, field, rows, max, value) {
    return '<textarea id="' + id + '" class="vt-field vt-review-input" data-review-field="' + field + '" rows="' + rows +
      '" maxlength="' + max + '">' + esc(value || "") + "</textarea>";
  }

  function radiosHtml(ids, chosen) {
    return '<span class="opts" role="radiogroup" aria-labelledby="' + ids.choice + '">' + DECISIONS.map(function (key) {
      return '<label class="opt"><input type="radio" name="' + ids.choice + '" value="' + key +
        '" data-review-field="decision"' + (chosen === key ? " checked" : "") + "> " + esc(DECISION_LABELS[key]) + "</label>";
    }).join("") + "</span>";
  }

  function confirmHtml(d, resume, shelf) {
    return '<div class="vt-review-confirm" data-review-part="confirm" role="group" aria-label="Confirm the start over" hidden>' +
      "<p>" + esc(restartConfirmText(d, resume, shelf)) + "</p>" +
      '<div class="vt-review-confirm-acts"><button type="button" class="btn btn-mini btn-danger" ' +
      'data-review-action="restart-confirm" title="' + esc(resume ? TIPS.resumeConfirm : TIPS.restartConfirm) + '">' +
      (resume ? "Resume now" : "Start over now") + "</button>" +
      '<button type="button" class="btn btn-mini" data-review-action="restart-cancel">Cancel</button></div></div>';
  }

  function lineHtml() {
    return '<div class="vt-review-result"><p class="result" data-review-part="line" role="status"></p>' +
      '<span class="vt-review-line-acts" data-review-part="line-actions"></span></div>';
  }

  // The decision block of a transect with a decision open: the report, the
  // radios (or only the resume), Start the upload now, the instructions, the
  // Start over confirmation, Record decision and the result line. A resume
  // whose first report and instructions are in the notes (kept) shows neither
  // field and posts those texts again.
  function formHtml(d, draft, ids, shelf, kept) {
    var resume = isResume(d);
    var chosen = resume ? DECISION_RESTART : (draft.decision || "");
    var hideTexts = resume && !!kept;
    var choices = (hideTexts ? "" : rowHtml(ids.report, "Admin report", TIPS.report,
      textareaHtml(ids.report, "report", 4, REPORT_MAX_CHARS, draft.report))) +
      (resume ? "" : '<div data-review-row="decision"><dt><span id="' + ids.choice + '">Next step</span>' +
        glyph("?", "the next step", TIPS.choice) + '</dt><dd class="dd-list">' + radiosHtml(ids, chosen) + "</dd></div>") +
      rowHtml(ids.startUpload, "Upload", TIPS.startUpload, '<label class="opt"><input type="checkbox" id="' +
        ids.startUpload + '" data-review-field="start_upload"' + (draft.startUpload ? " checked" : "") +
        "> Start the upload now</label>", ' data-review-row="start_upload"' + (chosen === "upload" ? "" : " hidden")) +
      (hideTexts ? "" : rowHtml(ids.instructions, "Instructions to the editor", TIPS.instructions,
        textareaHtml(ids.instructions, "instructions", 3, INSTRUCTIONS_MAX_CHARS, draft.instructions),
        ' data-review-row="instructions"' + (chosen && chosen !== "upload" ? "" : " hidden")));
    var label = resume ? RESUME_LABEL : "Record decision";
    var resumeLines = resume ? dimLine(resumeLine(d)) + dimLine(kept ? LINES.resumeKept : LINES.resumeRewrite) : "";
    return resumeLines + (choices ? '<dl class="choices vt-review-choices">' + choices + "</dl>" : "") +
      confirmHtml(d, resume, shelf) + '<div class="actions"><button type="button" class="btn btn-primary" ' +
      'data-review-action="record" title="' + esc(resume ? TIPS.resume : TIPS.record) + '"' +
      (chosen ? "" : " disabled") + ">" + esc(label) + "</button></div>" + lineHtml();
  }

  // The decision section: the form while a decision is open; otherwise one
  // line saying why none is, with Back to admin review when the transect
  // can still go back (ruling O3).
  function decisionHtml(d, draft, ids, shelf, kept) {
    var head = sectionHead("Decision", TIPS.decision);
    if (offeredDecisions(d).length) {
      return '<div data-review-part="decision">' + head + formHtml(d, draft, ids, shelf, kept) + "</div>";
    }
    var stateWord = STATE_WORDS[d.state] || d.state || "not recorded";
    var back = d.can_return_to_review === true
      ? '<div class="actions"><button type="button" class="btn btn-primary" data-review-action="back" title="' +
        esc(TIPS.back) + '">Back to admin review</button></div>' : "";
    return '<div data-review-part="decision">' + head + dimLine("No decision is open: the transect is " + stateWord + ".") +
      back + lineHtml() + "</div>";
  }

  // view = {draft, ids, initials, ownInitials, times, shelf, kept}.
  function paneHtml(d, view) {
    return factsHtml(d) + (view.ownInitials ? initialsHtml(view.ids, view.initials) : "") + openHtml() + checksHtml(d) +
      scaleAlignHtml(d) + timingsHtml(d, view.times) + certificationHtml(d) + notesHtml(d) + benchHtml(d) +
      decisionHtml(d, view.draft, view.ids, view.shelf, view.kept);
  }

  /* ---- requests ------------------------------------------------------------ */

  function isLocked(status, body) {
    var session = window.VICADMINSESSION;
    if (session && typeof session.isLockedResponse === "function") return session.isLockedResponse(status, body);
    return (status === 401 || status === 403) && !!(body && body.need_unlock);
  }

  // fetch with the session cookie and a 30 s limit; resolves {status, body,
  // timedOut} and never rejects (status 0 when the request failed or timed out).
  function requestJSON(url, init) {
    var options = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, init || {});
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = null;
    if (controller) {
      options.signal = controller.signal;
      timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
    }
    return window.fetch(url, options).then(function (r) {
      return r.json().then(null, function () { return null; }).then(function (body) {
        return { status: r.status, body: isObject(body) ? body : {} };
      });
    }, function (err) {
      return { status: 0, body: {}, timedOut: !!(err && err.name === "AbortError") };
    }).then(function (answer) {
      if (timer !== null) clearTimeout(timer);
      return answer;
    });
  }

  function postJSON(url, body) {
    return requestJSON(url, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                              body: JSON.stringify(body) });
  }

  // The initials live for this browser session only (sessionStorage), as in
  // the walkthrough and the checks window, so the next person at the machine
  // never finds the last reviewer's initials filled in (V-claim-audit-10).
  function readSession(key) { try { return window.sessionStorage.getItem(key); } catch (e) { return null; } }
  function writeSession(key, value) { try { window.sessionStorage.setItem(key, value); } catch (e) { /* the review still works */ } }
  // forgetOldInitials: an earlier build kept the initials in browser-wide
  // storage under the same key; that copy is removed so it fills nothing.
  function forgetOldInitials() { try { window.localStorage.removeItem(SS_REVIEW_INITIALS); } catch (e) { /* nothing kept */ } }

  // The initials this session remembers: the panel's key, then the desktop login's typed value.
  function rememberedInitials() {
    var v = readSession(SS_REVIEW_INITIALS) || readSession(SS_INITIALS);
    return String(v || "").trim().toUpperCase();
  }

  /* ---- the mount ------------------------------------------------------------ */

  var mountCount = 0;

  function reviewRoot(el) {
    if (el.classList && el.classList.contains("vt-review")) return el;
    return el.querySelector(".vt-review") || el;
  }

  function mountReview(el, opts) {
    if (!el || typeof el.querySelector !== "function") throw new TypeError("VoyagerTools.mountReview needs the review's element.");
    var o = opts || {};
    if (typeof o.jobId !== "string" || !o.jobId || typeof o.psx !== "string" || !o.psx) {
      throw new TypeError("VoyagerTools.mountReview needs opts.jobId and opts.psx.");
    }
    var root = reviewRoot(el);
    var body = root.querySelector('[data-review-part="body"]') || root;
    var base = root.getAttribute("data-vt-base") || DEFAULT_BASE;
    var shelf = root.getAttribute("data-shelf-location") || "";
    var panelInitials = o.initialsInput && typeof o.initialsInput.value === "string" ? o.initialsInput : null;
    var key = o.jobId + "/" + o.psx;
    var n = ++mountCount;
    var ids = { initials: "vt-review-initials-" + n, report: "vt-review-report-" + n, choice: "vt-review-choice-" + n,
                instructions: "vt-review-instructions-" + n, startUpload: "vt-review-start-" + n };
    var st = { dossier: null, kept: null, busy: false, opening: false, recorded: false, relocked: false, live: true };
    var session = window.VICADMINSESSION;
    var unsubscribe = session && typeof session.onChange === "function" ? session.onChange(function (change) {
      if (!root.isConnected) { st.live = false; return; }
      if (st.live && st.relocked && change && change.unlocked) { st.relocked = false; load(); }
    }) : null;

    function part(name) { return root.querySelector('[data-review-part="' + name + '"]'); }
    function field(name) { return root.querySelector('[data-review-field="' + name + '"]'); }
    function chosenDecision() {
      if (isResume(st.dossier || {})) return DECISION_RESTART;
      var picked = root.querySelector('[data-review-field="decision"]:checked');
      return picked ? picked.value : "";
    }
    function initialsBox() { return panelInitials || field("initials"); }
    // The initials refusal names where the field is: the panel's own field, or the review's.
    function initialsLine() { return panelInitials ? LINES.initialsPanel : LINES.initials; }
    function confirmOpen() { var c = part("confirm"); return !!(c && !c.hidden); }

    // The decision line: one sentence (kind "error" or "done" colours it) and
    // the one action that resolves it beside it: Reload, or Close once recorded.
    function setLine(text, kind, withReload) {
      var line = part("line");
      var actions = part("line-actions");
      if (!line || !actions) return;
      line.className = "result" + (kind === "error" ? " result-error" : "");
      line.textContent = text;
      actions.innerHTML = (withReload ? '<button type="button" class="btn btn-mini" data-review-action="reload" title="' +
        esc(TIPS.reload) + '">Reload</button>' : "") +
        (kind === "done" ? '<button type="button" class="btn btn-mini" data-review-action="close">Close</button>' : "");
    }

    // Keep the typed text for this page load; a recorded decision has no draft left to keep.
    function saveDraft() {
      if (st.recorded || !field("report") || !field("instructions")) return;
      var box = field("start_upload");
      drafts[key] = { report: field("report").value, instructions: field("instructions").value,
                      decision: chosenDecision(), startUpload: !!(box && box.checked) };
    }

    function noteAnswer(answer) {
      if (session && typeof session.noteResponse === "function") session.noteResponse(answer.status, answer.body);
    }

    function showRelock() {
      saveDraft();
      st.relocked = true;
      body.innerHTML = '<p class="result result-error vt-review-relock">' + esc(LINES.relock) + "</p>" +
        '<div class="actions"><button type="button" class="btn btn-mini btn-primary" data-review-action="unlock" title="' +
        esc(TIPS.unlock) + '">Unlock</button></div>';
    }

    function showError(text) {
      body.innerHTML = '<p class="result result-error" data-review-part="error">' + esc(text) + "</p>" +
        '<div class="actions"><button type="button" class="btn btn-mini" data-review-action="reload" title="' +
        esc(TIPS.reload) + '">Try again</button></div>';
    }

    function readDossier() {
      return requestJSON(projectUrl(o.jobId, o.psx, "/dossier")).then(function (answer) {
        if (isLocked(answer.status, answer.body)) { noteAnswer(answer); return { locked: true }; }
        if (answer.status !== HTTP_OK) {
          return { error: refusalText(answer, "VICARIUS could not read the review record of " + o.psx + ": ") };
        }
        var d = isObject(answer.body.dossier) ? answer.body.dossier : answer.body;
        return { dossier: d };
      });
    }

    // Program A's intervals of the transect, for the time in editing and in
    // review; a refusal or a failure leaves both unknown and never blocks the review.
    function readIntervals() {
      var url = base + DETAIL_ROUTE + encodeURIComponent(o.jobId) + "&psx=" + encodeURIComponent(o.psx);
      return requestJSON(url).then(function (answer) {
        if (isLocked(answer.status, answer.body)) { noteAnswer(answer); return { locked: true }; }
        return { intervals: answer.status === HTTP_OK && isObject(answer.body.intervals) ? answer.body.intervals : null };
      });
    }

    // Read the record and the intervals, then draw the review; flash is a
    // result line to show once drawn (the answer of a Back to admin review).
    function load(flash) {
      body.innerHTML = '<p class="vt-review-dim">' + esc(LINES.loading) + "</p>";
      return Promise.all([readDossier(), readIntervals()]).then(function (got) {
        if (!st.live) return;
        var record = got[0];
        if (record.locked || got[1].locked) { showRelock(); return; }
        if (record.error) { showError(record.error); return; }
        st.dossier = record.dossier;
        // A fresh form: nothing recorded, nothing in flight (a Reload or an unlock
        // after a refused or stale decision must leave working controls).
        st.recorded = false;
        st.busy = false;
        st.opening = false;
        st.kept = isResume(record.dossier) ? resumeTexts(record.dossier.notes) : null;
        body.innerHTML = paneHtml(record.dossier, { draft: drafts[key] || {}, ids: ids, initials: rememberedInitials(),
                                                    ownInitials: !panelInitials, shelf: shelf, kept: st.kept,
                                                    times: stateTimes(got[1].intervals, record.dossier.readable_ids) });
        if (flash) setLine(flash, "done");
      });
    }

    function setBusy(busy) {
      st.busy = busy;
      var record = root.querySelector('[data-review-action="record"]');
      var confirm = root.querySelector('[data-review-action="restart-confirm"]');
      var back = root.querySelector('[data-review-action="back"]');
      if (record) record.disabled = busy || st.recorded || !chosenDecision() || confirmOpen();
      if (confirm) confirm.disabled = busy;
      if (back) back.disabled = busy || st.recorded;
    }

    // The form to check and post; a resume whose first texts are kept posts those texts.
    function formFor(decision) {
      var box = field("start_upload");
      var initials = initialsBox();
      var texts = st.kept && isResume(st.dossier || {}) ? st.kept
        : { report: field("report") ? field("report").value : "", instructions: field("instructions") ? field("instructions").value : "" };
      return { decision: decision, report: texts.report, instructions: texts.instructions,
               initials: initials ? initials.value : "", startUpload: decision === "upload" && !!(box && box.checked) };
    }

    // Record one decision: re-read the record, post nothing if it changed,
    // else post the review route and report the answer.
    function record(checked) {
      var shown = st.dossier;
      setBusy(true);
      setLine(LINES.recording);
      return readDossier().then(function (got) {
        if (!st.live) return null;
        if (got.locked) { showRelock(); return null; }
        if (got.error) { setBusy(false); setLine(got.error, "error", true); return null; }
        if (got.dossier.round !== shown.round || got.dossier.state !== shown.state) {
          setBusy(false);
          setLine(LINES.stale, "error", true);
          return null;
        }
        return postJSON(projectUrl(o.jobId, o.psx, "/review"), checked.body);
      }).then(function (answer) {
        if (!answer || !st.live) return;
        if (isLocked(answer.status, answer.body)) { noteAnswer(answer); showRelock(); return; }
        if (answer.status !== HTTP_OK && answer.status !== HTTP_ACCEPTED) {
          setBusy(false);
          setLine(refusalText(answer, "VICARIUS could not record the decision: "), "error",
                  answer.status === HTTP_CONFLICT || !!answer.timedOut);
          return;
        }
        finish(checked.body, answer.body.upload_id || null, answer.body.upload_error || "");
      }).then(null, function (e) {
        setBusy(false);
        setLine("VICARIUS could not record the decision: " + ((e && e.message) || "an unknown error") + ".", "error");
      });
    }

    function rememberInitials(value) {
      writeSession(SS_REVIEW_INITIALS, value);
      if (panelInitials) panelInitials.value = value;
    }

    // Hand an upload the decision started to the panel's upload window, cache-busted with
    // the manual edit module's asset version (the component is that module's file).
    function openUpload(uploadId, said) {
      var vt = window.VoyagerTools || {};
      if (typeof vt.openUploadWindow !== "function") return;
      vt.openUploadWindow(uploadId, { version: root.getAttribute("data-manual-edit-version") ||
                                               root.getAttribute("data-vt-version") || "",
                                      readInitials: function () { var b = initialsBox(); return b ? b.value.trim().toUpperCase() : ""; } });
      setLine(said, "done");
    }

    // After a recorded decision: the result line (with the reason when Start
    // the upload now could not start one), the state chip moved to where the
    // transect went, the form and Open in Metashape closed, the host told, an
    // upload handed on.
    function finish(sent, uploadId, uploadError) {
      st.recorded = true;
      delete drafts[key];
      rememberInitials(sent.initials);
      setBusy(false);
      root.querySelectorAll("[data-review-field], [data-review-action=\"open\"], [data-review-action=\"record\"]")
        .forEach(function (f) { f.disabled = true; });
      var next = uploadId ? STATE_UPLOADING : STATE_AFTER[sent.decision];
      var chipBox = part("state");
      if (chipBox && next) chipBox.innerHTML = stateHtml(STATE_ROLES[next], STATE_WORDS[next]);
      var said = resultLine({ decision: sent.decision, by: sent.initials, at: astStamp(new Date()) }) +
        (uploadError ? " " + String(uploadError) : "");
      setLine(said, "done");
      var result = { decision: sent.decision, job_id: o.jobId, psx: o.psx, upload_id: uploadId };
      if (typeof o.onDecision === "function") o.onDecision(result);
      if (uploadId) openUpload(uploadId, said);
    }

    // Read the initials the open, the decision and the move back are signed
    // with; a miss shows the refusal on `line` and focuses the field.
    function checkedInitials(lineEl) {
      var box = initialsBox();
      var value = String(box ? box.value : "").trim().toUpperCase();
      if (INITIALS_RE.test(value)) return value;
      if (lineEl) { lineEl.className = lineEl.className.replace(/ is-error/g, "") + " is-error"; lineEl.textContent = initialsLine(); }
      if (box && typeof box.focus === "function") box.focus();
      return "";
    }

    function decide() {
      if (st.busy || st.recorded || !st.dossier) return;
      var decision = chosenDecision();
      var confirm = part("confirm");
      if (confirm) confirm.hidden = true;
      var checked = readyToRecord(formFor(decision));
      if (!checked.ok) {
        var initialsMiss = checked.line === LINES.initials;
        setLine(initialsMiss ? initialsLine() : checked.line, "error");
        if (initialsMiss) checkedInitials(null);
        return;
      }
      if (decision === DECISION_RESTART) {
        if (confirm) confirm.hidden = false;
        setLine("");
        setBusy(st.busy);
        return;
      }
      record(checked);
    }

    // Open in Metashape through B's admin open route, signed with the
    // initials; the button stays disabled until the answer, so a double press posts once.
    function openInMetashape() {
      if (st.opening) return;
      var line = part("open-line");
      line.className = "count vt-review-open-line";
      var initials = checkedInitials(line);
      if (!initials) return;
      var button = root.querySelector('[data-review-action="open"]');
      st.opening = true;
      if (button) button.disabled = true;
      line.textContent = LINES.opening;
      postJSON(projectUrl(o.jobId, o.psx, "/open"), { initials: initials }).then(function (answer) {
        if (!st.live) return;
        st.opening = false;
        if (button) button.disabled = st.recorded;
        if (isLocked(answer.status, answer.body)) { noteAnswer(answer); showRelock(); return; }
        if (answer.status === HTTP_OK) {
          rememberInitials(initials);
          line.textContent = "Metashape opened " + o.psx + (answer.body.pid ? " (process " + answer.body.pid + ")" : "") + ".";
          return;
        }
        line.className = "count vt-review-open-line is-error";
        line.textContent = refusalText(answer, "VICARIUS could not open Metashape: ");
      });
    }

    // Move a transect marked for upload back to admin review (ruling O3), then read the record again.
    function backToReview() {
      if (st.busy || st.recorded) return;
      var initials = checkedInitials(null);
      if (!initials) { setLine(initialsLine(), "error"); return; }
      setBusy(true);
      setLine(LINES.moving);
      postJSON(projectUrl(o.jobId, o.psx, "/back-to-review"), { initials: initials }).then(function (answer) {
        if (!st.live) return;
        if (isLocked(answer.status, answer.body)) { noteAnswer(answer); showRelock(); return; }
        if (answer.status !== HTTP_OK) {
          setBusy(false);
          setLine(refusalText(answer, "VICARIUS could not move the transect back: "), "error", true);
          return;
        }
        rememberInitials(initials);
        if (typeof o.onDecision === "function") {
          o.onDecision({ decision: DECISION_BACK, job_id: o.jobId, psx: o.psx, upload_id: null });
        }
        load(resultLine({ decision: DECISION_BACK, by: initials, at: astStamp(new Date()) }));
      });
    }

    // A radio change shows the rows that decision needs and enables Record decision.
    function onChange(ev) {
      var target = ev.target;
      if (!target || target.getAttribute("data-review-field") !== "decision" || !root.contains(target)) return;
      var decision = target.value;
      var startRow = root.querySelector('[data-review-row="start_upload"]');
      var instructionsRow = root.querySelector('[data-review-row="instructions"]');
      if (startRow) startRow.hidden = decision !== "upload";
      if (instructionsRow) instructionsRow.hidden = decision === "upload";
      if (decision !== "upload" && field("start_upload")) field("start_upload").checked = false;
      var confirm = part("confirm");
      if (confirm) confirm.hidden = true;
      setBusy(st.busy);
    }

    function onClick(ev) {
      var target = ev.target && ev.target.closest ? ev.target.closest("[data-review-action]") : null;
      if (!target || !root.contains(target) || target.disabled) return;
      var action = target.getAttribute("data-review-action");
      if (action === "open") openInMetashape();
      else if (action === "record") decide();
      else if (action === "back") backToReview();
      else if (action === "restart-cancel") { part("confirm").hidden = true; setBusy(st.busy); }
      else if (action === "restart-confirm") {
        var checked = readyToRecord(formFor(DECISION_RESTART));
        if (!checked.ok) { setLine(checked.line === LINES.initials ? initialsLine() : checked.line, "error"); return; }
        part("confirm").hidden = true;
        record(checked);
      }
      else if (action === "reload") { saveDraft(); load(); }
      else if (action === "unlock") {
        if (session && typeof session.promptUnlock === "function") session.promptUnlock();
      }
      else if (action === "close" && typeof o.onClose === "function") o.onClose();
    }

    root.addEventListener("click", onClick);
    root.addEventListener("change", onChange);
    load();
    return {
      unmount: function () {
        st.live = false;
        saveDraft();
        root.removeEventListener("click", onClick);
        root.removeEventListener("change", onChange);
        if (typeof unsubscribe === "function") unsubscribe();
      },
    };
  }

  /* ---- the Review queue pane ---------------------------------------------------- */

  function nameHtml(item) {
    var site = item.site_name ? '<span class="vt-site">' + esc(item.site_name) + "</span> " + chip(item.site)
      : '<span title="' + esc("The site table has no full name for " + item.site + ".") + '">' + chip(item.site) + "</span>";
    return site + " " + esc(item.transect) + " " + chip(item.psx);
  }

  function reviewButtonHtml(list, index) {
    return '<button type="button" class="btn btn-mini" data-vt-review-open="' + list + ":" + index + '" title="' +
      esc(TIPS.queueOpen) + '">Review</button>';
  }

  function projectMeta(item, facts) {
    return '<span class="row-meta"><span class="count">project ' + chip(item.job_id) + "</span>" +
      facts.filter(Boolean).map(function (f) { return '<span class="count">' + esc(f) + "</span>"; }).join("") + "</span>";
  }

  function itemRowHtml(item, index) {
    var checks = [item.failed_checks ? plural(item.failed_checks, "check") + " failed" : "",
                  item.warned_checks ? plural(item.warned_checks, "check") + " warned" : ""].filter(Boolean).join(", ");
    return '<li class="row" data-vt-review-key="' + esc(item.job_id + "/" + item.psx) + '"><span class="row-name">' +
      nameHtml(item) + '</span><span class="row-act">' + reviewButtonHtml("items", index) + "</span>" +
      '<span class="row-state">' + stateHtml(STATE_ROLES.review, roundLabel(item.round) || "admin review") +
      (checks ? '<span class="count">' + esc(checks) + "</span>" : "") + "</span>" +
      projectMeta(item, ["edited by " + (item.editor || "-"), item.confirmed_at ? "confirmed " + astText(item.confirmed_at) : ""]) +
      "</li>";
  }

  function stoppedRowHtml(item, index) {
    return '<li class="row" data-vt-review-key="' + esc(item.job_id + "/" + item.psx) + '"><span class="row-name">' +
      nameHtml(item) + '</span><span class="row-act">' + reviewButtonHtml("stopped", index) + "</span>" +
      '<span class="row-state">' + stateHtml(ROLE_STOPPED, "Stopped at " + (RESTART_STAGE_WORDS[item.stage] ||
        "a stage not recorded")) + "</span>" +
      '<span class="row-meta"><span class="vt-error-text">' + esc(item.error) + "</span></span>" +
      projectMeta(item, [roundLabel(item.round), item.at ? "started over " + astText(item.at) : ""]) + "</li>";
  }

  function returnableRowHtml(item, index) {
    return '<li class="row" data-vt-review-key="' + esc(item.job_id + "/" + item.psx) + '"><span class="row-name">' +
      nameHtml(item) + '</span><span class="row-act">' + reviewButtonHtml("returnable", index) + "</span>" +
      '<span class="row-state">' + stateHtml(STATE_ROLES.upload, "Marked for upload") + "</span>" +
      projectMeta(item, [roundLabel(item.round), "edited by " + (item.editor || "-")]) + "</li>";
  }

  function unmatchedHtml(id) {
    return "<li>" + esc(id + " reads review in the registry, but no unfinished manual edit project holds it; " +
      "correct the cell in the atlas table.") + "</li>";
  }

  function emptyRowHtml(text) { return '<li class="row"><span class="row-meta">' + esc(text) + "</span></li>"; }

  function register(key, init) {
    var vt = window.VoyagerTools = window.VoyagerTools || {};
    if (typeof vt.registerPane === "function") vt.registerPane(key, init);
    else (vt._pendingPanes = vt._pendingPanes || []).push([key, init]);
  }

  register("review", function (ctx) {
    var el = {
      meta: ctx.q("review-meta"), items: ctx.q("review-items"), stopped: ctx.q("review-stopped"),
      stoppedWrap: ctx.q("review-stopped-wrap"), returnable: ctx.q("review-returnable"),
      returnableWrap: ctx.q("review-returnable-wrap"), unmatched: ctx.q("review-unmatched"),
      area: ctx.q("review-area"), result: ctx.q("review-result")
    };
    var st = { lists: { items: [], stopped: [], returnable: [] }, timer: null, handle: null, open: null, reopen: null,
               opening: false };

    function selectRow(key) {
      Array.prototype.forEach.call(ctx.pane.querySelectorAll("[data-vt-review-key]"), function (row) {
        if (row.getAttribute("data-vt-review-key") === key) row.setAttribute("aria-current", "true");
        else row.removeAttribute("aria-current");
      });
    }

    function render(payload) {
      st.lists = { items: listOf(payload.items), stopped: listOf(payload.stopped_restarts),
                   returnable: listOf(payload.marked_for_upload) };
      el.meta.textContent = queueMeta(payload);
      el.items.innerHTML = st.lists.items.map(itemRowHtml).join("") || emptyRowHtml(LINES.noneInReview);
      el.stopped.innerHTML = st.lists.stopped.map(stoppedRowHtml).join("");
      el.stoppedWrap.hidden = st.lists.stopped.length === 0;
      el.returnable.innerHTML = st.lists.returnable.map(returnableRowHtml).join("");
      el.returnableWrap.hidden = st.lists.returnable.length === 0;
      var unmatched = Array.isArray(payload.unmatched) ? payload.unmatched.filter(function (id) { return typeof id === "string"; }) : [];
      el.unmatched.innerHTML = unmatched.map(unmatchedHtml).join("");
      el.unmatched.hidden = unmatched.length === 0;
      if (st.open) selectRow(st.open.job_id + "/" + st.open.psx);
    }

    function load() {
      schedule();
      return ctx.api(QUEUE_ROUTE).then(function (res) {
        if (res.status === HTTP_UNAVAILABLE && res.data && res.data.not_mounted) {
          el.meta.textContent = "";
          ctx.setResult(el.result, esc(LINES.notMounted), "error");
          return;
        }
        if (!res.ok) { if (!ctx.isRelocked()) ctx.fail(el.result, res); return; }
        ctx.setResult(el.result, "");
        render(res.data || {});
        if (st.reopen && !st.handle) { var again = st.reopen; st.reopen = null; openReview(again); }
      });
    }

    // schedule(): read the queue again every QUEUE_POLL_MS while the pane is
    // shown; the open review is left alone. Stopped at a relock or unmount.
    function schedule() {
      if (st.timer !== null) return;
      st.timer = setInterval(function () { if (ctx.isShown() && !st.opening) load(); }, QUEUE_POLL_MS);
    }

    function closeReview() {
      if (st.handle) { st.handle.unmount(); st.handle = null; }
      st.open = null;
      el.area.innerHTML = "";
      el.area.hidden = true;
      selectRow("");
    }

    // A relock or an unmount: stop the poll and the open review (its typed
    // text stays in drafts), and reopen it when the pane loads again.
    ctx.onStop(function () {
      if (st.timer !== null) { clearInterval(st.timer); st.timer = null; }
      if (st.handle) { st.reopen = st.open; st.handle.unmount(); st.handle = null; }
    });

    // The area's heading and Close; the fragment's own head names the transect.
    function areaHead(item) {
      return '<div class="vt-list-head vt-review-area-head"><h3 class="vt-list-title" data-vt-review-of="' +
        esc(transectWords(item)) + '">Admin review</h3>' +
        '<button type="button" class="btn btn-mini" data-vt="review-close">Close</button></div>';
    }

    function openReview(item) {
      if (st.opening) return;
      st.opening = true;
      if (st.handle) { st.handle.unmount(); st.handle = null; }
      st.open = item;
      selectRow(item.job_id + "/" + item.psx);
      el.area.hidden = false;
      el.area.innerHTML = areaHead(item) + '<p class="vt-review-dim">' + esc(LINES.loading) + "</p>";
      ctx.api(FRAGMENT_ROUTE + encodeURIComponent(item.job_id) + "&psx=" + encodeURIComponent(item.psx)).then(function (res) {
        st.opening = false;
        if (st.open !== item) return;
        if (!res.ok || !res.data || typeof res.data.html !== "string") {
          el.area.innerHTML = areaHead(item);
          if (!ctx.isRelocked()) ctx.fail(el.result, res);
          return;
        }
        el.area.innerHTML = areaHead(item) + res.data.html;
        var section = el.area.querySelector(".vt-review");
        if (!section) return;
        st.handle = mountReview(section, { jobId: item.job_id, psx: item.psx,
                                           initialsInput: ctx.root.querySelector("#vt-initials"),
                                           onDecision: function () { load(); }, onClose: closeReview });
        if (typeof el.area.scrollIntoView === "function") el.area.scrollIntoView({ block: "start" });
      });
    }

    ctx.pane.addEventListener("click", function (e) {
      var target = e.target && e.target.closest ? e.target.closest("[data-vt-review-open], [data-vt=\"review-close\"]") : null;
      if (!target || !ctx.pane.contains(target)) return;
      if (target.getAttribute("data-vt") === "review-close") { closeReview(); return; }
      var ref = String(target.getAttribute("data-vt-review-open") || "").split(":");
      var item = (st.lists[ref[0]] || [])[Number(ref[1])];
      if (item) openReview(item);
    });
    ctx.q("review-refresh").addEventListener("click", load);
    return load;
  });

  forgetOldInitials();
  window.VoyagerTools = Object.assign(window.VoyagerTools || {}, { mountReview: mountReview });
})();
