/* static/vt_settings.js - the settings, desktop and history panes of the
 * Atlas control panel (program C spec sections 3.3, 3.4, 4.8, 4.9 and 5.3),
 * registered on the pane seam of static/voyager_tools.js.
 *
 * Four settings panes write through the desktop's one write path per store
 * (vicarius_ui_os/settings_views.py): Manual edit settings and Shelf copy
 * (/api/settings/manual-edit, /api/settings/shelf), Voyager runs
 * (/api/settings/voyager) and Space floors (/api/settings/floors). Each
 * renders its fields from the GET payload {store, fields, values, saved_at,
 * saved_by, drift}, enables its one primary button only when a value
 * differs, asks for a confirmation that names the old and new values when a
 * confirmed field changes (with a reason typed first), posts only the
 * changed keys with the saved_at it read, and shows one result line. A drift
 * line names a setting whose file changed outside the write path, with
 * Record as found. A save that gets no answer (the 30 s timeout) may still
 * have happened, so its button stays disabled until the pane reads the
 * stored values again. The GPU ladder, Module locks, Module details and
 * Appearance panes are read only (the last three open the place that edits
 * them today, until their stores gain a settings history write path); the
 * Settings history pane lists settings_history.csv newest first with
 * filters and Copy as CSV.
 *
 * The pure helpers (fieldsHtml, parseValue, changedKeys, reasonReady,
 * cleanInitials, historyCsv, valueWords, valueClass, shortProcess,
 * metaLine, driftLines) are exported
 * to node through module.exports for tests/test_frontend_pins.py; in a
 * browser the IIFE registers the panes, queueing them on
 * window.VoyagerTools._pendingPanes when this script loads before the seam.
 */
