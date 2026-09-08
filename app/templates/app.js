/* Upstream SPA v2 — vanilla JS over window.UPSTREAM_DATA. Hash-routed, no external libs. */
(function () {
  "use strict";
  var D = window.UPSTREAM_DATA || {};
  /* app/build.py carries every ticker's FULL daily series, compactly encoded as
     {start, d: day offsets, c: closes} (encode_series_rows). Rebuild the exact
     [date, close] rows once at boot; nothing downstream knows the encoding existed. */
  function decodeSeries(mk) {
    var s = mk && mk.series;
    if (!s || s.rows || !s.rows_c) return;
    var c = s.rows_c, rows = [];
    if (c.start) {
      var t0 = Date.UTC(+c.start.slice(0, 4), +c.start.slice(5, 7) - 1, +c.start.slice(8, 10));
      for (var i = 0; i < (c.d || []).length; i++) {
        var d = new Date(t0 + c.d[i] * 86400000).toISOString().slice(0, 10);
        var v = c.c[i];
        rows.push(Array.isArray(v) ? [d].concat(v) : [d, v]);
      }
    }
    (c.raw || []).forEach(function (r) { rows.push(r); });
    s.rows = rows;
  }
  Object.keys(D.market || {}).forEach(function (k) { decodeSeries(D.market[k]); });
  /* One evidence row, the same everywhere a cited source is drawn: tag, claim, source
     and date, the link, and the verbatim excerpt the gates verified, behind a toggle. */
  function evRow(e) {
    if (!e || typeof e !== "object") return "<div class='evli'>" + esc(String(e)) + "</div>";
    var src = e.source_name ? (e.url ? "<a href='" + esc(e.url) + "' target='_blank' rel='noopener'>" + esc(e.source_name) + "</a>" : esc(e.source_name)) : (e.url ? "<a href='" + esc(e.url) + "' target='_blank' rel='noopener'>source</a>" : "");
    return "<div class='evli'>" + (e.tag ? chip(e.tag) + " " : "") + esc(e.claim || "") +
      (src || e.source_date ? " <span class='muted'>[" + src + (e.source_date ? (src ? ", " : "") + esc(e.source_date) : "") + "]</span>" : "") +
      (e.source_excerpt ? "<details class='excerpt'><summary class='muted small'>verbatim excerpt</summary><blockquote class='small'>" + esc(e.source_excerpt) + "</blockquote></details>" : "") +
      "</div>";
  }
  function evList(items) { return (items || []).map(evRow).join(""); }
  var app = document.getElementById("app");
  /* BUILT_AT is when the data was assembled. TODAY is when someone is LOOKING at it, and
     those are different questions. Staleness chips, review-due lists and the radar-silence
     check all ask the second one, and all of them used to read built_at — so a published
     artifact froze the day it was built and went on reporting "all quiet" and no stale
     badges however long it sat open. A page about aging data that cannot itself age is
     the one thing on the screen guaranteed to be wrong eventually. */
  var BUILT_AT = (D.built_at || "").slice(0, 10);
  var TODAY = new Date().toISOString().slice(0, 10);
  /* The scoring thresholds come from the build (app/build.py injects payload.method from
     the same constants tools/validate.py computes verdicts with) rather than being retyped
     here. The fallbacks keep an older page rendering, and they are the only copy left. */
  var METHOD = D.method || {};
  var MC = METHOD.money_corner || { impact_min: 60, crowd_max: 40, capture_min: 60 };

  /* ---------------- helpers ---------------- */
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* One definition of "this link can be plotted", used by the scatter and by the
     Not-scored list below it, so every link lands in exactly one of the two. They used
     to disagree: the plot required impact AND crowdedness, the list tested crowdedness
     alone, so a link with a crowdedness score and a null impact silently appeared in
     neither. `!= null` everywhere: 0 is a score. */
  /* A score of 0 is a score. `x || "–"` printed it as missing, which is the one
     rendering error that turns a real finding into an apparent data gap. */
  function str_or_empty(v) { return typeof v === "string" ? v.trim() : ""; }
  function num(v, dash) { return v == null ? (dash || "–") : v; }
  function isPlottable(l) {
    return !!(l.heat && l.heat.impact && l.heat.impact.score != null &&
              l.heat.crowdedness && l.heat.crowdedness.score != null);
  }
  function byId(list, id) { for (var i = 0; i < (list || []).length; i++) if (list[i].id === id) return list[i]; return null; }
  function daysBetween(a, b) { return Math.round((new Date(b) - new Date(a)) / 86400000); }
  function staleChip(asOf) {
    if (!asOf) return "";
    var d = daysBetween(asOf, TODAY);
    if (d > 90) return '<span class="chip verystale">stale · ' + esc(asOf) + "</span>";
    if (d > 30) return '<span class="chip stale">stale · ' + esc(asOf) + "</span>";
    return "";
  }
  function chip(text, cls) { return '<span class="chip ' + esc(cls || "neutral") + '">' + esc(text) + "</span>"; }
  function tierChip(t) { return t ? '<span class="chip tier">' + esc(t) + "</span>" : ""; }
  /* The impact appraisal (method 0.2). Every value here is read, never derived: the band
     is computed by tools/impact_score.py and the page prints what that wrote. A renderer
     that re-derived the band from the legs would be a second implementation of the rule,
     free to disagree with the gate that enforced it. */
  function impactFor(occId) {
    for (var i = 0; i < (D.impact || []).length; i++) {
      if (D.impact[i].occurrence_id === occId) return D.impact[i];
    }
    return null;
  }
  function impactChip(a) {
    if (!a || !a.impact_band) return "";
    var b = a.impact_band;
    var label = b === "UNRANKED" ? "unranked"
      : b.toLowerCase() + (a.impact_score != null ? " " + a.impact_score : "");
    return '<span class="chip impact-' + esc(b) + '" title="' + esc(impactTitle(a)) + '">'
      + esc(label) + "</span>";
  }
  function impactTitle(a) {
    if (!a) return "";
    if (a.impact_band === "UNRANKED") {
      return "Not appraised: " + (a.unranked_reason || "a leg is NULL");
    }
    var money = a.money_at_stake && a.money_at_stake.band ? a.money_at_stake.band : "no band";
    return "money " + money
      + " / reach " + num((a.public_reach || {}).score)
      + " / capture " + num((a.capture_odds || {}).score)
      + " / timing " + num((a.timing_fit || {}).score);
  }
  function opportunityChip(t) {
    if (!t) return "";
    var cls = t === "O1" ? "opp-O1" : t === "O2" ? "opp-O2" : t === "O3" ? "opp-O3" : "opp-none";
    return '<span class="chip opportunity ' + cls + '">' + esc(t) + "</span>";
  }
  function verdColor(v) { return { QUIET: "var(--quiet)", UNDISCOVERED: "var(--und)", EMERGING: "var(--emg)", CROWDED: "var(--crd)", OVER_CROWDED: "var(--ovr)" }[v] || "var(--border-strong)"; }
  function marketFor(t) { return (D.market || {})[String(t).replace(/\./g, "-")] || (D.market || {})[t] || null; }
  function fmtMoney(x) { return typeof x === "number" ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : esc(x); }
  function seclabel(t) { return '<div class="seclabel">' + esc(t) + "</div>"; }

  /* ---------------- agents ----------------
     The eight contracts, shipped into the page by app/build.py through
     tools/agent_registry.py. `owners` is the command-shape-to-agent map parsed out of
     CLAUDE.md's command table by the same module Adam's machine audit uses, so the button
     Ron presses and the audit that reports an unowned command can never disagree about who
     owns what. `unowned` is the table's real backlog (refresh, request data, log trade,
     note, run review): those buttons show no agent because no agent is accountable for
     them, which is the honest rendering and not a gap in this code. */
  var AX = D.agentix || { agents: [], owners: [], unowned: [] };
  function agentBySlug(slug) {
    for (var i = 0; i < AX.agents.length; i++) if (AX.agents[i].slug === slug) return AX.agents[i];
    return null;
  }
  /* Longest key first (app/build.py sorts them), matched on a word boundary so
     "run universe-audit x" cannot be claimed by the "run universe" key. */
  function agentFor(cmd) {
    var c = String(cmd || "");
    for (var i = 0; i < AX.owners.length; i++) {
      var k = AX.owners[i][0];
      if (c === k || c.indexOf(k + " ") === 0) return agentBySlug(AX.owners[i][2]);
    }
    return null;
  }
  function agentChip(cmd) {
    var a = agentFor(cmd);
    if (!a) return "";
    return '<a class="chip agent" href="#/agent/' + esc(a.slug) +
      '" title="read ' + esc(a.name) + "'s instructions · " + esc(a.role) +
      '">' + esc(a.name) + " ▸</a>";
  }

  /* A contract is 200-plus lines of markdown and reading it as one monospace wall is the
     same as not shipping it. This renders the subset the contracts actually use. It runs
     over ALREADY-ESCAPED text and only ever inserts tags written here, so nothing in a
     contract body can become markup. Link targets are linkified only when they are http(s):
     a relative path renders as code, because a contract is data and a data file does not
     get to decide what the page navigates to. */
  function mdInline(t) {
    return t
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, function (_m, txt, href) {
        return '<a href="' + href + '" rel="noreferrer noopener" target="_blank">' + txt + "</a>";
      })
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "$1 <code>$2</code>");
  }
  /* Split on UNESCAPED pipes only, and without a lookbehind: a cell like
     `<SIG-id\|CAND-id>` carries an escaped pipe, and splitting on it shifts every later
     cell by one. This is the same defect tools/agent_registry.py documents on the Python
     side; the swap keeps it working on engines with no lookbehind support. */
  var PIPE_SLOT = "\u0000P";
  function mdRow(line) {
    var cells = line.replace(/^\||\|$/g, "").replace(/\\\|/g, PIPE_SLOT).split("|");
    return cells.map(function (c) { return c.split(PIPE_SLOT).join("|").trim(); });
  }
  function md(src) {
    var lines = String(src || "").split("\n");
    var out = [], list = null, table = null, fence = null, para = [], li = null;
    /* These files hard-wrap at about 92 columns, so one line is NOT one paragraph.
       Emitting a <p> per source line broke sentences mid-clause, which is worse than
       showing the raw file: it reads as though the document itself is disjointed.
       Consecutive lines join with a space and flush on a structural boundary. */
    function flushPara() { if (para.length) { out.push("<p>" + mdInline(esc(para.join(" "))) + "</p>"); para = []; } }
    function flushLi() { if (li !== null) { out.push("<li>" + mdInline(esc(li.join(" "))) + "</li>"); li = null; } }
    function closeList() { flushLi(); if (list) { out.push("</" + list + ">"); list = null; } }
    function closeTable() {
      if (table) { out.push("<div class='mdtable'><table>" + table.join("") + "</table></div>"); table = null; }
    }
    function closeAll() { flushPara(); closeList(); closeTable(); }
    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i], t = raw.trim();
      if (fence !== null) {
        if (t.indexOf("```") === 0) { out.push("<pre>" + esc(fence.join("\n")) + "</pre>"); fence = null; }
        else fence.push(raw);
        continue;
      }
      if (t.indexOf("```") === 0) { closeAll(); fence = []; continue; }
      if (!t) { closeAll(); continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { closeAll(); out.push("<hr>"); continue; }
      var h = /^(#{1,6})\s+(.*)$/.exec(t);
      if (h) {
        closeAll();
        var lvl = Math.min(6, h[1].length + 1);
        out.push("<h" + lvl + ">" + mdInline(esc(h[2])) + "</h" + lvl + ">");
        continue;
      }
      if (t.charAt(0) === "|" && t.charAt(t.length - 1) === "|") {
        flushPara(); closeList();
        var cells = mdRow(t);
        if (cells.every(function (c) { return /^:?-{2,}:?$/.test(c); })) continue;
        var tag = table ? "td" : "th";
        if (!table) table = [];
        table.push("<tr>" + cells.map(function (c) {
          return "<" + tag + ">" + mdInline(esc(c)) + "</" + tag + ">";
        }).join("") + "</tr>");
        continue;
      }
      closeTable();
      var b = /^[-*]\s+(.*)$/.exec(t), n = /^\d+[.)]\s+(.*)$/.exec(t);
      if (b || n) {
        flushPara();
        var want = b ? "ul" : "ol";
        if (list !== want) { closeList(); list = want; out.push("<" + want + ">"); }
        else flushLi();
        li = [(b || n)[1]];
        continue;
      }
      // A plain line inside a list is the wrapped tail of the bullet above it.
      if (li !== null) { li.push(t); continue; }
      para.push(t);
    }
    if (fence !== null) out.push("<pre>" + esc(fence.join("\n")) + "</pre>");
    closeAll();
    return out.join("");
  }
  /* The frontmatter is name + description and the view prints both as headings already;
     rendering it twice reads as a bug. Returned separately so the EDITOR can put it back:
     what Ron edits and what gets written to disk is the whole file, never this remainder. */
  function contractBody(text) {
    var m = /^---\r?\n[\s\S]*?\r?\n---\r?\n/.exec(String(text || ""));
    return m ? text.slice(m[0].length) : text;
  }

  /* ---------------- click queue (artifact capability) ----------------
     A Run click publishes a new version of this page with the command queued
     in the #upstream-queue block. Any live Claude session watching the
     artifact is notified, executes the command against the repo, and
     republishes the page with the results baked in. Where queueing is
     unavailable (local preview, read-only viewer) the button degrades to a
     copy affordance automatically. */
  var QUEUE_ID = "upstream-queue", EDITS_ID = "upstream-edits";
  var QUEUE = { v: 1, queue: [] };
  try { QUEUE = JSON.parse(document.getElementById(QUEUE_ID).textContent) || QUEUE; } catch (e) {}
  /* The second channel. A command is a fixed grammar an allowlist can check with a
     fullmatch; an agent contract is 15 KB of free text, and every shape in
     tools/queue_allowlist.py refuses control characters precisely so a second command
     cannot hide behind the first. Widening the queue to carry a body would delete that
     property, so edits ride their own block and are checked by their own function
     (queue_allowlist.is_allowed_edit): the target must be an agent file already on disk,
     and the body is written verbatim and never obeyed. Same erase hazard as the queue,
     for the same reason: app/build.py resets both on every build, so a session about to
     republish drains or splices BOTH. */
  var EDITS = { v: 1, edits: [] };
  try { EDITS = JSON.parse(document.getElementById(EDITS_ID).textContent) || EDITS; } catch (e) {}
  function pendingEditFor(slug) {
    var list = (EDITS.edits || []).filter(function (e) { return e.target === slug; });
    return list.length ? list[list.length - 1] : null;
  }
  var QSTATE = { readonly: false, busy: false };
  var SCRIPT_END = "</scr" + "ipt>";
  function canQueue() { return !!(window.claude && typeof window.claude.use === "function") && !QSTATE.readonly; }
  function isQueued(cmd) { return (QUEUE.queue || []).some(function (q) { return q.cmd === cmd; }); }
  function toast(msg, ms) {
    var t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ms || 5600);
  }
  function fetchSelfSource(id) {
    function ok(r) { return r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status)); }
    return fetch("index.html", { cache: "no-store" }).then(ok)
      .catch(function () { return fetch(location.href.split("#")[0], { cache: "no-store" }).then(ok); })
      .then(function (src) {
        if (src.indexOf('id="' + id + '"') < 0) throw new Error(id + " block not found in source");
        return src;
      });
  }
  function blockOf(src, id, fallback) {
    try {
      var o = src.indexOf('id="' + id + '"');
      var start = src.indexOf(">", o) + 1;
      return JSON.parse(src.slice(start, src.indexOf(SCRIPT_END, start))) || fallback;
    } catch (e) { return fallback; }
  }
  function replaceBlock(src, id, obj) {
    var open = src.indexOf('id="' + id + '"');
    var start = src.indexOf(">", open) + 1;
    var end = src.indexOf(SCRIPT_END, start);
    return src.slice(0, start) + JSON.stringify(obj).replace(/<\//g, "<\\/") + src.slice(end);
  }
  function enqueue(cmd, btn) {
    if (QSTATE.busy) return;
    QSTATE.busy = true;
    if (btn) { btn.disabled = true; btn.textContent = "Queuing…"; }
    var fail = function (msg, permanent) {
      QSTATE.busy = false;
      if (permanent) QSTATE.readonly = true;
      toast(msg, 6500);
      route();
    };
    if (!canQueue()) return fail("Queueing unavailable in this view — copy the command into a Claude session instead.", false);
    window.claude.use("artifact").then(function (ns) {
      if (!ns) return fail("This view cannot queue — copy the command into a Claude session instead.", true);
      return fetchSelfSource(QUEUE_ID).then(function (src) {
        var cur = blockOf(src, QUEUE_ID, { v: 1, queue: [] });
        if ((cur.queue || []).some(function (q) { return q.cmd === cmd; })) {
          QSTATE.busy = false; QUEUE = cur; toast("Already queued: " + cmd); route(); return;
        }
        cur.queue = (cur.queue || []).concat([{ id: "q-" + Date.now(), cmd: cmd, ts: new Date().toISOString() }]);
        try { sessionStorage.setItem("upstream.justQueued", cmd); } catch (e) {}
        return ns.publish(replaceBlock(src, QUEUE_ID, cur)).catch(function (err) {
          try { sessionStorage.removeItem("upstream.justQueued"); } catch (e) {}
          var code = (err && err.code) || "upstream_error";
          if (code === "conflict") { QSTATE.busy = false; return; } // view reloads to the winner; re-click there
          if (code === "not_writer" || code === "not_granted" || code === "not_declared" ||
              code === "capability_disabled" || code === "capability_removed")
            return fail("This view is read-only — buttons switched to copy mode.", true);
          if (code === "rate_limited") return fail("Queueing too fast — wait a minute and try again.", false);
          return fail("Queueing failed (" + code + ") — use copy this time.", false);
        });
      });
    }).catch(function () { fail("Queueing unavailable — use copy instead.", false); });
  }
  /* Saving an edited contract. Ron chose apply-immediately: the next session that drains
     writes the file with no diff step. So the checks that survive here are the ones about
     INTEGRITY, not approval. An empty body, an oversize one, or one carrying a literal
     script-close would break the page it rides on, and none of those is worth discovering
     at drain time. `base_sha256` is the digest of the contract this edit was composed
     against, so the ledger line can say whether the on-disk file had moved on. Note
     crypto.subtle needs a secure context: where it is missing the field is null and the
     draining session reports that it could not compare, which is the truth rather than a
     silent pass. */
  var EDIT_MAX_BYTES = 200000;
  function sha256Hex(text) {
    try {
      if (!(window.crypto && window.crypto.subtle && window.TextEncoder)) return Promise.resolve(null);
      return window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))
        .then(function (buf) {
          return Array.prototype.map.call(new Uint8Array(buf), function (b) {
            return ("0" + b.toString(16)).slice(-2);
          }).join("");
        }).catch(function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }
  function editByteLength(body) {
    try { return new Blob([body]).size; } catch (e) { return body.length; }
  }
  function publishEdit(slug, body, base, btn) {
    if (QSTATE.busy) return;
    var bytes = editByteLength(body);
    if (!body || !body.trim()) return toast("Refused: an empty contract would leave that agent with no instructions.", 6500);
    if (bytes > EDIT_MAX_BYTES) return toast("Refused: " + bytes + " bytes is over the " + EDIT_MAX_BYTES + "-byte cap for one contract.", 6500);
    if (body.indexOf("</scr" + "ipt") > -1) return toast("Refused: the body carries a literal script-close, which would break the page it travels on.", 6500);
    QSTATE.busy = true;
    var prev = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
    var fail = function (msg, permanent) {
      QSTATE.busy = false;
      if (permanent) QSTATE.readonly = true;
      if (btn) { btn.disabled = false; if (prev !== null) btn.textContent = prev; }
      toast(msg, 6500);
      route();
    };
    if (!canQueue()) return fail("This view cannot write back — edit .claude/agents/" + slug + ".md in the repo instead.", false);
    sha256Hex(base).then(function (digest) {
      return window.claude.use("artifact").then(function (ns) {
        if (!ns) return fail("This view is read-only — edit the file in the repo instead.", true);
        return fetchSelfSource(EDITS_ID).then(function (src) {
          var cur = blockOf(src, EDITS_ID, { v: 1, edits: [] });
          // One pending edit per agent: a second save supersedes the first rather than
          // queueing two writes to one file whose order nobody controls.
          cur.edits = (cur.edits || []).filter(function (e) { return e.target !== slug; })
            .concat([{ id: "e-" + Date.now(), target: slug, body: body,
                       base_sha256: digest, ts: new Date().toISOString() }]);
          try { sessionStorage.setItem("upstream.justSaved", slug); } catch (e) {}
          return ns.publish(replaceBlock(src, EDITS_ID, cur)).catch(function (err) {
            try { sessionStorage.removeItem("upstream.justSaved"); } catch (e) {}
            var code = (err && err.code) || "upstream_error";
            if (code === "conflict") { QSTATE.busy = false; return; }
            if (code === "not_writer" || code === "not_granted" || code === "not_declared" ||
                code === "capability_disabled" || code === "capability_removed")
              return fail("This view is read-only — edit the file in the repo instead.", true);
            if (code === "rate_limited") return fail("Saving too fast — wait a minute and try again.", false);
            return fail("Save failed (" + code + ") — the contract on disk is unchanged.", false);
          });
        });
      });
    }).catch(function () { fail("Save unavailable — the contract on disk is unchanged.", false); });
  }

  var RUN_LABELS = [
    [/^run chain /, "Build chain"], [/^run heat /, "Score heat map"], [/^run scenarios /, "Write scenarios"],
    [/^run screen /, "Screen stocks"], [/^run deepdive /, "Run deep dive"], [/^run redteam /, "Red-team it"],
    [/^request data /, "Fetch data"], [/^refresh /, "Update"], [/^run radar/, "Run radar"], [/^run digest/, "Build digest"],
    [/^run campaign init/, "Start campaign"], [/^run universe-audit /, "Audit universe"],
    [/^run universe /, "Map issuers"], [/^run profile /, "Profile issuer"],
    [/^run selection /, "Select O1"],
    // --queue before the parameterised shape: first match wins, and `run impact ` would
    // otherwise claim `run impact --queue` and label a batch as one appraisal.
    [/^run impact --queue/, "Appraise the queue"], [/^run impact /, "Size the money"],
    [/^run themes/, "Cluster occurrences"], [/^run devil /, "Review this file"],
    [/^check health/, "Check health"],
  ];
  function runLabel(cmd) {
    for (var i = 0; i < RUN_LABELS.length; i++) if (RUN_LABELS[i][0].test(cmd)) return RUN_LABELS[i][1];
    return "Run";
  }
  function cmdline(cmd) {
    return '<span class="cmdline"><code>' + esc(cmd) + '</code><button data-copy="' + esc(cmd) + '" title="copy command">copy</button></span>';
  }
  /* Every Run button carries the chip of the agent that will execute it, linking to that
     agent's full instructions. Placed HERE rather than at the twenty-odd call sites, so a
     button added tomorrow inherits it and cannot be the one that quietly has no owner
     shown. A command the table leaves unowned (refresh, request data) renders no chip,
     which is the honest state and the same backlog check_machine.audit_owners reports.
     The compact form drops the chip: it sits inside table rows where the owner is already
     named by the section it is in, and a chip per row is noise, not information. */
  function runButton(cmd, caption, opts) {
    opts = opts || {};
    var who = opts.compact ? "" : agentChip(cmd);
    if (isQueued(cmd)) {
      if (opts.compact) return '<button class="btn-run q" disabled>Queued ✓</button>';
      return '<div class="runwrap"><div class="runhead"><button class="btn-run q" disabled>Queued ✓</button>' + who +
        '</div><span class="cmdline">waiting for a live Claude session · ' + cmdline(cmd) + "</span></div>";
    }
    var btn = canQueue()
      ? '<button class="btn-run" data-run="' + esc(cmd) + '">' + esc(runLabel(cmd)) + "</button>"
      : '<button class="btn-run" data-copyrun="' + esc(cmd) + '">' + esc(runLabel(cmd)) + (opts.compact ? "" : " — copy") + "</button>";
    if (opts.compact) return btn;
    return '<div class="runwrap"><div class="runhead">' + btn + who + "</div>" +
      '<span class="cmdline">' + esc(caption || (canQueue() ? "runs in a live Claude session; results land on this page" : "copies the command — paste into a Claude session on this repo")) +
      " · " + cmdline(cmd) + "</span></div>";
  }

  /* ---------------- shell ---------------- */
  function pendingCount() {
    return ((D.requests || {}).requests || []).filter(function (x) { return x.status === "PENDING"; }).length;
  }
  function failedCount() {
    return ((D.requests || {}).requests || []).filter(function (x) { return x.status === "FAILED"; }).length;
  }
  function healthState() {
    if (failedCount() > 0) return ["var(--bad)", failedCount() + " data request(s) FAILED — see data/requests.json"];
    var rs = ((D.health || {}).sessions || {}).routine_status || {};
    if (rs.radar === "LIVE") {
      var lastRadar = null;
      (D.ledger || []).forEach(function (l) { if (l.indexOf("| RADAR") > -1) lastRadar = l.slice(0, 10); });
      if (lastRadar && daysBetween(lastRadar, TODAY) > 4) return ["var(--bad)", "radar silent since " + lastRadar];
    }
    if (pendingCount() > 0) return ["var(--warn)", pendingCount() + " data request(s) pending"];
    return ["var(--good)", "all loops quiet"];
  }
  function navHrefChains() {
    return (D.chains || []).length === 1 ? "#/chain/" + D.chains[0].id : "#/chains";
  }
  function topbar(active) {
    var hs = healthState();
    var q = (QUEUE.queue || []).length;
    function na(href, label, key) {
      return '<a href="' + href + '" class="' + (active === key ? "on" : "") + '">' + label + "</a>";
    }
    return '<div class="topbar">' +
      '<a href="#/" class="wordmark"><span class="tick">▲</span>UPSTREAM</a>' +
      '<nav class="nav">' +
      na("#/", "Board", "board") +
      na("#/cortex", "Cortex", "cortex") +
      na("#/radar", "Radar", "radar") +
      na(navHrefChains(), (D.chains || []).length === 1 ? "Chain" : "Chains", "chain") +
      na("#/campaign", "Campaign", "campaign") +
      na("#/themes", "Log", "themes") +
      na("#/agents", "Agents", "agents") +
      na("#/book", "Book", "book") +
      na("#/shadow", "Shadow", "shadow") +
      na("#/guide", "Guide", "guide") +
      "</nav>" +
      '<span class="spacer"></span>' +
      (q ? '<span class="tb-chip" title="queued commands awaiting a live Claude session"><span class="healthdot" style="background:var(--accent)"></span>' + q + " queued</span>" : "") +
      (pendingCount() ? '<span class="tb-chip"><span class="healthdot" style="background:var(--warn)"></span>' + pendingCount() + " fetching</span>" : "") +
      '<span class="tb-chip" title="' + esc(hs[1]) + '"><span class="healthdot" style="background:' + hs[0] + '"></span>health</span>' +
      '<button class="tb-btn" id="themeBtn" title="theme">◐</button>' +
      "</div>";
  }
  function crumbs(parts) {
    var h = '<div class="crumbs"><a href="#/radar">Radar</a>';
    parts.forEach(function (p, i) {
      h += '<span class="sep">/</span>';
      if (p.href && i < parts.length - 1) h += '<a href="' + p.href + '">' + esc(p.label) + "</a>";
      else h += '<span class="here">' + esc(p.label) + "</span>";
    });
    return h + "</div>";
  }
  function footer() {
    return '<div class="footer">Upstream is a private research tool for Ron and the collaborators he invites. Verdicts, zones, and levels are analytical outputs from public data with stated methods and gaps — not investment advice. Built ' + esc(D.built_at || "?") + " · canonical copy: <span class='mono'>app/index.html</span> in the repo.</div>";
  }

  /* ---------------- what changed ---------------- */
  function lastVisit() { try { return localStorage.getItem("upstream.lastVisit"); } catch (e) { return null; } }
  function stampVisit() { try { localStorage.setItem("upstream.lastVisit", new Date().toISOString()); } catch (e) {} }
  function deltaItems() {
    var since = lastVisit();
    var items = [];
    (D.signals || []).forEach(function (s) {
      if (!since || (s.created_at && s.created_at > since.slice(0, 10)))
        items.push({ k: "new", h: "Signal: <a href='#/signal/" + s.id + "'>" + esc(s.title) + "</a>" });
    });
    (D.stocks || []).forEach(function (st) {
      (st.changelog || []).forEach(function (c) {
        if (since && c.ts > since) items.push({ k: "upd", h: "<a href='#/stock/" + st.ticker + "/" + st.chain_id + "'>" + esc(st.ticker) + "</a> — " + esc(c.change) });
      });
      if (st.review_by && st.review_by <= TODAY && st.status !== "ARCHIVED")
        items.push({ k: "due", h: "Review due: <a href='#/stock/" + st.ticker + "/" + st.chain_id + "'>" + esc(st.ticker) + "</a> (by " + esc(st.review_by) + ")" });
    });
    (D.chains || []).forEach(function (c) {
      (c.scenarios || []).forEach(function (sc) {
        (sc.leading_indicators || []).forEach(function (ind) {
          if (ind.tripped_at) items.push({ k: "trip", h: esc(ind.indicator) + " → <a href='#/chain/" + c.id + "/scen'>" + esc(sc.id + " · " + sc.title) + "</a>" });
        });
      });
    });
    ((D.indicators || {}).trips || []).forEach(function (t) {
      if (!since || t.tripped_at >= since.slice(0, 10))
        items.push({ k: "trip", h: esc(t.indicator) + " (" + esc(t.ticker) + " " + esc(t.op) + " " + esc(t.level) + ", seen " + esc(t.seen) + ") → <a href='#/chain/" + t.chain + "/scen'>" + esc(t.chain + " " + t.scenario) + "</a>" });
    });
    return { since: since, items: items };
  }

  /* ---------------- home ---------------- */
  function funnelCard() {
    var nSig = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; }).length;
    var nCh = (D.chains || []).length;
    var nScen = 0, nScr = (D.screens || []).length;
    (D.chains || []).forEach(function (c) { nScen += (c.scenarios || []).length; });
    var nVer = (D.stocks || []).length;
    var one = (D.chains || [])[0];
    var stages = [
      { n: nSig, l: "Signals", href: "#/radar" },
      { n: nCh, l: nCh === 1 ? "Chain" : "Chains", href: navHrefChains() },
      { n: nScen, l: "Scenarios", href: one ? "#/chain/" + one.id + "/scen" : "#/radar" },
      { n: nScr, l: "Screens", href: nScr && one ? "#/screen/" + D.screens[0].chain_id + "/" + D.screens[0].scenario_id : "#/radar" },
      { n: nVer, l: "Verdicts", href: nVer ? "#/stock/" + D.stocks[0].ticker + "/" + D.stocks[0].chain_id : "#/radar" },
    ];
    var W = 520, H = 132, n = stages.length, gap = 14, segW = (W - gap * (n - 1)) / n;
    var s = '<svg class="funnel" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="pipeline">';
    stages.forEach(function (st, i) {
      var x = i * (segW + gap);
      var op = 1 - i * 0.17;
      s += '<a href="' + st.href + '">';
      s += '<rect class="fseg" x="' + x + '" y="46" width="' + segW + '" height="46" rx="10" fill="var(--accent)" opacity="' + op.toFixed(2) + '"/>';
      s += '<text class="stagecount" x="' + (x + segW / 2) + '" y="30" text-anchor="middle">' + st.n + "</text>";
      s += '<text class="stagelabel" x="' + (x + segW / 2) + '" y="112" text-anchor="middle">' + esc(st.l) + "</text>";
      s += "</a>";
      if (i < n - 1) s += '<path d="M' + (x + segW + 2.5) + ' 63 l8 6 -8 6" fill="none" stroke="var(--border-strong)" stroke-width="1.6" stroke-linecap="round"/>';
    });
    s += "</svg>";
    return '<div class="card funnelcard"><div class="fc-title">The funnel — click a stage</div>' + s +
      '<div class="muted" style="margin-top:10px">Lazy by design: each stage runs only when someone asks, and everything ever run is saved here.</div></div>';
  }
  function todayCard() {
    var d = deltaItems();
    var body;
    if (!d.items.length) {
      body = '<div class="quiet"><span class="big">All quiet.</span><span class="muted">Nothing changed since ' +
        (d.since ? "your last visit (" + esc(d.since.slice(0, 10)) + ")" : "the seed build") + ". The radar accumulates on weekdays; Saturday brings the digest.</span></div>";
    } else {
      body = '<ul class="delta">' + d.items.slice(0, 6).map(function (i) {
        var lbl = { new: "new", trip: "tripped", due: "due", upd: "updated" }[i.k];
        return '<li><span class="k ' + i.k + '">' + lbl + "</span><span>" + i.h + "</span></li>";
      }).join("") + "</ul>";
    }
    var dg = (D.digests || [])[0];
    return '<div class="card today"><h2>' + (d.items.length ? "Since you last looked" : "Today") + "</h2>" +
      '<div class="when">' + esc(TODAY) + "</div>" + body +
      (dg ? '<div class="muted" style="margin-top:12px">Latest digest: <b>' + esc(dg.week || "") + "</b> — " + esc((dg.summary || "").slice(0, 140)) + "</div>" : "") +
      (dg && dg.machine && dg.machine.next_action
        ? '<div class="muted" style="margin-top:6px">Machine: ' + esc(dg.machine.next_action) + "</div>" : "") +
      "</div>";
  }
  function sigRow(s) {
    var laneName = { MACRO: "Macro", INDUSTRY: "Industry", USE_CASE: "Use case" }[s.lane] || s.lane;
    var un = (s.unmappedness || {}).score;
    var cta = s.status === "NEW"
      ? runButton("run chain " + s.id, null, { compact: true })
      : s.status === "CHAINED"
        ? '<a class="chip accent" href="#/chain/' + esc(s.chain_id) + '">Open chain →</a>'
        : chip(s.status);
    var occ = s.occurrence || null;
    var occLine = '<div class="occline">' + esc(laneName.toUpperCase()) + " &middot; " + esc(s.suggested_clock) +
      (occ ? " &middot; " + esc(occ.kind) + " " + esc(occ.anchor_date) : " &middot; UNDATED") +
      (occ && occ.label ? '<span class="ol">' + esc(occ.label) + "</span>" : "") + "</div>";
    return '<div class="sigrow bracket" data-nav="#/signal/' + esc(s.id) + '" tabindex="0" role="link" aria-label="' + esc(s.title) + '">' +
      '<i class="bk tl"></i><i class="bk tr"></i><i class="bk bl"></i><i class="bk br"></i>' +
      "<div>" + occLine +
      '<div class="meta">' +
      '<span class="num">' + esc((s.horizon_years || []).join("–")) + "y</span>" +
      "<span>" + (s.evidence || []).length + " evidence</span>" + staleChip(s.updated_at) + "</div>" +
      '<div class="t">' + esc(s.title) + "</div>" +
      '<div class="th">' + esc(s.thesis) + "</div>" +
      "</div>" +
      '<div class="right">' +
      '<span class="gauge" title="how unmapped this event\'s chain consequences are"><span class="track"><i style="width:' + (un || 0) + '%"></i></span><span class="num">' + esc(un) + "</span> unmapped</span>" +
      impactChip(impactFor(s.id)) +
      "<span data-stop>" + cta + "</span>" +
      "</div></div>";
  }
  /* Scout panel: the radar grading its own intake. Every figure prints its denominator,
     so "0 misses" can be told apart from "nothing was checked". Source: data/radar/scout-log.json. */
  function scoutCard() {
    var sc = D.scout;
    if (!sc || !sc.calibration) return "";
    var c = sc.calibration, cv = c.conversion || {}, dn = c.denominators || {}, fd = c.funnel_depth || {};
    var lat = c.latency || [], unm = c.latency_unmatched || [];
    var days = lat.map(function (l) { return l.days_late; }).filter(function (n) { return typeof n === "number"; }).sort(function (a, b) { return a - b; });
    var med = days.length ? days[Math.floor(days.length / 2)] : null;
    var dd = c.death_outcomes || {};
    var hardened = (sc.proposed_rules || []).filter(function (r) { return r.status === "HARDENED"; });
    var proposed = (sc.proposed_rules || []).filter(function (r) { return r.status === "PROPOSED"; });
    var esc_ = (sc.notes || []).filter(function (n) { return /^ESCALATION/.test(String(n.text || "")); });

    function stat(v, label) {
      return '<div class="scstat"><div class="scnum">' + esc(v) + '</div><div class="muted">' + esc(label) + "</div></div>";
    }
    return seclabel("Scout — how good this intake has been") +
      '<div class="statgrid">' +
      '<div class="card"><h3>Conversion</h3><div class="scrow">' +
      stat(cv.promoted + "/" + cv.candidates_triaged, "candidates promoted") +
      stat(fd.reached_chain + "/" + fd.signals_total, "signals chained") +
      stat(fd.in_money_corner_chain + "/" + fd.signals_total, "reached a money corner") +
      "</div><div class='small'>" + esc(cv.ambient) + " AMBIENT still open · " +
      esc(dn.feed_items_examined) + " feed items examined this run</div></div>" +

      '<div class="card"><h3>Latency</h3><div class="scrow">' +
      stat(med === null ? "n/a" : med + "d", days.length >= 3 ? "median days late" : "days late (n=" + days.length + ", not a median)") +
      stat(lat.length + "/" + dn.signals_examined, "measurable") +
      "</div><div class='small'>" +
      (unm.length ? esc(unm.length) + " signal(s) unmeasurable: " + esc(unm[0].reason) +
        ". Latency is the gap between an occurrence first appearing in the feed store and radar writing the card."
        : "Every signal is joined back to its first feed appearance.") +
      "</div></div>" +

      '<div class="card"><h3>My misses</h3><div class="scrow">' +
      stat((dd.signals_new_past_review_by || []).length + "/" + dn.signals_examined, "past review_by") +
      stat((dd.candidates_past_expiry || []).length + "/" + cv.ambient, "past 45d expiry") +
      stat(dn.calendar_examined ? (dd.calendar_passed_unpromoted || []).length + "/" + dn.calendar_examined : "—", "calendar passed") +
      "</div><div class='small'>False positives are counted against the radar, not hidden." +
      (dn.calendar_examined ? "" : " The forward calendar is empty, so that leg checked nothing: a scope boundary, not a clean result.") +
      "</div></div>" +

      '<div class="card"><h3>Taste rules</h3>' +
      (hardened.length ? hardened.map(function (r) {
        return '<div class="evli">' + chip(r.origin) + " <b>" + esc(r.id) + "</b> " + esc(r.pattern) + "</div>";
      }).join("") : "<div class='small'>No rules yet.</div>") +
      (proposed.length ? "<div class='muted' style='margin-top:8px'>" + esc(proposed.length) +
        " proposed, waiting on a second occurrence</div>" : "") +
      "</div>" + "</div>" +
      esc_.map(function (n) {
        return '<div class="sysline"><span class="healthdot" style="background:var(--crd)"></span>' +
          esc(n.text) + "</div>";
      }).join("");
  }

  /* Map quality: the cartographer grading his own maps. Every figure prints its denominator,
     so "0 findings" can be told apart from "nothing examined". Source: data/chains/_map-log.json. */
  function mapStat(v, label) {
    return '<div class="scstat"><div class="scnum">' + esc(v) + '</div><div class="muted">' + esc(label) + "</div></div>";
  }
  function mapCard(chainId) {
    var ml = D.map;
    if (!ml || !ml.calibration) return "";
    var cal = ml.calibration, per = cal.per_chain || {}, dn = cal.denominators || {};
    var arch = (ml.archetypes || []).filter(function (a) { return a.status === "HARDENED"; });
    if (chainId) {
      var v = per[chainId];
      if (!v) return "";
      var st = v.structure || {};
      var findings = [];
      if ((st.orphans || []).length) findings.push(st.orphans.length + " orphan link(s)");
      if ((st.one_way_edges || []).length) findings.push(st.one_way_edges.length + " one-way edge(s)");
      if ((st.wrong_direction_edges || []).length) findings.push(st.wrong_direction_edges.length + " edge(s) against the direction rule");
      if ((st.verdict_mismatches || []).length) findings.push(st.verdict_mismatches.length + " verdict(s) disagreeing with their scores");
      if ((st.thin_choke_points || []).length) findings.push("thin choke point: " + st.thin_choke_points.join(", "));
      if ((st.links_without_tickers || []).length) findings.push(st.links_without_tickers.length + " link(s) with no tickers");
      return '<div class="card mapq">' +
        '<div class="row" style="justify-content:space-between;align-items:flex-start">' +
        "<h3>Map quality</h3>" +
        '<span class="muted">' + esc(st.links_without_evidence ? st.links_without_evidence.length : 0) +
        "/" + esc(v.links) + " links uncited</span></div>" +
        '<div class="scrow">' +
        mapStat(v.yielded_a_name + "/" + v.links, "links that produced a name") +
        mapStat(v.scored + "/" + v.links, "scored") +
        mapStat((st.non_us_ticker_share == null ? "n/a" : Math.round(st.non_us_ticker_share * 100) + "%"), "non-US tickers") +
        mapStat((v.money_corner_links || []).length, "money corner") +
        "</div>" +
        "<div class='small'>" +
        (findings.length ? "Findings: " + esc(findings.join(" · ")) : "No structural findings across " +
          esc(v.links) + " links and " + esc(st.tickers_examined) + " tickers examined.") +
        ((v.dead_links || []).length
          ? " Links that have never produced a name: " + esc(v.dead_links.join(", ")) + "."
          : "") +
        "</div></div>";
    }
    // global view (#/chains)
    var ly = cal.link_yield || {}, dep = ly.depth || {};
    return seclabel("Atlas — how good these maps have been") +
      '<div class="statgrid">' +
      '<div class="card"><h3>Link yield</h3><div class="scrow">' +
      mapStat(ly.yielded_a_name + "/" + ly.links_total, "produced a name") +
      mapStat((dep.DIVED || 0) + "/" + ly.links_total, "reached a dive") +
      mapStat(ly.money_corner_links, "money corner") +
      "</div><div class='small'>Joined on link_id only. " + esc(ly.note || "") + "</div></div>" +
      '<div class="card"><h3>Depth</h3><div class="scrow">' +
      mapStat(dep.MAPPED || 0, "mapped only") + mapStat(dep.SCORED || 0, "scored") +
      mapStat(dep.SCREENED || 0, "screened") + mapStat(dep.DIVED || 0, "dived") +
      "</div><div class='small'>" + esc(dn.chains_examined) + " chains, " + esc(dn.links_examined) +
      " links, " + esc(dn.edges_examined) + " edges, " + esc(dn.screen_rows_examined) +
      " screen rows examined.</div></div>" +
      '<div class="card"><h3>Archetypes</h3>' +
      (arch.length ? arch.map(function (a) {
        return '<div class="evli">' + chip(a.origin) + " <b>" + esc(a.id) + "</b> " + esc(a.pattern) +
          ' <span class="muted">(' + esc(a.occurrences) + " chain" + (a.occurrences === 1 ? "" : "s") + ")</span></div>";
      }).join("") : "<div class='small'>None yet.</div>") + "</div>" +
      "</div>" +
      (ml.notes || []).filter(function (n) { return /^ESCALATION/.test(String(n.text || "")); })
        .map(function (n) {
          return '<div class="sysline"><span class="healthdot" style="background:var(--crd)"></span>' + esc(n.text) + "</div>";
        }).join("");
  }

  /* Read from Tally's derived log, never counted here: the log excludes dismissed and
     expired candidates on purpose, and a second count on this page would quietly include
     them and disagree. */
  function unappraisedCount() {
    var d = ((D.rank || {}).calibration || {}).denominators || {};
    return d.unappraised == null ? 0 : d.unappraised;
  }
  function homeView() {
    var sigs = (D.signals || []).slice().sort(function (a, b) {
      return ((b.unmappedness || {}).score || 0) - ((a.unmappedness || {}).score || 0);
    });
    var hs = healthState();
    var sys = (D.ledger || []).slice(-3).reverse();
    return topbar("radar") + "<main>" +
      '<div class="hero">' + todayCard() + funnelCard() + "</div>" +
      /* The screen called Radar could not start a radar. The two `run radar` buttons that
         existed were both inside cortex drawers, two clicks and a graph node away. */
      seclabel("Run the intake") +
      "<div class='card runstrip'>" +
      runButton("run radar", "sweeps the feed store and the calendar, triages candidates, writes signal cards") +
      runButton("run themes", "logs every occurrence on disk and clusters it into themes") +
      /* The count is the argument for pressing it. An impact queue at one sixteenth
         coverage is not ordering anything, and the number says so without a session
         having to notice. */
      (unappraisedCount() > 0
        ? runButton("run impact --queue", "sizes the money behind the next 15 of " +
            unappraisedCount() + " unappraised occurrences, in the log's own order")
        : "") +
      "</div>" +
      seclabel("Signals — ranked by how unmapped they still are") +
      '<div class="siglist">' + (sigs.map(sigRow).join("") ||
        '<div class="emptystate">No signals yet — the weekday radar routine fills this.</div>') + "</div>" +
      scoutCard() +
      seclabel("System") +
      "<div>" +
      '<div class="sysline"><span class="healthdot" style="background:' + hs[0] + '"></span>' + esc(hs[1]) +
      " · radar " + esc((((D.health || {}).sessions || {}).routine_status || {}).radar || "?").toLowerCase() +
      " · digest " + esc((((D.health || {}).sessions || {}).routine_status || {}).digest || "?").toLowerCase() + "</div>" +
      sys.map(function (l) { return '<div class="sysline"><span class="mono">' + esc(l) + "</span></div>"; }).join("") +
      "</div>" +
      footer() + "</main>";
  }
  function chainsView() {
    return topbar("chain") + "<main><div class='pagehead'><h1>Chains</h1></div>" + mapCard(null) + "<div class='siglist'>" +
      (D.chains || []).map(function (c) {
        var money = (c.links || []).filter(function (l) { return l.heat && l.heat.money_corner; });
        return '<div class="sigrow" data-nav="#/chain/' + esc(c.id) + '" tabindex="0" role="link"><div>' +
          '<div class="meta">' + chip(c.clock) + "<span>" + (c.links || []).length + " links</span><span>" + (c.scenarios || []).length + " scenarios</span>" + staleChip(c.heat_as_of) + "</div>" +
          '<div class="t">' + esc(c.title) + "</div>" +
          (money.length ? '<div class="th">★ money corner: ' + esc(money.map(function (l) { return l.name; }).join(", ")) + "</div>" : "") +
          "</div></div>";
      }).join("") + "</div>" + footer() + "</main>";
  }

  /* ---------------- signal ---------------- */
  /* The money half of the pair (method 0.2). Rendered NEXT TO unmappedness, never
     instead of it: the queue orders by money and section 0 still selects by how unmapped
     the chain is, and a page that showed only one of the two would quietly restate the
     hunt. An occurrence with no appraisal says so and offers the command, rather than
     rendering a blank where a score would go. */
  function impactCard(s) {
    var a = impactFor(s.id);
    if (!a) {
      return seclabel("Financial impact") +
        "<div class='card'><div class='small muted'>Not appraised yet. Unmappedness says how " +
        "unmapped this chain is; nothing here says how much investable money is behind it.</div>" +
        "<div style='margin-top:10px'>" + runButton("run impact " + s.id,
          "scores the money at stake, how much of it reaches listed issuers, whether it is " +
          "kept as profit, and whether it moves inside the horizon") + "</div></div>";
    }
    if (a.impact_band === "UNRANKED") {
      return seclabel("Financial impact") +
        "<div class='card'><div class='row'>" + impactChip(a) + staleChip(a.as_of) + "</div>" +
        "<div class='small' style='margin-top:8px'>" + esc(a.unranked_reason ||
          "A leg could not be sourced.") + "</div>" +
        "<div class='small muted' style='margin-top:6px'>No published sizing was found. That is " +
        "a finding about how early this occurrence is, not a gap in the writeup.</div></div>";
    }
    /* The four legs, each with its rationale and every cited source's verbatim excerpt
       (app/build.py carries appraisals whole since 2026-09-04). */
    var file = a.id ? "data/impact/" + a.id + ".json" : "data/impact/";
    var legs = [
      ["Money at stake", (a.money_at_stake || {}).band, a.money_at_stake],
      ["Public reach", num((a.public_reach || {}).score), a.public_reach],
      ["Value capture", num((a.capture_odds || {}).score), a.capture_odds],
      ["Timing fit", num((a.timing_fit || {}).score), a.timing_fit]
    ].map(function (r) {
      var leg = r[2] || {}, n = (leg.evidence || []).length;
      return "<div class='evli'><b>" + esc(r[0]) + "</b> " + chip(num(r[1])) +
        chip(n + " cited", n ? "neutral" : "stale") +
        "<div class='small muted'>" + esc(leg.rationale || leg.basis || "") + "</div>" +
        (n ? "<div style='margin-top:6px'>" + evList(leg.evidence) + "</div>" : "") + "</div>";
    }).join("");
    var ca = a.confidence_audit;
    var audit = ca && typeof ca === "object"
      ? "<div class='small muted' style='margin-top:10px'><b>Confidence audit:</b> " +
        Object.keys(ca).map(function (k) { return esc(k) + " " + esc(typeof ca[k] === "object" ? JSON.stringify(ca[k]) : ca[k]); }).join(" · ") + "</div>"
      : "";
    return seclabel("Financial impact") +
      "<div class='card'><div class='row'>" + impactChip(a) +
      chip("unmapped " + num((s.unmappedness || {}).score)) + staleChip(a.as_of) +
      (a.review_by ? "<span class='muted'>review <span class='num'>" + esc(a.review_by) + "</span></span>" : "") + "</div>" +
      "<div class='small' style='margin-top:8px'>" + esc(impactTitle(a)) + "</div>" +
      "<div style='margin-top:10px'>" + legs + "</div>" + audit +
      ((a.ticker_refs || []).length ? "<div class='small muted' style='margin-top:6px'>Tickers referenced: " + esc((a.ticker_refs || []).map(function (t) { return typeof t === "string" ? t : (t.ticker || JSON.stringify(t)); }).join(", ")) + "</div>" : "") +
      "<div class='small muted' style='margin-top:8px'>Canonical file: <span class='mono'>" + esc(file) + "</span></div>" +
      notesBlock(a) + changelogBlock(a) + "</div>";
  }

  function signalView(id) {
    var s = byId(D.signals, id);
    if (!s) return notFound("signal " + id);
    var evid = (s.evidence || []).map(function (e) {
      return '<div class="evli">' + chip(e.tag) + " " + esc(e.claim) +
        (e.source_name ? ' <span class="muted">[' + esc(e.source_name) + (e.source_date ? ", " + esc(e.source_date) : "") + "]</span>" : "") + "</div>";
    }).join("");
    return topbar("radar") + crumbs([{ label: s.title }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip(s.lane) + chip(s.suggested_clock) + chip(s.status, s.status === "NEW" ? "accent" : "neutral") + staleChip(s.updated_at) + "</div>" +
      "<h1>" + esc(s.title) + "</h1><p class='sub'>" + esc(s.thesis) + "</p></div>" +
      '<div class="statgrid" style="margin-top:14px">' +
      "<div class='card'><h3>Why now</h3><div class='small'>" + esc(s.why_now) + "</div></div>" +
      "<div class='card'><h3>The retail gap</h3><div class='small'>" + esc(s.retail_gap) + "</div></div>" +
      "<div class='card'><div class='stat'><span class='v'>" + esc((s.unmappedness || {}).score) + "</span><span class='l'>unmapped / 100 — " + esc((s.unmappedness || {}).rationale) + "</span></div></div></div>" +
      impactCard(s) +
      seclabel("Evidence") + "<div class='card'>" + (evid || "<div class='muted'>none</div>") + "</div>" +
      seclabel("Next step") +
      (s.chain_id ? "<a class='chip accent' href='#/chain/" + esc(s.chain_id) + "'>Open the value chain →</a>" :
        s.status === "NEW" ? runButton("run chain " + s.id, "maps this signal into an 8-15 link value chain") : "<span class='muted'>" + esc(s.status) + "</span>") +
      notesBlock(s) + changelogBlock(s) + footer() + "</main>";
  }

  /* ---------------- chain ----------------
     A finished chain is ~200 KB on disk and ~140 KB projected, and ten of them is 70% of
     the whole file's budget, so app/build.py made chains elastic the way dives already
     were: whole chains in the campaign manifest's theme-rank order while the budget
     lasts, then chains without their cited evidence rows, then a navigation row with no
     written analysis at all. Everything the flow strip, the heat scatter, the scenario
     cards and the cortex draw survives at every fidelity — what a reduced chain loses is
     prose. These two say which, per chain, and name the file that holds the rest, the
     same rule seriesNote() follows. */
  function chainPath(c) { return "data/chains/" + ((c && c.id) || "") + ".json"; }
  var chainTab = "flow";
  function chainView(id, tab) {
    var c = byId(D.chains, id);
    if (!c) return notFound("chain " + id);
    chainTab = (tab === "heat" || tab === "scen" || tab === "flow" || tab === "graph") ? tab : "flow";
    var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
    var scored = links.filter(isPlottable);
    var money = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    var body = chainTab === "heat" ? heatTab(c, links, scored) : chainTab === "scen" ? scenTab(c) : chainTab === "graph" ? graphTab(c, links) : flowTab(c, links);
    var subtitle = scored.length
      ? (money.length ? "Money corner: " + money.map(function (l) { return l.name; }).join(", ") + ". " : "No link clears all three thresholds yet. ") +
        scored.length + " of " + links.length + " links scored, as of " + (c.heat_as_of || "—") + "."
      : "Chain mapped; heat not scored yet.";
    return topbar("chain") + crumbs([{ label: sigTitle(c.signal_id), href: "#/signal/" + c.signal_id }, { label: c.title }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip(c.clock) + (money.length ? '<span class="chip UNDISCOVERED">★ ' + esc(money.map(function (l) { return l.name; }).join(" · ")) + "</span>" : "") + staleChip(c.heat_as_of) +
      (c.chain_fidelity && c.chain_fidelity !== "FULL" ? chip("reduced fidelity", "CROWDED") : "") + "</div>" +
      "<h1>" + esc(c.title) + "</h1><p class='sub'>" + esc(subtitle) + "</p></div>" +
       chainScreenAction(c) + mapCard(c.id) +
      '<div class="seg">' +
      [["flow", "Flow"], ["graph", "Graph"], ["heat", "Heat map"], ["scen", "Scenarios"]].map(function (k) {
        return '<button class="' + (chainTab === k[0] ? "on" : "") + '" data-tab="' + k[0] + '" data-chain="' + esc(c.id) + '">' + k[1] + "</button>";
      }).join("") + "</div>" + body +
      notesBlock(c) + changelogBlock(c) + footer() + "</main>";
  }
  function sigTitle(id) { var s = byId(D.signals, id); return s ? s.title : id; }
  function triad(h) {
    if (!h) return "";
    function bar(o, cls) {
      var v = o && o.score != null ? o.score : 0;
      return '<i class="' + cls + '" style="height:' + Math.max(2, (v / 100) * 22).toFixed(0) + 'px"></i>';
    }
    return '<span class="triad" aria-hidden="true">' + bar(h.impact, "ti") + bar(h.crowdedness, "tc") + bar(h.capture, "tv") + "</span>";
  }
  function flowTab(c, links) {
    var h = '<div class="flow">';
    links.forEach(function (l, i) {
      var hv = (l.heat && l.heat.verdict) || null;
      h += '<div class="fnode ' + (hv ? "v-" + hv : "v-none") + '" data-drawer="' + esc(l.id) + '" tabindex="0" role="button" aria-label="' + esc(l.name) + '">' +
        (l.heat && l.heat.money_corner ? '<span class="star" title="money corner">★</span>' : "") +
        (l.bottleneck && l.bottleneck.criticality === "CHOKE_POINT" ? '<span class="choke" title="choke point"></span>' : "") +
        '<div class="pos">' + String(i + 1).padStart(2, "0") + "</div>" +
        '<div class="nm">' + esc(l.name) + "</div>" +
        '<div class="verd ' + (hv ? "v-" + hv : "v-none") + '">' + (hv ? esc(hv.replace("_", " ")) : "unscored") + "</div>" +
        triad(l.heat) + "</div>";
      if (i < links.length - 1) h += '<div class="farrow">›</div>';
    });
    h += "</div>";
    h += '<div class="legend">' +
      '<span><span class="tri-demo"><i style="height:11px;background:var(--accent)"></i><i style="height:7px;background:var(--crd)"></i><i style="height:9px;background:var(--und)"></i></span>impact · crowdedness · capture</span>' +
      [["Undiscovered", "--und"], ["Emerging", "--emg"], ["Crowded", "--crd"], ["Over-crowded", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") +
      '<span style="color:var(--gold)">★ money corner</span><span><span class="choke" style="position:static;display:inline-block;vertical-align:-1px;margin-right:6px"></span>choke point</span>' +
      "</div>";
    h += "<div id='drawerHost'></div>";
    return h;
  }
  function scoreBar(name, obj, colorVar) {
    if (!obj || obj.score == null) return '<div class="scorebar"><span class="lb">' + esc(name) + '</span><div class="tk"></div><span class="vl muted">—</span></div>';
    return '<div class="scorebar"><span class="lb">' + esc(name) + '</span><div class="tk"><i style="width:' + obj.score + "%;background:var(" + colorVar + ')"></i></div><span class="vl">' + obj.score + "</span></div>";
  }
  // The link click opens this near-fullscreen modal (~70% of the viewport): the heat
  // analysis and its "why" on the left, the stocks screened onto THIS link with their
  // fundamentals on the right. It replaced the narrow right-side drawer. Stock rows and
  // their fundamentals come from the chain-wide screen (chainScreen), which app/build.py
  // keeps whole; cap/price/52w ride on each row's valuation block (build.py attaches it
  // from data/market, because project_market strips raw fundamentals off the page).
  function linkModal(c, l) {
    var h = l.heat || {};
    // The page carries the first few evidence items per leg, not all of them, and it says so.
    // A silent window is the defect check_render.py exists to catch: a reader counting three
    // sources under a score would otherwise believe three is all there is.
    function ev(o) {
      var items = (o || {}).evidence || [];
      return evList(items) + (items.length ? "" : "<div class='evli muted'>no cited source recorded on this leg</div>");
    }
    function block(title, o) {
      if (!o) return "";
      var why = o.rationale != null
        ? "<div class='small'>" + esc(o.rationale) + "</div>"
        : (o.score != null ? "<div class='small muted'>Scored " + o.score + "/100 with no written rationale on file.</div>" : "");
      return "<h3>" + title + "</h3>" + why + ev(o);
    }
    function kvBlock(obj) {
      if (!obj) return "";
      if (Array.isArray(obj)) return "<ul class='bullets'>" + obj.map(function (x) { return "<li>" + esc(typeof x === "string" ? x : JSON.stringify(x)) + "</li>"; }).join("") + "</ul>";
      if (typeof obj !== "object") return "<div class='small'>" + esc(String(obj)) + "</div>";
      return "<div class='kv'>" + Object.keys(obj).map(function (k) {
        var v = obj[k];
        var text = v == null ? "null" : (typeof v === "object" ? (v.note || v.basis ? [v.state, v.note || v.basis].filter(Boolean).join(" · ") : JSON.stringify(v)) : String(v));
        return "<dt>" + esc(k.replace(/_/g, " ")) + "</dt><dd class='small'>" + esc(text) + "</dd>";
      }).join("") + "</div>";
    }
    var chips = '<div class="row">' + (h.verdict ? chip(h.verdict.replace("_", " "), h.verdict) : chip("unscored")) + (h.money_corner ? '<span class="chip UNDISCOVERED">★ money corner</span>' : "") + chip((l.investability || "").replace(/_/g, " ")) + ((l.bottleneck || {}).criticality ? chip(l.bottleneck.criticality === "CHOKE_POINT" ? "choke point" : "bottleneck " + String(l.bottleneck.criticality).toLowerCase()) : chip("bottleneck not rated", "stale")) + "</div>";
    var analysis = explainerBlock(c, l) +
      scoreBar("Impact", h.impact, "--accent") + scoreBar("Crowdedness", h.crowdedness, "--crd") + scoreBar("Value capture", h.capture, "--und") +
      block("Impact", h.impact) + block("Crowdedness", h.crowdedness) + block("Value capture", h.capture) +
      (h.repricing_check && (h.repricing_check.note || h.repricing_check.legs) ? "<h3>Repricing check</h3><div class='small'>" + (h.repricing_check.legs_met != null ? "<span class='num'>" + h.repricing_check.legs_met + "/4 legs</span> · " : "") + esc(h.repricing_check.note || "") + "</div>" +
        (h.repricing_check.legs ? kvBlock(h.repricing_check.legs) : "") : "") +
      (l.capture_inputs ? "<h3>Capture inputs</h3>" + kvBlock(l.capture_inputs) : "") +
      (((l.bottleneck || {}).note || (l.bottleneck || {}).basis) ? "<h3>Bottleneck</h3><div class='small'>" + esc(l.bottleneck.note || l.bottleneck.basis) + "</div>" : "") +
      "<h3>Feeds</h3><div class='small'>" + ((l.upstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "none") + "</div>" +
      "<h3>Fed by</h3><div class='small'>" + ((l.downstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "none") + "</div>" +
      ((l.evidence || []).length ? "<h3>Map citations</h3><div class='small muted'>" + l.evidence.length + " dated source(s) behind this link</div>" + evList(l.evidence) : "<h3>Map citations</h3><div class='small muted'>no dated source recorded on this link</div>") +
      (h.verdict ? "" : "<div style='margin-top:16px'>" + runButton("run heat " + c.id, "scores every unscored link with fetched evidence", { compact: true }) + "</div>");

    var sc = chainScreen(c.id);
    var rows = [];
    if (sc) Object.keys(sc.buckets || {}).forEach(function (k) { (sc.buckets[k] || []).forEach(function (r) { if (r.link_id === l.id) rows.push({ r: r, bucket: k }); }); });
    var seen = {};
    rows.forEach(function (x) { seen[x.r.ticker] = 1; });
    var stocks;
    if (rows.length) {
      stocks = rows.map(function (x) { return stockCard(c, x.r, x.bucket); }).join("");
      var others = (l.example_tickers || []).filter(function (t) { return !seen[t]; });
      if (others.length) stocks += "<div class='stk-more'><h4>Also named on this link</h4><div class='row'>" + others.map(function (t) { return chip(t, "neutral"); }).join("") + "</div><div class='muted small' style='margin-top:6px'>not screened yet, no fundamentals fetched</div></div>";
    } else {
      stocks = "<div class='stk-empty'>No stocks screened onto this link yet." +
        ((l.example_tickers || []).length ? "<div class='row' style='margin:10px 0'>" + (l.example_tickers || []).map(function (t) { return chip(t, "neutral"); }).join("") + "</div><div class='muted small'>example names only; run a screen to appraise them and fetch fundamentals</div>" : "") +
        "<div style='margin-top:12px'>" + runButton("run screen " + c.id, "screens every link on this chain for listed names", { compact: true }) + "</div></div>";
    }

    return '<div class="scrim" data-closedrawer></div><div class="modal-card" role="dialog" aria-label="' + esc(l.name) + '"><button class="x" data-closedrawer>✕</button>' +
      '<div class="modal-head">' + chips + "<h2>" + esc(l.name) + "</h2><p class='small'>" + esc(l.role) + "</p></div>" +
      '<div class="modal-body">' +
        '<div class="modal-pane modal-analysis">' + analysis + "</div>" +
        '<div class="modal-pane modal-stocks"><div class="stocks-h">Stocks on this link' + (rows.length ? " <span class='num'>" + rows.length + "</span>" : "") + "</div>" + stocks + "</div>" +
      "</div></div>";
  }

  // One stock card in the modal's right pane. Fundamentals come from the screen row
  // (curated, VERIFIED-tagged); cap/price/52w from row.valuation. Every figure renders
  // only when it is really present: absent is a muted "n/a", never an invented number,
  // and a legitimate 0 (a low Piotroski) renders as 0. See tools/check_render.py.
  function stockCard(c, r, bucket) {
    var f = (r.fundamentals && typeof r.fundamentals === "object") ? r.fundamentals : null;
    var v = (r.valuation && typeof r.valuation === "object") ? r.valuation : null;
    var BN = { pure_play: "Pure play", picks_and_shovels: "Picks and shovels", second_order: "Second order", hedge: "Hedge" };
    function fval(x) { return (x && typeof x === "object" && "value" in x) ? x.value : (typeof x === "number" ? x : null); }
    function bigMoney(n) {
      if (typeof n !== "number") return null;
      var a = Math.abs(n);
      if (a >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
      if (a >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
      if (a >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
      return "$" + Math.round(n).toLocaleString();
    }
    function pct(x) { return (typeof x === "number") ? (x * 100).toFixed(0) + "%" : null; }
    function price(n) { return (typeof n === "number") ? "$" + n.toFixed(2) : null; }
    function stat(label, val) { return "<div class='sc-stat'><span class='sc-l'>" + esc(label) + "</span><span class='sc-v'>" + (val == null ? "<span class='muted'>n/a</span>" : val) + "</span></div>"; }

    var cap = v ? bigMoney(v.market_cap) : null;
    var px = v ? price(v.price) : null;
    var w52 = (v && typeof v.week52_low === "number" && typeof v.week52_high === "number") ? ("$" + Math.round(v.week52_low) + " to $" + Math.round(v.week52_high)) : null;
    var rev = f ? bigMoney(fval(f.revenue_fy)) : null;
    var ni = f ? bigMoney(fval(f.net_income_fy)) : null;
    var revg = f ? pct(fval(f.revenue_cagr_3y)) : null;
    var fcf = f ? pct(fval(f.market_implied_fcf_cagr)) : null;
    var pio = (f && typeof f.piotroski === "number") ? (f.piotroski + " / 9") : null;
    var ben = (f && typeof f.beneish_state === "string") ? f.beneish_state : null;
    var cwo = (r.crowdedness && typeof r.crowdedness === "object") ? r.crowdedness : null;
    var cw = (cwo && cwo.state) ? chip(cwo.state + (typeof cwo.pcs_axis_a === "number" ? " " + Math.round(cwo.pcs_axis_a) : ""), cwo.state === "DARK" ? "UNDISCOVERED" : cwo.state === "CROWDED" ? "CROWDED" : "neutral") : "";
    var nug = (r.earnings_nuggets || []).length ? "<details class='sc-nug'><summary>" + r.earnings_nuggets.length + " earnings nugget(s)</summary>" + r.earnings_nuggets.map(function (n) { return "<blockquote>“" + esc(n.quote) + "”<div class='muted'>" + esc(n.form || "") + (n.url ? " · <a href='" + esc(n.url) + "' target='_blank' rel='noopener'>" + esc(n.accession || "filing") + "</a>" : "") + "</div></blockquote>"; }).join("") + "</details>" : "";
    var act = r.status === "DIVED" ? '<a class="chip accent" href="#/stock/' + esc(r.ticker) + "/" + esc(c.id) + '">Full dive →</a>' :
      r.status === "CANDIDATE" ? "<span data-stop>" + runButton("run deepdive " + r.ticker + " " + c.id, null, { compact: true }) + "</span>" :
      (r.status ? chip(r.status) : "");

    return "<div class='stockcard'>" +
      "<div class='sc-head'><div class='sc-id'><span class='sc-tk'>" + esc(r.ticker) + "</span>" + (r.money_corner ? "<span class='star' title='money-corner link'>★</span>" : "") + tierChip(r.tier) + "<span class='sc-bkt'>" + esc(BN[bucket] || bucket) + "</span></div><div class='sc-co'>" + esc(r.name || "") + (r.exchange ? " · " + esc(r.exchange) : "") + "</div></div>" +
      (r.thesis_1line ? "<div class='sc-thesis'>" + esc(r.thesis_1line) + "</div>" : "") +
      "<div class='sc-grid'>" +
        stat("Market cap", cap) + stat("Price", px) + stat("52 week", w52) +
        stat("Revenue FY", rev) + stat("Rev 3y CAGR", revg) + stat("Net income", ni) +
        stat("Implied FCF CAGR", fcf) + stat("Piotroski F", pio) + stat("Beneish", ben) +
      "</div>" +
      (cw ? "<div class='sc-cw'><span class='sc-l'>Crowdedness</span> " + cw + "</div>" : "") +
      nug +
      (act ? "<div class='sc-act'>" + act + "</div>" : "") +
      "</div>";
  }
  function linkName(c, id) { var l = byId(c.links, id); return l ? l.name : id; }
  function shortName(n) { return n.split("(")[0].replace(" & ", " · ").trim(); }

  /* ---------------- the chain graph and the Hebrew explainer layer ----------------
     The Flow strip draws links in position order and throws the graph away: upstream_of
     and downstream_of are validated on disk (reciprocal, acyclic, position-ordered) and
     were used by no visual. chainLayout() lays the real DAG out in columns (longest path
     from the roots; every sink in the last column so the demand anchors sit rightmost),
     rows ordered by two barycenter sweeps, deterministic, no force simulation. The
     explainer is an authored Hebrew field on each link and on the chain
     (data/chains/<slug>.json, gated by tools/check_chain.py: no figures, no dashes); the
     recipients under "what passes on" are always derived from upstream_of at render time
     so the prose can never disagree with the graph. */
  var G = { NODE_W: 150, NODE_H: 58, COL_GAP: 70, ROW_GAP: 16, PAD: { l: 24, r: 24, t: 28, b: 28 } };
  function chainLayout(links) {
    var by = {};
    links.forEach(function (l) { by[l.id] = l; });
    var memo = {}, visiting = {};
    function layer(id) {
      if (memo[id] != null) return memo[id];
      if (visiting[id]) return 0;
      visiting[id] = 1;
      var best = -1;
      ((by[id] && by[id].downstream_of) || []).forEach(function (pid) { if (by[pid]) best = Math.max(best, layer(pid)); });
      visiting[id] = 0;
      memo[id] = best + 1;
      return memo[id];
    }
    var col = {}, last = 0;
    links.forEach(function (l) { col[l.id] = layer(l.id); last = Math.max(last, col[l.id]); });
    links.forEach(function (l) { if (!(l.upstream_of || []).length) col[l.id] = last; });
    var cols = [];
    for (var i = 0; i <= last; i++) cols.push([]);
    links.slice().sort(function (a, b) { return a.position - b.position; }).forEach(function (l) { cols[col[l.id]].push(l.id); });
    var row = {};
    function settle() { cols.forEach(function (cl) { cl.forEach(function (id, r) { row[id] = r; }); }); }
    settle();
    function sweep(indices, side) {
      indices.forEach(function (ci) {
        var keys = {};
        cols[ci].forEach(function (id) {
          var nb = (by[id][side] || []).filter(function (o) { return by[o] && col[o] !== ci; });
          keys[id] = nb.length ? nb.reduce(function (a, o) { return a + row[o]; }, 0) / nb.length : row[id];
        });
        cols[ci].sort(function (a, b) { return (keys[a] - keys[b]) || (by[a].position - by[b].position); });
        settle();
      });
    }
    var fwd = [], back = [];
    for (var k = 1; k <= last; k++) fwd.push(k);
    for (var m = last - 1; m >= 0; m--) back.push(m);
    sweep(fwd, "downstream_of");
    sweep(back, "upstream_of");
    sweep(fwd, "downstream_of");
    var rowsMax = cols.reduce(function (a, cl) { return Math.max(a, cl.length); }, 0);
    var stride = G.NODE_H + G.ROW_GAP, hMax = rowsMax * stride - G.ROW_GAP;
    var nodes = {};
    cols.forEach(function (cl, ci) {
      var hCol = cl.length * stride - G.ROW_GAP;
      cl.forEach(function (id, r) {
        nodes[id] = { x: G.PAD.l + ci * (G.NODE_W + G.COL_GAP), y: G.PAD.t + (hMax - hCol) / 2 + r * stride, col: ci, row: r };
      });
    });
    var edges = [];
    links.forEach(function (l) { (l.upstream_of || []).forEach(function (t) { if (nodes[t]) edges.push({ from: l.id, to: t }); }); });
    return { nodes: nodes, edges: edges, cols: cols, W: G.PAD.l + (last + 1) * (G.NODE_W + G.COL_GAP) - G.COL_GAP + G.PAD.r, H: G.PAD.t + hMax + G.PAD.b };
  }
  function wrapText(name, maxChars, maxLines) {
    var words = name.split(" "), out = [], cur = "";
    words.forEach(function (w) {
      if (cur && (cur + " " + w).length > maxChars) { out.push(cur); cur = w; } else cur = cur ? cur + " " + w : w;
    });
    if (cur) out.push(cur);
    if (out.length > maxLines) { out = out.slice(0, maxLines); out[maxLines - 1] = out[maxLines - 1].slice(0, maxChars - 1) + "…"; }
    return out;
  }
  /* Three bars per node, one per heat leg. A null score draws NO bar and the group title
     says unscored; it is never drawn at zero height, because 0 is a score. */
  function triadSvg(h, x, y) {
    if (!h) return "";
    var s = "", legs = [[h.impact, "var(--accent)"], [h.crowdedness, "var(--crd)"], [h.capture, "var(--und)"]];
    legs.forEach(function (lg, i) {
      var o = lg[0];
      if (!o || o.score == null) return;
      var bh = Math.max(2, (o.score / 100) * 18);
      s += '<rect x="' + (x + i * 7) + '" y="' + (y - bh).toFixed(1) + '" width="5" height="' + bh.toFixed(1) + '" rx="1.5" fill="' + lg[1] + '"/>';
    });
    return s;
  }
  function graphTab(c, links) {
    var g = chainLayout(links), by = {};
    links.forEach(function (l) { by[l.id] = l; });
    var s = overviewBlock(c);
    s += '<div class="card"><div class="chartwrap"><svg viewBox="0 0 ' + g.W + " " + g.H + '" width="' + g.W + '" height="' + g.H + '" role="img" aria-label="Value chain graph, upstream on the left">';
    s += '<defs><marker id="gArrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0.5 L8,4 L0,7.5 z" fill="var(--border-strong)"/></marker></defs>';
    g.edges.forEach(function (e) {
      var a = g.nodes[e.from], b = g.nodes[e.to];
      var x1 = a.x + G.NODE_W, y1 = a.y + G.NODE_H / 2, x2 = b.x, y2 = b.y + G.NODE_H / 2;
      s += '<path class="gedge" data-from="' + esc(e.from) + '" data-to="' + esc(e.to) + '" marker-end="url(#gArrow)" d="M' + x1 + "," + y1 + " C" + (x1 + 35) + "," + y1 + " " + (x2 - 35) + "," + y2 + " " + x2 + "," + y2 + '"><title>' + esc(linkName(c, e.from)) + " → " + esc(linkName(c, e.to)) + "</title></path>";
    });
    links.forEach(function (l) {
      var n = g.nodes[l.id];
      if (!n) return;
      var hv = (l.heat && l.heat.verdict) || null;
      var vcls = hv ? "v-" + hv : "v-none";
      s += '<g class="gnode ' + vcls + '" transform="translate(' + n.x + "," + n.y + ')" data-drawer="' + esc(l.id) + '" tabindex="0" role="button" aria-label="' + esc(l.name) + '">';
      s += "<title>" + esc(l.name) + (hv ? " · " + esc(hv.replace("_", " ")) : " · unscored") + "</title>";
      s += '<rect class="body' + (hv ? "" : " unscored") + '" x="0" y="0" width="' + G.NODE_W + '" height="' + G.NODE_H + '" rx="10"/>';
      s += '<rect x="0" y="10" width="4" height="' + (G.NODE_H - 20) + '" rx="2" fill="' + verdColor(hv) + '"/>';
      if (l.bottleneck && l.bottleneck.criticality === "CHOKE_POINT") s += '<circle cx="14" cy="0" r="4.5" fill="var(--bg)" stroke="var(--ovr)" stroke-width="2"><title>choke point</title></circle>';
      if (l.heat && l.heat.money_corner) s += '<text class="star" x="' + (G.NODE_W - 16) + '" y="17">★<title>money corner</title></text>';
      s += '<text class="pos" x="12" y="15">' + String(l.position).padStart(2, "0") + "</text>";
      wrapText(shortName(l.name), 21, 2).forEach(function (ln, i) { s += '<text class="nm" x="12" y="' + (29 + i * 12) + '">' + esc(ln) + "</text>"; });
      s += '<text class="verd ' + vcls + '" x="12" y="53">' + (hv ? esc(hv.replace("_", " ")) : "unscored") + "</text>";
      s += triadSvg(l.heat, G.NODE_W - 34, 52);
      s += "</g>";
    });
    s += "</svg></div>";
    s += '<div class="legend">' +
      '<span><span class="tri-demo"><i style="height:11px;background:var(--accent)"></i><i style="height:7px;background:var(--crd)"></i><i style="height:9px;background:var(--und)"></i></span>impact · crowdedness · capture</span>' +
      [["Undiscovered", "--und"], ["Emerging", "--emg"], ["Crowded", "--crd"], ["Over-crowded", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") +
      '<span style="color:var(--gold)">★ money corner</span><span><span class="choke" style="position:static;display:inline-block;vertical-align:-1px;margin-right:6px"></span>choke point</span>' +
      "<span>upstream on the left, demand on the right; hover a stage to light its edges</span></div>";
    var roots = links.filter(function (l) { return !(l.downstream_of || []).length; }).length;
    var widest = links.slice().sort(function (a, b) { return (b.downstream_of || []).length - (a.downstream_of || []).length; })[0];
    s += '<div class="gshape he" dir="rtl" lang="he">נקודות מוצא: <bdi>' + roots + "</bdi> · שלבים: <bdi>" + g.cols.length + "</bdi> · צומת ההתכנסות הרחב ביותר: <bdi>" + esc(widest ? widest.name : "") + "</bdi> (<bdi>" + (widest ? (widest.downstream_of || []).length : 0) + "</bdi> חוליות נכנסות)</div>";
    s += "</div>";
    s += "<div id='drawerHost'></div>";
    return s;
  }
  function explainerBlock(c, l) {
    var x = l.explainer;
    if (x == null) return '<section class="he" dir="rtl" lang="he"><div class="absent">אין עדיין הסבר בעברית לחוליה זו. הוא ייכתב אל <bdi>' + esc(chainPath(c)) + "</bdi>.</div></section>";
    function sec(t, v) { return v == null ? "" : "<h3>" + t + "</h3><p>" + esc(v) + "</p>"; }
    var to = (l.upstream_of || []).map(function (id) { return "<bdi>" + esc(linkName(c, id)) + "</bdi>"; });
    return '<section class="he" dir="rtl" lang="he">' +
      sec("מה זה", x.what) + sec("מי משחק כאן", x.players) + sec("למה זה חשוב", x.why) + sec("צוואר בקבוק?", x.bottleneck) +
      "<h3>מה עובר הלאה</h3><p>" + esc(x.hands_to) + (to.length ? " · אל: " + to.join(", ") : " · סוף השרשרת") + "</p>" +
      '<div class="stamp">נכתב <bdi>' + esc(x.as_of) + "</bdi> · <bdi>" + esc(x.by) + "</bdi></div></section>";
  }
  function overviewBlock(c) {
    var x = c.explainer;
    if (x == null) return '<div class="card he" dir="rtl" lang="he"><div class="absent">אין עדיין הסבר בעברית לשרשרת זו. הוא ייכתב אל <bdi>' + esc(chainPath(c)) + "</bdi>.</div></div>";
    function sec(t, v) { return v == null ? "" : "<h3>" + t + "</h3><p>" + esc(v) + "</p>"; }
    return '<div class="card he" dir="rtl" lang="he">' + sec("צורת השרשרת", x.shape) + sec("התזה", x.thesis) +
      '<div class="stamp">נכתב <bdi>' + esc(x.as_of) + "</bdi> · <bdi>" + esc(x.by) + "</bdi></div></div>";
  }
  function heatTab(c, links, scored) {
    if (!scored.length) return '<div class="emptystate">No links scored yet.<div class="runwrap">' + runButton("run heat " + c.id) + "</div></div>";
    var W = 940, H = 560, P = { l: 64, r: 40, t: 34, b: 52 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    function X(v) { return P.l + (v / 100) * iw; }
    function Y(cr) { return P.t + (cr / 100) * ih; } // crowdedness 0 at TOP → money corner top-right
    var s = '<div class="card"><div class="chartwrap"><svg viewBox="0 0 ' + W + " " + H + '" width="100%" style="max-width:' + W + 'px" role="img" aria-label="Impact vs crowdedness map">';
    s += '<rect x="' + X(60) + '" y="' + Y(0) + '" width="' + (X(100) - X(60)) + '" height="' + (Y(40) - Y(0)) + '" fill="var(--band-good)" rx="10"/>';
    s += '<text x="' + X(80) + '" y="' + (Y(0) + 20) + '" text-anchor="middle" font-size="11" font-weight="650" fill="var(--und)" letter-spacing="1">★ MONEY CORNER</text>';
    [0, 25, 50, 75, 100].forEach(function (v) {
      s += '<line x1="' + X(v) + '" y1="' + P.t + '" x2="' + X(v) + '" y2="' + (H - P.b) + '" stroke="var(--chart-grid)"/>';
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)"/>';
      s += '<text x="' + X(v) + '" y="' + (H - P.b + 20) + '" text-anchor="middle" font-size="10" class="mono-t" fill="var(--chart-axis)">' + v + "</text>";
      s += '<text x="' + (P.l - 12) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t" fill="var(--chart-axis)">' + v + "</text>";
    });
    s += '<line x1="' + X(60) + '" y1="' + P.t + '" x2="' + X(60) + '" y2="' + (H - P.b) + '" stroke="var(--chart-axis)" stroke-dasharray="3 5"/>';
    s += '<line x1="' + P.l + '" y1="' + Y(40) + '" x2="' + (W - P.r) + '" y2="' + Y(40) + '" stroke="var(--chart-axis)" stroke-dasharray="3 5"/>';
    s += '<text x="' + (P.l + iw / 2) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="11.5" font-weight="550">Impact →</text>';
    s += '<text transform="rotate(-90)" x="' + (-(P.t + ih / 2)) + '" y="18" text-anchor="middle" font-size="11.5" font-weight="550">Quieter →</text>';
    var placed = [];
    function labelSpot(x, yCands, name) {
      var w = name.length * 5.6;
      for (var ci = 0; ci < yCands.length; ci++) {
        var y = yCands[ci];
        if (y < P.t + 10 || y > H - P.b - 4) continue;
        var hit = placed.some(function (p) { return Math.abs(p.y - y) < 13 && (Math.abs(p.x - x) < (p.w + w) / 2 + 6); });
        if (!hit) { placed.push({ x: x, y: y, w: w }); return y; }
      }
      var y2 = yCands[yCands.length - 1];
      placed.push({ x: x, y: y2, w: w });
      return y2;
    }
    scored.slice().sort(function (a, b) { return a.heat.crowdedness.score - b.heat.crowdedness.score; }).forEach(function (l) {
      /* capture used to default to 40 when the block was absent, and that invented 40 was
         printed in the hover title as though it were a score. A missing score is drawn at
         a fixed small radius and SAYS it is unscored. Note `!= null` throughout rather
         than a truthiness test: a legitimate score of 0 is a finding, not a blank. */
      var im = l.heat.impact ? l.heat.impact.score : null, cr = l.heat.crowdedness.score;
      var cp = (l.heat.capture && l.heat.capture.score != null) ? l.heat.capture.score : null;
      if (im == null || cr == null) return;
      var r = cp == null ? 6 : 6 + cp / 10;
      var nm = shortName(l.name);
      var ly = labelSpot(X(im), [Y(cr) - r - 8, Y(cr) + r + 14, Y(cr) - r - 22, Y(cr) + r + 28, Y(cr) - r - 36], nm);
      s += '<circle cx="' + X(im) + '" cy="' + Y(cr) + '" r="' + r.toFixed(1) + '" fill="' + verdColor(l.heat.verdict) + '" fill-opacity="0.85" stroke="var(--surface)" stroke-width="2"' +
        (cp == null ? ' stroke-dasharray="2 2"' : "") + "><title>" + esc(l.name) + " — impact " + im + ", crowdedness " + cr + ", capture " + (cp == null ? "unscored" : cp) + "</title></circle>";
      s += '<text x="' + X(im) + '" y="' + ly + '" text-anchor="middle" font-size="10.5" font-weight="600" fill="var(--ink)">' + esc(nm) + "</text>";
    });
    s += "</svg></div>";
    s += '<div class="legend"><span>dot size = value capture</span>' +
      [["Undiscovered", "--und"], ["Emerging", "--emg"], ["Crowded", "--crd"], ["Over-crowded", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") + "</div></div>";
    var un = links.filter(function (l) { return !isPlottable(l); });
    if (un.length) s += '<div class="muted" style="margin-top:10px">Not scored: ' + un.map(function (l) { return esc(l.name); }).join(", ") + "</div>";
    var mc = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    s += '<div class="callout" style="margin-top:14px">' +
      (function () {
        var bars = "impact ≥ " + MC.impact_min + ", crowdedness ≤ " + MC.crowd_max + ", capture ≥ " + MC.capture_min;
        return mc.length
          ? "<b>★ " + mc.map(function (l) { return esc(l.name); }).join(" · ") + "</b> clears all three bars (" + bars + "). "
          : "<b>No money corner yet</b> — no link clears " + bars + " together. ";
      })() +
      "Quiet links with <b>small dots</b> are the trap: ignored because capture is capped, not because the market missed them.</div>";
    return s;
  }
  // method section 5 and check_scenarios.py both accept an indicator keyed `signal` OR
  // `indicator`, and the corpus uses both: 141 of 173 on disk are `signal`. app.js read only
  // `.indicator`, so 141 of them rendered as an empty <li> with a stray "(where to watch)"
  // beside it. The data was never wrong; the page was reading one of two valid shapes.
  function indText(i) { return (i && (i.indicator || i.signal || i.name)) || ""; }

  function scenTab(c) {
    var scens = c.scenarios || [];
    if (!scens.length) return '<div class="emptystate">No scenarios yet.<div class="runwrap">' + runButton("run scenarios " + c.id) + "</div></div>";
    return scens.map(function (s) {
      var mv = (s.links_moved || []).map(function (m) {
        return '<span class="mv" title="' + esc(m.why || "") + '"><span class="' + (m.direction === "UP" ? "up" : "down") + '">' + (m.direction === "UP" ? "↑" : "↓") + "</span>" + esc(shortName(linkName(c, m.link_id))) + " · " + esc(String(m.magnitude || "").toLowerCase()) + "</span>";
      }).join("");
      var inds = (s.leading_indicators || []).map(function (i) {
        var trip = ((D.indicators || {}).trips || []).filter(function (t) {
          return t.chain === c.id && t.scenario === s.id && t.indicator === indText(i);
        })[0];
        var trippedAt = i.tripped_at || (trip && trip.tripped_at);
        var badge = trippedAt ? chip("tripped " + trippedAt, "OVER_CROWDED") : i.armed ? chip("armed", "accent") : "";
        return "<li>" + esc(indText(i)) + (i.where_to_watch ? " <span class='muted'>(" + esc(i.where_to_watch) + ")</span>" : "") + " " + badge +
          (i.check_basis ? "<div class='muted small'>" + esc(i.check_basis) + "</div>" : "") + "</li>";
      }).join("");
      var whys = (s.links_moved || []).filter(function (m) { return m && m.why; }).map(function (m) {
        return "<div class='small'><b>" + esc(shortName(linkName(c, m.link_id))) + ":</b> " + esc(m.why) + "</div>";
      }).join("");
      return '<div class="card scen"><div class="head"><span class="t">' + esc(s.id) + " — " + esc(s.title) + "</span>" +
        chip(s.status, s.status === "SCREENED" ? "accent" : "neutral") + (s.clock && s.clock !== c.clock ? chip(s.clock) : "") +
        '<span class="p">' + esc(s.probability_pct) + "<small>%</small></span></div>" +
        '<div class="pbar"><i style="width:' + esc(s.probability_pct) + '%"></i></div>' +
        "<div class='small'>" + esc(s.narrative) + "</div>" +
        "<div style='margin:10px 0 2px'>" + mv + "</div>" +
        (whys ? "<div style='margin:6px 0'>" + whys + "</div>" : "") +
        "<details><summary>Leading indicators & invalidation</summary><div class='body'><ul class='bullets'>" + inds + "</ul>" +
        "<div class='small' style='margin-top:8px'><b>Invalidation:</b> " + ((s.invalidation_signs || []).length ? (s.invalidation_signs || []).map(esc).join(" · ") : "<span class='muted'>none recorded</span>") + "</div>" +
        ((s.evidence || []).length ? "<div style='margin-top:8px'><b class='small'>Sources (" + s.evidence.length + ")</b>" + evList(s.evidence) + "</div>" : "") +
        "</div></details>" +
        "<div style='margin-top:14px'>" + (s.screen_ref ? '<a class="chip accent" href="#/screen/' + esc(c.id) + "/" + esc(s.id) + '">Open stock screen →</a>' : runButton("run screen " + c.id + " " + s.id, "screens stocks for this scenario, bucketed and tiered")) + "</div></div>";
    }).join("");
  }

  /* ---------------- screen ---------------- */
  function chainScreen(chainId) {
    var found = null;
    (D.screens || []).forEach(function (s) {
      if (s.chain_id === chainId && (s.scenario_id === null || s.scenario_id === undefined)) found = s;
    });
    return found;
  }
  function screenNameCount(sc) {
    var n = 0;
    Object.keys(sc.buckets || {}).forEach(function (k) { n += (sc.buckets[k] || []).length; });
    return n;
  }
  function chainScreenAction(c) {
    var sc = chainScreen(c.id);
    if (sc) {
      var n = screenNameCount(sc);
      return '<div class="chainaction"><div><span class="lbl">Stock opportunities</span>' +
        '<span class="val">' + n + ' name' + (n === 1 ? "" : "s") + ' across the chain</span>' +
        '<span class="lbl">as of ' + esc(sc.as_of || "?") + "</span></div>" +
        '<div class="row"><a class="chip accent" href="#/screen/' + esc(c.id) + '">Open →</a>' +
        "<span data-stop>" + runButton("run screen " + c.id, null, { compact: true }) + "</span></div></div>";
    }
    /* "not screened yet" is only true of the CHAIN-level screen. Scenario screens
       (<chain>__Sn.json) are a different scope and chainScreen() correctly ignores them,
       but the sentence read as though nothing had ever been screened on this chain while
       a scenario screen sat in data/screens/ saying otherwise. Say which scope is missing. */
    var scen = (D.screens || []).filter(function (x) { return x.chain_id === c.id && x.scenario_id; });
    return '<div class="chainaction"><div><span class="lbl">Stock opportunities</span>' +
      '<span class="val">' + (scen.length
        ? "no chain-wide screen yet — " + scen.length + " scenario screen" + (scen.length === 1 ? "" : "s")
        : "not screened yet") + "</span>" +
      '<span class="lbl">finds every name this chain touches, link by link</span></div>' +
      "<div class='row'>" + (scen.length
        ? scen.map(function (x) { return '<a class="chip" href="#/screen/' + esc(x.chain_id) + "/" + esc(x.scenario_id) + '">' + esc(x.scenario_id) + " →</a>"; }).join("")
        : "") +
      "<span data-stop>" + runButton("run screen " + c.id, null, { compact: true }) + "</span></div></div>";
  }

  function chainScreenView(chainId, byBucket) {
    var c = byId(D.chains, chainId);
    var sc = chainScreen(chainId);
    if (!sc) return notFound("chain screen " + chainId);
    var BN = { pure_play: "Pure play", picks_and_shovels: "Picks and shovels", second_order: "Second order", hedge: "Hedge" };
    var rows = [];
    Object.keys(BN).forEach(function (k) {
      (sc.buckets[k] || []).forEach(function (r) { rows.push({ r: r, bucket: k }); });
    });
    function nameCell(x) {
      var r = x.r;
      var mk = marketFor(r.ticker);
      var dot = mk ? '<span class="pxdot on" title="market data present"></span>'
                   : '<span class="pxdot" title="no market data yet — request queued"></span>';
      var act = r.status === "DIVED"
        ? '<a class="chip accent" href="#/stock/' + esc(r.ticker) + "/" + esc(chainId) + '">Dive →</a>'
        : r.status === "CANDIDATE" ? "<span data-stop>" + runButton("run deepdive " + r.ticker + " " + chainId, null, { compact: true }) + "</span>"
        : chip(r.status);
      return "<tr><td>" + dot + "<span class='tk-name'>" + esc(r.ticker) + "</span>" +
        (r.money_corner ? '<span class="star" title="money-corner link">★</span>' : "") +
        "<div class='tk-co'>" + esc(r.name || "") + "</div></td>" +
        "<td>" + tierChip(r.tier) + "</td>" +
        "<td class='small' style='max-width:300px'>" + esc(r.thesis_1line) + "</td>" +
        "<td>" + chip(BN[x.bucket] || x.bucket) + "</td>" +
        "<td>" + act + "</td></tr>";
    }
    function table(inner) {
      return '<div class="tablewrap"><table><thead><tr><th>Name</th><th>Tier</th><th>Thesis</th><th>Bucket</th><th></th></tr></thead><tbody>' +
        inner + "</tbody></table></div>";
    }
    var body;
    if (byBucket) {
      body = Object.keys(BN).map(function (k) {
        var rs = rows.filter(function (x) { return x.bucket === k; });
        if (!rs.length) return "";
        return seclabel(BN[k] + " — " + rs.length) + table(rs.map(nameCell).join(""));
      }).join("");
    } else {
      var links = ((c || {}).links || []).slice().sort(function (a, b) { return a.position - b.position; });
      body = links.map(function (l) {
        var rs = rows.filter(function (x) { return x.r.link_id === l.id; });
        if (!rs.length) return "";
        var hv = (l.heat || {}).verdict;
        return '<div class="linkhead ' + (hv ? "v-" + hv : "v-none") + '">' +
          "<span class='nm'>" + esc(l.name) + "</span>" +
          (hv ? chip(hv.replace("_", " "), hv) : chip("unscored")) +
          ((l.heat || {}).money_corner ? '<span class="chip UNDISCOVERED">★ money corner</span>' : "") +
          ((l.bottleneck || {}).criticality === "CHOKE_POINT" ? chip("choke point", "OVER_CROWDED") : "") +
          "<span class='ct'>" + rs.length + " name" + (rs.length === 1 ? "" : "s") + "</span></div>" +
          table(rs.map(nameCell).join(""));
      }).join("");
      var orphan = rows.filter(function (x) { return !byId((c || {}).links || [], x.r.link_id); });
      if (orphan.length) body += seclabel("Unlinked — " + orphan.length) + table(orphan.map(nameCell).join(""));
    }
    var h = sc.health || {};
    return topbar("chain") + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: "Stock opportunities" }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip("chain screen", "accent") +
      chip(screenNameCount(sc) + " names") + staleChip(sc.as_of) + "</div>" +
      "<h1>Stock opportunities — " + esc(c ? c.title : chainId) + "</h1>" +
      "<p class='sub'>" + esc(sc.universe_note || "") + "</p></div>" +
      '<div class="seg"><button class="' + (byBucket ? "" : "on") + '" data-nav="#/screen/' + esc(chainId) + '">By link</button>' +
      '<button class="' + (byBucket ? "on" : "") + '" data-nav="#/screen/' + esc(chainId) + '/bucket">By bucket</button></div>' +
      (body || '<div class="emptystate">No names surfaced yet.</div>') +
      ((sc.taste_filtered || []).length ? '<div class="callout" style="margin-top:16px"><b>Filtered by taste:</b> ' + sc.taste_filtered.map(esc).join(", ") + "</div>" : "") +
      "<div style='margin-top:18px'>" + runButton("run screen " + chainId, "re-runs discovery across every link") + "</div>" +
      "<div class='healthline'>examined " + esc(h.tickers_examined) + " · scored " + esc(h.fully_scored) +
      " · pending " + esc(h.pending) + " · errors " + esc(h.errors) + "</div>" +
      notesBlock(sc) + changelogBlock(sc) + footer() + "</main>";
  }
  function screenView(chainId, scenId) {
    if (!scenId || scenId === "bucket") return chainScreenView(chainId, scenId === "bucket");
    var sc = null;
    (D.screens || []).forEach(function (s) { if (s.chain_id === chainId && s.scenario_id === scenId) sc = s; });
    var c = byId(D.chains, chainId);
    if (!sc) return notFound("screen " + chainId + " " + scenId);
    var scen = c ? byId(c.scenarios, scenId) : null;
    var names = { pure_play: "Pure play", picks_and_shovels: "Picks and shovels", second_order: "Second order", hedge: "Hedge" };
    var body = Object.keys(names).map(function (k) {
      var rows = (sc.buckets || {})[k] || [];
      if (!rows.length) return "";
      return seclabel(names[k] + " — " + rows.length) +
        '<div class="tablewrap"><table><thead><tr><th>Name</th><th>Tier</th><th>Thesis</th><th>Exposure</th><th>Fundamentals</th><th>Attention</th><th></th></tr></thead><tbody>' +
        rows.map(function (r) {
          var f = r.fundamentals === "PENDING_DATA" ? '<span class="pend"><span class="dot"></span>pending</span>' :
            (typeof r.fundamentals === "object" && r.fundamentals ? "<span class='num small'>" + esc(r.fundamentals.summary || "fetched") + "</span>" : "—");
          var cw = r.crowdedness === "PENDING_DATA" ? '<span class="pend"><span class="dot"></span>pending</span>' :
            (r.crowdedness && r.crowdedness.state ? chip(r.crowdedness.state + (r.crowdedness.pcs_score != null ? " " + r.crowdedness.pcs_score : ""), r.crowdedness.state === "DARK" ? "UNDISCOVERED" : r.crowdedness.state === "CROWDED" ? "CROWDED" : "neutral") : "—");
          var ex = r.theme_revenue_exposure && r.theme_revenue_exposure.pct != null ? "<span class='num'>" + esc(r.theme_revenue_exposure.pct) + "%</span>" :
            '<span class="muted">null</span>' + ((r.theme_revenue_exposure || {}).basis ? "<div class='muted small'>" + esc(r.theme_revenue_exposure.basis) + "</div>" : "");
          var nug = (r.earnings_nuggets || []).length ? "<details><summary>" + r.earnings_nuggets.length + " earnings nugget(s)</summary>" +
            r.earnings_nuggets.map(function (n) { return "<blockquote>“" + esc(n.quote) + "”<div class='muted'>" + esc(n.form || "") + " · <a href='" + esc(n.url) + "'>" + esc(n.accession) + "</a></div></blockquote>"; }).join("") + "</details>" : "";
          var act = r.status === "DIVED" ? '<a class="chip accent" href="#/stock/' + esc(r.ticker) + "/" + esc(chainId) + '">Dive →</a>' :
            r.status === "CANDIDATE" ? runButton("run deepdive " + r.ticker + " " + chainId, "opens the last-mile analysis on this name", { compact: true }) : chip(r.status);
          return "<tr><td><div class='tk-name'>" + esc(r.ticker) + "</div><div class='tk-co'>" + esc(r.name || "") + " · " + esc(r.exchange || "") + "</div>" + nug + "</td>" +
            "<td>" + tierChip(r.tier) + "</td><td class='small' style='max-width:280px'>" + esc(r.thesis_1line) + "</td>" +
            "<td>" + ex + "</td><td>" + f + "</td><td>" + cw + "</td><td>" + act + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).join("");
    var gaps = sc.data_gaps || [];
    return topbar("chain") + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: "Screen " + scenId }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip(scenId) + chip((sc.health || {}).fully_scored + "/" + (sc.health || {}).tickers_examined + " scored") + "</div>" +
      "<h1>" + esc(scen ? scen.title : scenId) + "</h1>" +
      "<p class='sub'>" + esc(sc.universe_note) + "</p></div>" + body +
      ((sc.taste_filtered || []).length ? '<div class="callout" style="margin-top:16px"><b>Filtered by taste:</b> ' + sc.taste_filtered.map(esc).join(", ") + "</div>" : "") +
      (gaps.length ? "<div style='margin-top:18px'>" + runButton("run screen " + chainId + " " + scenId, "re-scores once fetched data lands (" + gaps.length + " names pending)") + "</div>" : "") +
      "<div class='healthline'>examined " + esc((sc.health || {}).tickers_examined) + " · scored " + esc((sc.health || {}).fully_scored) + " · pending " + esc((sc.health || {}).pending) + " · errors " + esc((sc.health || {}).errors) + "</div>" +
      notesBlock(sc) + changelogBlock(sc) + footer() + "</main>";
  }

  /* ---------------- stock ---------------- */
  function stockView(ticker, chainId) {
    var st = null;
    (D.stocks || []).forEach(function (s) { if (s.ticker === ticker && s.chain_id === chainId) st = s; });
    if (!st) return notFound("deep dive " + ticker + " / " + chainId);
    var c = byId(D.chains, chainId);
    var mk = marketFor(ticker);
    var pos = (D.trades || []).filter(function (t) { return t.ticker === ticker; });
    var zone = "";
    if (st.verdict === "INVESTABLE" && st.entry_zone) {
      zone = '<div class="zone"><span class="zl">Entry zone</span><span class="zv">' + fmtMoney(st.entry_zone.low) + "–" + fmtMoney(st.entry_zone.high) + "</span></div>" +
        '<div class="zone"><span class="zl">No entry above</span><span class="zv">' + fmtMoney(st.no_entry_above) + "</span></div>";
    } else if (st.verdict === "WATCH") {
      zone = '<div class="zone"><span class="zl">Waiting on</span><span class="zv" style="font-size:14px">' +
        (st.watch_triggers || []).map(function (t) { return esc(t.metric) + " " + esc(t.direction || "") + " " + esc(t.level); }).join(" · ") + "</span></div>";
      // Ron, 2026-09-03: a WATCH says at what price it would have been a yes, or names the
      // cap that binds at any price. A dive written before that date carries neither, and
      // the page says so rather than drawing nothing.
      if (st.would_buy_zone != null) {
        zone += '<div class="zone"><span class="zl">Would buy</span><span class="zv">' + fmtMoney(st.would_buy_zone.low) + "–" + fmtMoney(st.would_buy_zone.high) + "</span></div>";
        // A bear-case zone that already contains today's price is the single most
        // actionable thing on this page, and until 2026-09-08 the gate forbade it from
        // ever happening. Say it in words rather than making the reader compare numbers.
        if (st.zone_contains_spot === true) {
          zone += '<div class="zone"><span class="zl">In zone now</span><span class="zv" style="font-size:14px">bear case clears at today\u2019s price</span></div>';
        }
      } else if (str_or_empty(st.would_buy_basis)) {
        zone += '<div class="zone"><span class="zl">No price fixes it</span><span class="zv" style="font-size:14px">' + esc(st.would_buy_basis) + "</span></div>";
      } else {
        zone += '<div class="zone"><span class="zl">Would buy</span><span class="zv muted" style="font-size:14px">not yet drawn (dive predates the 2026-09-03 rule)</span></div>';
      }
    } else {
      zone = '<div class="zone"><span class="zl">Shadow book</span><span class="zv" style="font-size:14px"><a href="#/shadow">graded at +90d vs SPY →</a></span></div>';
    }
    var bigval = "";
    if (mk && mk.series && (mk.series.rows || []).length) {
      /* The strip's window is a DATE range, not a row count. It used to slice the last 90
         rows and label them as that many trading sessions, which held only while the page
         carried every daily bar; app/build.py now carries an evenly-spaced subset, so the
         last 90 rows could be three years rather than three months. Six months of dates
         means six months whatever the sampling, and the label prints the date it starts
         from rather than a point count the reader would read as trading days. */
      var sr = mk.series.rows, lastDate = new Date(sr[sr.length - 1][0]);
      lastDate.setDate(lastDate.getDate() - 182);
      var cut = lastDate.toISOString().slice(0, 10);
      var sn = sr.filter(function (r) { return r[0] >= cut; });
      if (sn.length < 2) sn = sr.slice(-2);
      var svals = sn.map(function (r) { return r[1]; });
      var slo = Math.min.apply(null, svals), shi = Math.max.apply(null, svals);
      var rng = (shi - slo) || 1;
      var sp = sn.map(function (r, i) {
        return (i ? "L" : "M") + (i / (sn.length - 1) * 64).toFixed(1) + " " + (20 - (r[1] - slo) / rng * 18).toFixed(1);
      }).join("");
      var lastRow = sr[sr.length - 1];
      var first = sn[0][1], chg = (first && sn.length > 1) ? ((lastRow[1] - first) / first) * 100 : null;
      /* Anchored to the first point's DATE, never to a count of points. The page carries
         an evenly-spaced subset of the file's series (see seriesNote), so "over 90
         sessions" would have counted inlined points as trading days the moment the
         series was downsampled — the hardcoded-window defect with an extra step. */
      bigval = '<div class="bigval"><div><span class="v">' + fmtMoney(lastRow[1]) + "</span>" +
        '<svg class="spark" viewBox="0 0 66 22" aria-hidden="true"><path d="' + sp + '" fill="none" stroke="' +
        (chg >= 0 ? "var(--und)" : "var(--ovr)") + '" stroke-width="1.6"/></svg></div>' +
        '<span class="l">last close · ' + esc(lastRow[0]) +
        (chg == null ? "" : " · " + (chg >= 0 ? "+" : "") + chg.toFixed(1) + "% since " + esc(sn[0][0])) + "</span></div>";
    }
    var hero = bigval + '<div class="vhero ' + esc(st.verdict) + '">' +
      '<span class="vword"><span class="dot"></span>' + esc(st.verdict.replace("_", " ")) + "</span>" + zone +
      '<span class="right">' + chip(st.clock) + tierChip(st.tier) + chip(st.status, st.status === "FINAL" ? "accent" : "stale") +
      (pos.length ? chip("in book @ " + pos[pos.length - 1].price, "accent") : "") +
      gradeChip(st.earnings_quality) +
      '<span class="muted">updated <span class="num">' + esc(st.updated_at) + '</span> · review <span class="num">' + esc(st.review_by) + "</span></span>" +
      staleChip(st.updated_at) + "</span></div>";
    var rt = st.red_team
      ? '<div class="card redteam"><div class="rt-label">Red team — attacked ' + esc(st.red_team.attacked_at) + " · " + (st.red_team.verdict_survived ? "verdict survived" : "verdict overturned") + "</div>" +
        (st.red_team.challenges || []).map(function (ch) { return "<div class='small' style='margin:9px 0'><b>" + esc(ch.dimension) + ":</b> " + esc(ch.attack) + " <span class='muted'>→ " + esc(ch.outcome) + "</span></div>"; }).join("") +
        (st.red_team.amendments ? "<div class='small'><b>Amended:</b> " + esc(st.red_team.amendments) + "</div>" : "") +
        /* The pre-mortem is the check that catches a lazy verdict — "it is twelve months
           on and this was wrong, why?" — and it rendered nowhere, so the page showed the
           attack's conclusion without the reasoning that is most likely to change a
           reader's mind. Raised by the first red team. */
        (st.red_team.pre_mortem ? "<div class='small' style='margin-top:10px'><b>Pre-mortem — if this verdict is wrong in twelve months:</b>" +
          (Array.isArray(st.red_team.pre_mortem)
            ? "<ul class='bullets'>" + st.red_team.pre_mortem.map(function (r) { return "<li>" + esc(typeof r === "string" ? r : (r.reason || JSON.stringify(r))) + "</li>"; }).join("") + "</ul>"
            : " " + esc(st.red_team.pre_mortem)) + "</div>" : "") +
        "<div class='small' style='margin-top:10px'><b>Surviving bear case:</b> " + esc(st.red_team.surviving_bear_case) + "</div></div>"
      : '<div class="card redteam"><div class="rt-label">Red team</div><div class="small" style="margin-top:8px">This dive is DRAFT — it becomes FINAL only after a fresh-context attack.</div><div style="margin-top:12px">' + runButton("run redteam " + ticker + " " + chainId) + "</div></div>";
    /* R7: confidence_audit was written by every dive and rendered by nothing, so the
       DEMO page showed INVESTABLE with an entry zone while every claim under it was
       SPECULATIVE. An audit that only the file knows about cannot inform the reader.
       R8: price_ref is the dive's own declaration of which price it reasoned from; the
       chart silently used marketFor(ticker) instead, so a divergence was invisible. */
    var ca = st.confidence_audit || {};
    var caTotal = (ca.verified || 0) + (ca.inferred || 0) + (ca.speculative || 0) + (ca.null || 0);
    var caWarn = caTotal > 0 && !ca.verified && st.verdict === "INVESTABLE";
    var caCard = caTotal ? "<div class='card" + (caWarn ? " redteam" : "") + "'><h3>Evidence strength</h3>" +
      "<div class='row' style='gap:6px;flex-wrap:wrap'>" +
      [["verified", ca.verified || 0], ["inferred", ca.inferred || 0],
       ["speculative", ca.speculative || 0], ["null", ca.null || 0]].map(function (t) {
        return chip(t[1] + " " + t[0], t[0] === "verified" && t[1] ? "accent" : (t[1] ? "neutral" : "stale"));
      }).join("") + "</div>" +
      (caWarn ? "<div class='small' style='margin-top:10px'><b>No VERIFIED evidence supports this INVESTABLE verdict.</b> " +
        "Every claim on this page is inferred or reasoned. Treat the entry zone as a hypothesis, not a level.</div>" : "") +
      "</div>" : "";
    var prCard = "";
    if (st.price_ref && st.price_ref.value != null) {
      var lastClose = (mk && mk.series && (mk.series.rows || []).length)
        ? mk.series.rows[mk.series.rows.length - 1][1] : null;
      var diverged = lastClose != null && Math.abs(lastClose - st.price_ref.value) / lastClose > 0.05;
      /* price_source_note is gate-required whenever the market file is SINGLE_SOURCE or
         DISPUTED, and it was rendered nowhere — so the gate passed while the reader was
         never told the levels rest on one unconfirmed print. A disclosure that only the
         JSON carries is not a disclosure. Raised by the first real dive. */
      var psn = str_or_empty(st.price_source_note);
      prCard = "<div class='card'><h3>Price the dive reasoned from</h3><div class='kv'>" +
        "<dt>price_ref</dt><dd class='num'>" + fmtMoney(st.price_ref.value) +
        " <span class='muted'>[" + esc(st.price_ref.source) + ", " + esc(st.price_ref.as_of) + "]</span></dd>" +
        (lastClose != null ? "<dt>latest close</dt><dd class='num'>" + fmtMoney(lastClose) +
          " <span class='muted'>" + esc((mk.series || {}).as_of) + "</span></dd>" : "") +
        "</div>" + (diverged ? "<div class='small' style='margin-top:8px'><b>The price this dive reasoned from is more than 5% away from the latest close.</b> Re-run the dive before acting on its levels.</div>" : "") +
        (psn ? "<div class='small' style='margin-top:8px'><b>Single-source price.</b> " + esc(psn) + "</div>" : "") +
        "</div>";
    }
    /* link_id and link_id_basis are required of every dive by method section 7 and by
       tools/check_analyst.py, and neither reached the page. Without them a reader cannot
       tell a name that IS the link from one parked on the nearest link the screen had —
       which is exactly what this dive says about itself in its basis, at length, where
       nobody could read it. Same defect as price_source_note: gate-required, invisible. */
    var linkc = "";
    if (st.link_id || str_or_empty(st.link_id_basis)) {
      linkc = "<div class='card'><h3>Chain link</h3><div class='row' style='gap:6px;flex-wrap:wrap'>" +
        (st.link_id ? chip(c ? linkName(c, st.link_id) : st.link_id, "accent")
                    : chip("unattributed", "stale")) +
        "</div>" +
        (str_or_empty(st.link_id_basis)
          ? "<div class='small' style='margin-top:8px'>" + esc(st.link_id_basis) + "</div>" : "") +
        "</div>";
    }
    /* filing_evidence is the strongest material in the file — passages lifted from the
       filing on disk, each verified verbatim against data/edgar/docs/<T>.json by
       tools/check_analyst.py — and it was the only evidence the page never showed. A
       quote the machine checks and the reader cannot see is a check performed for nobody. */
    var fe = st.filing_evidence || [];
    var fec = fe.length ? "<div class='card'><h3>Filing evidence</h3>" +
      "<div class='small muted'>" + fe.length + " passage(s), each verified word for word against the filing on disk.</div>" +
      fe.map(function (q) {
        return "<blockquote>\u201c" + esc(q.quote) + "\u201d<div class='muted'>" + chip(q.tag || "VERIFIED", q.tag === "VERIFIED" ? "accent" : "neutral") +
          " " + esc(q.form || "") + " · " + esc(q.filing_date || "") + " · " +
          (q.url ? "<a href='" + esc(q.url) + "'>" + esc(q.accession || "source") + "</a>" : esc(q.accession || "")) +
          "</div></blockquote>";
      }).join("") +
      (str_or_empty(st.filing_evidence_note)
        ? "<div class='small' style='margin-top:10px'>" + esc(st.filing_evidence_note) + "</div>" : "") +
      "</div>" : "";
    var val = "<div class='card'><h3>Valuation snapshot</h3><div class='kv'>" +
      "<dt>Price</dt><dd class='num'>" + fmtMoney((st.valuation_snapshot.price || {}).value) + " <span class='muted'>[" + esc((st.valuation_snapshot.price || {}).source) + ", " + esc((st.valuation_snapshot.price || {}).as_of) + "]</span></dd>" +
      "<dt>Market cap</dt><dd class='num'>" + esc(num((st.valuation_snapshot.market_cap || {}).value)) + "</dd>" +
      (st.valuation_snapshot.lines || []).map(function (l) { return "<dt>" + esc(l.name) + "</dt><dd class='num'>" + esc(l.value) + " <span class='muted'>[" + esc(l.tag) + "]</span></dd>"; }).join("") +
      (st.entry_zone ? "<dt>Entry basis</dt><dd class='small'>" + esc(st.entry_zone.basis) + "</dd>" : "") +
      (st.would_buy_zone != null ? "<dt>Would-buy basis</dt><dd class='small'>" + esc(st.would_buy_zone.basis) + (str_or_empty(st.would_buy_zone.as_of) ? " <span class='muted'>[drawn " + esc(st.would_buy_zone.as_of) + "]</span>" : "") + "</dd>" : "") +
      // method §7's upward check: a WATCH that cleared every downward cap has to name what
      // still binds. That line IS the verdict's reason, so it renders beside the zone.
      (str_or_empty(st.watch_basis) ? "<dt>What still caps this</dt><dd class='small'>" + esc(st.watch_basis) + "</dd>" : "") +
      (str_or_empty(st.clock_basis) ? "<dt>Clock basis</dt><dd class='small'>" + esc(st.clock_basis) + "</dd>" : "") + "</div></div>";
    var priced = "<div class='card'><h3>What is already priced in</h3>" +
      (st.what_is_priced_in || []).map(function (p) { return "<div class='evli'>" + chip(p.tag) + " " + esc(p.expectation) + "</div>"; }).join("") +
      "<div class='small' style='margin-top:10px'>" + esc(st.priced_in_summary || "") + "</div></div>";
    var gapc = gapTable(st);
    var qualc = qualityCard(mk, st);
    return topbar("chain") + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: ticker }]) + "<main>" +
      (st.fixture ? '<div class="fixturebanner">Fixture page — synthetic demo data so the UI can be reviewed; deleted when the first real deep dive lands.</div>' : "") +
      '<div class="pagehead"><h1>' + esc(st.ticker) + ' <span style="font-weight:400;font-size:16px;color:var(--ink-3)">' + esc(st.name || "") + "</span></h1></div>" + hero +
      (linkc ? "<div style='margin-top:14px'>" + linkc + "</div>" : "") +
      seclabel("Price") + "<div class='card'>" + priceChart(mk, st) + "</div>" +
      seclabel("The case") +
      '<div class="statgrid"><div><h3 style="color:var(--good)">Bull</h3><ul class="bullets good">' + (st.bull || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div>" +
      '<div><h3 style="color:var(--bad)">Bear</h3><ul class="bullets bad">' + (st.bear || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div></div>" +
      seclabel("Diligence") +
      (gapc ? gapc : "") +
      '<div class="statgrid">' + priced + val + "</div>" +
      (caCard || prCard ? '<div class="statgrid" style="margin-top:14px">' + caCard + prCard + "</div>" : "") +
      (qualc ? "<div style='margin-top:14px'>" + qualc + "</div>" : "") +
      (fec ? "<div style='margin-top:14px'>" + fec + "</div>" : "") +
      "<div style='margin-top:14px'>" + rt + "</div>" +
      notesBlock(st) + changelogBlock(st) + footer() + "</main>";
  }
  // --- analyst surfaces (2026-08-29). Every number here is READ from the dive or from
  // data/market/<T>.json.quality; the UI never computes a financial figure of its own.
  function gradeChip(eq) {
    if (!eq || eq.grade === undefined) return "";
    var g = eq.grade;
    var cls = (g === "A" || g === "B") ? "accent" : (g === "C" ? "CROWDED" : g === "D" ? "OVER_CROWDED" : "stale");
    return chip("earnings " + (g === null ? "NULL" : esc(g)), cls);
  }
  function pct(v) { return v == null ? "–" : (v * 100).toFixed(1) + "%"; }
  function ord(n) {
    var r = n % 100;
    if (r >= 11 && r <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }
  function gapTable(st) {
    var g = st.expectations_gap;
    if (!g || !(g.rows || []).length) return "";
    var LABEL = { revenue_cagr_5y: "5y revenue CAGR", operating_margin: "Steady-state op margin",
                  reinvestment_return: "Reinvestment return", terminal: "Terminal multiple / g",
                  net_gap_direction: "Net gap direction" };
    var rows = g.rows.map(function (r) {
      var mine = typeof r.mine === "number" ? pct(r.mine) : esc(r.mine == null ? "—" : r.mine);
      var mkt = typeof r.market_implied === "number" ? pct(r.market_implied)
        : esc(r.market_implied == null ? "—" : r.market_implied);
      var flag = (typeof r.percentile === "number" && r.percentile > 80)
        ? " " + chip(ord(r.percentile) + " pct", "CROWDED") : (r.percentile != null ? " <span class='muted'>" + ord(r.percentile) + "</span>" : "");
      return "<tr><td>" + esc(LABEL[r.driver] || r.driver) + "</td>" +
        "<td class='num'>" + mkt + "</td><td class='num'>" + mine + "</td>" +
        "<td class='small'>" + flag + "</td>" +
        "<td class='small'>" + esc(r.leading_indicator || r.structural_reason || "") + "</td></tr>";
    }).join("");
    return "<div class='card'><h3>The expectations gap</h3>" +
      "<div class='small muted' style='margin-bottom:8px'>What the price assumes, against what this dive expects. " +
      "The implied column is solved in the data plane, never in session: " + esc(g.market_implied_source || "") + "</div>" +
      "<div style='overflow-x:auto'><table class='gaptable'><thead><tr><th>Driver</th><th>Market implies</th>" +
      "<th>This dive</th><th>Base rate</th><th>Verification</th></tr></thead><tbody>" + rows + "</tbody></table></div>" +
      (st.independence_test ? "<div class='small' style='margin-top:12px'><b>Largest disagreement:</b> " +
        esc(st.independence_test.largest_disagreement) + "<br><b>Why the gap exists:</b> " +
        esc(st.independence_test.why_the_gap_exists) + "<br><b>Falsified by:</b> " +
        esc(st.independence_test.falsification) + "</div>" : "") + "</div>";
  }
  function qualityCard(mk, st) {
    var q = mk && mk.quality;
    var eq = st.earnings_quality;
    if (!q && !eq) return "";
    var s = "<div class='card'><h3>Earnings quality and distress</h3>";
    if (eq) {
      s += "<div class='small' style='margin-bottom:10px'>" + gradeChip(eq) + " " + esc(eq.basis || "") +
        (eq.grade === "D" ? " <b>Grade D forbids INVESTABLE.</b>"
          : (eq.grade === "C" || eq.grade === null) ? " <b>Caps the verdict at WATCH.</b>" : "") + "</div>";
    }
    if (!q) {
      return s + "<div class='emptystate'>No quality block yet.<div class='runwrap'>" +
        runButton("request data " + st.ticker, "fundamentals then quality, ~5 minutes") + "</div></div></div>";
    }
    function line(label, blk, fmt) {
      if (!blk) return "";
      var v = blk.score;
      var body = v == null
        ? "<span class='pend'><span class='dot'></span>" + esc(blk.state || "pending") + "</span>" +
          (blk.missing && blk.missing.length ? " <span class='muted small'>missing " + esc(blk.missing.slice(0, 3).join(", ")) +
            (blk.missing.length > 3 ? " +" + (blk.missing.length - 3) : "") + "</span>" : "")
        : "<span class='num'>" + esc(fmt ? fmt(v) : v) + "</span> " + chip(blk.state);
      return "<dt>" + label + "</dt><dd>" + body + "</dd>";
    }
    var rd = q.reverse_dcf || {};
    s += "<div class='kv'>" +
      line("Piotroski F", q.piotroski, function (v) { return v + " / 9"; }) +
      line("Beneish M", q.beneish) +
      line("Altman Z", q.altman) +
      "<dt>Market-implied FCF CAGR</dt><dd>" +
      (rd.implied_fcf_cagr == null
        ? "<span class='pend'><span class='dot'></span>" + esc(rd.state || "pending") + "</span>" +
          (rd.reason ? " <span class='muted small'>" + esc(rd.reason) + "</span>" : "")
        : "<span class='num'>" + pct(rd.implied_fcf_cagr) + "</span> <span class='muted small'>at " +
          pct((rd.assumptions || {}).discount_rate) + " discount, " + pct((rd.assumptions || {}).terminal_growth) +
          " terminal, " + esc((rd.assumptions || {}).horizon_years) + "y</span>") + "</dd>" +
      "</div>";
    if (q.health) {
      s += "<div class='small muted' style='margin-top:10px'>" + esc(q.health.statement_fields_found) + " of " +
        esc(q.health.statement_fields_needed) + " statement fields on file · as of " + esc(q.as_of || "—") +
        " · formulas: " + esc(q.formulas || "") + "</div>";
    }
    return s + "</div>";
  }
  /* What this page is holding of a price series, in the page's own words. Since
     2026-09-04 app/build.py carries every ticker's full daily series, so this states
     the count against the file and can only disagree with it if the build broke. */
  function seriesNote(mk, ticker) {
    var s = (mk || {}).series;
    if (!s || s.row_count == null) return "";
    var path = "data/market/" + String(ticker || "").replace(/\./g, "-") + ".json";
    var have = (s.rows || []).length;
    if (have < s.row_count) return have + " of " + s.row_count + " price points on this page (build defect: the page is meant to carry them all) · " + path;
    return "all " + s.row_count + " daily price points on file · " + path;
  }
  function priceChart(mk, st) {
    if (!mk || !mk.series) {
      return '<div class="emptystate">No price series yet.<div class="runwrap">' + runButton("request data " + st.ticker, "the fetch workflow fills data/market in ~5 minutes") + "</div></div>";
    }
    if ((mk.series.rows || []).length < 2) {
      return '<div class="emptystate">' + ((mk.series.rows || []).length ? "Only one price point on file, nothing to draw yet." : "No price series yet.") +
        '<div class="runwrap">' + runButton("request data " + st.ticker, "the fetch workflow fills data/market in ~5 minutes") + "</div></div>";
    }
    var rows = mk.series.rows;
    var step = Math.max(1, Math.floor(rows.length / 420));
    var pts = rows.filter(function (_, i) { return i % step === 0 || i === rows.length - 1; });
    var W = 940, H = 360, P = { l: 52, r: 76, t: 30, b: 34 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    var vals = pts.map(function (r) { return r[1]; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    /* Every drawn level extends the axis on BOTH ends, or a zone above the plotted max
       is drawn outside the plot (found 2026-09-04). */
    function ext(v) { if (typeof v === "number" && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
    if (st.entry_zone) { ext(st.entry_zone.low); ext(st.entry_zone.high); }
    ext(st.no_entry_above);
    var wbz = st.verdict === "WATCH" && st.would_buy_zone != null ? st.would_buy_zone : null;
    if (wbz) { ext(wbz.low); ext(wbz.high); }
    var pad = (hi - lo) * 0.07; lo -= pad; hi += pad;
    function X(i) { return P.l + (i / (pts.length - 1)) * iw; }
    function Y(v) { return P.t + (1 - (v - lo) / (hi - lo)) * ih; }
    var s = '<div class="chartwrap"><svg id="pxchart" viewBox="0 0 ' + W + " " + H + '" width="100%" style="max-width:' + W + 'px" role="img" aria-label="price chart">';
    if (st.entry_zone) {
      s += '<rect x="' + P.l + '" y="' + Y(st.entry_zone.high) + '" width="' + iw + '" height="' + (Y(st.entry_zone.low) - Y(st.entry_zone.high)) + '" fill="var(--band-good)"/>' +
        '<line x1="' + P.l + '" y1="' + Y(st.entry_zone.high) + '" x2="' + (W - P.r) + '" y2="' + Y(st.entry_zone.high) + '" stroke="var(--und)" stroke-width="0.9" stroke-dasharray="4 4" opacity="0.7"/>' +
        '<line x1="' + P.l + '" y1="' + Y(st.entry_zone.low) + '" x2="' + (W - P.r) + '" y2="' + Y(st.entry_zone.low) + '" stroke="var(--und)" stroke-width="0.9" stroke-dasharray="4 4" opacity="0.7"/>' +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(st.entry_zone.high) + 4) + '" font-size="10" class="mono-t" fill="var(--und)">' + esc(st.entry_zone.high) + "</text>" +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(st.entry_zone.low) + 4) + '" font-size="10" class="mono-t" fill="var(--und)">' + esc(st.entry_zone.low) + "</text>" +
        '<text x="' + (P.l + 8) + '" y="' + (Y(st.entry_zone.high) + 14) + '" font-size="10" font-weight="600" fill="var(--und)">ENTRY ZONE</text>';
    }
    if (wbz) {
      s += '<rect x="' + P.l + '" y="' + Y(wbz.high) + '" width="' + iw + '" height="' + (Y(wbz.low) - Y(wbz.high)) + '" fill="var(--band-good)"/>' +
        '<line x1="' + P.l + '" y1="' + Y(wbz.high) + '" x2="' + (W - P.r) + '" y2="' + Y(wbz.high) + '" stroke="var(--und)" stroke-width="0.9" stroke-dasharray="4 4" opacity="0.7"/>' +
        '<line x1="' + P.l + '" y1="' + Y(wbz.low) + '" x2="' + (W - P.r) + '" y2="' + Y(wbz.low) + '" stroke="var(--und)" stroke-width="0.9" stroke-dasharray="4 4" opacity="0.7"/>' +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(wbz.high) + 4) + '" font-size="10" class="mono-t" fill="var(--und)">' + esc(wbz.high) + "</text>" +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(wbz.low) + 4) + '" font-size="10" class="mono-t" fill="var(--und)">' + esc(wbz.low) + "</text>" +
        '<text x="' + (P.l + 8) + '" y="' + (Y(wbz.high) + 14) + '" font-size="10" font-weight="600" fill="var(--und)">WOULD BUY</text>';
    }
    if (st.no_entry_above) {
      s += '<line x1="' + P.l + '" y1="' + Y(st.no_entry_above) + '" x2="' + (W - P.r) + '" y2="' + Y(st.no_entry_above) + '" stroke="var(--ovr)" stroke-width="0.9" stroke-dasharray="2 5"/>' +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(st.no_entry_above) + 4) + '" font-size="10" class="mono-t" fill="var(--ovr)">' + esc(st.no_entry_above) + " no entry</text>";
    }
    /* Tick precision follows the axis span: a 3-to-6 range printed with toFixed(0) drew
       two gridlines both labelled "5" (688425.SS, 2026-09-04). */
    var tickStep = (hi - lo) / 4;
    var tickDp = tickStep >= 5 ? 0 : tickStep >= 0.5 ? 1 : 2;
    for (var t = 0; t <= 4; t++) {
      var v = lo + ((hi - lo) * t) / 4;
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)"/>';
      // top tick carries the axis name inline; the rest keep bare numbers (Evidence axis grammar)
      // sits just inside the plot on the top gridline, so the event-label band above stays clear
      if (t === 4) s += '<text x="' + (P.l + 6) + '" y="' + (Y(v) + 13) + '" font-size="10" class="mono-t" fill="var(--ink-3)">' + v.toFixed(tickDp) + "   price" + (mk.series.currency ? ", " + esc(mk.series.currency) : "") + "</text>";
      else s += '<text x="' + (P.l - 8) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t" fill="var(--chart-axis)">' + v.toFixed(tickDp) + "</text>";
    }
    var lbl = Math.max(1, Math.floor(pts.length / 6));
    pts.forEach(function (r, i) { if (i % lbl === 0 && i < pts.length - 3) s += '<text x="' + X(i) + '" y="' + (H - P.b + 16) + '" text-anchor="middle" font-size="9.5" class="mono-t" fill="var(--chart-axis)">' + esc(r[0].slice(0, 7)) + "</text>"; });
    // An event outside the price window is exactly the one that must not vanish: a SCHEDULED
    // occurrence or a dated calendar entry is future by definition. Clamp it to the edge instead.
    /* Two event shapes exist on disk: {label, date} and {what, when, why} (688425.SS,
       2026-09-02). The second carries its date inside prose; take the first ISO date in
       it, and skip the marker (never the page) when there is none. Found 2026-09-04:
       `e.label.length` threw and the whole stock page failed to route. */
    (st.events || []).map(function (e) {
      if (!e || typeof e !== "object") return null;
      var label = e.label || e.what || e.title || "";
      var m = String(e.date || e.when || "").match(/\d{4}-\d{2}-\d{2}/);
      return m ? { label: label, date: m[0] } : null;
    }).filter(Boolean).forEach(function (e, ei) {
      var idx = -1, off = "";
      pts.forEach(function (r, i) { if (idx < 0 && r[0] >= e.date) idx = i; });
      if (idx < 0) { idx = pts.length - 1; off = "after"; }
      else if (e.date < pts[0][0]) { idx = 0; off = "before"; }
      var ex = X(idx) + (off === "after" ? 10 : off === "before" ? -10 : 0);
      var lyy = ei % 2 === 0 ? P.t - 6 : P.t - 18;
      var col = off ? "var(--emg)" : "var(--chart-axis)";
      s += '<line x1="' + ex + '" y1="' + (P.t - 2) + '" x2="' + ex + '" y2="' + (H - P.b) + '" stroke="' + col + '" stroke-dasharray="' + (off ? "1 4" : "2 4") + '"/>';
      if (off) s += '<path d="M' + ex + " " + (P.t + 4) + " l" + (off === "after" ? 5 : -5) + " 5 l" + (off === "after" ? -5 : 5) + ' 5 z" fill="var(--emg)"/>';
      s += '<text x="' + ex + '" y="' + lyy + '" text-anchor="' + (off === "after" ? "end" : off === "before" ? "start" : "middle") + '" font-size="9" fill="' + (off ? "var(--emg)" : "var(--ink-3)") + '">' +
        esc(e.label.length > 26 ? e.label.slice(0, 25) + "…" : e.label) + (off ? " · " + esc(e.date) : "") + "</text>";
    });
    var path = pts.map(function (r, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(r[1]).toFixed(1); }).join("");
    s += '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>';
    var last = pts[pts.length - 1];
    s += '<circle cx="' + X(pts.length - 1) + '" cy="' + Y(last[1]) + '" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>';
    s += '<text x="' + (X(pts.length - 1) + 9) + '" y="' + (Y(last[1]) + 4) + '" font-size="10" class="mono-t" fill="var(--ink-3)">' + esc(last[0]) + "</text>";
    s += '<text x="' + (W - P.r) + '" y="' + (H - 6) + '" text-anchor="end" font-size="10.5" class="mono-t" fill="var(--ink-3)">date \u2192</text>';
    s += '<rect id="pxhover" x="' + P.l + '" y="' + P.t + '" width="' + iw + '" height="' + ih + '" fill="transparent"/>';
    s += "</svg></div>";
    s += '<div class="muted num" style="margin-top:8px">prices [' + esc(mk.series.source) + ", as of " + esc(mk.series.as_of) + "] · " + esc(mk.price_status) +
      (mk.price_status === "DISPUTED" ? " — both prints kept in data/market, never averaged" : "") +
      " · " + esc(seriesNote(mk, st.ticker)) + "</div>";
    window.__px = { pts: pts, X: X, Y: Y, P: P, W: W };
    return s;
  }

  /* ---------------- book & shadow ---------------- */
  function bookView() {
    var trades = D.trades || [];
    var rows = trades.map(function (t) {
      var st = null;
      (D.stocks || []).forEach(function (s) { if (s.ticker === t.ticker) st = s; });
      return "<tr><td class='num'>" + esc((t.ts || "").slice(0, 10)) + "</td><td class='tk-name'>" + esc(t.ticker) + "</td><td>" + chip(t.action) + "</td><td class='num'>" + fmtMoney(t.price) + "</td><td>" + esc(t.by) + "</td><td>" +
        (st ? '<a href="#/stock/' + esc(st.ticker) + "/" + esc(st.chain_id) + '">' + chip(st.verdict.replace("_", " "), st.verdict) + "</a>" : "<span class='muted'>no dive</span>") + "</td><td class='small'>" + esc(t.note || "") + "</td></tr>";
    }).join("");
    return topbar("book") + "<main><div class='pagehead'><h1>Book</h1><p class='sub'>Real positions, one line each. Calibration measures your money, not hypotheticals.</p></div>" +
      (trades.length ? '<div class="tablewrap"><table><thead><tr><th>Date</th><th>Ticker</th><th>Action</th><th>Price</th><th>By</th><th>Machine call</th><th>Note</th></tr></thead><tbody>' + rows + "</tbody></table></div>" :
        '<div class="emptystate">No trades logged yet.<div style="margin-top:12px">' + cmdline('log trade VRT bought 112 "starter position"') + "</div></div>") +
      footer() + "</main>";
  }
  function shadowView() {
    var rows = ((D.shadow || {}).book || {}).rows || [];
    var res = (D.shadow || {}).results || {};
    var right = 0, graded = 0;
    var body = rows.map(function (r) {
      var x = res[r.id];
      if (x && x.call) { graded++; if (x.call === "RIGHT") right++; }
      return "<tr><td class='num'>" + esc(r.verdict_date) + "</td><td class='tk-name'>" + esc(r.ticker) + "</td><td>" + chip(r.origin.replace(/_/g, " ")) + "</td><td class='num'>" + fmtMoney((r.spot || {}).value) + "</td><td class='num'>" + esc(r.review_at) + "</td>" +
        "<td>" + (x ? "<span class='num'>" + esc(x.delta_pct) + "% vs SPY</span> " + chip(x.call, x.call) : chip("awaiting +90d")) + "</td></tr>";
    }).join("");
    return topbar("shadow") + "<main><div class='pagehead'><h1>Shadow book</h1><p class='sub'>Every TOO LATE verdict and dismissed signal, repriced at +90 days against SPY. RIGHT means skipping was correct — the machine's \"no\" gets graded here.</p></div>" +
      (graded ? '<div class="card" style="max-width:320px;margin-bottom:16px"><div class="stat"><span class="v">' + Math.round((100 * right) / graded) + '%</span><span class="l">of graded TOO LATE calls were right (' + right + " of " + graded + ")</span></div></div>" : "") +
      (rows.length ? '<div class="tablewrap"><table><thead><tr><th>Verdict date</th><th>Ticker</th><th>Origin</th><th>Spot</th><th>Reprice at</th><th>Result</th></tr></thead><tbody>' + body + "</tbody></table></div>" :
        '<div class="emptystate">Empty — fills automatically from TOO LATE verdicts and dismissed signals.</div>') +
      footer() + "</main>";
  }

  /* ---------------- board: names first ---------------- */
  /* Ron, 2026-09-01. The page used to open on the cortex, a map of the machine's own state
     with no ticker on it. This is the front door now: every dive with its verdict, then the
     O1 queue, then O2 by the heat of its link, then what blocks the rest, one line each.
     Every number here is a projection app/build.py made from a store; nothing is computed
     in the renderer, and every cut list prints its *_total beside it. */
  function boardVerdictChip(v) {
    if (v == null) return chip("no verdict", "neutral");
    return chip(v.replace(/_/g, " "), v);
  }
  function boardEntry(r) {
    var z = r.entry_zone;
    if (r.verdict === "INVESTABLE" && z && z.low != null && z.high != null) {
      return "<span class='num'>" + fmtMoney(z.low) + "–" + fmtMoney(z.high) + "</span>" +
        (r.no_entry_above != null ? " <span class='muted'>no entry above <span class='num'>" + fmtMoney(r.no_entry_above) + "</span></span>" : "");
    }
    var t = r.watch_triggers;
    if (r.verdict === "WATCH" && t && t.length) {
      return t.map(function (w) {
        return esc(w.metric) + " " + esc(w.direction) + " <span class='num'>" + esc(num(w.level, "?")) + "</span>";
      }).join("; ");
    }
    if (r.verdict === "TOO_LATE") return "<span class='muted'>in the shadow book</span>";
    return "<span class='muted'>–</span>";
  }
  function boardStockHref(r) {
    return "#/stock/" + encodeURIComponent(r.ticker) + "/" + encodeURIComponent(r.chain_id);
  }
  function boardView() {
    var b = D.board || { verdicts: [], o1_queue: [], o2: [], blocked: [], themes: [], counts: {} };
    var c = b.counts || {};
    var verdictRows = (b.verdicts || []).map(function (r) {
      return "<tr><td class='tk-name'><a href='" + boardStockHref(r) + "'>" + esc(r.ticker) + "</a> <span class='muted'>" + esc(r.name || "") + "</span></td>" +
        "<td>" + boardVerdictChip(r.verdict) + " " + (r.status === "FINAL" ? chip("FINAL", "accent") : chip("DRAFT, no red team yet", "stale")) + "</td>" +
        "<td>" + esc(num(r.clock, "–")) + "</td>" +
        "<td>" + boardEntry(r) + "</td>" +
        "<td class='muted'>" + esc(r.chain_title || r.chain_id || "") + (r.link_name ? " · " + esc(r.link_name) : "") + "</td>" +
        "<td class='num'>" + esc(num(r.review_by, "–")) + "</td></tr>";
    }).join("");
    var o1Rows = (b.o1_queue || []).map(function (r) {
      return "<tr><td class='tk-name'>" + esc(r.ticker || r.issuer_id) + " <span class='muted'>" + esc(r.name || "") + "</span></td>" +
        "<td>" + (r.dive_status ? chip(r.dive_status, r.dive_status === "FINAL" ? "accent" : "stale") : chip("not dived", "neutral")) + "</td>" +
        "<td class='muted'>" + esc(r.chain_id || "") + (r.link_name ? " · " + esc(r.link_name) : "") + "</td>" +
        "<td>" + (r.data_tier ? esc(r.data_tier) : "<span class='muted'>tier not set</span>") + "</td></tr>";
    }).join("");
    var o2Rows = (b.o2 || []).map(function (r) {
      return "<tr><td class='tk-name'>" + esc(r.ticker || r.issuer_id) + " <span class='muted'>" + esc(r.name || "") + "</span></td>" +
        "<td>" + (r.heat_verdict ? chip(r.heat_verdict.replace(/_/g, " "), r.heat_verdict) : chip("unscored link", "neutral")) + (r.money_corner ? " " + chip("money corner", "accent") : "") + "</td>" +
        "<td class='muted'>" + esc(r.chain_id || "") + (r.link_name ? " · " + esc(r.link_name) : "") + "</td>" +
        "<td>" + (r.data_tier ? esc(r.data_tier) : "<span class='muted'>tier not set</span>") + "</td></tr>";
    }).join("");
    var blockedRows = (b.blocked || []).map(function (r) {
      return "<tr><td class='tk-name'>" + esc(r.ticker || r.issuer_id) + " <span class='muted'>" + esc(r.name || "") + "</span></td>" +
        "<td>" + chip(r.status || "BLOCKED", "verystale") + "</td>" +
        "<td class='muted'>" + esc(r.chain_id || "") + "</td>" +
        "<td>" + esc(r.on || "no data gap recorded on the profile") + "</td></tr>";
    }).join("");
    var themeRows = (b.themes || []).map(function (t) {
      return "<tr><td class='tk-name'><a href='#/campaign/" + encodeURIComponent(t.id) + "'>" + esc(t.title || t.id) + "</a></td>" +
        "<td>" + campaignStatusChip(t.stage) + "</td>" +
        "<td class='num'>" + esc(num(t.profiles, "–")) + " / " + esc(num(t.o1, "–")) + " / " + esc(num(t.finals, "–")) + "</td>" +
        "<td>" + esc(t.refusing || "nothing recorded as blocking") + "</td></tr>";
    }).join("");
    function table(head, body, empty) {
      return body ? '<div class="tablewrap"><table><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table></div>" :
        '<div class="emptystate">' + esc(empty) + "</div>";
    }
    return topbar("board") + "<main><div class='pagehead'><h1>Board</h1><p class='sub'>Names first. Every dive with its verdict, then the O1 queue waiting on a dive, then O2 by the heat of its link, then what blocks the rest. Verdicts come from the stock files; nothing on this page is computed here.</p></div>" +
      '<div class="statgrid">' +
      '<div class="card"><div class="stat"><span class="v num">' + esc(num(c.final, "0")) + '</span><span class="l">FINAL verdicts</span></div></div>' +
      '<div class="card"><div class="stat"><span class="v num">' + esc(num(c.o1, "0")) + '</span><span class="l">O1 selected</span></div></div>' +
      '<div class="card"><div class="stat"><span class="v num">' + esc(num(c.o2, "0")) + '</span><span class="l">O2 complete, not selected</span></div></div>' +
      '<div class="card"><div class="stat"><span class="v num">' + esc(num(c.blocked, "0")) + '</span><span class="l">profiles blocked</span></div></div>' +
      "</div>" +
      seclabel("Verdicts") +
      table("<th>Name</th><th>Verdict</th><th>Clock</th><th>Entry zone / triggers</th><th>Chain · link</th><th>Review by</th>", verdictRows,
        "No dive has been written yet. A verdict exists only as a data/stocks file; none is on disk.") +
      seclabel("O1 queue") +
      table("<th>Name</th><th>Dive</th><th>Chain · link</th><th>Data tier</th>", o1Rows,
        "No O1 issuer is waiting. run selection promotes COMPLETE O2 profiles here.") +
      seclabel("O2, by link heat" + (b.o2_total != null && b.o2_total > (b.o2 || []).length ? " (showing " + (b.o2 || []).length + " of " + b.o2_total + ")" : "")) +
      table("<th>Name</th><th>Link heat</th><th>Chain · link</th><th>Data tier</th>", o2Rows,
        "No COMPLETE O2 profile exists.") +
      seclabel("Blocked, and on what" + (b.blocked_total != null && b.blocked_total > (b.blocked || []).length ? " (showing " + (b.blocked || []).length + " of " + b.blocked_total + ")" : "")) +
      table("<th>Name</th><th>State</th><th>Chain</th><th>First recorded data gap</th>", blockedRows,
        "No profile is BLOCKED or DRAFT.") +
      seclabel("Themes: stage, and what refuses the next stage") +
      table("<th>Theme</th><th>Stage</th><th>profiles / O1 / FINAL</th><th>Recorded blocker</th>", themeRows,
        "No campaign manifest on disk.") +
      footer() + "</main>";
  }

  /* ---------------- campaign ---------------- */
  function campaignMetric(actual, target) {
    var value = '<span class="num">' + esc(num(actual)) + "</span>";
    return target != null ? value + '<span class="campaign-target"> / ' + esc(target) + "</span>" : value;
  }
  function campaignStatusChip(status) {
    if (!status) return "";
    var cls = status === "COMPLETE" || status === "TARGET_MET" ? "accent" :
      status === "EXHAUSTED" ? "QUIET" :
      status === "OPEN" || status === "PENDING_DATA" ? "stale" :
      status === "BLOCKED" ? "verystale" :
      status === "NOT_STARTED" ? "verystale" : "neutral";
    return chip(status.replace(/_/g, " "), cls);
  }
  function campaignSummaryStat(value, label, target) {
    return '<div class="card campaign-stat"><div class="stat"><span class="v">' +
      campaignMetric(value, target) + '</span><span class="l">' + esc(label) + "</span></div></div>";
  }
  function campaignSelectionStamp(asOf) {
    return asOf ? '<span class="muted">selected <span class="num">' + esc(asOf) + "</span></span>" : "";
  }
  function campaignBlockers(items) {
    if (!(items || []).length) return "";
    return '<div class="callout campaign-blockers"><b>Blockers</b><ul class="bullets bad">' +
      items.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") +
      "</ul></div>";
  }
  function campaignO1Queue(ix) {
    var rows = ix.o1 || [];
    if (!rows.length) {
      return seclabel("O1 queue") +
        '<div class="emptystate campaign-empty-small">No O1 names have been selected. The dashboard does not promote names from data tier alone.</div>';
    }
    var body = rows.map(function (row) {
      var action, ticker = row.stock_ticker;
      if (row.final && row.handoff_present && ticker) {
        action = '<a href="#/stock/' + encodeURIComponent(ticker) + "/" + encodeURIComponent(row.chain_id) + '">' +
          chip("FINAL", "accent") + "</a>";
      } else if (row.pending) {
        action = '<span class="pend"><span class="dot"></span>pending data</span>';
      } else if (row.handoff_present && ticker) {
        action = runButton("run deepdive " + ticker + " " + row.chain_id, null, { compact: true });
      } else {
        action = '<span class="muted">screen handoff unavailable</span>';
      }
      return "<tr><td class='num'>" + esc(num(row.rank)) + "</td>" +
        "<td><div class='tk-name'>" + esc(ticker || row.issuer_id) + "</div><div class='tk-co'>" + esc(row.name) + "</div></td>" +
        "<td><span class='tier-plane'><span class='plane-label'>data</span>" + tierChip(row.data_tier) + "</span></td>" +
        "<td><span class='tier-plane'><span class='plane-label'>work</span>" + opportunityChip(row.opportunity_tier) + "</span></td>" +
        "<td>" + (row.handoff_present
          ? "<a href='#/campaign/" + encodeURIComponent(row.chain_id) + "'>" + esc(row.chain_id) + "</a><div class='tk-co'>" + esc(row.link_id) + "</div>"
          : '<span class="muted">unresolved</span>') + "</td>" +
        "<td>" + campaignStatusChip(row.profile_status) + staleChip(row.as_of) + "</td>" +
        "<td>" + action + "</td></tr>";
    }).join("");
    return seclabel("O1 queue") +
      '<div class="tablewrap campaign-o1"><table><thead><tr><th>Rank</th><th>Company</th><th>Data tier</th><th>Opportunity</th><th>Primary theme</th><th>Profile</th><th>Next step</th></tr></thead><tbody>' +
      body + "</tbody></table></div>";
  }
  /* The orchestrator's resume point: tools/campaign_board.py derives every row from
     disk with the SAME stage ladder tools/check_campaign.py enforces, and validates
     every command it emits through tools/queue_allowlist.py before writing it. The page
     prints those rows and nothing else — it never infers a stage, a next step, or a
     blocker of its own, because a second implementation of the ladder here would be free
     to disagree with the gate and the reader could not tell which one was lying.
     A null board means "the board has not been generated", never "no work outstanding". */
  function campaignBoardBlockers(rows) {
    if (!(rows || []).length) return '<span class="muted">none</span>';
    return rows.map(function (b) {
      return "<div>" + campaignStatusChip(b.kind) +
        "<div class='tk-co campaign-bad'>" + esc(b.detail) + "</div></div>";
    }).join("");
  }
  function campaignBoardNext(row) {
    if (!row.next_command) {
      return '<span class="muted">nothing to run</span>' +
        (row.next_reason ? "<div class='tk-co'>" + esc(row.next_reason) + "</div>" : "");
    }
    return runButton(row.next_command, null, { compact: true }) +
      "<div class='tk-co mono'>" + esc(row.next_command) + "</div>" +
      (row.next_reason ? "<div class='tk-co'>" + esc(row.next_reason) + "</div>" : "");
  }
  function campaignBoard() {
    var board = ((D.health || {}).board) || null;
    if (!board) {
      return seclabel("Worklist") +
        '<div class="emptystate campaign-empty-small"><b>No board has been generated.</b><br>' +
        "The resume point is derived from disk, never hand-written. Run " +
        cmdline("python3 tools/campaign_board.py --write") +
        " in a session on this repo, rebuild, and every theme's stage, next command and blocker appear here.</div>";
    }
    if (board.scope === "SCOPE_EMPTY") {
      return seclabel("Worklist") +
        '<div class="emptystate campaign-empty-small"><b>SCOPE EMPTY.</b><br>' +
        esc(board.scope_note) + "</div>";
    }
    var d = board.denominators || {}, t = board.targets || {};
    var rows = (board.worklist || []).map(function (row) {
      return "<tr><td class='num'>" + esc(num(row.rank)) + "</td>" +
        "<td><div class='tk-name'>" + esc(row.title) + "</div>" +
        "<div class='tk-co mono'>" + esc(row.theme_id) + "</div></td>" +
        "<td>" + campaignStatusChip(row.stage) +
        (row.provisional ? chip("provisional", "stale") : "") + "</td>" +
        "<td>" + campaignBoardNext(row) + "</td>" +
        "<td>" + campaignBoardBlockers(row.blockers) + "</td></tr>";
    }).join("");
    return seclabel("Worklist") +
      "<div class='row'>" + staleChip(board.as_of) +
      '<span class="muted">computed from disk ' +
      '<span class="num">' + esc(board.as_of) + "</span> · " + esc(board.scope_note) +
      "</span></div>" +
      '<div class="statgrid campaign-summary">' +
      campaignSummaryStat(d.themes_total, "themes on the board") +
      campaignSummaryStat(d.distinct_mapped_issuers, "distinct mapped issuers") +
      campaignSummaryStat(d.complete_profiles, "complete profiles", t.completed_profiles_min) +
      campaignSummaryStat(d.o1, "O1", (t.o1_min != null && t.o1_max != null) ? t.o1_min + "–" + t.o1_max : null) +
      campaignSummaryStat(d.final_dives, "FINAL dives", d.o1) +
      campaignSummaryStat(d.pending_requests_blocking, "pending rows blocking work") +
      "</div>" +
      (rows
        ? '<div class="tablewrap campaign-o1"><table><thead><tr><th>Rank</th><th>Theme</th><th>Stage</th><th>Next command</th><th>Blocker</th></tr></thead><tbody>' +
          rows + "</tbody></table></div>"
        : '<div class="emptystate campaign-empty-small">The board resolved no themes. Nothing has been inferred.</div>') +
      (board.next_command
        ? runButton(board.next_command, "the first step on the board, in rank then stage order")
        : "");
  }
  function campaignThemeCard(theme, profileTarget) {
    var c = theme.counts || {};
    var pct = profileTarget != null && profileTarget > 0
      ? Math.min(100, (100 * c.profiles) / profileTarget) : null;
    return '<article class="card campaign-theme">' +
      '<div class="campaign-theme-head"><div><div class="row">' +
      campaignStatusChip(theme.status) + staleChip(theme.coverage_as_of) +
      campaignSelectionStamp(theme.selection_as_of) +
      '</div><h3>' + esc(theme.title) + '</h3><div class="muted mono">' + esc(theme.id) + "</div></div>" +
      '<a class="campaign-open" href="#/campaign/' + encodeURIComponent(theme.id) + '">link coverage →</a></div>' +
      '<div class="coverage-line"><span>complete profiles</span><b>' + campaignMetric(c.profiles, profileTarget) + "</b></div>" +
      '<div class="coverage-track"' + (pct == null ? ' data-empty="true"' : "") + ">" +
      (pct == null ? "" : '<i style="width:' + pct.toFixed(1) + '%"></i>') + "</div>" +
      '<div class="campaign-mini">' +
      "<span><b class='num'>" + esc(num(c.links)) + "</b> links</span>" +
      (theme.mapping_present
        ? "<span><b class='num'>" + esc(num(c.mapped_issuers)) + "</b> mapped issuers</span>"
        : "<span class='campaign-warn'>mapping unavailable</span>") +
      "<span>" + opportunityChip("O1") + " <b class='num'>" + esc(num(c.o1)) + "</b></span>" +
      "<span><b class='num'>" + esc(num(c.finals)) + " / " + esc(num(c.o1)) + "</b> O1 FINAL</span>" +
      (c.pending ? "<span class='campaign-warn'><b class='num'>" + esc(c.pending) + "</b> pending</span>" : "") +
      (c.blockers ? "<span class='campaign-bad'><b class='num'>" + esc(c.blockers) + "</b> blockers</span>" : "") +
      "</div></article>";
  }
  function campaignThemeView(ix, id) {
    var theme = byId(ix.themes, id);
    if (!theme) return notFound("campaign theme " + id);
    var c = theme.counts || {};
    var rows = (theme.links || []).map(function (link) {
      return "<tr><td class='num'>" + esc(num(link.position)) + "</td>" +
        "<td><div class='tk-name'>" + esc(link.name) + "</div><div class='tk-co mono'>" + esc(link.id) + "</div></td>" +
        "<td>" + (theme.mapping_present ? campaignStatusChip(link.status) : '<span class="muted">mapping unavailable</span>') + "</td>" +
        "<td>" + (theme.mapping_present ? campaignMetric(link.mapped, link.target) : '<span class="muted">mapping unavailable</span>') + "</td>" +
        "<td class='num'>" + esc(num(link.profiled)) + "</td>" +
        "<td>" + opportunityChip("O1") + " <span class='num'>" + esc(num(link.o1)) + "</span></td>" +
        "<td class='num'>" + esc(num(link.finals)) + "</td>" +
        "<td>" + (link.pending ? '<span class="pend"><span class="dot"></span>' + esc(link.pending) + "</span>" : '<span class="muted">none</span>') + "</td>" +
        "<td>" + (link.blocker ? '<span class="campaign-bad">' + esc(link.blocker) + "</span>" : '<span class="muted">none</span>') +
        staleChip(link.coverage_as_of) + "</td></tr>";
    }).join("");
    return topbar("campaign") +
      '<div class="crumbs"><a href="#/campaign">Campaign</a><span class="sep">/</span><span class="here">' + esc(theme.title) + "</span></div>" +
      "<main><div class='pagehead'><div class='row'>" + campaignStatusChip(theme.status) + staleChip(theme.coverage_as_of) +
      campaignSelectionStamp(theme.selection_as_of) +
      "</div><h1>" + esc(theme.title) + "</h1><p class='sub'>Every value-chain link keeps its own issuer denominator. EXHAUSTED is shown as a finding, not filled with a proxy.</p></div>" +
      '<div class="statgrid campaign-theme-stats">' +
      campaignSummaryStat(c.links, "links") +
      campaignSummaryStat(theme.mapping_present ? c.mapped_issuers : "mapping unavailable", "mapped issuers") +
      campaignSummaryStat(c.profiles, "complete profiles", ix.targets.profiles_per_theme) +
      campaignSummaryStat(c.o1, "O1 names") +
      campaignSummaryStat(c.finals, "O1 FINAL", c.o1) +
      campaignSummaryStat(c.pending, "pending data") +
      "</div>" +
      campaignBlockers(theme.blockers) +
      seclabel("Per-link coverage") +
      ((theme.links || []).length
        ? '<div class="tablewrap campaign-links"><table><thead><tr><th>Pos</th><th>Link</th><th>Status</th><th>Mapped / target</th><th>Profiles</th><th>Opportunity</th><th>FINAL</th><th>Pending</th><th>Blocker / freshness</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
        : '<div class="emptystate">No links are projected for this theme. A missing mapping store is not treated as zero coverage.</div>') +
      "<div class='campaign-chain-link'><a href='#/chain/" + encodeURIComponent(theme.id) + "'>open value chain →</a></div>" +
      footer() + "</main>";
  }
  function campaignView(themeId) {
    var ix = D.campaign_ix || { present: false, themes: [], o1: [] };
    if (!ix.present) {
      return topbar("campaign") + "<main><div class='pagehead'><h1>Campaign</h1><p class='sub'>Ten-theme research coverage, from mappings through final verdicts.</p></div>" +
        '<div class="emptystate campaign-empty"><b>No campaign data exists yet.</b><br>A campaign manifest must exist under <span class="mono">data/campaigns/</span> before mappings or company files are counted. Nothing has been inferred.</div>' +
        campaignBoard() +
        footer() + "</main>";
    }
    if (themeId) return campaignThemeView(ix, themeId);
    var c = ix.counts || {}, t = ix.targets || {};
    var opportunityTarget = t.opportunity_min != null && t.opportunity_max != null
      ? t.opportunity_min + "–" + t.opportunity_max : null;
    return topbar("campaign") + "<main><div class='pagehead'><div class='row'>" +
      campaignStatusChip(ix.status) + staleChip(ix.coverage_as_of) +
      campaignSelectionStamp(ix.selection_as_of) +
      "</div><h1>" + esc(ix.title || "Campaign") + "</h1><p class='sub'>Ten themes in one bounded view. Coverage counts come from saved mapping and company stores; raw evidence and full profiles stay out of this artifact.</p></div>" +
      '<div class="statgrid campaign-summary">' +
      campaignSummaryStat(c.themes, "themes", t.themes) +
      campaignSummaryStat(c.links, "links") +
      campaignSummaryStat(c.mapped_issuers, "mapped issuers") +
      campaignSummaryStat(c.profiles, "complete profiles", t.profiles_min) +
      campaignSummaryStat(c.o1, "O1 queue", opportunityTarget) +
      campaignSummaryStat(c.finals, "O1 FINAL", c.o1) +
      campaignSummaryStat(c.pending, "pending data") +
      campaignSummaryStat(c.blockers, "blockers") +
      "</div>" +
      campaignBlockers(ix.blockers) +
      campaignBoard() +
      seclabel("Theme coverage") +
      ((ix.themes || []).length
        ? '<div class="campaign-grid">' + ix.themes.map(function (theme) { return campaignThemeCard(theme, t.profiles_per_theme); }).join("") + "</div>"
        : '<div class="emptystate">The campaign exists, but its theme slate is empty. No ten-theme target is claimed.</div>') +
      campaignO1Queue(ix) +
      footer() + "</main>";
  }

  /* ---------------- cortex ---------------- */
  /* v7 (2026-09-06). Ron's pick from the "Cortex, Second Edition" canvas: direction A,
     the observatory field, with B's crisp square link markers. The field is a WHEEL, not
     a force layout, so every position says something: angle is the occurrence family
     (sectors, labelled at the rim with their real headline and signal counts); distance
     from the centre is how unmapped the signal still is (the eye of the field is the
     biggest gap); size is the money Tally found reachable (the impact score). Each signal
     wears its chain as a ring of links coloured by heat: a gold star is the money corner,
     a white ring a choke point, an amber square a verdict, a dashed orbit a signal with no
     chain yet. The raw feed is a belt at the rim, one dot per headline, in its family's
     sector. Three phases share one camera: NETWORK (the wheel), RADIAL (one signal's links
     ranked by heat with the three factor bars), TIMELINE (distance is time from today,
     past left, future right, the feed a crescent at NOW). Nothing here invents a number:
     a missing score draws as an empty track or a hollow marker and prints as unscored. */
  var CXP = {
    bg0: "#07070C", bg1: "#0D0D18",
    ink: "#ECEDF6", ink2: "#A9ACC0", ink3: "#6C7089", ink4: "#3D4058",
    accent: "#8687F0", accent2: "#C4C5FF",
    verd: { QUIET: "#7B8496", UNDISCOVERED: "#34C77E", EMERGING: "#E3B23C", CROWDED: "#E97F4E", OVER_CROWDED: "#E25C78" },
    dive: { INVESTABLE: "#34C77E", WATCH: "#E3B23C", TOO_LATE: "#E25C78" },
    none: "#3A3C52", gold: "#D9A94B", bad: "#E25C78", dust: "#B3B6D8", ambient: "#B4B7D6",
    fam: { POLICY: "#E3B23C", CORPORATE: "#8687F0", TECH: "#34C77E", PHYSICAL: "#E97F4E", GEO: "#E25C78", MACRO: "#E3B23C", LEGAL: "#E3B23C" }
  };
  var CX_FAMS = ["GEO", "CORPORATE", "TECH", "POLICY", "PHYSICAL"];
  // sector order around the wheel: signal-heavy families face each other so neither
  // half of the field crowds (POLICY and the unfiled signals sit across from each other)
  var CX_SECTOR_ORDER = ["GEO", "POLICY", "TECH", "UNFILED", "CORPORATE", "PHYSICAL", "MACRO", "LEGAL"];
  var CX_RIM = 396, CX_DUST0 = 326, CX_DUST1 = 384, CX_AMB0 = 262, CX_AMB1 = 296, CX_HUB0 = 72, CX_HUB1 = 248;
  var CX_CLAMP = 1460;   // days: the time scale saturates at four years
  var CX_HEAT_ORDER = { UNDISCOVERED: 0, EMERGING: 1, CROWDED: 2, OVER_CROWDED: 3, QUIET: 4 };
  var CX_FACTORS = [["impact", "IMPACT", "#8687F0"], ["crowdedness", "CROWDEDNESS", "#E97F4E"], ["capture", "CAPTURE", "#34C77E"]];
  var CX_MODE = { phase: "network", sig: null };
  var CX_FILTER = { kinds: {}, fam: null, q: "" };
  var CX_KIND_CHIPS = [["sig", "signals"], ["link", "links"], ["scen", "scenarios"], ["co", "names"], ["dive", "verdicts"], ["cand", "ambient"], ["evt", "future"], ["dust", "feed"]];
  var CX_CACHE = null;
  // viewer-tuned render gains: per-type size multipliers plus a label density cut.
  // Per-viewer convenience only: never touches the data, a cleared localStorage restores
  // the defaults.
  var CX_GAIN_DEF = { contrast: 1, sig: 1, link: 1, scen: 1, co: 1, intake: 1, dust: 1, labels: 0.7 };
  var CX_GAIN = (function () {
    var d = {};
    Object.keys(CX_GAIN_DEF).forEach(function (k) { d[k] = CX_GAIN_DEF[k]; });
    try {
      var s = JSON.parse(localStorage.getItem("upstream.cxGain") || "null");
      if (s && typeof s === "object") Object.keys(CX_GAIN_DEF).forEach(function (k) {
        if (typeof s[k] === "number" && s[k] >= 0 && s[k] <= 3) d[k] = s[k];
      });
    } catch (e) {}
    return d;
  })();
  function cxSaveGain() { try { localStorage.setItem("upstream.cxGain", JSON.stringify(CX_GAIN)); } catch (e) {} }
  function cxGainOf(kind) {
    if (kind === "sig") return CX_GAIN.sig;
    if (kind === "link") return CX_GAIN.link;
    if (kind === "scen") return CX_GAIN.scen;
    if (kind === "co" || kind === "dive") return CX_GAIN.co;
    if (kind === "cand" || kind === "evt") return CX_GAIN.intake;
    if (kind === "dust") return CX_GAIN.dust;
    return 1;
  }
  function cxTrim(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function cxFmtDate(d) { return d ? String(d).slice(0, 10) : ""; }
  function cxSigFamily(sigId) {
    var cands = (D.candidates || {}).candidates || [];
    for (var i = 0; i < cands.length; i++) {
      if (cands[i].promoted_signal_id === sigId && cands[i].family) return cands[i].family;
    }
    return null;
  }
  /* The short map name: the card's own `short_title` when it carries one, else the part
     of the title before its colon, the way a chart abbreviates a long feature name. The
     full title lives in the panel and the inspector. */
  function cxShort(title) {
    var t = String(title || "");
    var i = t.indexOf(":");
    if (i > 0 && i <= 60) return t.slice(0, i).trim();
    if (t.length <= 44) return t;
    var cut = t.lastIndexOf(" ", 42);
    return (cut > 20 ? t.slice(0, cut) : t.slice(0, 40)) + "…";
  }
  function cxTier(un) { return un == null ? "LONG TAIL" : un >= 80 ? "MAJOR" : un >= 60 ? "WATCH" : "LONG TAIL"; }
  /* Field captions (Ron's pick, 2026-09-08: "E + D"). The read-out under a signal's name
     is a sentence in roman serif, not a mono log line: "88 unmapped · money reachable 65 ·
     since Jul 2025". Every figure is the record's own or the word for its absence; nothing
     here rounds a missing number into a plausible one. The second line names the money
     corner with the tickers named at it, and draws nothing when the chain has none. */
  var CX_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function cxMonthYear(d) {
    var m = /^(\d{4})-(\d{2})/.exec(String(d || ""));
    return m ? CX_MON[parseInt(m[2], 10) - 1] + " " + m[1] : String(d || "");
  }
  function cxCaption(un, ap, c, occ) {
    var parts = [num(un, "?") + " unmapped"];
    if (ap) parts.push(ap.impact_score == null ? "money unranked" : "money reachable " + Math.round(ap.impact_score));
    else parts.push("not yet appraised");
    if (!c) parts.push("no chain yet");
    else parts.push(occ && occ.anchor_date ? "since " + cxMonthYear(occ.anchor_date) : "undated");
    return parts.join(" · ");
  }
  function cxCornerCaption(c) {
    if (!c) return null;
    var money = (c.links || []).filter(function (l) { return l.heat && l.heat.money_corner; });
    if (!money.length) return null;
    var l = money[0];
    return { name: cxTrim(l.name, 36) + (money.length > 1 ? " +" + (money.length - 1) : ""), tickers: (l.example_tickers || []).slice(0, 3).join("  ") };
  }
  function cxOdds(pct) {
    if (pct == null) return "unscored";
    if (pct <= 0) return "0%";
    if (pct >= 50) return pct + "%";
    return "one in " + Math.round(100 / pct);
  }
  function cxVerdictWord(v, status) {
    var w = v === "INVESTABLE" ? "investable" : v === "WATCH" ? "watch" : v === "TOO_LATE" ? "too late" : String(v || "").toLowerCase().replace(/_/g, " ");
    return status === "DRAFT" ? w + ", draft" : w;
  }
  function cxLiveSignals() {
    return (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; });
  }
  function cxRanked() {
    return cxLiveSignals().slice().sort(function (a, b) {
      var ua = (a.unmappedness || {}).score, ub = (b.unmappedness || {}).score;
      return (ub == null ? -1 : ub) - (ua == null ? -1 : ua);
    });
  }
  function cxScore(l, leg) { var h = (l.heat || {})[leg]; return h && h.score != null ? h.score : null; }
  function cxHeatRank(l) { var v = (l.heat || {}).verdict; return v in CX_HEAT_ORDER ? CX_HEAT_ORDER[v] : 5; }
  function cxRankedLinks(c) {
    return (c.links || []).slice().sort(function (a, b) {
      var d = cxHeatRank(a) - cxHeatRank(b);
      if (d) return d;
      var ia = cxScore(a, "impact"), ib = cxScore(b, "impact");
      return (ib == null ? -1 : ib) - (ia == null ? -1 : ia);
    });
  }
  function cxDays(dateStr) { var d = daysBetween(TODAY, cxFmtDate(dateStr)); return isNaN(d) ? null : d; }
  function cxTimeR(days) { return 40 + 320 * Math.sqrt(Math.min(Math.abs(days == null ? 0 : days), CX_CLAMP) / CX_CLAMP); }
  // deterministic jitter so the belt and the ambient band sit still between rebuilds
  function cxRng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function cxPol(r, deg) { var a = deg * Math.PI / 180; return { x: r * Math.sin(a), y: -r * Math.cos(a) }; }

  /* ---- sectors: angle is family ---- */
  function cxSectors() {
    var feedFam = (D.feeds || {}).families || null;
    var heads = {};
    if (feedFam) Object.keys(feedFam).forEach(function (f) { heads[f] = feedFam[f]; });
    else ((D.feeds || {}).items || []).forEach(function (it) { if (it.f) heads[it.f] = (heads[it.f] || 0) + 1; });
    var sigs = {}, unfiled = 0;
    cxLiveSignals().forEach(function (s) {
      var f = cxSigFamily(s.id);
      if (f) sigs[f] = (sigs[f] || 0) + 1; else unfiled++;
    });
    var cands = {};
    (((D.candidates || {}).candidates) || []).forEach(function (c) { if (c.status === "AMBIENT" && c.family) cands[c.family] = (cands[c.family] || 0) + 1; });
    var fams = CX_SECTOR_ORDER.filter(function (f) {
      if (f === "UNFILED") return unfiled > 0;
      return (heads[f] || 0) + (sigs[f] || 0) + (cands[f] || 0) > 0;
    });
    // a family the feed store names that the order list does not know still gets a sector
    Object.keys(heads).forEach(function (f) { if (fams.indexOf(f) < 0) fams.push(f); });
    if (!fams.length) fams = ["UNFILED"];
    var tf = 0, ts = 0, fw = {}, sw = {};
    fams.forEach(function (f) {
      fw[f] = Math.sqrt(heads[f] || 0); sw[f] = (f === "UNFILED" ? unfiled : (sigs[f] || 0)) + 1;
      tf += fw[f]; ts += sw[f];
    });
    var out = [], a = 0;
    fams.forEach(function (f) {
      // width: 35% feed volume, 65% signal count, so the sectors with the analysis get the room
      var w = (tf ? 0.35 * fw[f] / tf : 0) + 0.65 * sw[f] / ts;
      out.push({ fam: f, a0: a, w: w, heads: heads[f] || 0, sigs: f === "UNFILED" ? unfiled : (sigs[f] || 0), cands: cands[f] || 0 });
      a += w;
    });
    var tot = a || 1;
    a = 0;
    out.forEach(function (s) { s.a0 = a; s.a1 = a + 360 * s.w / tot; s.mid = (s.a0 + s.a1) / 2; a = s.a1; });
    return out;
  }
  function cxSectorOf(secs, fam) {
    for (var i = 0; i < secs.length; i++) if (secs[i].fam === fam) return secs[i];
    for (var j = 0; j < secs.length; j++) if (secs[j].fam === "UNFILED") return secs[j];
    return secs[0];
  }

  /* ---- graph model: every research object with its wheel, timeline and radial seats ---- */
  function cxBuild() {
    var nodes = [], byKey = {}, rng = cxRng(7), i;
    var secs = cxSectors();
    function add(n) { n.key = n.kind + ":" + n.id; byKey[n.key] = n; nodes.push(n); return n; }
    function hubR(imp) { return imp == null ? 6.5 : 6.5 + 13 * Math.pow(Math.max(0, Math.min(1, (imp - 30) / 55)), 1.15); }
    function unR(un) { return Math.max(CX_HUB0, Math.min(CX_HUB1, 62 + (100 - (un == null ? 0 : un)) * 4.1)); }

    // signals: one system each (hub + chain ring)
    var perSec = {};
    cxRanked().forEach(function (sg) {
      var fam = cxSigFamily(sg.id) || "UNFILED";
      var sec = cxSectorOf(secs, fam);
      var c = sg.chain_id ? byId(D.chains, sg.chain_id) : null;
      var nLinks = c ? (c.links || []).length : 0;
      var un = (sg.unmappedness || {}).score;
      var ap = impactFor(sg.id);
      var imp = ap && ap.impact_score != null ? ap.impact_score : null;
      var occ = sg.occurrence || {};
      var n = add({
        kind: "sig", id: sg.id, ref: sg, chain: c, fam: fam, sec: sec, un: un, imp: imp,
        band: ap ? ap.impact_band : null, tier: cxTier(un), color: CXP.accent,
        hr: hubR(imp), R: nLinks ? 18 + 1.9 * nLinks : 24, sysR: (nLinks ? 18 + 1.9 * nLinks : 24) + 6,
        dashed: occ.kind === "SCHEDULED", r: hubR(imp), rWorld: unR(un),
        label: sg.short_title || cxShort(sg.title),
        cap: cxCaption(un, ap, c, occ),
        corner: cxCornerCaption(c),
        tip: sg.title + " — " + (occ.kind || "undated") + (occ.anchor_date ? " · " + occ.anchor_date : ""),
        days: cxDays(occ.anchor_date || sg.created_at)
      });
      (perSec[sec.fam] = perSec[sec.fam] || []).push(n);
    });
    // angle: spread by rank inside the sector; radius: how unmapped; then relax overlaps
    Object.keys(perSec).forEach(function (f) {
      var ns = perSec[f], sec = ns[0].sec, pad = 9;
      ns.forEach(function (n, k) {
        n.th = ns.length > 1 ? sec.a0 + pad + (sec.a1 - sec.a0 - 2 * pad) * ((k + 0.5) / ns.length) : sec.mid;
        n.rw = n.rWorld;
      });
    });
    var sigsN = nodes.filter(function (n) { return n.kind === "sig"; });
    function place(n) { var p = cxPol(n.rw, n.th); n.wx = p.x; n.wy = p.y; }
    sigsN.forEach(place);
    for (var it = 0; it < 140; it++) {
      var moved = false;
      for (var a = 0; a < sigsN.length; a++) for (var b = a + 1; b < sigsN.length; b++) {
        var A = sigsN[a], B = sigsN[b];
        var dx = B.wx - A.wx, dy = B.wy - A.wy, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        var need = A.sysR + B.sysR + 22;
        if (d >= need) continue;
        var push = (need - d) / 2 * 0.55, ux = dx / d, uy = dy / d;
        [[A, -1], [B, 1]].forEach(function (pr) {
          var N = pr[0], sg = pr[1];
          var x = N.wx + sg * ux * push, y = N.wy + sg * uy * push;
          N.rw = Math.sqrt(x * x + y * y);
          var th = Math.atan2(x, -y) * 180 / Math.PI;
          var s0 = N.sec.a0, s1 = N.sec.a1;
          while (th < s0 - 180) th += 360;
          while (th > s1 + 180) th -= 360;
          N.th = Math.max(s0 + 6, Math.min(s1 - 6, th));
          N.rw = Math.max(N.rWorld - 46, Math.min(N.rWorld + 46, Math.max(CX_HUB0, Math.min(CX_HUB1, N.rw))));
          place(N);
        });
        moved = true;
      }
      if (!moved) break;
    }
    // TIMELINE seats for signals: radius is time from today, the angle spreads the ranked
    // list over the correct half so labels breathe (angle carries no family meaning there)
    var past = sigsN.filter(function (n) { return (n.days == null ? 0 : n.days) < 0; });
    var futu = sigsN.filter(function (n) { return (n.days == null ? 0 : n.days) >= 0; });
    [[past, 196, 344], [futu, 40, 140]].forEach(function (grp) {
      var ns = grp[0], lo = grp[1], hi = grp[2];
      var order = [];
      for (var k = 0; k < ns.length; k++) order.push(k);
      order.sort(function (p, q) { return (p % 2) - (q % 2) || p - q; });
      ns.forEach(function (n, k) {
        var slot = order.indexOf(k);
        var th = ns.length > 1 ? lo + (hi - lo) * ((slot + 0.5) / ns.length) : (lo + hi) / 2;
        var p = cxPol(cxTimeR(n.days), th);
        n.tx = p.x; n.ty = p.y; n.tth = th;
      });
    });

    // chain rings: links in position order, companies and scenarios around them
    sigsN.forEach(function (n) {
      var c = n.chain;
      if (!c) return;
      var links = (c.links || []).slice().sort(function (p, q) { return p.position - q.position; });
      var m = links.length || 1;
      links.forEach(function (l, k) {
        var th = -90 + 360 * k / m, ht = l.heat || {};
        var ln = add({
          kind: "link", id: c.id + "/" + l.id, ref: l, chainId: c.id, sig: n, ringA: th, ringIx: k,
          verdict: ht.verdict || null, money: !!ht.money_corner,
          choke: (l.bottleneck || {}).criticality === "CHOKE_POINT",
          color: ht.verdict ? CXP.verd[ht.verdict] : CXP.none, r: 2.4,
          label: l.name, sub: ht.verdict ? "i" + num(cxScore(l, "impact")) + " · c" + num(cxScore(l, "crowdedness")) + " · v" + num(cxScore(l, "capture")) : "unscored",
          tip: l.name + " — " + (ht.verdict ? ht.verdict.replace(/_/g, " ") : "unscored") + (ht.money_corner ? " · money corner" : "")
        });
        n.links = n.links || []; n.links.push(ln);
        var ticks = (l.example_tickers || []).slice(0, 6);
        ticks.forEach(function (t, q) {
          var key = "co:" + t;
          if (byKey[key]) { byKey[key].also = (byKey[key].also || []).concat([c.title + " · " + l.name]); return; }
          add({ kind: "co", id: t, r: 1.4, color: "#C9CBE3", link: ln, sig: n, chainIds: [c.id],
                ringA: th + (q - (ticks.length - 1) / 2) * 5.5, hollow: !marketFor(t),
                label: t, tip: t + " — named at " + l.name, chains: [cxTrim(c.title, 26)] });
        });
      });
      (c.scenarios || []).forEach(function (s, k, arr) {
        var th = -120 + 360 * (k + 0.5) / Math.max(1, arr.length);
        add({ kind: "scen", id: c.id + "/" + s.id, ref: s, chainId: c.id, sig: n, ringA: th, r: 3.6,
              color: s.status === "SCREENED" ? CXP.verd.UNDISCOVERED : (s.status === "INVALIDATED" || s.status === "PLAYED_OUT") ? CXP.ink3 : "#C9CBE3",
              label: s.id + " · " + (s.probability_pct == null ? "unscored" : s.probability_pct + "%"),
              word: s.id + ", " + cxOdds(s.probability_pct),
              tip: s.id + " · " + s.title + " — " + (s.probability_pct == null ? "unscored" : s.probability_pct + "%") });
      });
    });
    // dives: an amber square on the link they were dived from
    (D.stocks || []).forEach(function (st) {
      var ln = byKey["link:" + st.chain_id + "/" + st.link_id] || null;
      var sg = ln ? ln.sig : null;
      if (!sg) sigsN.forEach(function (n) { if (!sg && n.chain && n.chain.id === st.chain_id) sg = n; });
      add({ kind: "dive", id: st.ticker + "__" + st.chain_id, ref: st, sig: sg, link: ln, ringA: ln ? ln.ringA : -90,
            color: CXP.dive[st.verdict] || CXP.none, r: 3, dashed: st.status === "DRAFT",
            label: st.ticker + " · " + (st.verdict || "").replace("_", " "),
            word: cxVerdictWord(st.verdict, st.status),
            tip: st.ticker + " — " + st.verdict + " (" + st.clock + ", " + st.status + ")" });
    });
    // ambient candidates: hollow circles in their family's band; PRIME ones carry a core
    var cands = (((D.candidates || {}).candidates) || []).filter(function (c) { return c.status === "AMBIENT"; });
    var perFam = {};
    cands.forEach(function (c) { var f = c.family || "UNFILED"; (perFam[f] = perFam[f] || []).push(c); });
    Object.keys(perFam).forEach(function (f) {
      var sec = cxSectorOf(secs, f), cs = perFam[f];
      cs.forEach(function (c, k) {
        var th = sec.a0 + (sec.a1 - sec.a0) * ((k + 0.5) / cs.length) + (rng() - 0.5) * 6;
        var rw = CX_AMB0 + (CX_AMB1 - CX_AMB0) * rng();
        var p = cxPol(rw, th), ap = impactFor(c.id);
        var d = cxDays(c.date), pt = cxPol(cxTimeR(d), cxFold(th, d));
        add({ kind: "cand", id: c.id, ref: c, wx: p.x, wy: p.y, tx: pt.x, ty: pt.y, r: ap && ap.impact_band === "PRIME" ? 3.2 : 2.4,
              prime: !!(ap && ap.impact_band === "PRIME"), color: CXP.ambient, hollow: true,
              label: cxTrim(c.title, 44), tip: c.title + " — " + (c.family || "?") + " · " + (c.date || ""), days: d });
      });
    });
    // known future events: dashed diamonds, spread around the band (they carry no family)
    var evs = (((D.calendar || {}).events) || []).filter(function (e) { return e.status === "WATCHING"; });
    evs.forEach(function (e, k) {
      var th = (k + 0.5) / evs.length * 360 + (rng() - 0.5) * 8;
      var rw = CX_AMB0 + 4 + (CX_AMB1 - CX_AMB0 - 8) * rng();
      var p = cxPol(rw, th), d = cxDays(e.date), pt = cxPol(cxTimeR(d), cxFold(th, d == null ? 200 : d));
      add({ kind: "evt", id: e.id, ref: e, wx: p.x, wy: p.y, tx: pt.x, ty: pt.y, r: 3.4, color: CXP.ambient, dashed: true,
            label: cxFmtDate(e.date).slice(0, 7) + " · " + cxTrim(e.title, 44), tip: e.title + " — " + (e.kind || "") + " · " + (e.date || ""), days: d });
    });
    // feed dust: one dot per headline the page carries, in its family's stretch of the belt
    var items = (D.feeds || {}).items || [];
    for (i = 0; i < items.length; i++) {
      var it = items[i], sec2 = cxSectorOf(secs, it.f || "UNFILED");
      var th2 = sec2.a0 + 2 + (sec2.a1 - sec2.a0 - 4) * rng();
      var u = (rng() + rng()) / 2;
      var rw2 = CX_DUST0 + (CX_DUST1 - CX_DUST0) * u;
      var p2 = cxPol(rw2, th2);
      var dd = cxDays(it.d);
      var p3 = cxPol(cxTimeR(dd == null ? -3 : dd), cxFold(th2, dd == null ? -3 : dd));
      add({ kind: "dust", id: "d" + i, ref: it, wx: p2.x, wy: p2.y, tx: p3.x, ty: p3.y, r: 0.55 + 0.6 * rng(),
            a: 0.16 + 0.34 * rng(), color: CXP.dust, seat: i,
            tip: cxTrim(it.t, 84) + " — " + (it.s || "") + " · " + cxFmtDate(it.d) });
    }
    nodes.forEach(function (n) { if (n.x == null) { n.x = n.wx || 0; n.y = n.wy || 0; } });
    return { nodes: nodes, byKey: byKey, sectors: secs, builtAt: D.built_at, sigs: sigsN };
  }
  // fold a wheel angle onto the timeline's half circles: past left, future right
  function cxFold(th, d) {
    th = ((th % 360) + 360) % 360;
    return (d != null && d < 0) ? 180 + th / 360 * 180 : th / 360 * 180;
  }

  /* ---- layouts: every node has a seat in each phase; the draw eases toward it ---- */
  function cxSeatNetwork(g) {
    g.nodes.forEach(function (n) {
      if (n.kind === "sig") { n.gx = n.wx; n.gy = n.wy; n.gR = n.R; return; }
      if (n.kind === "link" || n.kind === "co" || n.kind === "scen" || n.kind === "dive") {
        var h = n.sig;
        if (!h) { n.gx = 0; n.gy = 0; return; }
        var rr = n.kind === "link" ? h.R : n.kind === "co" ? h.R + 13 : n.kind === "scen" ? h.R + 24 : h.R + 9;
        var a = n.ringA * Math.PI / 180;
        n.gx = h.wx + rr * Math.cos(a); n.gy = h.wy + rr * Math.sin(a);
        return;
      }
      n.gx = n.wx; n.gy = n.wy;
    });
    g.scale = 1; g.dim = 0;
  }
  function cxSeatTimeline(g) {
    g.nodes.forEach(function (n) {
      if (n.kind === "sig") { n.gx = n.tx; n.gy = n.ty; n.gR = n.R * 0.7; return; }
      if (n.kind === "link" || n.kind === "co" || n.kind === "scen" || n.kind === "dive") {
        var h = n.sig;
        if (!h) { n.gx = 0; n.gy = 0; return; }
        var rr = (n.kind === "link" ? h.R : n.kind === "co" ? h.R + 13 : n.kind === "scen" ? h.R + 24 : h.R + 9) * 0.7;
        var a = n.ringA * Math.PI / 180;
        n.gx = h.tx + rr * Math.cos(a); n.gy = h.ty + rr * Math.sin(a);
        return;
      }
      n.gx = n.tx; n.gy = n.ty;
    });
    g.scale = 1; g.dim = 0;
  }
  /* The focused radial: one signal's links on a ranked circle at the origin, most
     investable first from twelve o'clock; the rest of the wheel pushed outward and dimmed
     so the ranking still shows the field it ranks. */
  function cxSeatRadial(g, sig) {
    var K = 1.6, R = 200;
    g.nodes.forEach(function (n) {
      var mine = n === sig || n.sig === sig;
      if (!mine) {
        var base = n.kind === "sig" ? { x: n.wx, y: n.wy } : null;
        if (n.kind === "link" || n.kind === "co" || n.kind === "scen" || n.kind === "dive") {
          var h = n.sig;
          if (!h) { n.gx = 0; n.gy = 0; return; }
          var rr = n.kind === "link" ? h.R : n.kind === "co" ? h.R + 13 : n.kind === "scen" ? h.R + 24 : h.R + 9;
          var a = n.ringA * Math.PI / 180;
          n.gx = (h.wx + rr * Math.cos(a)) * K; n.gy = (h.wy + rr * Math.sin(a)) * K;
          return;
        }
        n.gx = (base ? base.x : n.wx) * K; n.gy = (base ? base.y : n.wy) * K;
        if (n.kind === "sig") n.gR = n.R * K;
        return;
      }
      if (n.kind === "sig") { n.gx = 0; n.gy = 0; n.gR = R; return; }
    });
    var ranked = cxRankedLinks(sig.chain), m = ranked.length || 1;
    var angleOf = {};
    ranked.forEach(function (l, k) { angleOf[l.id] = -90 + 360 * k / m; });
    sig.radialAngles = angleOf; sig.radialN = m; sig.radialR = R;
    var scenAngles = [];
    g.nodes.forEach(function (n) {
      if (n.sig !== sig) return;
      var lid = n.kind === "link" ? n.ref.id : n.link ? n.link.ref.id : null;
      var th = lid != null && angleOf[lid] != null ? angleOf[lid] : n.ringA;
      if (n.kind === "co") {
        var k0 = 0, q = 0;
        // spread a link's names as a short row of ticks just outside the ring
        var sibs = g.nodes.filter(function (o) { return o.kind === "co" && o.link === n.link; });
        k0 = sibs.length; q = sibs.indexOf(n);
        th = th + (q - (k0 - 1) / 2) * 3.2;
        var a1 = th * Math.PI / 180;
        n.gx = (R + 16) * Math.cos(a1); n.gy = (R + 16) * Math.sin(a1);
        return;
      }
      if (n.kind === "scen") {
        var moved = (n.ref.links_moved || []).map(function (x) { return angleOf[x.link_id]; }).filter(function (v) { return v != null; });
        var sth = moved.length ? moved.reduce(function (p, c) { return p + c; }, 0) / moved.length : n.ringA;
        sth += 360 / m / 2;
        // two scenarios moving the same links would share a marker: one spoke apart
        while (scenAngles.some(function (v) { return Math.abs((((sth - v) % 360) + 540) % 360 - 180) < 12; })) sth += 360 / m;
        scenAngles.push(sth);
        var a2 = sth * Math.PI / 180;
        n.gx = (R + 62) * Math.cos(a2); n.gy = (R + 62) * Math.sin(a2);
        n.radialA = sth;
        return;
      }
      var a3 = th * Math.PI / 180, rr2 = n.kind === "dive" ? R + 16 : R;
      n.gx = rr2 * Math.cos(a3); n.gy = rr2 * Math.sin(a3);
      n.radialA = th;
    });
    g.scale = K; g.dim = 1;
  }

  /* ---- chrome (HTML) ---- */
  function cxUIHidden() { try { return localStorage.getItem("upstream.cxUI") === "hidden"; } catch (e) { return false; } }
  function cxMeasureTop() {
    var tb = document.querySelector(".topbar");
    if (!tb) return;
    document.documentElement.style.setProperty("--cx-top", Math.round(tb.getBoundingClientRect().height) + 12 + "px");
    if (!tb.__cxRO && window.ResizeObserver) {
      tb.__cxRO = new ResizeObserver(function () {
        var b = document.querySelector(".topbar");
        if (b) document.documentElement.style.setProperty("--cx-top", Math.round(b.getBoundingClientRect().height) + 12 + "px");
      });
      tb.__cxRO.observe(tb);
    }
  }
  function cxToggleUI() {
    var full = document.getElementById("cxFull");
    if (!full) return;
    var hid = !full.classList.contains("ui-hidden");
    full.classList.toggle("ui-hidden", hid);
    document.body.classList.toggle("cx-ui-hidden", hid);
    var b = document.getElementById("cxUIBtn");
    if (b) { b.textContent = hid ? "SHOW UI" : "HIDE UI"; b.setAttribute("aria-pressed", hid ? "true" : "false"); }
    try { localStorage.setItem("upstream.cxUI", hid ? "hidden" : "shown"); } catch (e) {}
  }
  var CX_ICON = {
    network: '<svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true"><circle cx="3" cy="11" r="1.8" fill="currentColor"/><circle cx="7" cy="3.2" r="1.8" fill="currentColor"/><circle cx="11.5" cy="9" r="1.8" fill="currentColor"/><path d="M4 9.6 6.3 4.8 M8.6 4.2 10.4 7.6 M4.8 11 9.7 9.4" stroke="currentColor" stroke-width="1.1" fill="none"/></svg>',
    radial: '<svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="7" cy="7" r="5.6"/><circle cx="7" cy="7" r="1.6" fill="currentColor"/><path d="M7 1.4V4 M7 10V12.6 M1.4 7H4 M10 7H12.6"/></svg>',
    timeline: '<svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M1 7H13 M3.5 4.5V9.5 M7 3V11 M10.5 4.5V9.5"/></svg>',
    eye: '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M1.6 12S5.3 5.2 12 5.2 22.4 12 22.4 12 18.7 18.8 12 18.8 1.6 12 1.6 12Z"/><circle cx="12" cy="12" r="3.1"/></svg>',
    search: '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="6" cy="6" r="4.3"/><path d="M9.3 9.3 12.5 12.5"/></svg>',
    sliders: '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><path d="M2 4h12 M2 8h12 M2 12h12"/><circle cx="6" cy="4" r="1.6" fill="#0C0C15"/><circle cx="11" cy="8" r="1.6" fill="#0C0C15"/><circle cx="5" cy="12" r="1.6" fill="#0C0C15"/></svg>'
  };
  function cxPhaseCtl() {
    var ph = CX_MODE.phase;
    return '<div class="cx-phasectl" role="group" aria-label="phase">' +
      [["network", "Network"], ["radial", "Radial"], ["timeline", "Timeline"]].map(function (p) {
        return '<button class="cx-phbtn' + (ph === p[0] ? " on" : "") + '" data-cxphase="' + p[0] + '" aria-pressed="' + (ph === p[0] ? "true" : "false") + '">' + CX_ICON[p[0]] + p[1] + "</button>";
      }).join("") + "</div>";
  }
  function cxTopRight() {
    var hid = cxUIHidden();
    return '<div class="cx-topright">' +
      '<label class="cx-search"><span class="ic">' + CX_ICON.search + '</span><input id="cxSearch" type="search" placeholder="Search signals, links, names" autocomplete="off" aria-label="search the field" value="' + esc(CX_FILTER.q) + '"><kbd>/</kbd></label>' +
      '<button class="cx-iconbtn" id="cxViewBtn" title="view settings: what shows, how big" aria-expanded="false" aria-controls="cxViewPop">' + CX_ICON.sliders + "</button>" +
      '<button class="cx-uibtn" id="cxUIBtn" aria-pressed="' + (hid ? "true" : "false") + '" title="hide or show the interface (H)">' + (hid ? "SHOW UI" : "HIDE UI") + ' <kbd>H</kbd></button>' +
      "</div>" + cxViewPop();
  }
  function cxViewPop() {
    function row(key, label, title) {
      var v = CX_GAIN[key];
      var lo = key === "contrast" ? 0.6 : key === "labels" ? 0 : 0.3;
      var hi = key === "contrast" ? 1.8 : key === "labels" ? 1 : 2.5;
      return '<div class="row" title="' + esc(title) + '"><label for="cxg-' + key + '">' + esc(label) + "</label>" +
        '<input type="range" id="cxg-' + key + '" data-cxgain="' + key + '" min="' + lo + '" max="' + hi + '" step="0.05" value="' + v + '">' +
        '<span class="val" id="cxgv-' + key + '">×' + v.toFixed(2) + "</span></div>";
    }
    return '<div class="cx-viewpop" id="cxViewPop" hidden>' +
      '<div class="lbl">SHOW</div><div class="cxchips">' + CX_KIND_CHIPS.map(function (kc) {
        return '<button class="cxfchip' + (CX_FILTER.kinds[kc[0]] === false ? " off" : "") + '" data-cxk="' + kc[0] + '">' + kc[1] + "</button>";
      }).join("") + "</div>" +
      '<div class="lbl">SIZE</div>' +
      row("contrast", "CONTRAST", "above 1: the biggest nodes grow, the smallest shrink; below 1 they even out") +
      row("sig", "SIGNALS", "signal hubs") + row("link", "LINKS", "value-chain links") + row("scen", "SCENARIOS", "scenarios") +
      row("co", "NAMES", "companies and verdicts") + row("intake", "INTAKE", "candidates and calendar events") + row("dust", "FEED", "raw feed dust") +
      row("labels", "LABELS", "label density: left shows only the most important names, right shows everything") +
      '<button class="cx-rst" id="cxGainReset">RESET VIEW</button></div>';
  }
  function cxOppRows() {
    var ranked = cxRanked(), out = [], last = null;
    ranked.forEach(function (s, i) {
      var un = (s.unmappedness || {}).score, t = cxTier(un);
      if (t !== last) { out.push('<div class="cx-tierlbl">' + t + "</div>"); last = t; }
      var title = String(s.title || ""), ci = title.indexOf(":");
      var head = ci > 0 ? title.slice(0, ci).trim() : title, rest = ci > 0 ? title.slice(ci + 1).trim() : "";
      var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
      var money = c ? (c.links || []).filter(function (l) { return l.heat && l.heat.money_corner; }).map(function (l) { return l.name; }) : [];
      var ap = impactFor(s.id);
      var on = CX_MODE.sig === s.id;
      if (t === "MAJOR") {
        out.push('<button class="cxopp major' + (on ? " on" : "") + '" data-cxfly="sig:' + esc(s.id) + '" title="' + esc(s.title) + '">' +
          '<span class="rk">' + (i < 9 ? "0" : "") + (i + 1) + "</span>" +
          '<span class="body"><span class="nm">' + esc(head) + "</span>" + (rest ? '<span class="rest">' + esc(rest) + "</span>" : "") +
          (money.length ? '<span class="mc">★ ' + esc(money.join(", ")) + "</span>" : '<span class="mc dim">' + (c ? "no money corner yet" : "unchained · run chain") + "</span>") + "</span>" +
          '<span class="sc"><span class="un">' + num(un, "?") + '</span><span class="band">' + (ap ? esc((ap.impact_band || "").toUpperCase()) + " " + (ap.impact_score == null ? "?" : Math.round(ap.impact_score)) : "UNAPPRAISED") + "</span></span></button>");
      } else {
        out.push('<button class="cxopp' + (on ? " on" : "") + '" data-cxfly="sig:' + esc(s.id) + '" title="' + esc(s.title) + '">' +
          '<span class="rk">' + (i < 9 ? "0" : "") + (i + 1) + "</span>" +
          '<span class="body"><span class="nm">' + esc(head) + "</span></span>" +
          '<span class="sc"><span class="un">' + num(un, "?") + "</span></span></button>");
      }
    });
    return out.join("");
  }
  function cxReg(k, v, dim) { return '<div class="cx-reg"><span class="k">' + esc(k) + '</span><span class="v' + (dim ? " dim" : "") + '">' + esc(v) + "</span></div>"; }
  function cxBar(share, color) { return '<div class="cx-bar"><i style="width:' + Math.round(share * 100) + "%;background:" + color + '"></i></div>'; }
  function cxAgeDays(ts) { if (!ts) return null; var d = daysBetween(String(ts).slice(0, 10), TODAY); return isNaN(d) ? null : d; }
  /* Adam's weekly machine sweep, surfaced from the latest digest (CLAUDE.md postlude 1i).
     Two registers, never one: a finding count without the denominator it was found over is
     exactly the unfalsifiable number method section 9 and Rule 21 forbid. When the digest has
     no machine section this renders "not audited", because "0 findings" over an unaudited
     machine is the false all-clear the audit itself exists to catch. */
  function machineReg() {
    var m = (((D.digests || [])[0] || {}).machine) || null;
    if (!m) return cxReg("machine", "not audited", true);
    var ex = m.examined || {};
    var denom = ex.commands != null ? ex.commands + " cmds" : "";
    return cxReg("machine", (m.findings != null ? m.findings + " findings" : "—") + (denom ? " / " + denom : "")) +
      cxReg("v8 tree", m.v8_reachable === true ? "read" : "unreachable", m.v8_reachable !== true);
  }
  function cxRegisters() {
    var sigs = cxLiveSignals();
    var links = [], scens = 0;
    (D.chains || []).forEach(function (c) { links = links.concat(c.links || []); scens += (c.scenarios || []).length; });
    var names = {};
    links.forEach(function (l) { (l.example_tickers || []).forEach(function (t) { names[t] = 1; }); });
    (D.screens || []).forEach(function (sc) {
      ["pure_play", "picks_and_shovels", "second_order", "hedge"].forEach(function (b) {
        ((sc.buckets || {})[b] || []).forEach(function (r) { names[r.ticker] = 1; });
      });
    });
    (D.stocks || []).forEach(function (st) { names[st.ticker] = 1; });
    var nNames = Object.keys(names).length;
    var vc = { QUIET: 0, UNDISCOVERED: 0, EMERGING: 0, CROWDED: 0, OVER_CROWDED: 0 }, unscored = 0, money = 0, choke = 0;
    links.forEach(function (l) {
      var v = (l.heat || {}).verdict;
      if (v && vc[v] != null) vc[v]++; else unscored++;
      if (l.heat && l.heat.money_corner) money++;
      if ((l.bottleneck || {}).criticality === "CHOKE_POINT") choke++;
    });
    var rs = ((D.health || {}).sessions || {}).routine_status || {};
    var act = (D.health || {}).actions || {};
    var fs = (act.feeds || {}).summary || {};
    var pcsOk = 0;
    Object.keys(D.market || {}).forEach(function (k) {
      var m = D.market[k];
      if (m && m.pcs && m.pcs.axis_a && m.pcs.axis_a.machine_admissible) pcsOk++;
    });
    var stalest = 0;
    (D.chains || []).forEach(function (c) { var a = cxAgeDays(c.heat_as_of); if (a > stalest) stalest = a; });
    (D.screens || []).forEach(function (s) { var a = cxAgeDays(s.as_of); if (a > stalest) stalest = a; });
    var cands = ((D.candidates || {}).candidates || []).filter(function (c) { return c.status === "AMBIENT"; }).length;
    var evts = ((D.calendar || {}).events || []).filter(function (e) { return e.status === "WATCHING"; }).length;
    var items = (D.feeds || {}).items || [];
    var feedHeld = (D.feeds || {}).total != null ? (D.feeds || {}).total : items.length;
    var scoredN = links.length - unscored;
    var mixbar = '<div class="cx-mix">' + ["UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED", "QUIET"].map(function (k) {
      return '<i style="width:' + (links.length ? (100 * vc[k] / links.length).toFixed(1) : 0) + "%;background:" + CXP.verd[k] + '"></i>';
    }).join("") + "</div>";
    function stat(s) { return String(s || "?").toLowerCase().replace(/\s*\(.*\)$/, ""); }
    var summary = cxReg("funnel", sigs.length + " · " + (D.chains || []).length + " · " + links.length + " · " + scens + " · " + (D.stocks || []).length) +
      '<div class="cx-cap">SIGNALS · CHAINS · LINKS · SCENARIOS · VERDICTS</div>' +
      cxReg("heat scored", scoredN + " / " + links.length) + mixbar +
      cxReg("money corners", money + " · choke " + choke) +
      cxReg("feed", feedHeld + " held · " + (fs.sources_ok != null ? fs.sources_ok + "/" + (fs.sources_ok + (fs.sources_failed || []).length) : "—") +
        (cxAgeDays((act.feeds || {}).last_run) != null ? " · " + cxAgeDays((act.feeds || {}).last_run) + "d" : "")) +
      cxReg("requests", pendingCount() + "P / " + failedCount() + "F" + ((D.requests || {}).settled ? " / " + D.requests.settled + " done" : ""), !pendingCount() && !failedCount()) +
      cxReg("radar / digest", stat(rs.radar) + " · " + stat(rs.digest));
    var mcal = (D.map || {}).calibration, mapReg = "";
    if (mcal) {
      var mly = mcal.link_yield || {}, mper = mcal.per_chain || {};
      var mFind = 0, mThin = 0, mUncited = 0;
      Object.keys(mper).forEach(function (k) {
        var st = mper[k].structure || {};
        mFind += (st.orphans || []).length + (st.one_way_edges || []).length + (st.wrong_direction_edges || []).length + (st.verdict_mismatches || []).length;
        mThin += (st.thin_choke_points || []).length;
        mUncited += (st.links_without_evidence || []).length;
      });
      mapReg = '<div class="cx-grp">MAP · ' + (mly.yielded_a_name || 0) + "/" + (mly.links_total || 0) + " YIELDED</div>" +
        cxReg("dead links", (mly.links_total || 0) - (mly.yielded_a_name || 0)) +
        cxBar(mly.links_total ? (mly.yielded_a_name || 0) / mly.links_total : 0, CXP.verd.UNDISCOVERED) +
        cxReg("structural findings", mFind, !mFind) + cxReg("thin choke points", mThin, !mThin) + cxReg("uncited links", mUncited, !mUncited) +
        cxReg("archetypes", ((D.map || {}).archetypes || []).filter(function (a) { return a.status === "HARDENED"; }).length);
    }
    var fam = {};
    items.forEach(function (it) { fam[it.f] = (fam[it.f] || 0) + 1; });
    var famTot = (D.feeds || {}).families || fam;
    var more = '<div class="cx-grp">FUNNEL</div>' +
      cxReg("screens", (D.screens || []).length) + cxReg("names", nNames) +
      cxReg("shadow", ((((D.shadow || {}).book) || {}).rows || []).length) +
      cxReg("ambient", cands) + cxReg("known future", evts) +
      '<div class="cx-grp">HEAT</div>' +
      [["undiscovered", vc.UNDISCOVERED, CXP.verd.UNDISCOVERED], ["emerging", vc.EMERGING, CXP.verd.EMERGING], ["crowded", vc.CROWDED, CXP.verd.CROWDED],
       ["over-crowded", vc.OVER_CROWDED, CXP.verd.OVER_CROWDED], ["quiet", vc.QUIET, CXP.verd.QUIET]].map(function (r) {
        return cxReg(r[0], r[1]) + cxBar(links.length ? r[1] / links.length : 0, r[2]);
      }).join("") + cxReg("unscored", unscored, !unscored) +
      mapReg +
      '<div class="cx-grp">FEED · ' + feedHeld + " HELD</div>" +
      Object.keys(famTot).sort(function (a, b) { return famTot[b] - famTot[a]; }).map(function (f) {
        return cxReg(f.toLowerCase(), famTot[f]) + cxBar(feedHeld ? famTot[f] / feedHeld : 0, CXP.fam[f] || CXP.ink3);
      }).join("") +
      '<div class="cx-grp">SYSTEM</div>' +
      cxReg("fetch", cxAgeDays((act.fetch || {}).last_run) != null ? cxAgeDays((act.fetch || {}).last_run) + "d" : "—") +
      cxReg("trips", ((D.indicators || {}).trips || []).length, !((D.indicators || {}).trips || []).length) +
      cxReg("pcs armed", pcsOk + "/" + nNames, !pcsOk) +
      cxReg("queued", (QUEUE.queue || []).length, !(QUEUE.queue || []).length) +
      cxReg("digest wk", ((D.digests || [])[0] || {}).week || "—") +
      machineReg() +
      cxReg("stalest", stalest ? stalest + "d" : "—") +
      cxReg("since visit", deltaItems().items.length);
    return '<div class="cx-machine"><button class="cx-mhead" id="cxMachineBtn" aria-expanded="false"><span>MACHINE</span><span class="dim">registers · more ▾</span></button>' +
      '<div class="cx-msum">' + summary + '</div><div class="cx-mmore" id="cxMachineMore" hidden>' + more + "</div></div>";
  }
  function cxPanel() {
    return '<div class="cx-panel" id="cxPanel">' +
      '<div class="cx-head"><span class="cx-title">CORTEX</span>' +
      '<button class="cxinfo-btn" id="cxInfoBtn" aria-expanded="false" aria-controls="cxInfo" title="what am I looking at?">' + CX_ICON.eye + "<span>How to read this</span></button></div>" +
      '<div class="cx-oppshead"><div class="cx-serif">Opportunities</div><div class="cx-sub">' + cxLiveSignals().length + " SIGNALS · RANKED BY HOW UNMAPPED · CLICK TO FLY</div></div>" +
      '<div class="cx-opps" id="cxOpps">' + cxOppRows() + "</div>" +
      cxRegisters() +
      '<div class="cx-note">Analytical outputs from public data · not investment advice</div>' +
      "</div>";
  }
  /* The dock (Ron's pick, 2026-09-08: "E + D"). The selected signal's numbers live here,
     not on the field: the name, three figures, every link ranked by heat with the tickers
     named at it, then its scenarios and verdicts in one line. A figure the record lacks
     prints as its absence ("not appraised", "no listed issuer"), never as a stand-in. */
  function cxInspectorHTML(sigId) {
    var s = byId(D.signals, sigId);
    if (!s) return "";
    var occ = s.occurrence || {}, un = (s.unmappedness || {}).score, ap = impactFor(s.id);
    var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
    var links = c ? (c.links || []).slice() : [];
    var dives = (D.stocks || []).filter(function (st) { return c && st.chain_id === c.id; });
    var scens = c ? (c.scenarios || []) : [];
    var fam = cxSigFamily(s.id);
    var order = { UNDISCOVERED: 0, EMERGING: 1, CROWDED: 2, OVER_CROWDED: 3, QUIET: 4 };
    links.sort(function (p, q) {
      var a = (p.heat || {}).verdict, b = (q.heat || {}).verdict;
      return (a in order ? order[a] : 5) - (b in order ? order[b] : 5) || (p.position || 0) - (q.position || 0);
    });
    function fig(k, v, cls) { return '<div class="fig"><span class="k">' + k + '</span><span class="n ' + (cls || "") + '">' + v + "</span></div>"; }
    var rows = links.map(function (l) {
      var ht = l.heat || {}, v = ht.verdict, tk = (l.example_tickers || []).slice(0, 6);
      var mark = ht.money_corner ? '<span class="mk gold">★</span>' : '<span class="mk sq" style="background:' + (v ? CXP.verd[v] : CXP.none) + '"></span>';
      var choke = (l.bottleneck || {}).criticality === "CHOKE_POINT" ? '<span class="choke" title="choke point"></span>' : "";
      return '<div class="lrow' + (v === "UNDISCOVERED" || v === "EMERGING" ? " hot" : "") + '">' + mark + '<span class="ln">' + esc(l.name) + choke + "</span>" +
        '<span class="lt mono">' + (tk.length ? esc(tk.join("  ")) : '<span class="none">no listed issuer</span>') + "</span></div>";
    }).join("");
    var foot = [];
    scens.forEach(function (sc) { foot.push('<span class="mono">' + esc(sc.id) + " " + (sc.probability_pct == null ? "unscored" : sc.probability_pct + "%") + "</span>"); });
    dives.forEach(function (st) {
      foot.push('<a class="mono verd" href="#/stock/' + esc(st.ticker) + "/" + esc(st.chain_id) + '" style="color:' + (CXP.dive[st.verdict] || CXP.ink2) + '">' + esc(st.ticker) + " " + esc((st.verdict || "").replace("_", " ")) + (st.status === "DRAFT" ? " draft" : "") +
        // a TOO LATE verdict's shadow row is part of the verdict: the page shows the pairing
        (st.shadow_ref ? ' <span class="dim">· shadow ' + esc(st.shadow_ref) + "</span>" : "") + "</a>");
    });
    return '<div class="cx-inspector" id="cxInspector" role="complementary" aria-label="selected signal">' +
      '<div class="ihead"><span class="eyebrow"><i class="pip"></i>SIGNAL · ' + esc(occ.kind || "undated") + (fam ? " · " + esc(fam) : "") + "</span>" +
      '<span class="id mono">' + esc(s.id) + "</span></div>" +
      '<div class="ititle">' + esc(s.short_title || cxShort(s.title)) + "</div>" +
      '<div class="isub mono">' + esc(cxTier(un)) + " · " + (occ.anchor_date ? "anchored " + esc(occ.anchor_date) : "undated") + (occ.label ? " · " + esc(cxTrim(occ.label, 40)) : "") + "</div>" +
      '<div class="ifigs">' +
      fig("UNMAPPED", num(un, "?"), "accent") +
      (ap ? fig(esc((ap.impact_band || "IMPACT").toUpperCase()), ap.impact_score == null ? "?" : Math.round(ap.impact_score), "gold") : fig("MONEY", '<span class="none">not appraised</span>')) +
      (c ? fig("LINKS", links.length) : fig("CHAIN", '<span class="none">none yet</span>')) +
      "</div>" +
      (c ? '<div class="ilinks"><div class="k">LINKS BY HEAT · ' + esc(c.heat_as_of ? "heat " + c.heat_as_of : "unscored") + "</div>" + rows + "</div>"
         : '<div class="ilinks"><div class="k">CHAIN</div><div class="none">unchained · run chain maps it</div></div>') +
      (foot.length ? '<div class="ifoot">' + foot.join('<span class="sep">·</span>') + "</div>" : "") +
      '<div class="ifoot dim">' + (s.evidence || []).length + " cited · horizon " + esc((s.horizon_years || []).join("-") || "?") + "y · review by " + esc(s.review_by || "—") + "</div>" +
      '<div class="ibtns"><button class="cx-btn primary" data-cxopen="' + esc(s.id) + '">OPEN CARD</button>' +
      (c ? '<a class="cx-btn" href="#/chain/' + esc(c.id) + '">OPEN CHAIN</a>' : '<button class="cx-btn" data-cxopen="' + esc(s.id) + '">RUN CHAIN</button>') +
      '<button class="cx-btn" data-cxfly="sig:' + esc(s.id) + '">FLY TO</button><button class="cx-btn x" id="cxInspClose" title="clear selection (Esc)">×</button></div>' +
      "</div>";
  }
  function cxFoot() {
    return '<div class="cx-foot" aria-live="polite">' +
      '<div class="l"><div class="read" id="cxRead"></div><div class="legend">' +
      ["UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED", "QUIET"].map(function (k) {
        return '<span><i style="background:' + CXP.verd[k] + '"></i>' + k.replace("_", " ") + "</span>";
      }).join("") +
      '<span class="sep">|</span><span><b style="color:' + CXP.gold + '">★</b> money corner</span><span><b>◎</b> choke point</span><span><b>◇</b> scenario</span><span><b style="color:' + CXP.verd.EMERGING + '">▢</b> verdict</span>' +
      "</div></div>" +
      '<div class="r"><div><span id="cxCounts"></span> · <span id="cxZoom"></span> · <button class="cx-rbtn" data-crot="-1" title="rotate left (Q)">⟲</button><button class="cx-rbtn" data-crot="1" title="rotate right (E)">⟳</button></div>' +
      "<div>BUILT " + esc(D.built_at || TODAY) + "</div><div>ANALYTICAL OUTPUTS FROM PUBLIC DATA · NOT INVESTMENT ADVICE</div></div></div>";
  }
  function cortexView() {
    var hid = cxUIHidden();
    return topbar("cortex") +
      '<div class="cxfull' + (hid ? " ui-hidden" : "") + '" id="cxFull">' +
      '<div class="cx-stage"><canvas id="cortexCanvas" tabindex="0" role="img" aria-label="Cortex field"></canvas></div>' +
      cxPanel() +
      '<div class="cxinfo" id="cxInfo" hidden><p>The machine as a wheel. Angle is the occurrence family, with the real headline and signal counts at the rim. Distance from the centre is how unmapped a signal still is: the eye of the field is the biggest gap. Size is the money Tally found reachable. Each signal wears its chain as a ring of links coloured by heat: a gold star is the money corner, a white ring a choke point, an amber square a verdict, a dashed orbit a signal with no chain yet. The belt at the rim is the raw feed, one dot per headline. Drag to pan, scroll to fly closer (names, then companies appear as you approach), Q and E turn the wheel. Hover any mark for its story, click a hub to select it, click anything else to open it. RADIAL ranks the selected signal\'s links by heat; TIMELINE makes distance time from today, past left, future right. H hides the interface.</p></div>' +
      '<div class="cx-top">' + cxPhaseCtl() + cxTopRight() + "</div>" +
      '<div id="cxInspHost">' + (CX_MODE.sig ? cxInspectorHTML(CX_MODE.sig) : "") + "</div>" +
      cxFoot() +
      "</div>" +
      "<div id='drawerHost'></div>";
  }

  /* ---- drawers (the record behind a mark) ---- */
  function cxShowDrawer(html) {
    var host = document.getElementById("drawerHost");
    if (!host || !html) return;
    host.innerHTML = html;
    host.querySelectorAll("[data-closedrawer]").forEach(function (x) {
      x.addEventListener("click", function () { host.innerHTML = ""; });
    });
    bindCopy(host);
    host.querySelectorAll("[data-run]").forEach(function (b) {
      b.addEventListener("click", function (e) { e.preventDefault(); enqueue(b.getAttribute("data-run"), b); });
    });
    host.querySelectorAll("[data-copyrun]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault();
        var cmd = b.getAttribute("data-copyrun");
        try { navigator.clipboard.writeText(cmd); } catch (err) { fallbackCopy(cmd); }
        b.textContent = "Copied ✓";
      });
    });
  }
  function cxNodeDrawer(n) {
    var o = n.ref;
    if (n.kind === "sig") return cortexDrawer("sig", n.id);
    if (n.kind === "cand") return cortexDrawer("cand", n.id);
    if (n.kind === "evt") return cortexDrawer("evt", n.id);
    if (n.kind === "link") { var c = byId(D.chains, n.chainId); return c ? linkModal(c, o) : ""; }
    if (n.kind === "dust") return cxDustDrawer(o);
    if (n.kind === "scen") {
      return cxDrawerShell(o.id + " — " + o.title,
        chip(o.status) + chip(o.probability_pct + "%", "accent") + (o.clock ? chip(o.clock) : ""),
        "<p class='small'>" + esc(o.narrative) + "</p>" +
        "<h3>Leading indicators</h3><ul class='bullets'>" + (o.leading_indicators || []).map(function (x) {
          return "<li>" + esc(indText(x)) + (x.armed ? " " + chip("armed", "accent") : "") + "</li>";
        }).join("") + "</ul>" +
        "<div class='small'><b>Invalidation:</b> " + ((o.invalidation_signs || []).length ? (o.invalidation_signs || []).map(esc).join(" · ") : "<span class='muted'>none recorded</span>") + "</div>" +
        '<div style="margin-top:14px">' + (o.screen_ref
          ? '<a class="chip accent" href="#/screen/' + esc(n.chainId) + "/" + esc(o.id) + '">Open screen →</a>'
          : runButton("run screen " + n.chainId + " " + o.id, "screens stocks for this scenario")) + "</div>");
    }
    if (n.kind === "dive") { location.hash = "#/stock/" + o.ticker + "/" + o.chain_id; return ""; }
    if (n.kind === "co") {
      var t = n.id, where = [], mk = marketFor(t), srow = null;
      (D.chains || []).forEach(function (c2) {
        (c2.links || []).forEach(function (l) {
          if ((l.example_tickers || []).indexOf(t) > -1) where.push(c2.title + " · " + l.name);
        });
      });
      (D.screens || []).forEach(function (sc) {
        ["pure_play", "picks_and_shovels", "second_order", "hedge"].forEach(function (b) {
          ((sc.buckets || {})[b] || []).forEach(function (r) { if (r.ticker === t) srow = { r: r, b: b, sc: sc }; });
        });
      });
      var dv = null;
      (D.stocks || []).forEach(function (st) { if (st.ticker === t) dv = st; });
      return cxDrawerShell(t + (srow && srow.r.name ? " — " + srow.r.name : ""),
        (srow ? tierChip(srow.r.tier) + chip(srow.b.replace(/_/g, " ")) : chip("named in a chain")) +
        (mk ? chip("market data", "accent") : chip("no market data")) + (dv ? chip(dv.verdict.replace("_", " "), dv.verdict) : ""),
        (srow && srow.r.thesis_1line ? "<p class='small'>" + esc(srow.r.thesis_1line) + "</p>" : "") +
        "<h3>Named in</h3><div class='small'>" + (where.map(esc).join("<br>") || "—") + "</div>" +
        (mk ? "<h3>Market</h3><div class='small num'>" + esc(mk.price_status) + (mk.series ? " · series as of " + esc(mk.series.as_of) : "") + "</div>"
            : "<div class='muted small' style='margin-top:10px'>No market file — queue a fetch to price it.</div>") +
        '<div style="margin-top:14px">' + (dv
          ? '<a class="chip accent" href="#/stock/' + esc(t) + "/" + esc(dv.chain_id) + '">Open dive →</a>'
          : srow ? runButton("run deepdive " + t + " " + srow.sc.chain_id, "full dive: clock, verdict, entry basis")
                 : runButton("request data " + t, "queues prices + fundamentals")) + "</div>");
    }
    return "";
  }
  function cxDrawerShell(title, chips, body) {
    return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="' + esc(title) + '">' +
      '<button class="x" data-closedrawer>✕</button><div class="row">' + chips + "</div>" +
      "<h2>" + esc(title) + "</h2>" + body + "</div>";
  }
  function cxDustDrawer(it) {
    return cxDrawerShell(it.t,
      chip(it.f || "?") + chip((it.d || "").slice(0, 10), "neutral") + chip("raw feed"),
      "<div class='muted small'>[" + esc(it.s || "?") + "] · untriaged feed item — the weekday radar sweep decides whether it becomes a candidate (method §0.1)</div>" +
      "<div style='margin-top:16px'>" + runButton("run radar", "triage the feed into candidates and signals") + "</div>");
  }

  /* ---- hover card: the story behind a mark, from the record itself ---- */
  function cxCardRow(k, v) { return "<div class='r'><span class='k'>" + esc(k) + "</span><span>" + esc(v) + "</span></div>"; }
  function cxCardBar(lab, v, col) {
    return "<div class='bar'><span class='k'>" + esc(lab) + "</span><span class='t'>" + (v == null ? "" : "<i style='width:" + v + "%;background:" + col + "'></i>") + "</span><span class='n'>" + num(v, "unscored") + "</span></div>";
  }
  function cxHoverCard(n) {
    var o = n.ref || {}, h = "";
    function head(kind, title, color) {
      return "<div class='hd'><span class='pip' style='background:" + (color || n.color) + "'></span><span class='kind'>" + esc(kind) + "</span></div><div class='tt'>" + esc(title) + "</div>";
    }
    if (n.kind === "sig") {
      var occ = o.occurrence || {}, ap = impactFor(o.id);
      h = head("signal · " + (occ.kind || "undated") + (n.fam && n.fam !== "UNFILED" ? " · " + n.fam : ""), o.title) +
        "<div class='bd'>" + esc(cxTrim(o.thesis, 200)) + "</div>" +
        cxCardRow("occurrence", (occ.label ? cxTrim(occ.label, 34) + " · " : "") + (occ.anchor_date || "?")) +
        cxCardRow("unmapped", num((o.unmappedness || {}).score, "?") + " / 100") +
        cxCardRow("impact", ap ? (ap.impact_score == null ? "?" : Math.round(ap.impact_score)) + " · " + (ap.impact_band || "") : "not appraised") +
        cxCardRow("chain", n.chain ? (n.chain.links || []).length + " links · " + (n.chain.scenarios || []).length + " scenarios" : "unchained") +
        cxCardRow("review by", o.review_by || "—");
    } else if (n.kind === "link") {
      var ht = o.heat || {}, c = byId(D.chains, n.chainId);
      h = head("link " + o.position + (c ? " of " + (c.links || []).length : "") + (ht.money_corner ? " · ★ money corner" : "") + (n.choke ? " · choke point" : ""), o.name) +
        "<div class='bd'>" + esc(cxTrim(o.role, 170)) + "</div>" +
        "<div class='vd' style='color:" + n.color + "'>" + esc(ht.verdict ? ht.verdict.replace("_", " ") : "UNSCORED") + "</div>" +
        cxCardBar("IMPACT", cxScore(o, "impact"), CXP.accent) + cxCardBar("CROWDEDNESS", cxScore(o, "crowdedness"), "#E97F4E") + cxCardBar("CAPTURE", cxScore(o, "capture"), "#34C77E") +
        ((o.example_tickers || []).length ? cxCardRow("names", o.example_tickers.slice(0, 5).join(" · ")) : "");
    } else if (n.kind === "scen") {
      h = head("scenario " + o.id, o.title) +
        "<div class='bd'>" + esc(cxTrim(o.narrative, 180)) + "</div>" +
        cxCardRow("probability", num(o.probability_pct, "?") + "% · " + (o.status || "")) +
        cxCardRow("indicators", (o.leading_indicators || []).length + " (" + (o.leading_indicators || []).filter(function (x) { return x.armed; }).length + " armed)");
    } else if (n.kind === "co") {
      h = head("company", n.id) + cxCardRow("named at", n.link ? n.link.ref.name : "—") +
        cxCardRow("market data", n.hollow ? "none yet" : "fetched") +
        (n.chains && n.chains.length ? cxCardRow("chain", n.chains.join(", ")) : "");
    } else if (n.kind === "dive") {
      h = head("deep dive", o.ticker) +
        cxCardRow("verdict", (o.verdict || "") + " · " + (o.clock || "") + (o.status === "DRAFT" ? " · draft" : "")) +
        (o.entry_zone ? cxCardRow("entry zone", o.entry_zone.low + " to " + o.entry_zone.high) : "") +
        cxCardRow("review by", o.review_by || "—");
    } else if (n.kind === "cand") {
      var ca = impactFor(o.id);
      h = head("ambient candidate", o.title) +
        "<div class='bd'>" + esc(cxTrim(o.why, 200)) + "</div>" +
        cxCardRow("family", (o.family || "") + " · " + (o.date || "")) +
        (ca ? cxCardRow("impact", (ca.impact_band || "").toLowerCase() + (ca.impact_score != null ? " " + ca.impact_score : "") + " · " + impactTitle(ca)) : "") +
        cxCardRow("source", cxTrim(o.source_name, 44));
    } else if (n.kind === "evt") {
      h = head("known future event", o.title) +
        "<div class='bd'>" + esc(cxTrim(o.why_it_matters, 200)) + "</div>" +
        cxCardRow("when", o.date + (o.window ? " · " + o.window : "")) +
        cxCardRow("source", cxTrim(o.source_name, 44));
    } else if (n.kind === "dust") {
      h = head("raw feed", o.t) + cxCardRow("source", (o.s || "") + " · " + ((o.d || "").slice(0, 10))) + cxCardRow("family", o.f || "?") +
        "<div class='bd dim'>untriaged — the radar sweep judges promotion</div>";
    }
    return h + "<div class='ft'>" + (n.kind === "sig" ? "click to select · double-click to open" : "click to open") + "</div>";
  }
  function cxMatch(n) {
    var kk = n.kind;
    if (CX_FILTER.kinds[kk] === false) return false;
    if (CX_FILTER.fam) {
      var f = n.kind === "sig" ? n.fam : n.sig ? n.sig.fam : n.kind === "cand" ? ((n.ref || {}).family || "UNFILED") : n.kind === "dust" ? ((n.ref || {}).f || "UNFILED") : null;
      if (f && f !== CX_FILTER.fam) return false;
    }
    if (CX_FILTER.q) {
      var hay = ((n.label || "") + " " + (n.tip || "") + " " + (n.kind === "sig" ? (n.ref || {}).title : "")).toLowerCase();
      if (hay.indexOf(CX_FILTER.q) < 0) return false;
    }
    return true;
  }

  /* ---- renderer + interaction ---- */
  function initCortex() {
    var canvas = document.getElementById("cortexCanvas");
    if (!canvas || canvas.__cxRunning) return;
    canvas.__cxRunning = true;
    var ctx = canvas.getContext("2d");
    var REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var g, cam;
    if (CX_CACHE && CX_CACHE.builtAt === D.built_at) { g = CX_CACHE.g; cam = CX_CACHE.cam; }
    else { g = cxBuild(); cam = null; CX_CACHE = { builtAt: D.built_at, g: g, cam: null }; }
    var hover = null, drag = null, fly = null, raf = 0, pinned = null, phaseSeated = null;
    var card = document.getElementById("cxcard");
    if (!card) { card = document.createElement("div"); card.className = "cxcard"; card.id = "cxcard"; card.style.display = "none"; document.body.appendChild(card); }
    var readEl = document.getElementById("cxRead"), zoomEl = document.getElementById("cxZoom"), cntEl = document.getElementById("cxCounts");
    // a read-only handle for the browser console and the verification pass: no data, no writes
    window.__cx = { g: g, cam: function () { return cam; }, mode: CX_MODE };
    // the camera exists before the first frame: a row clicked in a throttled tab (no
    // animation frame yet) must fly, not throw
    if (!cam) { cam = fitCam(440); CX_CACHE.k0 = cam.k; }
    var nObj = g.nodes.filter(function (n) { return n.kind !== "dust"; }).length;
    var nDust = g.nodes.length - nObj;
    if (cntEl) cntEl.textContent = nObj + " OBJECTS · " + nDust + " HEADLINES";
    canvas.setAttribute("aria-label", "Cortex wheel: " + nObj + " research objects by family, unmappedness and reachable money, with " + nDust + " feed headlines at the rim");
    // the serif for names arrives from Google Fonts; the loop simply starts using it
    try {
      if (document.fonts && document.fonts.load) {
        document.fonts.load("italic 400 21px Newsreader"); document.fonts.load("italic 400 15px Newsreader");
        document.fonts.load("400 11px Newsreader"); document.fonts.load("600 8.5px 'JetBrains Mono'");
        // widths measured before the faces arrived describe the fallback face, not these
        if (document.fonts.ready) document.fonts.ready.then(function () { CX_TW = {}; CX_TWN = 0; CX_TWA = {}; });
      }
    } catch (e) {}

    var noise = (function () {
      var c = document.createElement("canvas"); c.width = c.height = 128;
      var x = c.getContext("2d"), im = x.createImageData(128, 128), r = cxRng(3);
      for (var i = 0; i < im.data.length; i += 4) { var v = 150 + Math.floor(r() * 60); im.data[i] = v; im.data[i + 1] = v; im.data[i + 2] = 200; im.data[i + 3] = 255; }
      x.putImageData(im, 0, 0);
      return c;
    })();
    var sprites = {};
    function haloSprite(color, r) {
      var key = color + "/" + Math.round(r);
      if (sprites[key]) return sprites[key];
      var s = Math.ceil(r), c = document.createElement("canvas");
      c.width = c.height = s * 2;
      var x = c.getContext("2d"), gr = x.createRadialGradient(s, s, 0, s, s, s);
      gr.addColorStop(0, color); gr.addColorStop(0.45, color); gr.addColorStop(1, "rgba(0,0,0,0)");
      x.globalAlpha = 0.2; x.fillStyle = gr; x.fillRect(0, 0, s * 2, s * 2);
      sprites[key] = { c: c, s: s };
      return sprites[key];
    }

    /* the free area: the stage minus the panel and the inspector, where the wheel centres */
    function freeRect() {
      var w = canvas.clientWidth, h = canvas.clientHeight, top = 0, left = 0, right = w, bottom = h;
      var full = document.getElementById("cxFull");
      var hidden = full && full.classList.contains("ui-hidden");
      var tb = document.querySelector(".topbar");
      if (tb && !hidden) top = tb.getBoundingClientRect().height;
      var phone = w < 640;
      if (!hidden) {
        var panel = document.getElementById("cxPanel");
        if (panel && !phone) left = panel.getBoundingClientRect().right + 8;
        if (panel && phone) bottom = h - panel.getBoundingClientRect().height - 8;
        var insp = document.getElementById("cxInspector");
        if (insp && !phone) right = insp.getBoundingClientRect().left - 8;
        top += 44;
        if (!phone) bottom = h - 60;
      }
      return { x0: left, y0: top, x1: right, y1: bottom, w: Math.max(120, right - left), h: Math.max(120, bottom - top) };
    }
    function fitCam(radius) {
      var fr = freeRect();
      var k = Math.min(fr.w / (2 * radius), fr.h / (2 * radius));
      return { k: k, rot: 0, tx: 0, ty: 0, cx: fr.x0 + fr.w / 2, cy: fr.y0 + fr.h / 2 };
    }
    function proj(x, y) {
      var c = Math.cos(cam.rot), s = Math.sin(cam.rot), dx = x - cam.tx, dy = y - cam.ty;
      return { x: cam.cx + cam.k * (dx * c - dy * s), y: cam.cy + cam.k * (dx * s + dy * c) };
    }
    function unproj(px, py) {
      var c = Math.cos(-cam.rot), s = Math.sin(-cam.rot), dx = (px - cam.cx) / cam.k, dy = (py - cam.cy) / cam.k;
      return { x: cam.tx + dx * c - dy * s, y: cam.ty + dx * s + dy * c };
    }
    function pick(px, py) {
      var best = null, bd = 1e9;
      for (var i = 0; i < g.nodes.length; i++) {
        var n = g.nodes[i];
        if (n._px == null || n._hid) continue;
        var dx = n._px - px, dy = n._py - py, d = Math.sqrt(dx * dx + dy * dy);
        var reach = n.kind === "dust" ? 3 : n.kind === "sig" ? n._R + 6 : n._R + 5;
        if (d < reach && d < bd) { bd = d; best = n; }
      }
      return best;
    }
    function flyToFit(pts, pad) {
      var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, k = 0;
      pts.forEach(function (p) { if (!p) return; k++; if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x; if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y; });
      if (!k) return;
      var fr = freeRect(), gw = Math.max(maxX - minX, 40) + (pad || 60), gh = Math.max(maxY - minY, 40) + (pad || 60);
      var kk = Math.min(fr.w / gw, fr.h / gh, 6);
      fly = { t0: Date.now(), ms: 700, from: { tx: cam.tx, ty: cam.ty, k: cam.k, rot: cam.rot }, to: { tx: (minX + maxX) / 2, ty: (minY + maxY) / 2, k: kk, rot: cam.rot } };
    }
    function camHome() {
      var fit = fitCam(CX_MODE.phase === "radial" ? 400 : 440);
      var tx = CX_MODE.phase === "timeline" ? -70 : 0;
      fly = { t0: Date.now(), ms: 700, from: { tx: cam.tx, ty: cam.ty, k: cam.k, rot: cam.rot }, to: { tx: tx, ty: 0, k: fit.k, rot: cam.rot } };
    }
    function flyToSig(n) {
      // frame the system with its neighbourhood: close enough to read its links, far
      // enough that the wheel around it still shows where it sits
      var k0 = CX_CACHE.k0 || cam.k, kk = Math.min(k0 * 3.2, Math.max(k0 * 1.6, (freeRect().h * 0.5) / (n.sysR + 60)));
      fly = { t0: Date.now(), ms: 700, from: { tx: cam.tx, ty: cam.ty, k: cam.k, rot: cam.rot }, to: { tx: n.gx, ty: n.gy, k: kk, rot: cam.rot } };
    }
    function seatPhase() {
      var key = CX_MODE.phase + ":" + (CX_MODE.sig || "");
      if (phaseSeated === key) return;
      phaseSeated = key;
      var sig = CX_MODE.sig ? g.byKey["sig:" + CX_MODE.sig] : null;
      if (CX_MODE.phase === "radial" && sig && sig.chain) cxSeatRadial(g, sig);
      else if (CX_MODE.phase === "timeline") cxSeatTimeline(g);
      else cxSeatNetwork(g);
      if (REDUCED) g.nodes.forEach(function (n) { n.x = n.gx; n.y = n.gy; });
    }

    var CXBOX = [];
    function boxFree(x0, y0, x1, y1) {
      for (var i = 0; i < CXBOX.length; i++) {
        var b = CXBOX[i];
        if (x0 < b[2] && x1 > b[0] && y0 < b[3] && y1 > b[1]) return false;
      }
      return true;
    }
    function claim(x0, y0, x1, y1) { CXBOX.push([x0, y0, x1, y1]); }
    /* Widths were estimated as 0.46 characters wide, which is wrong for Newsreader and
       Inter alike: they are proportional, so an estimate is out by a fifth on a
       caps-heavy name and the boxes it produces do not describe the pixels. Mono is
       exactly its advance, measured once per size rather than assumed, because a font
       that failed to load falls back to a different monospace. */
    function tw(font, txt) {
      txt = String(txt == null ? "" : txt);
      if (!txt) return 0;
      if (font.indexOf("JetBrains") >= 0) {
        var a = CX_TWA[font];
        if (a == null) { ctx.font = font; a = ctx.measureText("MMMMMMMMMM").width / 10; CX_TWA[font] = a; }
        return txt.length * a;
      }
      var k = font + " " + txt, v = CX_TW[k];
      if (v != null) return v;
      if (CX_TWN > 3000) { CX_TW = {}; CX_TWN = 0; }
      ctx.font = font;
      v = ctx.measureText(txt).width;
      CX_TW[k] = v; CX_TWN++;
      return v;
    }
    function fontPx(font) { var m = /([0-9.]+)px/.exec(font); return m ? parseFloat(m[1]) : 11; }
    /* Draw one label at the first candidate offset that is clear, or not at all. cands
       are {x, y, align} in preference order; force means the label answers something the
       reader just did (a hover, the view's own title) and is drawn regardless. */
    function put(txt, font, color, cands, alpha, force) {
      if (txt == null || txt === "") return false;
      var c = seat(tw(font, txt), fontPx(font) + 3, cands, force);
      if (!c) return false;
      label(txt, c.x, c.y, font, color, c.align, alpha);
      return true;
    }
    // the seat search alone: the first clear candidate for a box this size, claimed, or
    // null. A label made of several runs (a ticker and its verdict word) seats once and
    // draws its parts itself.
    function seat(wpx, hpx, cands, force) {
      var i, c, x0, y0;
      for (i = 0; i < cands.length; i++) {
        c = cands[i];
        x0 = c.align === "right" ? c.x - wpx : c.x;
        y0 = c.y - hpx / 2;
        if (force || boxFree(x0 - 2, y0 - 1, x0 + wpx + 2, y0 + hpx + 1)) {
          claim(x0 - 2, y0 - 1, x0 + wpx + 2, y0 + hpx + 1);
          return { x: c.x, y: c.y, align: c.align, x0: x0, w: wpx };
        }
      }
      return null;
    }
    // one candidate on the side the caller wanted, then the other side, then above and
    // below it: the nudge before the drop
    function sides(x, y, off, right) {
      var a = right ? "left" : "right", b = right ? "right" : "left";
      return [{ x: right ? x + off : x - off, y: y, align: a },
              { x: right ? x - off : x + off, y: y, align: b },
              { x: right ? x + off : x - off, y: y - 13, align: a },
              { x: right ? x + off : x - off, y: y + 13, align: a }];
    }
    /* Measured text widths, kept across frames because every label string on this canvas
       comes from the payload and none of them changes between frames. Wholesale wipe past
       a few thousand entries, the same shape the sprite cache uses. */
    var CX_TW = {}, CX_TWN = 0, CX_TWA = {};
    /* text helpers: an outlined label reads over dust; an arc label follows the rim */
    function label(txt, x, y, font, color, align, alpha) {
      ctx.font = font; ctx.textAlign = align || "left"; ctx.textBaseline = "middle";
      ctx.globalAlpha = alpha == null ? 1 : alpha;
      ctx.lineJoin = "round"; ctx.lineWidth = 4; ctx.strokeStyle = CXP.bg0;
      ctx.strokeText(txt, x, y); ctx.fillStyle = color; ctx.fillText(txt, x, y);
    }
    function arcText(txt, r, midDeg, font, color, alpha) {
      // characters laid along the circle; the bottom half runs the other way so it stays upright
      ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = color;
      ctx.globalAlpha = alpha == null ? 1 : alpha;
      var bottom = ((midDeg % 360) + 360) % 360 > 90 && ((midDeg % 360) + 360) % 360 < 270;
      var chars = txt.split(""), widths = chars.map(function (ch) { return ctx.measureText(ch).width; });
      var total = widths.reduce(function (p, c) { return p + c; }, 0) + (chars.length - 1) * 2.2;
      var rr = r * cam.k, arc = total / rr;
      var a0 = (midDeg * Math.PI / 180) + cam.rot - (bottom ? -arc / 2 : arc / 2);
      var acc = 0;
      chars.forEach(function (ch, i) {
        var w = widths[i], a = bottom ? a0 - (acc + w / 2) / rr : a0 + (acc + w / 2) / rr;
        var p = proj(0, 0);
        var px = p.x + rr * Math.sin(a), py = p.y - rr * Math.cos(a);
        ctx.save(); ctx.translate(px, py); ctx.rotate(bottom ? a + Math.PI : a);
        ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = CXP.bg0; ctx.strokeText(ch, 0, 0);
        ctx.fillText(ch, 0, 0); ctx.restore();
        acc += w + 2.2;
      });
    }
    function star(x, y, r, col) {
      ctx.beginPath();
      for (var i = 0; i < 10; i++) {
        var rr = i % 2 === 0 ? r : r * 0.42, a = -Math.PI / 2 + i * Math.PI / 5;
        if (i === 0) ctx.moveTo(x + rr * Math.cos(a), y + rr * Math.sin(a)); else ctx.lineTo(x + rr * Math.cos(a), y + rr * Math.sin(a));
      }
      ctx.closePath(); ctx.fillStyle = col; ctx.fill();
    }
    function diamond(x, y, s, col, dashed) {
      ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x, y + s); ctx.lineTo(x - s, y); ctx.closePath();
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      if (dashed) ctx.setLineDash([2, 1.6]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    function square(x, y, s, col, fill) {
      if (fill) { ctx.fillStyle = col; ctx.fillRect(x - s, y - s, s * 2, s * 2); }
      else { ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.strokeRect(x - s + 0.5, y - s + 0.5, s * 2 - 1, s * 2 - 1); }
    }
    var rimHits = [];   // sector labels are clickable: family focus

    function drawWheel(w, h, al, scale) {
      // sectors, rim, names and counts: the wheel's own chart furniture
      var o = proj(0, 0), K = cam.k * scale;
      var fam = CX_FILTER.fam;
      rimHits = [];
      g.sectors.forEach(function (s, i) {
        var a0 = s.a0 * Math.PI / 180 + cam.rot, a1 = s.a1 * Math.PI / 180 + cam.rot;
        var dimS = fam && fam !== s.fam;
        // wedge tint, alternating like a clock face
        ctx.globalAlpha = al * (i % 2 === 0 ? 0.03 : 0.012) * (dimS ? 0.4 : 1);
        ctx.fillStyle = "#FFFFFF";
        ctx.beginPath(); ctx.moveTo(o.x, o.y); ctx.arc(o.x, o.y, CX_RIM * K, a0 - Math.PI / 2, a1 - Math.PI / 2); ctx.closePath(); ctx.fill();
        // rim arc with a gap and a tick at the boundary
        ctx.globalAlpha = al * (dimS ? 0.3 : 0.9); ctx.strokeStyle = "#33355A"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(o.x, o.y, CX_RIM * K, a0 - Math.PI / 2 + 0.028, a1 - Math.PI / 2 - 0.028); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(o.x + (CX_RIM - 5) * K * Math.sin(a0), o.y - (CX_RIM - 5) * K * Math.cos(a0));
        ctx.lineTo(o.x + (CX_RIM + 5) * K * Math.sin(a0), o.y - (CX_RIM + 5) * K * Math.cos(a0)); ctx.stroke();
        var name = s.fam === "UNFILED" ? "NO FAMILY" : s.fam;
        var col = s.fam === "UNFILED" ? "#5E617C" : (fam === s.fam ? "#FFFFFF" : "#C7CAE0");
        var fsz = Math.max(9, Math.min(13, 10.5 * Math.sqrt(cam.k / (CX_CACHE.k0 || cam.k))));
        arcText(name, (CX_RIM + 16) * scale, s.mid, "600 " + fsz.toFixed(1) + "px Inter, sans-serif", col, al * (dimS ? 0.45 : 1));
        var sub = s.fam === "UNFILED" ? s.sigs + " signal" + (s.sigs === 1 ? "" : "s") + " not yet filed"
          : s.heads + " headline" + (s.heads === 1 ? "" : "s") + " · " + s.sigs + " signal" + (s.sigs === 1 ? "" : "s");
        var md = ((s.mid % 360) + 360) % 360, inside = md > 140 && md < 220;
        var lp = cxPol((inside ? CX_RIM - 26 : CX_RIM + 32) * scale, s.mid), sp = proj(lp.x, lp.y);
        var mdr = ((s.mid + cam.rot * 180 / Math.PI) % 360 + 360) % 360;
        var align = inside ? "center" : (mdr > 0 && mdr < 180 ? "left" : mdr > 180 ? "right" : "center");
        label(sub, sp.x, sp.y, "9px 'JetBrains Mono', monospace", CXP.ink3, align, al * (dimS ? 0.45 : 1));
        var np = cxPol((CX_RIM + 16) * scale, s.mid), npp = proj(np.x, np.y);
        rimHits.push({ x: npp.x, y: npp.y, fam: s.fam, r: 46 });
      });
      // the belt: a faint halo under the dust
      ctx.globalAlpha = al * 0.035; ctx.strokeStyle = "#9FA3C8"; ctx.lineWidth = 58 * K;
      ctx.beginPath(); ctx.arc(o.x, o.y, ((CX_DUST0 + CX_DUST1) / 2) * K, 0, 7); ctx.stroke();
      // the eye of the field
      var eg = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, 150 * K);
      eg.addColorStop(0, "rgba(134,135,240,0.13)"); eg.addColorStop(0.6, "rgba(134,135,240,0.03)"); eg.addColorStop(1, "rgba(134,135,240,0)");
      ctx.globalAlpha = al; ctx.fillStyle = eg; ctx.beginPath(); ctx.arc(o.x, o.y, 150 * K, 0, 7); ctx.fill();
      ctx.strokeStyle = CXP.ink3; ctx.globalAlpha = al * 0.7; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(o.x - 5, o.y); ctx.lineTo(o.x + 5, o.y); ctx.moveTo(o.x, o.y - 5); ctx.lineTo(o.x, o.y + 5); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    function drawTimeRings(w, h, al) {
      var o = proj(0, 0), K = cam.k, y0 = parseInt(TODAY.slice(0, 4), 10);
      ctx.globalAlpha = al;
      for (var k = 1; k <= 4; k++) {
        var rr = cxTimeR(k * 365) * K;
        ctx.strokeStyle = "#33355A"; ctx.globalAlpha = al * 0.7; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(o.x, o.y, rr, 0, 7); ctx.stroke();
        var left = k === 4 ? "≤" + (y0 - k) : String(y0 - k), right = k === 4 ? (y0 + k) + "+" : String(y0 + k);
        label(left, o.x - rr - 6, o.y, "9.5px 'JetBrains Mono', monospace", CXP.ink3, "right", al);
        label(right, o.x + rr + 6, o.y, "9.5px 'JetBrains Mono', monospace", CXP.ink3, "left", al);
      }
      var top = cxTimeR(CX_CLAMP) * K + 32;
      ctx.globalAlpha = al * 0.55; ctx.strokeStyle = CXP.accent2; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(o.x, o.y - top); ctx.lineTo(o.x, o.y + top); ctx.stroke();
      label("NOW · " + TODAY, o.x + 8, o.y - top + 10, "9.5px 'JetBrains Mono', monospace", CXP.accent2, "left", al);
      label("PAST", o.x - top - 10, o.y - top + 10, "600 10.5px Inter, sans-serif", "#C7CAE0", "left", al);
      label("FUTURE", o.x + top + 10, o.y - top + 10, "600 10.5px Inter, sans-serif", "#C7CAE0", "right", al);
      ctx.globalAlpha = 1;
    }

    function draw() {
      if (!canvas.isConnected) { canvas.__cxRunning = false; cancelAnimationFrame(raf); card.style.display = "none"; return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!cam) { cam = fitCam(440); CX_CACHE.k0 = cam.k; }
      else { var fr0 = freeRect(); cam.cx = fr0.x0 + fr0.w / 2; cam.cy = fr0.y0 + fr0.h / 2; }
      CX_CACHE.cam = cam;
      seatPhase();
      var t = Date.now() / 1000;
      if (fly) {
        var ft = Math.min(1, (Date.now() - fly.t0) / fly.ms);
        fly.fp = (fly.fp || 0) + 1 / 40;
        if (fly.fp > ft) ft = Math.min(1, fly.fp);
        var e = ft < 0.5 ? 2 * ft * ft : 1 - Math.pow(-2 * ft + 2, 2) / 2;
        cam.tx = fly.from.tx + (fly.to.tx - fly.from.tx) * e; cam.ty = fly.from.ty + (fly.to.ty - fly.from.ty) * e;
        cam.k = fly.from.k + (fly.to.k - fly.from.k) * e; cam.rot = fly.from.rot + (fly.to.rot - fly.from.rot) * e;
        if (ft >= 1) fly = null;
      }
      // seats: every node eases toward its seat in the current phase
      var ease = REDUCED ? 1 : 0.14;
      g.nodes.forEach(function (n) {
        if (n.gx == null) return;
        n.x += (n.gx - n.x) * ease; n.y += (n.gy - n.y) * ease;
        if (n.kind === "sig") { n.Rc = n.Rc == null ? n.gR : n.Rc + (n.gR - n.Rc) * ease; }
      });
      var dimBg = g.dim ? 1 : 0;
      var isTL = CX_MODE.phase === "timeline", isRad = CX_MODE.phase === "radial" && g.dim;

      // ground
      var bg = ctx.createRadialGradient(w / 2, h / 2, 40, w / 2, h / 2, Math.max(w, h) * 0.72);
      bg.addColorStop(0, CXP.bg1); bg.addColorStop(1, CXP.bg0);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
      ctx.globalAlpha = 0.05; ctx.fillStyle = ctx.createPattern(noise, "repeat"); ctx.fillRect(0, 0, w, h); ctx.globalAlpha = 1;
      if (isTL) drawTimeRings(w, h, 1);
      else drawWheel(w, h, isRad ? 0.22 : 1, g.scale || 1);

      var zoomF = cam.k / (CX_CACHE.k0 || cam.k);   // 1 = the wheel as first seen
      // on a phone the wheel fits at a third of the desktop zoom; markers and names
      // shrink with it so the field stays a field, not a pile of labels
      var ui = Math.min(1, Math.max(0.55, (CX_CACHE.k0 || cam.k) / 0.85));
      var hi = hover, sel = CX_MODE.sig ? g.byKey["sig:" + CX_MODE.sig] : null;
      var anyFilter = CX_FILTER.q || CX_FILTER.fam || CX_KIND_CHIPS.some(function (kc) { return CX_FILTER.kinds[kc[0]] === false; });
      function sysOf(n) { return n.kind === "sig" ? n : n.sig || null; }
      var hiSys = hi ? sysOf(hi) : null;
      function alphaOf(n, base) {
        var a = base;
        if (isRad) a *= (n === sel || n.sig === sel) ? 1 : 0.18;
        if (anyFilter && !cxMatch(n)) a *= 0.08;
        if (hi && hiSys && n.kind !== "dust") a *= (sysOf(n) === hiSys || n === hi) ? 1 : 0.35;
        var gv = cxGainOf(n.kind);
        if (gv < 1) a *= Math.max(0.08, 0.35 + 0.65 * gv);
        return a;
      }
      function sizeOf(n, base) {
        var gain = cxGainOf(n.kind), rr = base * gain;
        if (CX_GAIN.contrast !== 1) rr = 5 * Math.pow(Math.max(0.1, rr) / 5, CX_GAIN.contrast);
        return rr;
      }
      // project
      g.nodes.forEach(function (n) {
        if (n.gx == null) { n._px = null; return; }
        var p = proj(n.x, n.y);
        n._px = p.x; n._py = p.y;
        n._hid = CX_FILTER.kinds[n.kind] === false;
      });

      // 1. dust: one dot per headline
      if (CX_FILTER.kinds.dust !== false) {
        ctx.fillStyle = CXP.dust;
        var dz = Math.min(1.6, Math.max(0.7, Math.sqrt(zoomF)));
        g.nodes.forEach(function (n) {
          if (n.kind !== "dust" || n._px == null) return;
          if (n._px < -4 || n._px > w + 4 || n._py < -4 || n._py > h + 4) return;
          var rr = sizeOf(n, n.r) * dz;
          ctx.globalAlpha = alphaOf(n, n.a * (isTL ? 0.6 : 1));
          ctx.beginPath(); ctx.arc(n._px, n._py, rr, 0, 7); ctx.fill();
          n._R = rr;
        });
      }
      // 2. ambient candidates and known future events
      g.nodes.forEach(function (n) {
        if ((n.kind !== "cand" && n.kind !== "evt") || n._px == null || n._hid) return;
        var rr = sizeOf(n, n.r);
        ctx.globalAlpha = alphaOf(n, 0.65);
        if (n.kind === "cand") {
          ctx.strokeStyle = CXP.ambient; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(n._px, n._py, rr, 0, 7); ctx.stroke();
          if (n.prime) { ctx.fillStyle = CXP.ambient; ctx.beginPath(); ctx.arc(n._px, n._py, 1.1, 0, 7); ctx.fill(); }
        } else diamond(n._px, n._py, rr, CXP.ambient, true);
        n._R = rr;
        if (isTL && n.kind === "evt") label(n.label, n._px + 8, n._py, "8.5px 'JetBrains Mono', monospace", CXP.ink3, "left", alphaOf(n, 0.9));
      });
      // 3. systems: ring thread, spokes, markers, hub
      var showCo = CX_FILTER.kinds.co !== false && (zoomF > 1.6 || isRad), showScen = CX_FILTER.kinds.scen !== false && (zoomF > 1.3 || isRad);
      g.sigs.forEach(function (n) {
        if (n._px == null || n._hid) return;
        var focused = isRad && n === sel;
        var a = alphaOf(n, 1);
        var hr = sizeOf(n, n.hr) * ui * Math.min(1.8, Math.max(0.85, Math.sqrt(zoomF)));
        if (focused) hr = 24 * ui;
        n._R = hr;
        // halo
        var hs = haloSprite(CXP.accent, hr * 3.6);
        ctx.globalAlpha = a * (focused ? 0.9 : 0.85);
        ctx.drawImage(hs.c, n._px - hs.s, n._py - hs.s, hs.s * 2, hs.s * 2);
        // chain thread (closed polyline through the links in position order) or a dashed empty orbit
        var links = (n.links || []).filter(function (l) { return l._px != null; });
        if (links.length && !focused) {
          ctx.globalAlpha = a * 0.22; ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1;
          ctx.beginPath();
          links.forEach(function (l, k) { if (k === 0) ctx.moveTo(l._px, l._py); else ctx.lineTo(l._px, l._py); });
          ctx.closePath(); ctx.stroke();
        } else if (!links.length) {
          ctx.globalAlpha = a * 0.28; ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1; ctx.setLineDash([2.5, 3.5]);
          ctx.beginPath(); ctx.arc(n._px, n._py, (n.Rc || n.R) * cam.k, 0, 7); ctx.stroke(); ctx.setLineDash([]);
        }
        if (focused) drawRadialFocus(n, a);
        // links: B's crisp squares, coloured by heat; gold spoke + star for the money corner; white ring for a choke point
        links.forEach(function (l) {
          if (l._hid) return;
          var la = alphaOf(l, 0.95), s = sizeOf(l, focused ? 4.2 : 2.4) * ui * (focused ? 1 : Math.min(1.5, Math.max(0.9, Math.sqrt(zoomF))));
          ctx.globalAlpha = la;
          if (l.money && !focused) { ctx.strokeStyle = CXP.gold; ctx.globalAlpha = la * 0.4; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(n._px, n._py); ctx.lineTo(l._px, l._py); ctx.stroke(); ctx.globalAlpha = la; }
          if (l.choke) { ctx.strokeStyle = "#FFFFFF"; ctx.globalAlpha = la * 0.5; ctx.lineWidth = 0.9; ctx.beginPath(); ctx.arc(l._px, l._py, s + 3.2, 0, 7); ctx.stroke(); ctx.globalAlpha = la; }
          if (l.verdict) square(l._px, l._py, l.money ? s + 0.8 : s, l.color, true);
          else square(l._px, l._py, s, CXP.ink3, false);
          if (l.money) star(l._px, l._py, s + 4.5, CXP.gold);
          if (l === hi || (sel === l)) { ctx.strokeStyle = CXP.ink; ctx.lineWidth = 1; ctx.strokeRect(l._px - s - 3.5, l._py - s - 3.5, s * 2 + 7, s * 2 + 7); }
          l._R = s + 1;
        });
        // companies (at zoom or in the radial), scenarios
        g.nodes.forEach(function (m) {
          if (m.sig !== n || m._px == null || m._hid) return;
          if (m.kind === "co") {
            if (!showCo) { m._px = null; return; }
            var ra = alphaOf(m, 0.7), rr = sizeOf(m, focused ? 1.6 : 1.3);
            ctx.globalAlpha = ra; ctx.fillStyle = m.color;
            ctx.beginPath(); ctx.arc(m._px, m._py, rr, 0, 7); ctx.fill();
            m._R = rr + 1;
          } else if (m.kind === "scen") {
            if (!showScen) { m._px = null; return; }
            ctx.globalAlpha = alphaOf(m, 0.75);
            diamond(m._px, m._py, sizeOf(m, focused ? 4.5 : 3.6), focused ? "#E4E5F5" : m.color, false);
            m._R = 5;
          } else if (m.kind === "dive") {
            var s2 = sizeOf(m, focused ? 3.2 : 2.6);
            ctx.globalAlpha = alphaOf(m, 1);
            ctx.fillStyle = CXP.bg0; ctx.fillRect(m._px - s2, m._py - s2, s2 * 2, s2 * 2);
            ctx.strokeStyle = m.color; ctx.lineWidth = 1.1;
            if (m.dashed) ctx.setLineDash([2, 1.5]);
            ctx.strokeRect(m._px - s2 + 0.5, m._py - s2 + 0.5, s2 * 2 - 1, s2 * 2 - 1); ctx.setLineDash([]);
            m._R = s2 + 1;
          }
        });
        // hub: ring, then the core
        ctx.globalAlpha = a * 0.38; ctx.strokeStyle = CXP.accent2; ctx.lineWidth = 1;
        if (n.dashed) ctx.setLineDash([3, 2.5]);
        ctx.beginPath(); ctx.arc(n._px, n._py, hr + 3, 0, 7); ctx.stroke(); ctx.setLineDash([]);
        var cg = ctx.createRadialGradient(n._px, n._py, 0, n._px, n._py, hr);
        cg.addColorStop(0, "#FFFFFF"); cg.addColorStop(0.38, "#DADBFF"); cg.addColorStop(1, CXP.accent);
        ctx.globalAlpha = a; ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(n._px, n._py, hr, 0, 7); ctx.fill();
        if (n === sel && !focused) { ctx.strokeStyle = CXP.ink; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(n._px, n._py, hr + 7, 0, 7); ctx.stroke(); }
        if (n === hi) { ctx.strokeStyle = CXP.accent2; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(n._px, n._py, hr + 10, 0, 7); ctx.stroke(); }
      });

      // 4. labels: signal names in the serif, then link/dive/company names by zoom
      drawLabels(w, h, zoomF, hi, sel, anyFilter, isTL, isRad, ui);
      ctx.globalAlpha = 1;

      if (pinned && pinned._px != null) placeCard(cxHoverCard(pinned), canvas.getBoundingClientRect().left + pinned._px, canvas.getBoundingClientRect().top + pinned._py);
      statusLine(w, h);
      if (zoomEl) zoomEl.textContent = "ZOOM " + zoomF.toFixed(2) + "× · TURN " + (Math.round(cam.rot * 180 / Math.PI) % 360) + "°";
      raf = requestAnimationFrame(draw);
    }

    /* the focused radial: verdict arcs on the ring, spokes with the three factor bars,
       names outside the ring, scenarios on the outer dotted ring, the chart title top-left */
    function drawRadialFocus(n, a) {
      var o = proj(0, 0), K = cam.k, R = n.radialR || 200, m = n.radialN || 1, hr = 24;
      var links = cxRankedLinks(n.chain);
      ctx.globalAlpha = a * 0.25; ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(o.x, o.y, R * K, 0, 7); ctx.stroke();
      ctx.globalAlpha = a * 0.5; ctx.strokeStyle = "#33355A"; ctx.setLineDash([1, 5]);
      ctx.beginPath(); ctx.arc(o.x, o.y, (R + 62) * K, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      // which stretch of the ring is which heat
      var seg = 0;
      ["UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED", "QUIET"].forEach(function (v) {
        var k = links.filter(function (l) { return (l.heat || {}).verdict === v; }).length;
        if (!k) return;
        var a0 = (-90 + 360 * seg / m - 180 / m + 2) * Math.PI / 180 + cam.rot, a1 = (-90 + 360 * (seg + k) / m - 180 / m - 2) * Math.PI / 180 + cam.rot;
        ctx.globalAlpha = a * 0.55; ctx.strokeStyle = CXP.verd[v]; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(o.x, o.y, (R + 34) * K, a0, a1); ctx.stroke();
        seg += k;
      });
      // spokes and factor bars
      links.forEach(function (l, i) {
        var th = (-90 + 360 * i / m) * Math.PI / 180 + cam.rot, c = Math.cos(th), s = Math.sin(th), px = -s, py = c;
        ctx.globalAlpha = a * 0.16; ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(o.x + (hr + 6) * c, o.y + (hr + 6) * s); ctx.lineTo(o.x + R * K * c, o.y + R * K * s); ctx.stroke();
        CX_FACTORS.forEach(function (f, q) {
          var v = cxScore(l, f[0]), off = (q - 1) * 4.2, r0 = (hr + 14), span = Math.max(40, R * K - hr - 60);
          var x0 = o.x + r0 * c + px * off, y0 = o.y + r0 * s + py * off;
          ctx.lineCap = "round"; ctx.lineWidth = 2.2;
          ctx.globalAlpha = a * 0.06; ctx.strokeStyle = "#FFFFFF";
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + span * c, y0 + span * s); ctx.stroke();
          if (v == null) return;   // an unscored leg keeps its empty track: that is the statement
          ctx.globalAlpha = a * 0.9; ctx.strokeStyle = f[2];
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + span * v / 100 * c, y0 + span * v / 100 * s); ctx.stroke();
        });
        ctx.lineCap = "butt";
      });
      ctx.globalAlpha = 1;
    }
    function drawLabels(w, h, zoomF, hi, sel, anyFilter, isTL, isRad, ui) {
      var lden = typeof CX_GAIN.labels === "number" ? CX_GAIN.labels : 1;
      var sz = Math.min(1.5, Math.max(0.85, Math.sqrt(zoomF))) * (ui || 1);
      CXBOX.length = 0;
      /* Claimed label boxes for this frame. Signal names were already nudged apart from
         each other, but nothing else on the canvas was: links, scenarios, companies,
         dives, candidates and events each drew at a fixed offset from their own dot with
         no idea what was already there, which is why headlines cut straight through the
         serif titles. Everything goes through claim() now, in importance order, and a
         label that cannot find a clear spot is not drawn. */
      // the panel, the inspector and the topbar float above this canvas, so the area
      // outside freeRect belongs to them and no label may be placed into it
      var FR = freeRect();
      claim(-9e5, -9e5, 9e5, FR.y0);
      claim(-9e5, FR.y1, 9e5, 9e5);
      claim(-9e5, -9e5, FR.x0, 9e5);
      claim(FR.x1, -9e5, 9e5, 9e5);
      // the radial's chart title sits at the top-left of the free area
      if (isRad && sel) {
        var fr = FR;
        var tf = "italic 400 " + Math.round(30 * (ui || 1)) + "px Newsreader, Georgia, serif";
        var sf2 = "9px 'JetBrains Mono', monospace";
        var sub2 = (sel.chain.links || []).length + " LINKS RANKED BY HEAT · UNMAPPED " + num(sel.un, "?") + " · " + (sel.band ? sel.band.toUpperCase() + " " + Math.round(sel.imp) : "UNAPPRAISED") + " · " + (sel.chain.scenarios || []).length + " SCENARIOS ON THE OUTER RING";
        label(sel.label, fr.x0 + 24, fr.y0 + 30, tf, CXP.ink, "left", 1);
        label(sub2, fr.x0 + 24, fr.y0 + 50, sf2, CXP.ink3, "left", 1);
        claim(fr.x0 + 20, fr.y0 + 12, fr.x0 + 28 + Math.max(tw(tf, sel.label), tw(sf2, sub2)), fr.y0 + 60);
        var lg = fr.y1 - 14, lgf = "8.5px 'JetBrains Mono', monospace";
        CX_FACTORS.forEach(function (f, q) {
          ctx.globalAlpha = 1; ctx.strokeStyle = f[2]; ctx.lineWidth = 3; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(fr.x0 + 24 + q * 118, lg); ctx.lineTo(fr.x0 + 46 + q * 118, lg); ctx.stroke(); ctx.lineCap = "butt";
          label(f[1], fr.x0 + 52 + q * 118, lg, lgf, CXP.ink2, "left", 1);
          claim(fr.x0 + 20 + q * 118, lg - 8, fr.x0 + 56 + q * 118 + tw(lgf, f[1]), lg + 8);
        });
      }
      var capF = "400 " + (11 * sz).toFixed(1) + "px Newsreader, Georgia, serif";
      var tkF = (8.2 * sz).toFixed(1) + "px 'JetBrains Mono', monospace";
      var capH = 11 * sz + 5, cornerH = 11 * sz + 6;
      function cornerW(n) {
        if (!n.corner) return 0;
        return 14 * sz + tw(capF, n.corner.name) + (n.corner.tickers ? 10 + tw(tkF, n.corner.tickers) : 0);
      }
      function place(n, size, txt, full) {
        // outward from the wheel at rest; toward the screen's centre once flown in, so a
        // framed system's name never runs under the panel or off the edge
        var right = zoomF > 1.3 ? n._px < cam.cx : n._px >= cam.cx;
        var off = (n.sysR || 24) * cam.k + 12;
        var lx = right ? n._px + off : n._px - off;
        var f = "italic 400 " + size.toFixed(1) + "px Newsreader, Georgia, serif";
        var wpx = (full ? Math.max(tw(f, txt), tw(capF, n.cap || ""), cornerW(n)) : tw(f, txt)) + 6;
        // the block: the name, then when `full` its caption sentence and the money-corner line
        return { right: right, lx: lx, ly: n._py, w: wpx, full: full, h: size + 6 + (full ? capH + (n.corner ? cornerH : 0) : 4) };
      }
      // signals: serif names with a leader; boxes nudged apart on each side
      var sigs = g.sigs.filter(function (n) { return n._px != null && !n._hid && n._px > -200 && n._px < w + 200 && n._py > -60 && n._py < h + 60; });
      sigs.forEach(function (n) {
        var focused = isRad && n === sel;
        var size = (n.tier === "MAJOR" ? 21 : n.tier === "WATCH" ? 15.5 : 12.5) * sz;
        var pri = n.tier === "MAJOR" ? 1 : n.tier === "WATCH" ? 0.7 : 0.4;
        var show = n === hi || n === sel || (isRad ? false : pri >= 1 - lden) || (anyFilter && cxMatch(n));
        if (isRad && (n === sel)) show = false;
        // a phone-sized wheel names only its MAJOR signals until you zoom or pick one
        if ((ui || 1) < 0.8 && zoomF < 1.6 && n.tier !== "MAJOR" && n !== sel && n !== hi) show = false;
        // the caption is worn at rest by the MAJOR signals only; every signal earns it on
        // approach, hover or selection, so the wheel at rest stays a field of names
        var full = n.tier === "MAJOR" || zoomF > 1.6 || n === hi || n === sel || (anyFilter && cxMatch(n));
        n._lab = show && !focused ? place(n, size, n.label, full) : null;
        if (n._lab) n._lab.size = size;
      });
      for (var it = 0; it < 30; it++) {
        var moved = false, ls = sigs.filter(function (n) { return n._lab; }).sort(function (p, q) { return p._lab.ly - q._lab.ly; });
        for (var i = 0; i < ls.length; i++) for (var j = i + 1; j < ls.length; j++) {
          var A = ls[i]._lab, B = ls[j]._lab;
          var ax0 = A.right ? A.lx : A.lx - A.w, ax1 = ax0 + A.w, bx0 = B.right ? B.lx : B.lx - B.w, bx1 = bx0 + B.w;
          if (ax1 < bx0 - 4 || bx1 < ax0 - 4) continue;
          var need = (A.h + B.h) / 2 + 2, gap = B.ly - A.ly;
          if (gap < need) { var push = (need - gap) / 2; A.ly -= push; B.ly += push; moved = true; }
        }
        if (!moved) break;
      }
      /* Slide each name inside the free area before anything is tested against the rails.
         A title that ended up five pixels under the topbar used to be dropped outright,
         which spends a whole signal name to avoid a five-pixel overlap; moving it down is
         the same answer at none of the cost. Horizontally it changes side rather than
         sliding, because the leader line has to reach its own dot. */
      sigs.forEach(function (n) {
        var L = n._lab;
        if (!L) return;
        var top = L.size * 0.7 + 2, bot = L.h - L.size * 0.7 + 2;
        L.ly = Math.max(FR.y0 + top, Math.min(FR.y1 - bot, L.ly));
        /* Neither side may fit once a framed system fills the free area, and the old rule
           flipped a name off the panel straight under the inspector. The side with more
           room wins; a block still wider than that room slides inward, no further than its
           own hub's edge, so the selected signal's caption sits inside its ring rather than
           being cut by the dock. */
        var off2 = (n.sysR || 24) * cam.k + 12;
        var roomR = FR.x1 - (n._px + off2), roomL = (n._px - off2) - FR.x0;
        if (L.right && roomR < L.w && roomL > roomR) { L.right = false; L.lx = n._px - off2; }
        else if (!L.right && roomL < L.w && roomR > roomL) { L.right = true; L.lx = n._px + off2; }
        if (L.right && L.lx + L.w > FR.x1) L.lx = Math.max(n._px + n._R + 10, FR.x1 - L.w);
        else if (!L.right && L.lx - L.w < FR.x0) L.lx = Math.min(n._px - n._R - 10, FR.x0 + L.w);
      });
      sigs.forEach(function (n) {
        var L = n._lab;
        if (!L) return;
        var a = (anyFilter && !cxMatch(n)) ? 0.15 : (hi && hi !== n && (hi.sig || hi) !== n ? 0.45 : 1);
        if (isRad) a *= 0.35;
        var col = n.tier === "LONG TAIL" ? "#B9BCD3" : CXP.ink;
        var ex = n._px + (n._R + 3) * (L.right ? 1 : -1), kx = L.lx - (L.right ? 6 : -6);
        // the name, its caption and the money-corner line are one block, so nothing else
        // can land between them
        var sy = L.ly + L.size * 0.5 + capH - 2, cy2 = sy + cornerH;
        var by0 = L.ly - L.size * 0.7, by1 = L.full ? (n.corner ? cy2 : sy) + 5 : L.ly + L.size * 0.5 + 4, forced = n === hi || n === sel;
        var bx0 = L.right ? L.lx : L.lx - L.w;
        if (!forced && !boxFree(bx0 - 2, by0, bx0 + L.w + 2, by1)) {
          // a signal name outranks everything else on the canvas, so before it is dropped
          // it tries the mirrored side of its own dot: the nudge, then the drop
          var alt = !L.right, ax0 = alt ? n._px + (n._px - L.lx) : n._px - (L.lx - n._px) - L.w;
          ax0 = alt ? n._px + ((n._px - L.lx)) : (n._px - (L.lx - n._px)) - L.w;
          if (!boxFree(ax0 - 2, by0, ax0 + L.w + 2, by1)) return;
          L.right = alt; L.lx = alt ? ax0 : ax0 + L.w; bx0 = ax0;
        }
        claim(bx0 - 2, by0, bx0 + L.w + 2, by1);
        ctx.globalAlpha = a * 0.3; ctx.strokeStyle = CXP.ink2; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(ex, n._py);
        if (Math.abs(L.ly - n._py) > 1) { ctx.lineTo((ex + kx) / 2, n._py); ctx.lineTo(kx, L.ly); } else ctx.lineTo(kx, n._py);
        ctx.stroke();
        var al = L.right ? "left" : "right";
        label(n.label, L.lx, L.ly, "italic 400 " + L.size.toFixed(1) + "px Newsreader, Georgia, serif", col, al, a);
        if (!L.full) return;
        label(n.cap, L.lx, sy, capF, CXP.ink2, al, a);
        if (n.corner) {
          // ★ money-corner link name, then the tickers named at it, reading away from the dot
          var starR = 4.2 * sz, nameW = tw(capF, n.corner.name);
          var x0 = L.right ? L.lx : L.lx - cornerW(n);
          ctx.globalAlpha = a; star(x0 + starR + 1, cy2 - starR * 0.9, starR, CXP.gold);
          label(n.corner.name, x0 + 14 * sz, cy2, capF, CXP.ink2, "left", a);
          if (n.corner.tickers) label(n.corner.tickers, x0 + 14 * sz + nameW + 10, cy2, tkF, CXP.ink3, "left", a);
        }
      });
      // links: named when zoomed in, when filtered to, when hovered, and always in the focused radial
      var linkNames = zoomF > 6 || (lden > 0.9 && zoomF > 3.4);
      var hiSys2 = hi ? (hi.kind === "sig" ? hi : hi.sig || null) : null;
      /* Emitted in importance order rather than in whatever order the node array happens
         to hold. A candidate headline drawn before a link name used to take the space the
         link needed purely by arriving first, which is what put the italic headlines
         through the titles in the first place. */
      var KIND_ORDER = ["link", "dive", "scen", "co", "cand", "evt"];
      var byKind = { link: [], dive: [], scen: [], co: [], cand: [], evt: [] };
      g.nodes.forEach(function (n) {
        if (n._px == null || n._hid) return;
        if (byKind[n.kind]) byKind[n.kind].push(n);
      });
      KIND_ORDER.forEach(function (kind) {
        byKind[kind].forEach(function (n) {
        var focused = isRad && n.sig === sel;
        var forced = n === hi;
        if (n.kind === "link") {
          var near = zoomF > 1.3 && (n.sig === sel || n.sig === hiSys2);
          if (!(focused || linkNames || near || n === hi || (anyFilter && cxMatch(n) && CX_FILTER.q))) return;
          if (focused) return drawRadialLinkLabel(n);
          var c = Math.cos(n.ringA * Math.PI / 180), s = Math.sin(n.ringA * Math.PI / 180);
          put(n.label, "500 10.5px Inter, sans-serif", CXP.ink2,
              sides(n._px, n._py + s * 9, 9, c >= 0), n === hi ? 1 : 0.85, forced);
        } else if (n.kind === "dive") {
          var show = focused || (zoomF > 0.9 && (ui || 1) >= 0.8) || zoomF > 1.6 || n === hi;
          if (!show) return;
          var c2 = Math.cos((n.radialA != null && focused ? n.radialA : n.ringA) * Math.PI / 180);
          var dtF = "600 8.5px 'JetBrains Mono', monospace", dwF = "italic 400 10.5px Newsreader, Georgia, serif";
          var tkW = tw(dtF, n.ref.ticker), wdW = tw(dwF, n.word || ""), gap = 6;
          var st2 = seat(tkW + gap + wdW, 14, sides(n._px, n._py + (focused ? 11 : 0), 7, c2 >= 0), forced);
          if (st2) {
            label(n.ref.ticker, st2.x0, st2.y, dtF, CXP.ink, "left", 0.95);
            label(n.word, st2.x0 + tkW + gap, st2.y, dwF, n.color, "left", 0.95);
            ctx.globalAlpha = 0.95; ctx.strokeStyle = n.color; ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.moveTo(st2.x0, st2.y + 6); ctx.lineTo(st2.x0 + tkW, st2.y + 6); ctx.stroke();
          }
        } else if (n.kind === "co") {
          if (!(zoomF > 3 || n === hi)) return;
          put(n.label, "8.5px 'JetBrains Mono', monospace", CXP.ink3,
              sides(n._px, n._py - 5, 4, true), 0.8, forced);
        } else if (n.kind === "scen") {
          if (!(focused || zoomF > 2.4 || n === hi)) return;
          var c3 = focused && n.radialA != null ? Math.cos(n.radialA * Math.PI / 180 + cam.rot) : 1;
          put(n.word || n.label, "italic 400 10.5px Newsreader, Georgia, serif", CXP.ink2,
              sides(n._px, n._py, 9, c3 >= -0.05), 0.9, forced);
        } else if (n.kind === "cand") {
          if (!(n === hi || (anyFilter && CX_FILTER.q && cxMatch(n)) || zoomF > 2.6)) return;
          put(n.label, "italic 400 11.5px Newsreader, Georgia, serif", "#B7BAD3",
              sides(n._px, n._py, 7, true), 0.9, forced);
        } else if (n.kind === "evt" && !isTL) {
          if (!(n === hi || zoomF > 2.6)) return;
          put(n.label, "8.5px 'JetBrains Mono', monospace", CXP.ink3,
              sides(n._px, n._py, 8, true), 0.9, forced);
        }
        });
      });
    }
    function drawRadialLinkLabel(n) {
      var th = (n.radialA == null ? n.ringA : n.radialA) * Math.PI / 180 + cam.rot, c = Math.cos(th), s = Math.sin(th);
      var o = proj(0, 0), R = (n.sig.radialR || 200) * cam.k;
      var tx = o.x + (R + 30) * c, ty = o.y + (R + 30) * s, right = c >= -0.05, ax = right ? "left" : "right";
      var name = n.label, lines = [], rest = name;
      while (rest.length > 22 && lines.length < 2) {
        var cut = rest.lastIndexOf(" ", 23);
        if (cut <= 0) break;
        lines.push(rest.slice(0, cut)); rest = rest.slice(cut + 1);
      }
      lines.push(rest);
      var y0 = ty - (lines.length - 1) * 7;
      lines.forEach(function (ln, q) { label(ln, tx, y0 + q * 14, "500 12px Inter, sans-serif", CXP.ink, ax, 1); });
      // the focused ring's own read-out: it always draws, and it claims so nothing else lands on it
      var lw = 0;
      lines.forEach(function (ln) { lw = Math.max(lw, ctx.measureText(ln).width); });
      claim(right ? tx : tx - lw - 6, y0 - 9, right ? tx + lw + 6 : tx, y0 + (lines.length - 1) * 14 + 22);
      var l = n.ref, tags = [];
      if (n.choke) tags.push("CHOKE POINT");
      if (n.money) tags.push("MONEY CORNER");
      var sub = (n.verdict ? n.verdict.replace("_", " ") : "UNSCORED") + " · i" + num(cxScore(l, "impact")) + " c" + num(cxScore(l, "crowdedness")) + " v" + num(cxScore(l, "capture")) + (tags.length ? " · " + tags.join(" · ") : "");
      label(sub, tx, y0 + (lines.length - 1) * 14 + 13, "8.6px 'JetBrains Mono', monospace", n.color, ax, 1);
    }
    function statusLine(w, h) {
      var txt;
      var sel = CX_MODE.sig ? g.byKey["sig:" + CX_MODE.sig] : null;
      if (hover) txt = hover.kind.toUpperCase() + " · " + (hover.tip || hover.label || "");
      else if (CX_MODE.phase === "radial" && sel) txt = "FOCUSED · " + sel.label.toUpperCase() + " · " + ((sel.chain || {}).links || []).length + " LINKS RANKED BY HEAT · BARS: IMPACT · CROWDEDNESS · CAPTURE · ESC TO CLEAR";
      else if (CX_MODE.phase === "timeline") txt = "TIMELINE · DISTANCE FROM CENTRE = TIME FROM TODAY · PAST LEFT, FUTURE RIGHT · THE FEED IS THE LAST FORTNIGHT";
      else if (CX_FILTER.fam) {
        var sec = null;
        g.sectors.forEach(function (s) { if (s.fam === CX_FILTER.fam) sec = s; });
        txt = "FILTERED · " + CX_FILTER.fam + (sec ? " · " + sec.sigs + " SIGNALS · " + sec.heads + " HEADLINES" : "") + " · ESC TO CLEAR";
      } else txt = "ANGLE = FAMILY · DISTANCE FROM CENTRE = HOW UNMAPPED · SIZE = MONEY REACHABLE · RING = ITS CHAIN, COLOURED BY HEAT";
      if (readEl) readEl.textContent = txt;
    }
    function placeCard(html, cx2, cy2) {
      card.innerHTML = html;
      card.style.display = "block";
      var cw = card.offsetWidth || 300, chh = card.offsetHeight || 120;
      var lx = cx2 + 16, ly = cy2 - 12;
      if (lx + cw > innerWidth - 8) lx = cx2 - cw - 16;
      if (ly + chh > innerHeight - 8) ly = innerHeight - chh - 8;
      if (ly < 8) ly = 8;
      card.style.left = lx + "px"; card.style.top = ly + "px";
    }

    /* ---- selection, phases, chrome ---- */
    function refreshOpps() {
      var opps = document.getElementById("cxOpps");
      if (opps) { opps.innerHTML = cxOppRows(); bindFly(opps); }
      var ph = document.querySelector(".cx-phasectl");
      if (ph) { ph.outerHTML = cxPhaseCtl(); bindPhase(); }
    }
    function showInspector() {
      var host = document.getElementById("cxInspHost");
      if (!host) return;
      host.innerHTML = CX_MODE.sig ? cxInspectorHTML(CX_MODE.sig) : "";
      bindFly(host);
      host.querySelectorAll("[data-cxopen]").forEach(function (b) {
        b.addEventListener("click", function () { cxShowDrawer(cortexDrawer("sig", b.getAttribute("data-cxopen"))); });
      });
      var x = document.getElementById("cxInspClose");
      if (x) x.addEventListener("click", function () { clearSel(); });
    }
    function selectSig(id, opts) {
      opts = opts || {};
      var n = g.byKey["sig:" + id];
      if (!n) return;
      var same = CX_MODE.sig === id;
      CX_MODE.sig = id;
      if (CX_MODE.phase === "radial") {
        if (!n.chain) CX_MODE.phase = "network";
        phaseSeated = null; seatPhase();
        if (CX_MODE.phase === "radial") camHome(); else flyToSig(n);
      } else if (!opts.noFly) flyToSig(n);
      pinned = null; card.style.display = "none";
      showInspector(); refreshOpps();
      if (same && opts.toggle) { clearSel(); }
    }
    function clearSel() {
      CX_MODE.sig = null;
      if (CX_MODE.phase === "radial") CX_MODE.phase = "network";
      phaseSeated = null; seatPhase();
      showInspector(); refreshOpps(); camHome();
    }
    function setPhase(ph) {
      if (ph === "radial") {
        var sig = CX_MODE.sig ? g.byKey["sig:" + CX_MODE.sig] : null;
        if (!sig || !sig.chain) {
          // no chained selection: the radial opens on the top-ranked chained signal
          var top = null;
          g.sigs.forEach(function (n) { if (!top && n.chain) top = n; });
          if (!top) return;
          CX_MODE.sig = top.id;
        }
      }
      CX_MODE.phase = ph;
      phaseSeated = null; seatPhase();
      pinned = null; card.style.display = "none";
      showInspector(); refreshOpps(); camHome();
    }
    function bindFly(scope) {
      scope.querySelectorAll("[data-cxfly]").forEach(function (b) {
        b.addEventListener("click", function () {
          var key = b.getAttribute("data-cxfly");
          if (key.indexOf("sig:") === 0) selectSig(key.slice(4));
        });
      });
    }
    function bindPhase() {
      document.querySelectorAll("[data-cxphase]").forEach(function (b) {
        b.addEventListener("click", function () { setPhase(b.getAttribute("data-cxphase")); });
      });
    }
    bindPhase();
    var panel = document.getElementById("cxPanel");
    if (panel) bindFly(panel);
    showInspector();
    var mBtn = document.getElementById("cxMachineBtn");
    if (mBtn) mBtn.addEventListener("click", function () {
      var more = document.getElementById("cxMachineMore");
      if (!more) return;
      var open = more.hasAttribute("hidden");
      if (open) more.removeAttribute("hidden"); else more.setAttribute("hidden", "");
      mBtn.setAttribute("aria-expanded", open ? "true" : "false");
      mBtn.querySelector(".dim").textContent = open ? "registers · less ▴" : "registers · more ▾";
    });
    // view popover: what shows and how big (bound once; sliders keep their positions)
    var vBtn = document.getElementById("cxViewBtn"), vPop = document.getElementById("cxViewPop");
    if (vBtn && vPop) {
      vBtn.addEventListener("click", function () {
        var open = vPop.hasAttribute("hidden");
        if (open) vPop.removeAttribute("hidden"); else vPop.setAttribute("hidden", "");
        vBtn.setAttribute("aria-expanded", open ? "true" : "false"); vBtn.classList.toggle("on", open);
      });
      vPop.querySelectorAll("[data-cxk]").forEach(function (b) {
        b.addEventListener("click", function () {
          var k = b.getAttribute("data-cxk");
          CX_FILTER.kinds[k] = CX_FILTER.kinds[k] === false;
          b.classList.toggle("off", CX_FILTER.kinds[k] === false);
        });
      });
      vPop.querySelectorAll("[data-cxgain]").forEach(function (sl) {
        sl.addEventListener("input", function () {
          var k = sl.getAttribute("data-cxgain"), v = parseFloat(sl.value);
          if (isNaN(v)) return;
          CX_GAIN[k] = v;
          var el = document.getElementById("cxgv-" + k);
          if (el) el.textContent = "×" + v.toFixed(2);
          cxSaveGain();
        });
      });
      var gRst = document.getElementById("cxGainReset");
      if (gRst) gRst.addEventListener("click", function () {
        Object.keys(CX_GAIN_DEF).forEach(function (k) { CX_GAIN[k] = CX_GAIN_DEF[k]; });
        CX_FILTER.kinds = {}; CX_FILTER.fam = null; CX_FILTER.q = "";
        var si0 = document.getElementById("cxSearch"); if (si0) si0.value = "";
        vPop.querySelectorAll("[data-cxk]").forEach(function (x) { x.classList.remove("off"); });
        vPop.querySelectorAll("[data-cxgain]").forEach(function (sl) {
          var k = sl.getAttribute("data-cxgain"); sl.value = CX_GAIN[k];
          var el = document.getElementById("cxgv-" + k); if (el) el.textContent = "×" + CX_GAIN[k].toFixed(2);
        });
        cxSaveGain(); camHome();
      });
    }
    var si = document.getElementById("cxSearch");
    if (si) {
      si.addEventListener("input", function () { CX_FILTER.q = si.value.trim().toLowerCase(); });
      si.addEventListener("keydown", function (e) { if (e.key === "Escape") { si.value = ""; CX_FILTER.q = ""; si.blur(); } });
    }
    document.querySelectorAll("[data-crot]").forEach(function (b) {
      b.addEventListener("click", function () { cam.rot += parseInt(b.getAttribute("data-crot"), 10) * Math.PI / 12; });
    });

    /* ---- pointer ---- */
    function rimPick(px, py) {
      for (var i = 0; i < rimHits.length; i++) {
        var r = rimHits[i];
        if (Math.abs(r.x - px) < r.r && Math.abs(r.y - py) < 12) return r;
      }
      return null;
    }
    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (drag) {
        drag.moved += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0);
        var c = Math.cos(-cam.rot), s = Math.sin(-cam.rot);
        var dx = -(e.movementX || 0) / cam.k, dy = -(e.movementY || 0) / cam.k;
        cam.tx += dx * c - dy * s; cam.ty += dx * s + dy * c;
        return;
      }
      hover = pick(px, py);
      var rim = hover ? null : (CX_MODE.phase === "network" ? rimPick(px, py) : null);
      canvas.style.cursor = hover || rim ? "pointer" : "grab";
      if (hover) { pinned = null; placeCard(cxHoverCard(hover), e.clientX, e.clientY); }
      else if (!pinned) card.style.display = "none";
    });
    canvas.addEventListener("mousedown", function (e) {
      pinned = null; fly = null;
      drag = { t0: Date.now(), moved: 0, sx: e.clientX, sy: e.clientY };
      canvas.style.cursor = "grabbing";
    });
    function endDrag(e) {
      if (!drag) return;
      var quick = (Date.now() - drag.t0) < 500 && drag.moved < 6;
      drag = null; canvas.style.cursor = "grab";
      if (!quick || !e) return;
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      var n = pick(px, py);
      card.style.display = "none";
      if (n) {
        if (n.kind === "sig") {
          if (e.detail >= 2) { cxShowDrawer(cortexDrawer("sig", n.id)); return; }
          selectSig(n.id, { toggle: true });
        } else cxShowDrawer(cxNodeDrawer(n));
        return;
      }
      var rim = CX_MODE.phase === "network" ? rimPick(px, py) : null;
      if (rim) {
        CX_FILTER.fam = CX_FILTER.fam === rim.fam ? null : rim.fam;
        if (CX_FILTER.fam) {
          var pts = [];
          g.nodes.forEach(function (m) { if ((m.kind === "sig" || m.kind === "cand") && cxMatch(m) && m._px != null) pts.push({ x: m.x, y: m.y }); });
          if (pts.length) flyToFit(pts, 140); else camHome();
        } else camHome();
      }
    }
    canvas.addEventListener("mouseup", endDrag);
    canvas.addEventListener("mouseleave", function () { drag = null; hover = null; if (!pinned) card.style.display = "none"; canvas.style.cursor = "grab"; });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault(); pinned = null; fly = null;
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      var dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1);
      var before = unproj(px, py);
      var k0 = CX_CACHE.k0 || cam.k;
      cam.k = Math.max(k0 * 0.5, Math.min(k0 * 14, cam.k * Math.pow(1.0015, -dy)));
      var after = unproj(px, py);
      cam.tx += before.x - after.x; cam.ty += before.y - after.y;   // zoom toward the cursor
    }, { passive: false });
    // touch: one finger pans, a tap opens, two fingers pinch
    var touch = null;
    canvas.addEventListener("touchstart", function (e) {
      if (e.touches.length === 1) { var t0 = e.touches[0]; touch = { x: t0.clientX, y: t0.clientY, t: Date.now(), moved: 0 }; }
      else if (e.touches.length === 2) { var a = e.touches[0], b = e.touches[1]; touch = { pinch: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), k: cam.k }; }
    }, { passive: true });
    canvas.addEventListener("touchmove", function (e) {
      if (!touch) return;
      e.preventDefault();
      if (e.touches.length === 2 && touch.pinch) {
        var a = e.touches[0], b = e.touches[1], d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        var k0 = CX_CACHE.k0 || cam.k;
        cam.k = Math.max(k0 * 0.5, Math.min(k0 * 14, touch.k * d / touch.pinch));
        return;
      }
      var t0 = e.touches[0], mx = t0.clientX - touch.x, my = t0.clientY - touch.y;
      touch.moved += Math.abs(mx) + Math.abs(my);
      var c = Math.cos(-cam.rot), s = Math.sin(-cam.rot), dx = -mx / cam.k, dy = -my / cam.k;
      cam.tx += dx * c - dy * s; cam.ty += dx * s + dy * c;
      touch.x = t0.clientX; touch.y = t0.clientY;
    }, { passive: false });
    canvas.addEventListener("touchend", function (e) {
      if (!touch || touch.pinch || touch.moved > 8 || Date.now() - touch.t > 500) { touch = null; return; }
      var r = canvas.getBoundingClientRect(), n = pick(touch.x - r.left, touch.y - r.top);
      touch = null;
      if (!n) return;
      if (n.kind === "sig") selectSig(n.id, { toggle: true }); else cxShowDrawer(cxNodeDrawer(n));
    });
    canvas.addEventListener("keydown", function (e) {
      var k0 = CX_CACHE.k0 || cam.k;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        var ranked = cxRanked().map(function (s) { return s.id; });
        var idx = CX_MODE.sig ? ranked.indexOf(CX_MODE.sig) : -1;
        idx = (idx + (e.key === "ArrowRight" ? 1 : -1) + ranked.length) % ranked.length;
        selectSig(ranked[idx]);
      } else if (e.key === "Enter" || e.key === " ") {
        if (CX_MODE.sig) { e.preventDefault(); cxShowDrawer(cortexDrawer("sig", CX_MODE.sig)); }
      } else if (e.key === "h" || e.key === "H") { e.preventDefault(); cxToggleUI(); }
      else if (e.key === "q" || e.key === "Q") { e.preventDefault(); cam.rot -= Math.PI / 12; }
      else if (e.key === "e" || e.key === "E") { e.preventDefault(); cam.rot += Math.PI / 12; }
      else if (e.key === "+" || e.key === "=") { e.preventDefault(); cam.k = Math.min(k0 * 14, cam.k * 1.25); }
      else if (e.key === "-") { e.preventDefault(); cam.k = Math.max(k0 * 0.5, cam.k * 0.8); }
      else if (e.key === "0" || e.key === "r" || e.key === "R") { e.preventDefault(); cam.rot = 0; camHome(); }
      else if (e.key === "Escape") {
        pinned = null; card.style.display = "none";
        // one level at a time: the radial, then the selection, then the family filter, then home
        if (CX_MODE.phase === "radial") setPhase("network");
        else if (CX_MODE.sig) clearSel();
        else if (CX_FILTER.fam) { CX_FILTER.fam = null; camHome(); }
        else if (CX_MODE.phase === "timeline") setPhase("network");
        else camHome();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      var t = e.target || {};
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      if (!document.getElementById("cxFull")) return;
      var s2 = document.getElementById("cxSearch");
      if (s2) { e.preventDefault(); s2.focus(); }
    });
    raf = requestAnimationFrame(draw);
  }

  function cortexDrawer(kind, id) {
    if (kind === "sig") {
      var sg = byId(D.signals, id);
      if (!sg) return "";
      var occ = sg.occurrence || {};
      return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="' + esc(sg.title) + '"><button class="x" data-closedrawer>✕</button>' +
        '<div class="row">' + chip(occ.kind || "undated") + (occ.anchor_date ? chip(occ.anchor_date, "neutral") : "") + chip(sg.lane) + chip(sg.suggested_clock) + chip(sg.status, sg.status === "NEW" ? "accent" : "neutral") + "</div>" +
        "<h2>" + esc(sg.title) + "</h2>" +
        (occ.label ? "<p class='small'><b>" + esc(occ.label) + "</b>" + (occ.window ? " — " + esc(occ.window) : "") + "</p>" : "") +
        "<p class='small'>" + esc(sg.thesis) + "</p>" +
        '<div class="row" style="margin-top:14px"><a class="chip accent" href="#/signal/' + esc(sg.id) + '">Open signal →</a>' +
        (sg.chain_id ? ' <a class="chip accent" href="#/chain/' + esc(sg.chain_id) + '">Open chain →</a>' : "") + "</div>" +
        (sg.status === "NEW" ? "<div style='margin-top:14px'>" + runButton("run chain " + sg.id, "maps this signal into a value chain") + "</div>" : "") +
        "</div>";
    }
    var obj = null, isEvt = kind === "evt";
    if (isEvt) obj = byId(((D.calendar || {}).events || []), id);
    else obj = byId(((D.candidates || {}).candidates || []), id);
    if (!obj) return "";
    /* An appraisal of a candidate had no reader before 2026-08-30: impactFor was only ever
       called with a signal id, so 30 of 36 appraisals were inlined and rendered nowhere.
       The chip fields are what the page keeps for them, so the page shows them here. */
    return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="' + esc(obj.title) + '"><button class="x" data-closedrawer>✕</button>' +
      '<div class="row">' + chip(isEvt ? obj.kind : obj.family) + chip(obj.date, "neutral") + chip(obj.status) +
      (isEvt ? "" : impactChip(impactFor(obj.id))) + "</div>" +
      "<h2>" + esc(obj.title) + "</h2>" +
      "<p class='small'>" + esc(isEvt ? obj.why_it_matters : obj.why) + "</p>" +
      "<div class='muted small'>[" + esc(obj.source_name) + (obj.source_date ? ", " + esc(obj.source_date) : "") + "]" + (obj.window ? " · " + esc(obj.window) : "") + "</div>" +
      "<div style='margin-top:16px'>" + runButton("run radar", "the radar run judges promotion to a full signal card (method §0.1)") + "</div>" +
      "</div>";
  }

  /* ---------------- shared blocks ---------------- */
  /* Notes and changelogs are append-only and, since 2026-09-04, carried whole. */
  function objPath(obj) {
    if (obj.ticker && obj.chain_id) return "data/stocks/" + obj.ticker + "__" + obj.chain_id + ".json";
    if (obj.links) return chainPath(obj);
    if (obj.id) return "the object's file in data/";
    return "data/";
  }
  function notesBlock(obj) {
    var n = obj.notes || [];
    /* Three states, not two. A chain carried at reduced fidelity has its notes dropped
       and `notes_total` kept: printing the "None — add one" empty state over that would
       tell the reader nobody has ever annotated the chain, which is the opposite of what
       the data says. */
    if (!n.length && obj.notes_total) {
      return seclabel("Notes") + "<div class='muted'>" + obj.notes_total +
        " note(s) on this object, none carried on this page — they are in <span class='mono'>" +
        esc(objPath(obj)) + "</span>.</div>";
    }
    return seclabel("Notes") + (n.length ? n.map(function (x) {
      return '<div class="note"><span class="who">' + esc(x.by) + " · " + esc((x.ts || "").slice(0, 10)) + "</span><br>" + esc(x.text) + "</div>";
    }).join("")
      : '<div class="muted">None — add one from any session: <span class="mono">note ' + esc(obj.id || obj.ticker || "") + ' "…"</span></div>');
  }
  function changelogBlock(obj) {
    var c = (obj.changelog || []).slice().reverse();
    if (!c.length) return seclabel("History") + "<div class='muted'>No history entries recorded on this object.</div>";
    return seclabel("History") + "<div class='timeline'>" + c.map(function (x) {
      return '<div class="t"><span class="when">' + esc((x.ts || "").slice(0, 10)) + " · " + esc(x.by) + "</span><br>" + esc(x.change) + (x.prior ? " <span class='muted'>(was: " + esc(x.prior) + ")</span>" : "") + "</div>";
    }).join("") + "</div>";
  }
  /* ---------------- themes: the occurrence log ----------------
     Everything this machine has SEEN, including the small things, clustered. The store
     behind it is append-only for a reason worth repeating here: data/feeds/latest.json
     prunes at 500 items over 14 days and its ids are content hashes, so anything radar did
     not promote was gone within a fortnight and nothing could be looked back at. */
  var TH = D.themes || null;
  var THM = (METHOD.themes || { surge_min_count: 8, surge_multiple: 2, baseline_weeks: 4 });
  function thByIdList() { return (TH && TH.themes) || []; }
  function thById(id) {
    var list = thByIdList();
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }
  function thCal() { return (TH && TH.calibration) || {}; }
  function thRows(themeId) {
    var rows = (TH && TH.rows) || [];
    if (themeId === "__unassigned") return rows.filter(function (r) { return !r.th; });
    if (!themeId) return rows;
    return rows.filter(function (r) { return r.th === themeId; });
  }
  var ORIGIN_LABEL = { feed: "feed", candidate: "candidate", signal: "signal card", calendar: "calendar" };
  function originChip(o) {
    return '<span class="chip origin o-' + esc(o || "none") + '">' + esc(ORIGIN_LABEL[o] || o || "?") + "</span>";
  }
  /* Cell shading is relative to the busiest cell in the grid, not to an absolute scale:
     the question the grid answers is "which week piled up under which theme", which is a
     comparison inside this table and nothing else. */
  function thCell(n, peak) {
    if (!n) return '<td class="thc zero">·</td>';
    var a = peak > 0 ? Math.min(1, 0.12 + 0.88 * (n / peak)) : 0.12;
    return '<td class="thc" style="background:color-mix(in srgb, var(--accent) ' +
      Math.round(a * 100) + '%, transparent)">' + esc(n) + "</td>";
  }
  function thGrid() {
    var cal = thCal(), weeks = cal.weeks || [], per = cal.per_theme || {};
    if (!weeks.length) return "<div class='muted small'>No weekly occurrence counts calibrated yet (run themes).</div>";
    var peak = 0;
    Object.keys(per).forEach(function (id) {
      weeks.forEach(function (w) { peak = Math.max(peak, (per[id].by_week || {})[w] || 0); });
    });
    var unas = cal.unassigned_by_week || {};
    weeks.forEach(function (w) { peak = Math.max(peak, unas[w] || 0); });
    var body = thByIdList().map(function (t) {
      var v = per[t.id] || { by_week: {}, total: 0 };
      return '<tr><th class="thname"><a href="#/themes/' + esc(t.id) + '">' + esc(t.label) + "</a>" +
        (v.surging ? '<span class="chip surge">surging</span>' : "") +
        (v.surging && !v.claimed ? '<span class="chip unclaimed">unclaimed</span>' : "") +
        "</th>" +
        weeks.map(function (w) { return thCell((v.by_week || {})[w] || 0, peak); }).join("") +
        '<td class="thtot">' + esc(v.total) + "</td></tr>";
    }).join("");
    var unrow = '<tr class="unassigned"><th class="thname"><a href="#/themes/__unassigned">Unassigned</a>' +
      '<span class="muted"> no theme claims these</span></th>' +
      weeks.map(function (w) { return thCell(unas[w] || 0, peak); }).join("") +
      '<td class="thtot">' + esc((cal.denominators || {}).unassigned) + "</td></tr>";
    return '<div class="mdtable"><table class="thgrid"><tr><th></th>' +
      weeks.map(function (w) { return "<th>" + esc(String(w).replace(/^\d{4}-/, "")) + "</th>"; }).join("") +
      "<th>all</th></tr>" + body + unrow + "</table></div>";
  }
  function thSurges() {
    var cal = thCal(), surges = cal.surges || [];
    var unclaimed = surges.filter(function (s) { return !s.claimed; });
    if (!surges.length) {
      return "<div class='card'><div class='small'>No theme cleared the surge bar in " +
        esc(cal.current_week || "this week") + ". A surge is at least " + esc(THM.surge_min_count) +
        " occurrences in one ISO week AND at least " + esc(THM.surge_multiple) +
        "x the mean of the previous " + esc(THM.baseline_weeks) + " weeks.</div></div>";
    }
    return "<div class='card'>" +
      "<div class='small'>A surge is at least " + esc(THM.surge_min_count) +
      " occurrences in one ISO week AND at least " + esc(THM.surge_multiple) +
      "x the mean of the previous " + esc(THM.baseline_weeks) +
      " weeks. Unclaimed means no signal card stands behind it yet.</div>" +
      surges.map(function (s) {
        return '<div class="evli"><a href="#/themes/' + esc(s.theme_id) + '"><b>' + esc(s.label) + "</b></a> " +
          esc(s.count) + " in " + esc(s.week) + " against a baseline of " + esc(s.baseline) +
          (s.claimed
            ? ' <span class="chip claimed">claimed by ' + esc((s.signal_refs || []).join(", ")) + "</span>"
            : ' <span class="chip unclaimed">unclaimed</span>') +
          "</div>";
      }).join("") +
      (unclaimed.length
        ? "<div class='small' style='margin-top:10px'>" + esc(unclaimed.length) +
          " unclaimed: volume is arriving and no signal card has been written for it. " +
          "That is the question <span class='mono'>run radar</span> answers.</div>"
        : "") +
      "</div>";
  }
  function thOccRow(r) {
    var t = r.u
      ? '<a href="' + esc(r.u) + '" rel="noreferrer noopener" target="_blank">' + esc(r.t) + "</a>"
      : esc(r.t);
    return '<div class="occrow"><div class="occmeta">' + originChip(r.o) +
      '<span class="mono">' + esc(r.d || "undated") + "</span>" +
      (r.s ? "<span>" + esc(r.s) + "</span>" : "") +
      (r.f ? chip(r.f) : "") + "</div>" +
      '<div class="occtitle">' + t + "</div>" +
      (r.b ? '<div class="muted occbasis">' + esc(r.b) + "</div>" : "") + "</div>";
  }
  function thHead() {
    var cal = thCal(), d = cal.denominators || {};
    return "<div class='pagehead'><h1>Occurrence log</h1>" +
      "<div class='small'>Everything this machine has seen, including the small things, " +
      "clustered into themes. Every row is snapshotted when it is first seen: the feed store " +
      "prunes at 500 items over 14 days and its ids are content hashes, so without this log " +
      "anything the radar did not promote is gone within a fortnight.</div>" +
      "<div class='small' style='margin-top:8px'><span class='mono'>" +
      esc(num(d.occurrences_logged)) + " logged · " + esc(num(d.assigned)) + " assigned · " +
      esc(num(d.unassigned)) + " unassigned · " + esc(num(d.themes)) + " themes</span> " +
      "over " + esc(num(d.feed_items_on_disk)) + " feed items, " + esc(num(d.candidates_on_disk)) +
      " candidates, " + esc(num(d.signals_on_disk)) + " signal cards and " +
      esc(num(d.calendar_on_disk)) + " calendar entries currently on disk." +
      "</div></div>";
  }
  function themesView(themeId) {
    if (!TH) {
      return topbar("themes") + "<main>" +
        "<div class='pagehead'><h1>Occurrence log</h1></div>" +
        "<div class='emptystate'>No occurrence log yet. <span class='mono'>run themes</span> " +
        "ingests every occurrence on disk and clusters it." +
        "<div class='runwrap'>" + runButton("run themes") + "</div></div>" +
        footer() + "</main>";
    }
    if (themeId) return themeView(themeId);
    return topbar("themes") + "<main>" + thHead() +
      seclabel("Run the clustering") +
      "<div class='card runstrip'>" +
      runButton("run themes", "ingests every occurrence on disk, applies each theme's match rule, recomputes the weeks") +
      "</div>" +
      seclabel("Surges in " + ((thCal().current_week) || "this week")) + thSurges() +
      seclabel("Occurrences by theme and ISO week") + thGrid() +
      "<div class='small'>" + esc((thCal().note) || "") + "</div>" +
      footer() + "</main>";
  }
  function themeView(themeId) {
    var unassigned = themeId === "__unassigned";
    var t = unassigned ? null : thById(themeId);
    if (!t && !unassigned) return notFound("theme " + themeId);
    var v = unassigned ? null : (thCal().per_theme || {})[themeId];
    var rows = thRows(themeId);
    var m = (t && t.match) || {};
    return topbar("themes") + "<main>" +
      "<div class='crumbs'><a href='#/themes'>Occurrence log</a><span class='sep'>/</span>" +
      "<span class='here'>" + esc(unassigned ? "Unassigned" : t.label) + "</span></div>" +
      "<div class='pagehead'><h1>" + esc(unassigned ? "Unassigned" : t.label) + "</h1>" +
      "<div class='small'>" + esc(unassigned
        ? "Occurrences no theme's rule claims. A large number here is not a defect: most of what a wire feed carries has no investable chain behind it, and filing it under a theme anyway would be the invention this log exists to avoid."
        : t.definition) + "</div></div>" +
      (v
        ? "<div class='statgrid'><div class='card'><h3>This week</h3><div class='scrow'>" +
          mapStat(num(v.current_week), "in " + esc(thCal().current_week || "?")) +
          mapStat(num(v.baseline), "weekly baseline") +
          mapStat(num(v.total), "logged all-time") +
          "</div><div class='small'>" +
          (v.surging ? "Surging" : "Not surging") + " · " +
          (v.claimed ? "claimed by " + esc((v.signal_refs || []).join(", ")) : "no signal card stands behind it") +
          " · baseline computed over " + esc(num(v.baseline_weeks)) + " prior week(s)</div></div>" +
          "<div class='card'><h3>Where it came from</h3><div class='scrow'>" +
          Object.keys(v.origins || {}).map(function (o) {
            return mapStat(v.origins[o], ORIGIN_LABEL[o] || o);
          }).join("") + "</div></div></div>"
        : "") +
      (t
        ? "<div class='card'><h3>Match rule</h3><div class='small'>An occurrence joins this " +
          "theme when its own snapshotted title or source carries one of these terms as a " +
          "whole word. The rule is written here so an assignment can be re-run and " +
          "disagreed with, rather than being an impression nobody can check.</div>" +
          "<div class='cmdrow' style='margin-top:8px'>" +
          ((m.any || []).map(function (x) { return "<code>" + esc(x) + "</code>"; }).join(" ") || "<span class='muted'>none</span>") +
          "</div>" +
          ((m.not || []).length
            ? "<div class='small' style='margin-top:6px'>excluded when it also carries: " +
              (m.not || []).map(function (x) { return "<code>" + esc(x) + "</code>"; }).join(" ") + "</div>"
            : "") +
          (t.limitation ? "<div class='small' style='margin-top:8px'>" + esc(t.limitation) + "</div>" : "") +
          "</div>"
        : "") +
      seclabel(rows.length + " occurrence" + (rows.length === 1 ? "" : "s") +
        (TH.total > (TH.rows || []).length ? " on this page" : "")) +
      (rows.length
        ? "<div class='occlist'>" + rows.map(thOccRow).join("") + "</div>"
        : "<div class='emptystate'>Nothing logged under this theme yet.</div>") +
      footer() + "</main>";
  }

  /* ---------------- agents ----------------
     Eight agents run this machine and until now the page named two of them, in section
     labels, with no way to read what either was told. These two views are the whole
     answer to that: an index, and one page per contract that also EDITS it. */
  function agentLedgerLine(name) {
    var needle = String(name || "").toLowerCase();
    var hits = (D.ledger || []).filter(function (l) { return l.toLowerCase().indexOf(needle) > -1; });
    return hits.length ? hits[hits.length - 1] : null;
  }
  function agentCommandList(a, opts) {
    opts = opts || {};
    if (!a.commands.length) {
      return "<div class='small'>No row in the command table names " + esc(a.name) +
        ". Nothing routes work here, which is the state <span class='mono'>check_machine</span> calls silent.</div>";
    }
    return a.commands.map(function (c) {
      if (c.runnable) return "<div class='cmdrow'>" + runButton(c.cmd, null, { compact: opts.compact }) +
        " <code>" + esc(c.cmd) + "</code></div>";
      /* Two different reasons a command has no button, and saying the wrong one is worse
         than saying nothing: `run chain <signal-id>` needs an argument only the object it
         acts on can supply, while `check health` takes none and is simply not one of the
         shapes tools/queue_allowlist.py lets a web page queue. */
      var why = /[<\[]/.test(c.cmd)
        ? "takes an argument; run it from the object it acts on"
        : "not a queueable shape; run it in a Claude session on this repo";
      return "<div class='cmdrow'><code>" + esc(c.cmd.replace(/\\\|/g, "|")) + "</code>" +
        "<span class='muted'>" + esc(why) + "</span></div>";
    }).join("");
  }
  function agentsView() {
    var list = AX.agents || [];
    return topbar("agents") + "<main>" +
      "<div class='pagehead'><h1>Agents</h1><div class='small'>Who runs what, and what each one was told. " +
      "Every contract here is the file on disk in <span class='mono'>.claude/agents/</span>, shipped into this page by the build, " +
      "so a page whose instructions have drifted from the repo fails CI rather than misleading you.</div></div>" +
      (list.length ? "<div class='agentgrid'>" + list.map(function (a) {
        var led = agentLedgerLine(a.name);
        return "<div class='card agentcard' data-nav='#/agent/" + esc(a.slug) + "' tabindex='0' role='link'>" +
          "<div class='row' style='justify-content:space-between;align-items:flex-start'>" +
          "<h3>" + esc(a.name) + "</h3><span class='muted'>" + esc(Math.round(a.bytes / 1024)) + " KB</span></div>" +
          "<div class='small'>" + esc(a.role) + "</div>" +
          "<div class='agentcmds'><span data-stop>" + agentCommandList(a, { compact: true }) + "</span></div>" +
          "<div class='muted' style='margin-top:10px'>" +
          (led ? "last seen in the ledger " + esc(led.slice(0, 10)) : "no ledger line names " + esc(a.name)) +
          "</div></div>";
      }).join("") + "</div>"
        : "<div class='emptystate'>No agent contracts reached this build.</div>") +
      ((AX.unowned || []).length
        ? seclabel("Commands with no agent") +
          "<div class='card'><div class='small'>These rows in the command table name no owner, so no contract stands behind their buttons. " +
          "It is the same backlog <span class='mono'>tools/check_machine.py</span> reports.</div>" +
          (AX.unowned || []).map(function (c) {
            return "<div class='cmdrow'><code>" + esc(String(c).replace(/\\\|/g, "|")) + "</code></div>";
          }).join("") + "</div>"
        : "") +
      footer() + "</main>";
  }
  var EDITING = null;
  function agentView(slug) {
    var a = agentBySlug(slug);
    if (!a) return notFound("agent " + slug);
    var led = agentLedgerLine(a.name);
    var pend = pendingEditFor(a.slug);
    var runnable = a.commands.filter(function (c) { return c.runnable; });
    var editing = EDITING === a.slug;
    var body = editing
      ? "<div class='editwrap'>" +
        "<div class='small'>Editing the whole file, frontmatter included. Saving publishes the new text to this page; " +
        "the next Claude session on this repo writes it to <span class='mono'>.claude/agents/" + esc(a.slug) + ".md</span> verbatim, " +
        "commits it, and rebuilds. The commit is the undo.</div>" +
        "<textarea id='agentEdit' class='contractedit' spellcheck='false' aria-label='agent instructions'>" +
        esc(a.body) + "</textarea>" +
        "<div class='row' style='gap:8px;margin-top:10px'>" +
        "<button class='btn-run' id='agentSave' data-slug='" + esc(a.slug) + "'>Save to repo</button>" +
        "<button class='btn-ghost' id='agentCancel'>Cancel</button></div></div>"
      : "<div class='card contract'>" + md(contractBody(a.body)) + "</div>";
    return topbar("agents") + "<main>" +
      "<div class='crumbs'><a href='#/agents'>Agents</a><span class='sep'>/</span><span class='here'>" + esc(a.name) + "</span></div>" +
      "<div class='pagehead'><h1>" + esc(a.name) + "</h1>" +
      "<div class='small'>" + esc(a.role) + " · <span class='mono'>.claude/agents/" + esc(a.slug) + ".md</span> · " +
      esc(a.bytes) + " bytes</div></div>" +
      (pend
        ? "<div class='sysline'><span class='healthdot' style='background:var(--warn)'></span>An edit saved " +
          esc(String(pend.ts).slice(0, 16).replace("T", " ")) + "Z is waiting for a Claude session to write it to the repo. " +
          "What you see below is still the version on disk.</div>"
        : "") +
      seclabel("What " + a.name + " owns") +
      "<div class='card'>" + agentCommandList(a, {}) + "</div>" +
      seclabel("Instructions") +
      "<div class='row' style='gap:8px;margin-bottom:10px'>" +
      (editing ? "" : "<button class='btn-run' id='agentEditBtn' data-slug='" + esc(a.slug) + "'>Edit instructions</button>") +
      "<span data-stop>" + runButton("run devil .claude/agents/" + a.slug + ".md",
        "a fresh-context adversary reads this file and the diff behind it", { compact: true }) + "</span>" +
      "</div>" +
      body +
      (led ? "<div class='sysline'><span class='mono'>" + esc(led) + "</span></div>" : "") +
      footer() + "</main>";
  }

  /* The onboarding guide: static markup app/build.py inlines from app/templates/guide.html
     into <template id="upstream-guide">, so the page carries its own instructions for
     getting in (Claude and ChatGPT, browser and local) and the same file is also served
     standalone as app/guide.html. A template element is inert until read, so the guide
     costs nothing on the other views. */
  function guideView() {
    var t = document.getElementById("upstream-guide");
    var body = t ? t.innerHTML : "<div class='emptystate'>The guide did not reach this build: app/templates/guide.html is missing.</div>";
    return topbar("guide") + "<main>" + body + footer() + "</main>";
  }

  function notFound(what) {
    return topbar() + "<main><div class='emptystate' style='margin-top:40px'>Not found: " + esc(what) + '<br><br><a href="#/radar">back to radar</a></div>' + footer() + "</main>";
  }

  /* ---------------- router & events ---------------- */
  function route() {
    var h = location.hash || "#/";
    var p = h.replace(/^#\//, "").split("/").map(decodeURIComponent);
    var html;
    if (!p[0]) html = boardView();
    else if (p[0] === "board") html = boardView();
    else if (p[0] === "radar") html = homeView();
    else if (p[0] === "signal") html = signalView(p[1]);
    else if (p[0] === "chains") html = chainsView();
    else if (p[0] === "chain") html = chainView(p[1], p[2]);
    else if (p[0] === "screen") html = screenView(p[1], p[2]);
    else if (p[0] === "stock") html = stockView(p[1], p[2]);
    else if (p[0] === "themes") html = themesView(p[1]);
    else if (p[0] === "agents") html = agentsView();
    else if (p[0] === "agent") html = agentView(p[1]);
    else if (p[0] === "cortex") html = cortexView();
    else if (p[0] === "campaign") html = campaignView(p[1]);
    else if (p[0] === "book") html = bookView();
    else if (p[0] === "shadow") html = shadowView();
    else if (p[0] === "guide") html = guideView();
    else if (!p[0]) html = cortexView();
    else html = notFound("route #/" + p.join("/"));
    app.innerHTML = html;
    // the cortex is the only full-viewport view: the page stops scrolling and the
    // topbar joins the overlays floating on the field
    var onCortex = !!document.getElementById("cxFull");
    document.body.classList.toggle("cortex-page", onCortex);
    document.body.classList.toggle("cx-ui-hidden", onCortex && cxUIHidden());
    if (onCortex) cxMeasureTop();
    window.scrollTo(0, 0);
    wire();
  }
  function bindCopy(scope) {
    scope.querySelectorAll("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        var txt = b.getAttribute("data-copy");
        function done() { b.textContent = "copied"; setTimeout(function () { b.textContent = "copy"; }, 1400); }
        try { navigator.clipboard.writeText(txt).then(done, function () { fallbackCopy(txt); done(); }); }
        catch (err) { fallbackCopy(txt); done(); }
      });
    });
  }
  function wire() {
    bindCopy(app);
    app.querySelectorAll("[data-run]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        enqueue(b.getAttribute("data-run"), b);
      });
    });
    app.querySelectorAll("[data-copyrun]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        var cmd = b.getAttribute("data-copyrun");
        try { navigator.clipboard.writeText(cmd); } catch (err) { fallbackCopy(cmd); }
        b.textContent = "Copied ✓ — paste into a Claude session";
        setTimeout(function () { b.textContent = runLabel(cmd) + " — copy"; }, 2600);
      });
    });
    app.querySelectorAll("[data-stop]").forEach(function (n) {
      n.addEventListener("click", function (e) { e.stopPropagation(); });
    });
    app.querySelectorAll("[data-nav]").forEach(function (n) {
      n.addEventListener("click", function () { location.hash = n.getAttribute("data-nav"); });
      n.addEventListener("keydown", function (e) { if (e.key === "Enter") location.hash = n.getAttribute("data-nav"); });
    });
    app.querySelectorAll("[data-tab]").forEach(function (b) {
      b.addEventListener("click", function () { location.hash = "#/chain/" + b.getAttribute("data-chain") + "/" + b.getAttribute("data-tab"); });
    });
    app.querySelectorAll("[data-drawer]").forEach(function (n) {
      function open() {
        var chainId = (location.hash.split("/")[2] || "");
        var c = byId(D.chains, chainId);
        var l = c && byId(c.links, n.getAttribute("data-drawer"));
        if (!l) return;
        var host = document.getElementById("drawerHost");
        host.innerHTML = linkModal(c, l);
        host.querySelectorAll("[data-closedrawer]").forEach(function (x) {
          x.addEventListener("click", function () { host.innerHTML = ""; });
        });
        bindCopy(host);
        host.querySelectorAll("[data-run]").forEach(function (b) {
          b.addEventListener("click", function (e) { e.preventDefault(); enqueue(b.getAttribute("data-run"), b); });
        });
      }
      n.addEventListener("click", open);
      n.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    });
    app.querySelectorAll(".gnode").forEach(function (n) {
      var id = n.getAttribute("data-drawer"), svg = n.closest("svg");
      function lit(on) {
        if (!svg) return;
        svg.querySelectorAll(".gedge").forEach(function (e) {
          var mine = e.getAttribute("data-from") === id || e.getAttribute("data-to") === id;
          e.classList.toggle("hi", on && mine);
          e.classList.toggle("dim", on && !mine);
        });
      }
      n.addEventListener("mouseenter", function () { lit(true); });
      n.addEventListener("mouseleave", function () { lit(false); });
      n.addEventListener("focusin", function () { lit(true); });
      n.addEventListener("focusout", function () { lit(false); });
    });
    var aEdit = document.getElementById("agentEditBtn");
    if (aEdit) aEdit.addEventListener("click", function () { EDITING = aEdit.getAttribute("data-slug"); route(); });
    var aCancel = document.getElementById("agentCancel");
    if (aCancel) aCancel.addEventListener("click", function () { EDITING = null; route(); });
    var aSave = document.getElementById("agentSave");
    if (aSave) aSave.addEventListener("click", function () {
      var ta = document.getElementById("agentEdit");
      var slug = aSave.getAttribute("data-slug");
      var a = agentBySlug(slug);
      if (!ta || !a) return;
      if (ta.value === a.body) { toast("Nothing changed — the text is identical to the file on disk."); return; }
      publishEdit(slug, ta.value, a.body, aSave);
    });
    var cxUB = document.getElementById("cxUIBtn");
    if (cxUB) cxUB.addEventListener("click", cxToggleUI);
    var cxIB = document.getElementById("cxInfoBtn");
    if (cxIB) {
      cxIB.addEventListener("click", function () {
        var panel = document.getElementById("cxInfo");
        if (!panel) return;
        var open = panel.hasAttribute("hidden");
        if (open) panel.removeAttribute("hidden"); else panel.setAttribute("hidden", "");
        cxIB.setAttribute("aria-expanded", open ? "true" : "false");
        cxIB.classList.toggle("on", open);
        try { localStorage.setItem("upstream.cxInfo", open ? "1" : "0"); } catch (e) {}
      });
      try {
        if (localStorage.getItem("upstream.cxInfo") === "1") {
          document.getElementById("cxInfo").removeAttribute("hidden");
          cxIB.setAttribute("aria-expanded", "true"); cxIB.classList.add("on");
        }
      } catch (e) {}
    }
    if (document.getElementById("cortexCanvas")) initCortex();
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { var host = document.getElementById("drawerHost"); if (host) host.innerHTML = ""; }
    });
    var tb = document.getElementById("themeBtn");
    if (tb) tb.addEventListener("click", cycleTheme);
    var hov = document.getElementById("pxhover");
    if (hov && window.__px) {
      var tip = document.createElement("div"); tip.className = "tooltip"; tip.style.display = "none"; document.body.appendChild(tip);
      var svg = document.getElementById("pxchart");
      hov.addEventListener("mousemove", function (e) {
        var r = svg.getBoundingClientRect();
        var sx = ((e.clientX - r.left) / r.width) * window.__px.W;
        var px = window.__px;
        var i = Math.round(((sx - px.P.l) / (px.W - px.P.l - px.P.r)) * (px.pts.length - 1));
        i = Math.max(0, Math.min(px.pts.length - 1, i));
        var pt = px.pts[i];
        tip.innerHTML = "<span class='num'>" + esc(pt[0]) + " · " + pt[1] + "</span>";
        tip.style.display = "block"; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY - 12 + "px";
      });
      hov.addEventListener("mouseleave", function () { tip.style.display = "none"; });
    }
  }
  function fallbackCopy(txt) {
    var ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }
  function cycleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") || "auto";
    var next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto";
    if (next === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("upstream.theme", next); } catch (e) {}
  }
  try {
    var saved = localStorage.getItem("upstream.theme");
    if (saved && saved !== "auto") document.documentElement.setAttribute("data-theme", saved);
  } catch (e) {}

  /* H hides the interface from anywhere on the cortex page. Installed once, not per
     route: wire() re-runs on every navigation, and a toggle bound N times toggles N
     times. Skips the canvas (which handles H itself) and any text field. */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "h" && e.key !== "H") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target || {};
    if (t.id === "cortexCanvas" || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
    if (!document.getElementById("cxFull")) return;
    e.preventDefault(); cxToggleUI();
  });

  window.addEventListener("resize", function () {
    if (document.getElementById("cxFull")) cxMeasureTop();
  });

  window.addEventListener("hashchange", route);
  route();
  try {
    var jq = sessionStorage.getItem("upstream.justQueued");
    if (jq) {
      sessionStorage.removeItem("upstream.justQueued");
      toast("Queued: " + jq + " — a live Claude session runs it and this page refreshes with the result.");
    }
    var js = sessionStorage.getItem("upstream.justSaved");
    if (js) {
      sessionStorage.removeItem("upstream.justSaved");
      toast("Saved: " + js + " — the next Claude session on this repo writes it to .claude/agents/ and rebuilds.");
    }
  } catch (e) {}
  setTimeout(stampVisit, 4000);
})();