(function () {
  "use strict";

  var INITIALS_RE = /^[A-Z]{2,4}$/;
  var NUMBER_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;
  var INTEGER_RE = /^[+-]?\d+$/;
  var COLOR_RE = /^#[0-9a-fA-F]{6}$/;
  var FORMULA_START_RE = /^[=+\-@]/;
  var HISTORY_LIMIT = 200;
  var SHORT_VALUE_CHARS = 16;  // a history value this short never breaks inside a word
  var KEY_FILTER_DEBOUNCE_MS = 300;
  var DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
  var SCRIPT_RE = /\.(py|sh|R|js)$/;
  var HISTORY_COLUMNS = ["at", "by", "store", "key", "old", "new", "reason"];
  var LS_HISTORY_FILTERS = "vt.history.filters";
  var MODULE_MENU_KEY = "__modules";
  var MODULE_MENU_URL = "/modules";
  var ADMIN_CHIP_ID = "tb-admin";
  var HTTP_NOT_FOUND = 404;
  var STORE_LABELS = {
    manual_edit: "Manual edit settings", voyager_config: "Voyager runs", driver_nas: "Space floors",
    desktop_prefs: "Appearance and icon layout", module_locks: "Module locks", module_meta: "Module details",
    voyagerparams: "Parameter defaults"
  };
  var KIND_WORDS = { model: "model run", editor: "person at work, never closed by VICARIUS", human: "person at work",
                     vesper: "language model" };
  var SKIN_WORDS = { fun: "Pink", pro: "Business" };
  var LINE_NOT_SERVED = "The settings are served by the VICARIUS desktop on port 5090; open the Atlas control panel there to change them.";
  var LINE_NO_SAVE = "No save recorded in the settings history yet.";
  var LINE_NOTHING = "Nothing changed.";
  var LINE_DRIVER_OFF = "The driver is not running; its batches read the new floors when it starts.";
  var LINE_RESTART_CHIP = "Driver batches use the new floors after the driver restarts";
  var LINE_NO_EDITOR_TIER = "The ladder file does not list the manual edit window tier yet.";
  var LINE_DRIFT_REASON = "Type a reason before recording the change as found.";
  var LINE_ARCHIVE = "Archive the Shelf copy: before each upload the Shelf folder is renamed to <folder>_voy1_archive, or a name with the edit round when an earlier archive holds that one, and kept.";
  var TIP_RELOAD = "Read the stored values again to see whether the save happened.";
  var LINE_BAD_DAY = "Type the first day as YYYY-MM-DD, for example 2026-09-27.";
  var LINE_SHELF_FIELD_TIP = "Pick whether an upload keeps the pre-edit Shelf folder or replaces it; a retry keeps the mode its first attempt used. VICARIUS asks for a reason and a confirmation for either change.";

  /* ---- pure helpers (exported to node) ------------------------------------ */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // stampWords: "2026-09-27T10:12:03-04:00" as "2026-09-27 10:12 AST".
  function stampWords(iso) {
    var t = String(iso || "");
    return t.length < 16 ? t : t.slice(0, 10) + " " + t.slice(11, 16) + " AST";
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // stateHtml(role, words): a state as the registry windows show one, the
  // role's 12 px swatch then the words (matrix window design, section 2).
  function stateHtml(role, words) {
    return '<span class="vt-state"><span class="chip ' + esc(role) + '" aria-hidden="true"></span>' + esc(words) + "</span>";
  }

  // controlHtml(id, field, value): the input for one field, keyed by
  // data-vt-key so readFields finds it again.
  function controlHtml(id, field, value) {
    var key = ' data-vt-key="' + esc(field.key) + '"';
    var kind = field.type;
    if (kind === "number" || kind === "integer") {
      return '<input type="text" id="' + id + '" class="vt-field w-num"' + key +
        ' inputmode="' + (kind === "integer" ? "numeric" : "decimal") + '" autocomplete="off" spellcheck="false" value="' +
        esc(value == null ? "" : String(value)) + '">' + (field.unit ? ' <span class="count">' + esc(field.unit) + "</span>" : "");
    }
    if (kind === "boolean") {
      return '<label class="opt"><input type="checkbox" id="' + id + '"' + key + (value === true ? " checked" : "") +
        "> <span data-vt-onoff>" + onOffWord(value === true) + "</span></label>";
    }
    if (kind === "color") {
      return '<input type="color" id="' + id + '" class="vt-color"' + key + ' value="' + esc(COLOR_RE.test(value || "") ? value : "#000000") + '">';
    }
    if (kind === "choice") {
      // Each option carries its own "?" glyph; a disabled option's glyph
      // says why it cannot be chosen.
      return '<div class="opts">' + (field.options || []).map(function (opt) {
        var disabled = opt.disabled === true;
        var tip = disabled ? (opt.disabled_reason || opt.tip) : opt.tip;
        var glyph = tip ? '<button type="button" class="tip" aria-label="About ' + esc(opt.label) + '" title="' + esc(tip) + '"><span>?</span></button>' : "";
        return '<span class="vt-opt-row"><label class="opt' + (disabled ? " is-disabled" : "") + '"><input type="radio" name="' + id + '"' + key +
          ' value="' + esc(JSON.stringify(opt.value)) + '"' + (opt.value === value ? " checked" : "") + (disabled ? " disabled" : "") + "> " +
          esc(opt.label) + "</label>" + glyph + "</span>";
      }).join("") + "</div>";
    }
    return '<input type="text" id="' + id + '" class="vt-field w-path"' + key + ' maxlength="4096" autocomplete="off" spellcheck="false" value="' +
      esc(value == null ? "" : String(value)) + '">';
  }

  function onOffWord(on) { return on ? "On" : "Off"; }

  // rowHtml(prefix, field, index, vals): one definition-list row, the label
  // and its "?" glyph in one inline run at the left (so the glyph follows
  // the last word of a wrapped label), the control at the right.
  function rowHtml(prefix, field, index, vals) {
    var id = "vt-" + esc(prefix) + "-f" + index;
    var glyph = '<button type="button" class="tip" aria-label="About ' + esc(field.label) + '" title="' + esc(field.tip) + '"><span>?</span></button>';
    var head = field.type === "choice"
      ? '<dt><span class="vt-dt-flow"><span id="' + id + '-l">' + esc(field.label) + "</span>" + glyph + '</span></dt><dd role="radiogroup" aria-labelledby="' + id + '-l">'
      : '<dt><span class="vt-dt-flow"><label for="' + id + '">' + esc(field.label) + "</label>" + glyph + "</span></dt><dd>";
    return '<div class="vt-setting" data-vt-row="' + esc(field.key) + '">' + head + controlHtml(id, field, vals[field.key]) + "</dd></div>";
  }

  // fieldsHtml(prefix, fields, values): the rows as definition lists, one
  // per run of fields sharing a group, each group under its sub-heading.
  function fieldsHtml(prefix, fields, values) {
    var vals = values || {};
    var groups = [];
    (fields || []).forEach(function (field, index) {
      var name = typeof field.group === "string" ? field.group : "";
      var last = groups[groups.length - 1];
      if (!last || last.name !== name) { last = { name: name, rows: [] }; groups.push(last); }
      last.rows.push(rowHtml(prefix, field, index, vals));
    });
    return groups.map(function (group) {
      return (group.name ? '<h3 class="vt-field-group">' + esc(group.name) + "</h3>" : "") + '<dl class="choices">' + group.rows.join("") + "</dl>";
    }).join("");
  }

  // parseValue(field, raw): {ok: true, value} or {ok: false, line}; the page
  // checks only the type, the desktop checks ranges and answers one line.
  function parseValue(field, raw) {
    var label = field.label;
    var text = typeof raw === "string" ? raw.trim() : raw;
    if (field.type === "number") {
      if (typeof text !== "string" || !NUMBER_RE.test(text) || !isFinite(Number(text))) return { ok: false, line: label + " must be a number." };
      return { ok: true, value: Number(text) };
    }
    if (field.type === "integer") {
      if (typeof text !== "string" || !INTEGER_RE.test(text)) return { ok: false, line: label + " must be a whole number." };
      return { ok: true, value: parseInt(text, 10) };
    }
    if (field.type === "boolean") {
      return typeof raw === "boolean" ? { ok: true, value: raw } : { ok: false, line: label + " must be ticked or unticked." };
    }
    if (field.type === "choice") {
      var parsed;
      try { parsed = JSON.parse(String(raw)); } catch (e) { return { ok: false, line: label + " must be one of the offered choices." }; }
      var known = (field.options || []).some(function (opt) { return opt.value === parsed; });
      return known ? { ok: true, value: parsed } : { ok: false, line: label + " must be one of the offered choices." };
    }
    if (field.type === "color") {
      return typeof text === "string" && COLOR_RE.test(text) ? { ok: true, value: text.toLowerCase() } : { ok: false, line: label + " must be a colour written #rrggbb." };
    }
    if (typeof text !== "string" || (field.type === "path" && !text)) return { ok: false, line: label + " must be a path." };
    return { ok: true, value: text };
  }

  function sameValue(a, b) {
    if (typeof a === "number" && typeof b === "number") return a === b;
    return a === b;
  }

  // changedKeys(fields, before, after): the keys, in field order, whose
  // value in after differs from before (numbers by value).
  function changedKeys(fields, before, after) {
    return (fields || []).filter(function (field) {
      return Object.prototype.hasOwnProperty.call(after || {}, field.key) && !sameValue((before || {})[field.key], after[field.key]);
    }).map(function (field) { return field.key; });
  }

  // reasonReady(text, required): a required reason needs one character that is not a space.
  function reasonReady(text, required) {
    if (!required) return true;
    return typeof text === "string" && text.trim().length > 0;
  }

  // cleanInitials(text): trimmed upper-case initials of two to four letters, else null.
  function cleanInitials(text) {
    if (typeof text !== "string") return null;
    var clean = text.trim().toUpperCase();
    return INITIALS_RE.test(clean) ? clean : null;
  }

  function csvCell(value) {
    var text = String(value == null ? "" : value);
    if (FORMULA_START_RE.test(text) && !NUMBER_RE.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }

  // historyCsv(rows): the history rows as CSV, every cell quoted, a cell that
  // would open a spreadsheet formula prefixed with an apostrophe.
  function historyCsv(rows) {
    var lines = [HISTORY_COLUMNS.map(csvCell).join(",")];
    (rows || []).forEach(function (row) {
      lines.push(HISTORY_COLUMNS.map(function (col) { return csvCell(row[col]); }).join(","));
    });
    return lines.join("\r\n");
  }

  // valueWords(jsonText): how a history or drift value reads on screen.
  function valueWords(jsonText) {
    if (jsonText == null) return "none";
    var parsed;
    try { parsed = JSON.parse(jsonText); } catch (e) { return String(jsonText); }
    if (parsed === null) return "none";
    if (typeof parsed === "number") return String(jsonText).trim();
    if (typeof parsed === "string") return parsed;
    if (typeof parsed === "boolean") return parsed ? "true" : "false";
    if (parsed && typeof parsed === "object" && parsed.__digest__) return "a value of " + parsed.chars + " characters (" + parsed.__digest__ + ")";
    return JSON.stringify(parsed);
  }

  // valueClass(jsonText): "is-short" for a history value that fits on one
  // line unbroken, "is-long" for a path or list that may wrap anywhere.
  function valueClass(jsonText) {
    return valueWords(jsonText).length <= SHORT_VALUE_CHARS ? "is-short" : "is-long";
  }

  function baseName(path) {
    var parts = String(path).split("/");
    return parts[parts.length - 1] || String(path);
  }

  // shortProcess(command): a process command line as "<program> <script>",
  // for example "python train.py"; the full command goes in the row's title.
  function shortProcess(command) {
    var tokens = String(command == null ? "" : command).trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) return "unknown";
    var args = tokens.slice(1).filter(function (t) { return t.charAt(0) !== "-"; });
    var script = args.filter(function (t) { return SCRIPT_RE.test(t); })[0] || args[0];
    return baseName(tokens[0]) + (script ? " " + baseName(script) : "");
  }

  // metaLine(savedAt, savedBy): the line under a settings pane's heading.
  function metaLine(savedAt, savedBy) {
    if (!savedAt) return LINE_NO_SAVE;
    return "Saved " + stampWords(savedAt) + (savedBy ? " by " + savedBy : "");
  }

  function labelOf(fields, key) {
    var hit = (fields || []).filter(function (f) { return f.key === key; })[0];
    return hit ? (hit.history_label || hit.label) : key;
  }

  // driftLines(fields, drift): one sentence per key whose file value is not
  // the settings history's last value.
  function driftLines(fields, drift) {
    return (drift || []).map(function (item) {
      var label = labelOf(fields, item.key);
      if (item.history == null) return label + " is " + valueWords(item.disk) + " on disk and has no line in the settings history.";
      return label + " is " + valueWords(item.disk) + " on disk and " + valueWords(item.history) +
        " in the settings history; that change has no history line.";
    });
  }

  // formatValue(field, value): a value as a confirmation names it.
  function formatValue(field, value) {
    if (field.type === "boolean") return value ? "on" : "off";
    if (field.type === "choice") {
      var hit = (field.options || []).filter(function (opt) { return opt.value === value; })[0];
      return hit ? hit.label : String(value);
    }
    return value == null ? "none" : String(value);
  }

  var helpers = {
    fieldsHtml: fieldsHtml, parseValue: parseValue, changedKeys: changedKeys, reasonReady: reasonReady,
    cleanInitials: cleanInitials, historyCsv: historyCsv, valueWords: valueWords, valueClass: valueClass,
    shortProcess: shortProcess, metaLine: metaLine, driftLines: driftLines, formatValue: formatValue
  };
  if (typeof module === "object" && module && module.exports) module.exports = helpers;
  if (typeof window === "undefined") return;

  /* ---- registration ----------------------------------------------------------- */

  function register(key, init) {
    var vt = window.VoyagerTools = window.VoyagerTools || {};
    if (typeof vt.registerPane === "function") vt.registerPane(key, init);
    else (vt._pendingPanes = vt._pendingPanes || []).push([key, init]);
  }

  function findKey(container, key) {
    var sel = '[data-vt-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]';
    return Array.prototype.slice.call(container.querySelectorAll(sel));
  }

  // readFields(container, fields): the typed values and the refusals, one per bad field.
  function readFields(container, fields) {
    var values = {};
    var errors = [];
    (fields || []).forEach(function (field) {
      var els = findKey(container, field.key);
      if (!els.length) return;
      var raw;
      if (field.type === "choice") {
        var picked = els.filter(function (el) { return el.checked; })[0];
        if (!picked) return;
        raw = picked.value;
      } else if (field.type === "boolean") {
        raw = els[0].checked;
      } else {
        raw = els[0].value;
      }
      var parsed = parseValue(field, raw);
      if (parsed.ok) values[field.key] = parsed.value;
      else errors.push(parsed.line);
    });
    return { values: values, errors: errors };
  }

  /* ---- the four settings panes ------------------------------------------------ */

  // settingsPane(cfg): the initialiser of one settings pane. cfg = {key,
  // route, store, title, okLabel, adjust(fields, payload), extra(ctx,
  // payload), consequences(changed, before, after, payload) -> [lines],
  // danger(after) -> true when the confirmation button takes the danger style}.
  function settingsPane(cfg) {
    return function (ctx) {
      var el = {
        fields: ctx.q(cfg.key + "-fields"), meta: ctx.q(cfg.key + "-meta"), drift: ctx.q(cfg.key + "-drift"),
        reason: ctx.q(cfg.key + "-reason"), save: ctx.q(cfg.key + "-save"), result: ctx.q(cfg.key + "-result")
      };
      // stale: a save got no answer, so it may have happened; the button
      // stays disabled until load() reads the stored values again.
      var st = { payload: null, fields: [], before: {}, busy: false, stale: false };

      function refreshButton() {
        Array.prototype.slice.call(el.fields.querySelectorAll("label.opt")).forEach(function (label) {
          var box = label.querySelector('input[type="checkbox"]');
          var word = label.querySelector("[data-vt-onoff]");
          if (box && word) word.textContent = onOffWord(box.checked);
        });
        if (!st.payload) { el.save.disabled = true; return; }
        var read = readFields(el.fields, st.fields);
        var changed = read.errors.length > 0 || changedKeys(st.fields, st.before, read.values).length > 0;
        el.save.disabled = st.busy || st.stale || !changed;
      }

      function renderDrift() {
        var lines = driftLines(st.fields, st.payload.drift);
        if (!lines.length) { el.drift.hidden = true; el.drift.innerHTML = ""; return; }
        el.drift.innerHTML = lines.map(function (line) { return '<p class="vt-drift-line">' + esc(line) + "</p>"; }).join("") +
          '<button type="button" class="btn btn-mini" data-vt-drift-record title="Record the value on disk as found, signed with your initials and the typed reason. VICARIUS writes one settings history line per setting named.">Record as found</button>';
        el.drift.hidden = false;
      }

      function load() {
        return ctx.desktopApi(cfg.route).then(function (res) {
          if (!res.ok) {
            st.payload = null;
            el.fields.innerHTML = "";
            el.meta.textContent = "";
            el.drift.hidden = true;
            el.save.disabled = true;
            if (res.status === HTTP_NOT_FOUND) ctx.setResult(el.result, esc(LINE_NOT_SERVED), "error");
            else if (!ctx.isRelocked()) ctx.fail(el.result, res);
            return;
          }
          st.payload = res.data;
          st.stale = false;
          st.fields = cfg.adjust ? cfg.adjust(res.data.fields || [], res.data) : (res.data.fields || []);
          st.before = Object.assign({}, res.data.values || {});
          el.fields.innerHTML = fieldsHtml(cfg.key, st.fields, st.before);
          el.meta.textContent = metaLine(res.data.saved_at, res.data.saved_by);
          renderDrift();
          if (cfg.extra) cfg.extra(ctx, res.data);
          refreshButton();
        });
      }

      function resultLine(data) {
        var changes = data.changes || [];
        if (!changes.length) return LINE_NOTHING;
        return "Saved " + stampWords(data.saved_at) + (data.saved_by ? " by " + data.saved_by : "") + ": " +
          plural(changes.length, "setting") + " changed." + (data.note ? " " + data.note : "");
      }

      function post(values, who, reason, confirmed) {
        st.busy = true;
        ctx.busy(el.save, true);
        return ctx.desktopApi(cfg.route, { method: "POST", body: { values: values, initials: who, reason: reason,
                                                                  confirm: confirmed, saved_at: st.payload.saved_at } })
          .then(function (res) {
            st.busy = false;
            ctx.busy(el.save, false);
            if (!res.ok && res.status === 0) { showNoAnswer(res); return; }
            if (!res.ok) { ctx.fail(el.result, res); refreshButton(); return; }
            ctx.setResult(el.result, esc(resultLine(res.data)));
            el.reason.value = "";
            return load();
          });
      }

      // showNoAnswer(res): the request timed out or never reached the
      // desktop; the save may still have happened, so the pane refuses
      // another until its values are read again.
      function showNoAnswer(res) {
        st.stale = true;
        el.save.disabled = true;
        ctx.fail(el.result, res);
        el.result.insertAdjacentHTML("beforeend", ' <button type="button" class="btn btn-mini" data-vt-reload title="' +
          esc(TIP_RELOAD) + '">Reload the pane</button>');
      }

      function confirmLines(changed, after) {
        var lines = cfg.consequences ? cfg.consequences(changed, st.before, after, st.payload) : [];
        changed.forEach(function (key) {
          var field = st.fields.filter(function (f) { return f.key === key; })[0];
          if (field && field.confirm) lines.push(field.label + ": " + formatValue(field, st.before[key]) + " to " + formatValue(field, after[key]) + ".");
        });
        return lines;
      }

      function save() {
        if (st.busy || !st.payload) return;
        var read = readFields(el.fields, st.fields);
        if (read.errors.length) { ctx.setResult(el.result, esc(read.errors[0]), "error"); return; }
        var changed = changedKeys(st.fields, st.before, read.values);
        if (!changed.length) { ctx.setResult(el.result, esc(LINE_NOTHING)); refreshButton(); return; }
        var who = ctx.initials(el.result);
        if (!who) return;
        var reason = el.reason.value.trim();
        var confirmed = st.fields.filter(function (f) { return f.confirm && changed.indexOf(f.key) !== -1; });
        if (confirmed.length && !reasonReady(reason, true)) {
          ctx.setResult(el.result, esc("Type a reason before saving a change to " + confirmed[0].label + "."), "error");
          el.reason.focus();
          return;
        }
        var values = {};
        changed.forEach(function (key) { values[key] = read.values[key]; });
        if (!confirmed.length) { post(values, who, reason, false); return; }
        st.busy = true;
        el.save.disabled = true;
        var danger = typeof cfg.danger === "function" && cfg.danger(read.values) === true;
        ctx.confirmDialog({ title: cfg.title, lines: confirmLines(changed, read.values).concat(["Reason: " + reason + "."]),
                            okLabel: cfg.okLabel, primary: !danger })
          .then(function (ok) {
            st.busy = false;
            if (!ok) { refreshButton(); return; }
            post(values, who, reason, true);
          });
      }

      function recordDrift(button) {
        var who = ctx.initials(el.result);
        if (!who) return;
        var reason = el.reason.value.trim();
        if (!reasonReady(reason, true)) { ctx.setResult(el.result, esc(LINE_DRIFT_REASON), "error"); el.reason.focus(); return; }
        ctx.busy(button, true);
        ctx.desktopApi("/api/settings/drift/" + encodeURIComponent(cfg.store), { method: "POST", body: { initials: who, reason: reason } })
          .then(function (res) {
            ctx.busy(button, false);
            if (!res.ok) { ctx.fail(el.result, res); return; }
            var n = (res.data.changes || []).length;
            ctx.setResult(el.result, esc("Recorded " + plural(n, "setting") + " as found by " + who + "."));
            el.reason.value = "";
            load();
          });
      }

      el.fields.addEventListener("input", refreshButton);
      el.fields.addEventListener("change", refreshButton);
      el.save.addEventListener("click", save);
      el.result.addEventListener("click", function (e) {
        if (!e.target.closest("[data-vt-reload]")) return;
        ctx.setResult(el.result, "");
        load();
      });
      el.drift.addEventListener("click", function (e) {
        var button = e.target.closest("[data-vt-drift-record]");
        if (button) recordDrift(button);
      });
      return load;
    };
  }

  register("settings", settingsPane({
    key: "settings", route: "/api/settings/manual-edit", store: "manual_edit",
    title: "Change the manual edit settings", okLabel: "Save manual edit settings"
  }));

  register("shelf", settingsPane({
    key: "shelf", route: "/api/settings/shelf", store: "manual_edit",
    title: "Change the Shelf copy setting", okLabel: "Save the Shelf copy setting",
    adjust: function (fields) {
      return fields.map(function (field) { return Object.assign({}, field, { tip: LINE_SHELF_FIELD_TIP }); });
    },
    extra: function (ctx, payload) {
      var glyph = ctx.q("shelf-glyph");
      var field = (payload.fields || [])[0];
      if (glyph && field && field.tip) glyph.setAttribute("title", field.tip);
    },
    danger: function (after) { return after.archive_shelf_copy === false; },
    consequences: function (changed, before, after, payload) {
      if (changed.indexOf("archive_shelf_copy") === -1) return [];
      if (after.archive_shelf_copy) return [LINE_ARCHIVE];
      var root = payload.shelf_root || "the Shelf";
      return ["Overwrite the Shelf copy: each uploaded folder replaces its Shelf folder under " + root +
              ", files the edit removed are deleted there, and once the bench copy is removed after the verify no pre-edit copy is kept anywhere."];
    }
  }));

  register("runs", settingsPane({
    key: "runs", route: "/api/settings/voyager", store: "voyager_config",
    title: "Change the Voyager run settings", okLabel: "Save Voyager run settings",
    extra: function (ctx, payload) {
      var chip = ctx.q("runs-v2");
      if (chip) chip.hidden = payload.has_voyager2 === true;
    }
  }));

  register("floors", settingsPane({
    key: "floors", route: "/api/settings/floors", store: "driver_nas",
    title: "Change the space floors", okLabel: "Save space floors",
    extra: function (ctx, payload) {
      var line = ctx.q("floors-driver");
      if (!line) return;
      if (!payload.driver_started_at) { line.textContent = LINE_DRIVER_OFF; return; }
      if (payload.restart_needed) { line.innerHTML = stateHtml("role-attention", LINE_RESTART_CHIP); return; }
      line.textContent = "The driver started " + stampWords(payload.driver_started_at) + " and its batches use these floors.";
    }
  }));

  /* ---- read-only panes ---------------------------------------------------------- */

  register("gpu", function (ctx) {
    var tiersEl = ctx.q("gpu-tiers");
    var claimsEl = ctx.q("gpu-claims");
    var resultEl = ctx.q("gpu-result");

    // memoryWords(tier): the free memory a tier needs beside the other kind of
    // work (ruling G1), from the ladder file's own keys; "" when it names none.
    function memoryWords(tier) {
      if (tier.beside_editor_min_free_mb) {
        return "starts beside a manual edit window with " + tier.beside_editor_min_free_mb + " MB free on every card" +
          (tier.beside_editor_min_available_gb ? " and " + tier.beside_editor_min_available_gb + " GB of memory available" : "") +
          (tier.beside_editor_max_pressure_avg10 != null ? ", memory pressure at most " + tier.beside_editor_max_pressure_avg10 + " percent" : "");
      }
      if (tier.min_free_mb) return "opens beside a model run with " + tier.min_free_mb + " MB free on every card";
      return "";
    }

    function tierRowHtml(tier, index) {
      var memory = memoryWords(tier);
      return '<li class="row"><span class="row-name">' + (index + 1) + ". " + esc(tier.label) + " " + ctx.idChip(tier.name) + "</span>" +
        '<span class="row-state"><span class="count">' + esc(KIND_WORDS[tier.kind] || tier.kind) + "</span></span>" +
        '<span class="row-meta"><span class="count">modules: ' + esc((tier.modules || []).join(", ") || "none") +
        "</span> <span class=\"count\">process hints: " + esc((tier.process_hints || []).join(", ") || "none") + "</span>" +
        (memory ? ' <span class="count">' + esc(memory) + "</span>" : "") + "</span></li>";
    }

    function claimRowsHtml(data) {
      var rows = [];
      Object.keys(data.claims || {}).forEach(function (id) {
        var c = data.claims[id] || {};
        rows.push('<li class="row"><span class="row-name">' + esc(c.module || "") + " " + ctx.idChip(c.run_id || id) + "</span>" +
          '<span class="row-state"><span class="count">' + esc(c.tier || "") + "</span></span>" +
          '<span class="row-meta"><span class="count">pid ' + esc(c.pid) + "</span> <span class=\"count\">cards " + esc((c.cards || []).join(", ") || "none yet") +
          "</span> <span class=\"count\">" + esc(c.by ? "by " + c.by : "") + (c.started ? " since " + esc(stampWords(c.started)) : "") + "</span></span></li>");
      });
      Object.keys(data.unclaimed || {}).forEach(function (pid) {
        var u = data.unclaimed[pid] || {};
        rows.push('<li class="row"><span class="row-name vt-process" title="' + esc(u.process || "") + '">' + esc(shortProcess(u.process)) + " " +
          ctx.idChip("pid " + pid) + "</span>" +
          '<span class="row-state">' + stateHtml("role-attention", "unclaimed") + "</span>" +
          '<span class="row-meta"><span class="count">cards ' + esc((u.cards || []).join(", ")) + "</span> <span class=\"count\">" + esc(u.used_mb) + " MB</span></span></li>");
      });
      return rows.join("") || '<li class="row"><span class="row-meta">No claim and no unclaimed process holds a card.</span></li>';
    }

    function load() {
      ctx.desktopApi("/api/settings/gpu-ladder").then(function (res) {
        if (!res.ok) {
          tiersEl.innerHTML = "";
          if (res.status === HTTP_NOT_FOUND) ctx.setResult(resultEl, esc(LINE_NOT_SERVED), "error");
          else if (!ctx.isRelocked()) ctx.fail(resultEl, res);
          return;
        }
        var data = res.data;
        ctx.q("gpu-meta").textContent = "Read from " + data.path + (data.updated ? ", updated " + data.updated : "") + ".";
        var error = ctx.q("gpu-error");
        error.hidden = !data.fallback;
        error.textContent = data.fallback ? "The ladder file at " + data.path + " could not be read (" + data.error +
          "); the desktop decides from its built-in ladder, shown below." : "";
        tiersEl.innerHTML = (data.tiers || []).map(tierRowHtml).join("");
        var editorLine = ctx.q("gpu-editor-line");
        editorLine.hidden = data.has_editor_tier === true;
        editorLine.textContent = data.has_editor_tier ? "" : LINE_NO_EDITOR_TIER;
      });
      ctx.desktopApi("/api/gpu/claims").then(function (res) {
        if (!res.ok) { claimsEl.innerHTML = ""; if (!ctx.isRelocked() && res.status !== HTTP_NOT_FOUND) ctx.fail(resultEl, res); return; }
        claimsEl.innerHTML = claimRowsHtml(res.data);
      });
    }
    ctx.q("gpu-refresh").addEventListener("click", load);
    return load;
  });

  // modulesList(data): the enriched module list from GET /api/modules/enriched.
  function modulesList(data) {
    return ((data && data.modules) || []).slice().sort(function (a, b) {
      return String(a.display_name || a.name).localeCompare(String(b.display_name || b.name));
    });
  }

  register("locks", function (ctx) {
    var body = ctx.q("locks-body");
    var resultEl = ctx.q("locks-result");
    function load() {
      Promise.all([ctx.desktopApi("/api/modules/enriched"), ctx.desktopApi("/api/modules/locks")]).then(function (answers) {
        var bad = answers.filter(function (res) { return !res.ok; })[0];
        if (bad) { body.innerHTML = ""; if (!ctx.isRelocked()) ctx.fail(resultEl, bad); return; }
        var locks = answers[1].data.locks || {};
        var mods = modulesList(answers[0].data);
        var lockedCount = 0;
        body.innerHTML = mods.map(function (m) {
          var entry = locks[m.name] || {};
          var locked = entry.locked === true;
          if (locked) lockedCount += 1;
          var who = locked && entry.by ? "locked by " + entry.by + (entry.ts ? " at " + stampWords(entry.ts) : "") : "";
          return '<li class="row"><span class="row-name">' + esc(m.display_name || m.name) + " " + ctx.idChip(m.name) + "</span>" +
            '<span class="row-state">' + stateHtml(locked ? "role-attention" : "role-empty", locked ? "locked" : "unlocked") + "</span>" +
            (who ? '<span class="row-meta"><span class="count">' + esc(who) + "</span></span>" : "") + "</li>";
        }).join("") || '<li class="row"><span class="row-meta">No module found.</span></li>';
        ctx.q("locks-meta").textContent = plural(mods.length, "module") + ", " + lockedCount + " locked.";
      });
    }
    ctx.q("locks-refresh").addEventListener("click", load);
    return load;
  });

  // openModuleMenu(): the desktop's Module Menu window, or its full page elsewhere.
  function openModuleMenu() {
    var boot = window.VICBOOT;
    var icons = window.VICICONS;
    var item = boot && boot.ICONS ? boot.ICONS.filter(function (i) { return i.key === MODULE_MENU_KEY; })[0] : null;
    if (item && icons && typeof icons.openItem === "function") { icons.openItem(item); return; }
    window.open(MODULE_MENU_URL, "_blank", "noopener");
  }

  register("modules", function (ctx) {
    var body = ctx.q("modules-body");
    var resultEl = ctx.q("modules-result");
    function load() {
      ctx.desktopApi("/api/modules/enriched").then(function (res) {
        if (!res.ok) { body.innerHTML = ""; if (!ctx.isRelocked()) ctx.fail(resultEl, res); return; }
        var mods = modulesList(res.data);
        body.innerHTML = mods.map(function (m) {
          var facts = [["category", m.category], ["stage", m.pipeline_stage], ["status", m.status]].filter(function (f) {
            return !!f[1];
          }).map(function (f) { return '<span class="count">' + esc(f[0] + " " + f[1]) + "</span>"; }).join(" ");
          return '<li class="row"><span class="row-name">' + esc(m.display_name || m.name) + " " + ctx.idChip(m.name) + "</span>" +
            (facts ? '<span class="row-meta">' + facts + "</span>" : "") + "</li>";
        }).join("") || '<li class="row"><span class="row-meta">No module found.</span></li>';
        ctx.q("modules-meta").textContent = plural(mods.length, "module") + ".";
      });
    }
    ctx.q("modules-open").addEventListener("click", openModuleMenu);
    ctx.q("modules-refresh").addEventListener("click", load);
    return load;
  });

  register("appearance", function (ctx) {
    var body = ctx.q("appearance-body");
    var resultEl = ctx.q("appearance-result");
    var open = ctx.q("appearance-open");
    var chip = document.getElementById(ADMIN_CHIP_ID);
    open.hidden = !chip;
    open.addEventListener("click", function () { var c = document.getElementById(ADMIN_CHIP_ID); if (c) c.click(); });
    function row(label, valueHtml) { return "<div><dt>" + esc(label) + "</dt><dd>" + valueHtml + "</dd></div>"; }
    function onOff(v) { return v ? "on" : "off"; }
    function load() {
      ctx.desktopApi("/api/desktop/prefs").then(function (res) {
        if (!res.ok) { body.innerHTML = ""; if (!ctx.isRelocked()) ctx.fail(resultEl, res); return; }
        var a = (res.data && res.data.appearance) || {};
        var colour = COLOR_RE.test(a.bg_color || "") ? a.bg_color : "";
        body.innerHTML =
          row("Skin", esc(SKIN_WORDS[a.skin] || a.skin || "")) +
          row("Background grid", esc(onOff(a.bg_grid))) +
          row("Scanlines", esc(onOff(a.bg_scan))) +
          row("Floating pixels", esc(onOff(a.bg_pixels))) +
          row("Grid size", esc(a.grid_px == null ? "" : a.grid_px + " px")) +
          row("Background colour", (colour ? '<span class="vt-swatch" style="background:' + esc(colour) + '"></span> ' : "") + '<span class="id code">' + esc(a.bg_color || "") + "</span>");
      });
    }
    ctx.q("appearance-refresh").addEventListener("click", load);
    return load;
  });

  /* ---- the settings history ------------------------------------------------------ */

  function readFilters() {
    try { var raw = window.localStorage.getItem(LS_HISTORY_FILTERS); var f = raw ? JSON.parse(raw) : {}; return f && typeof f === "object" ? f : {}; }
    catch (e) { return {}; }
  }
  function writeFilters(f) { try { window.localStorage.setItem(LS_HISTORY_FILTERS, JSON.stringify(f)); } catch (e) { /* private mode */ } }

  register("history", function (ctx) {
    var storeEl = ctx.q("history-store");
    var keyEl = ctx.q("history-key");
    var sinceEl = ctx.q("history-since");
    var clearEl = ctx.q("history-clear");
    var bodyEl = ctx.q("history-body");
    var resultEl = ctx.q("history-result");
    var csvEl = ctx.q("history-csv");
    var rows = [];
    var keyTimer = null;
    var saved = readFilters();
    storeEl.value = STORE_LABELS[saved.store] ? saved.store : "";
    keyEl.value = typeof saved.key === "string" ? saved.key : "";
    sinceEl.value = typeof saved.since === "string" && DAY_RE.test(saved.since) ? saved.since : "";

    function filters() { return { store: storeEl.value, key: keyEl.value.trim(), since: sinceEl.value }; }

    function query(f) {
      var parts = ["limit=" + HISTORY_LIMIT];
      if (f.store) parts.push("store=" + encodeURIComponent(f.store));
      if (f.key) parts.push("key=" + encodeURIComponent(f.key));
      if (f.since) parts.push("since=" + encodeURIComponent(f.since));
      return "/api/settings/history?" + parts.join("&");
    }

    function metaWords(shown, total, filtered) {
      if (!total) return filtered ? "No line matches these filters." : "No settings change recorded yet.";
      if (shown >= total) return plural(total, "line") + ", newest first.";
      return shown + " of " + total + " lines shown, newest first; " + plural(total - shown, "more line") + " match.";
    }

    function render(data) {
      rows = data.rows || [];
      bodyEl.innerHTML = rows.map(function (r) {
        var when = stampWords(r.at);
        var split = when.indexOf(" ");
        return '<tr><td class="vt-mono vt-when"><span class="vt-when-day">' + esc(split > 0 ? when.slice(0, split) : when) + "</span> " +
          '<span class="vt-when-time">' + esc(split > 0 ? when.slice(split + 1) : "") + '</span><span class="count vt-by">by ' + esc(r.by) + "</span></td>" +
          '<td class="vt-setting-cell">' +
          (r.label ? '<span class="vt-setting-label">' + esc(r.label) + "</span> " : "") + '<span class="id code">' + esc(r.key) + "</span>" +
          '<span class="count vt-store">' + esc(STORE_LABELS[r.store] || r.store) + "</span></td>" +
          '<td class="vt-mono vt-value ' + valueClass(r.old) + '">' + esc(valueWords(r.old)) + "</td>" +
          '<td class="vt-mono vt-value ' + valueClass(r["new"]) + '">' + esc(valueWords(r["new"])) + "</td><td>" + esc(r.reason) + "</td></tr>";
      }).join("");
      csvEl.disabled = rows.length === 0;
      var f = filters();
      ctx.q("history-meta").textContent = metaWords(rows.length, data.total || 0, !!(f.store || f.key || f.since));
      var problems = [];
      if (data.skipped) problems.push(plural(data.skipped, "line") + " of the settings history could not be read.");
      (data.conflict_copies || []).forEach(function (path) {
        problems.push("A conflict copy of the settings history exists at " + path + "; it is not merged. Compare it by hand.");
      });
      ctx.q("history-problems").innerHTML = problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("");
    }

    function load() {
      var f = filters();
      if (f.since && !DAY_RE.test(f.since)) { ctx.setResult(resultEl, esc(LINE_BAD_DAY), "error"); sinceEl.focus(); return Promise.resolve(); }
      ctx.setResult(resultEl, "");
      writeFilters(f);
      clearEl.hidden = !(f.store || f.key || f.since);
      return ctx.desktopApi(query(f)).then(function (res) {
        if (!res.ok) {
          bodyEl.innerHTML = "";
          rows = [];
          csvEl.disabled = true;
          if (res.status === HTTP_NOT_FOUND) ctx.setResult(resultEl, esc(LINE_NOT_SERVED), "error");
          else if (!ctx.isRelocked()) ctx.fail(resultEl, res);
          return;
        }
        render(res.data);
      });
    }

    [storeEl, keyEl, sinceEl].forEach(function (input) { input.addEventListener("change", load); });
    keyEl.addEventListener("input", function () {
      if (keyTimer !== null) clearTimeout(keyTimer);
      keyTimer = setTimeout(function () { keyTimer = null; load(); }, KEY_FILTER_DEBOUNCE_MS);
    });
    ctx.onStop(function () { if (keyTimer !== null) { clearTimeout(keyTimer); keyTimer = null; } });
    clearEl.addEventListener("click", function () { storeEl.value = ""; keyEl.value = ""; sinceEl.value = ""; load(); });
    ctx.q("history-refresh").addEventListener("click", load);
    csvEl.addEventListener("click", function () {
      var text = historyCsv(rows);
      var done = function () { ctx.setResult(resultEl, esc("Copied " + plural(rows.length, "line") + " as CSV.")); };
      var failed = function () { ctx.setResult(resultEl, esc("The browser refused the clipboard; nothing was copied."), "error"); };
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") navigator.clipboard.writeText(text).then(done, failed);
      else failed();
    });
    return load;
  });
})();
