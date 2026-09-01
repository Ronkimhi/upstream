/* Upstream SPA v2 — vanilla JS over window.UPSTREAM_DATA. Hash-routed, no external libs. */
(function () {
  "use strict";
  var D = window.UPSTREAM_DATA || {};
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
      na("#/", "Cortex", "cortex") +
      na("#/radar", "Radar", "radar") +
      na(navHrefChains(), (D.chains || []).length === 1 ? "Chain" : "Chains", "chain") +
      na("#/campaign", "Campaign", "campaign") +
      na("#/themes", "Log", "themes") +
      na("#/agents", "Agents", "agents") +
      na("#/book", "Book", "book") +
      na("#/shadow", "Shadow", "shadow") +
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
    return '<div class="footer">Upstream is a private research tool for its two users. Verdicts, zones, and levels are analytical outputs from public data with stated methods and gaps — not investment advice. Built ' + esc(D.built_at || "?") + " · canonical copy: <span class='mono'>app/index.html</span> in the repo.</div>";
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
    /* The four legs. app/build.py carries each leg's rationale only for appraisals a
       signal card can reach, and NEVER carries the leg's `evidence` array: those hold the
       verbatim source_excerpt spans method §1 requires, they exist so a machine can
       cross-check the claim offline, and they were 259 KB of a page that rendered none of
       them. What the page carries instead is the COUNT, printed here beside the file that
       holds the excerpts — so a reader can see the leg is sourced and where, rather than
       reading a rationale that looks unsupported. */
    var file = a.id ? "data/impact/" + a.id + ".json" : "data/impact/";
    if (a.legs_inlined === false) {
      return seclabel("Financial impact") +
        "<div class='card'><div class='row'>" + impactChip(a) +
        chip("unmapped " + num((s.unmappedness || {}).score)) + staleChip(a.as_of) + "</div>" +
        "<div class='small' style='margin-top:8px'>" + esc(impactTitle(a)) + "</div>" +
        "<div class='small muted' style='margin-top:8px'>The four scores are on this page; " +
        "their written reasoning is not. It is in " + esc(file) + ".</div></div>";
    }
    var legs = [
      ["Money at stake", (a.money_at_stake || {}).band, (a.money_at_stake || {}).rationale,
       (a.money_at_stake || {}).basis, (a.money_at_stake || {}).evidence_count],
      ["Public reach", num((a.public_reach || {}).score), (a.public_reach || {}).rationale,
       (a.public_reach || {}).basis, (a.public_reach || {}).evidence_count],
      ["Value capture", num((a.capture_odds || {}).score), (a.capture_odds || {}).rationale,
       (a.capture_odds || {}).basis, (a.capture_odds || {}).evidence_count],
      ["Timing fit", num((a.timing_fit || {}).score), (a.timing_fit || {}).rationale,
       (a.timing_fit || {}).basis, (a.timing_fit || {}).evidence_count]
    ].map(function (r) {
      return "<div class='evli'><b>" + esc(r[0]) + "</b> " + chip(num(r[1])) +
        (r[4] == null ? "" : chip(r[4] + " cited", r[4] ? "neutral" : "stale")) +
        "<div class='small muted'>" + esc(r[2] || r[3] || "") + "</div></div>";
    }).join("");
    return seclabel("Financial impact") +
      "<div class='card'><div class='row'>" + impactChip(a) +
      chip("unmapped " + num((s.unmappedness || {}).score)) + staleChip(a.as_of) + "</div>" +
      "<div class='small' style='margin-top:8px'>" + esc(impactTitle(a)) + "</div>" +
      "<div style='margin-top:10px'>" + legs + "</div>" +
      "<div class='small muted' style='margin-top:10px'>Each source's verbatim excerpt stays in " +
      esc(file) + "; this page carries the count, not the text.</div></div>";
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
     same rule seriesNote() and carriedTail() follow. */
  function chainPath(c) { return "data/chains/" + ((c && c.id) || "") + ".json"; }
  function chainFidelityNote(c) {
    var f = c && c.chain_fidelity;
    if (!f || f === "FULL") return "";
    var cn = (D.carried || {}).chains || {};
    var lost = f === "SUMMARY"
      ? "The heat reasoning behind every score is here. What is not: the cited evidence rows under each score, each link's bottleneck note, and each scenario's per-link reasoning."
      : "This chain is carried as navigation only — links, order, verdicts, scores, scenarios, indicators and invalidation signs. What is not on this page: the written reasoning behind every heat score, the cited evidence rows under it, the bottleneck notes, each scenario's per-link reasoning, and this chain's own notes.";
    var across = cn.total
      ? " Across " + cn.total + " chains this build carried " + cn.carried +
        " in full, " + cn.summary + " without evidence rows and " + cn.index_only +
        " as navigation only, in " + (cn.order || "chain id") + " order."
      : "";
    return "<div class='card' style='margin-top:14px'><h3>Reduced fidelity on this page</h3>" +
      "<div class='small'>" + esc(lost) + " It is all in <span class='mono'>" +
      esc(chainPath(c)) + "</span>." + esc(across) + "</div></div>";
  }
  var chainTab = "flow";
  function chainView(id, tab) {
    var c = byId(D.chains, id);
    if (!c) return notFound("chain " + id);
    chainTab = (tab === "heat" || tab === "scen" || tab === "flow") ? tab : "flow";
    var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
    var scored = links.filter(isPlottable);
    var money = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    var body = chainTab === "heat" ? heatTab(c, links, scored) : chainTab === "scen" ? scenTab(c) : flowTab(c, links);
    var subtitle = scored.length
      ? (money.length ? "Money corner: " + money.map(function (l) { return l.name; }).join(", ") + ". " : "No link clears all three thresholds yet. ") +
        scored.length + " of " + links.length + " links scored, as of " + (c.heat_as_of || "—") + "."
      : "Chain mapped; heat not scored yet.";
    return topbar("chain") + crumbs([{ label: sigTitle(c.signal_id), href: "#/signal/" + c.signal_id }, { label: c.title }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip(c.clock) + (money.length ? '<span class="chip UNDISCOVERED">★ ' + esc(money.map(function (l) { return l.name; }).join(" · ")) + "</span>" : "") + staleChip(c.heat_as_of) +
      (c.chain_fidelity && c.chain_fidelity !== "FULL" ? chip("reduced fidelity", "CROWDED") : "") + "</div>" +
      "<h1>" + esc(c.title) + "</h1><p class='sub'>" + esc(subtitle) + "</p></div>" +
      chainFidelityNote(c) + chainScreenAction(c) + mapCard(c.id) +
      '<div class="seg">' +
      [["flow", "Flow"], ["heat", "Heat map"], ["scen", "Scenarios"]].map(function (k) {
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
      var total = (o || {}).evidence_total;
      var rows = items.map(function (e) { return '<div class="evli">' + chip(e.tag) + " " + esc(e.claim) + (e.source_name ? " <span class='muted'>[" + esc(e.source_name) + "]</span>" : "") + "</div>"; }).join("");
      if (typeof total === "number" && total > items.length) {
        /* Two different states, and printing them the same way would be the "300 HELD"
           defect again: a windowed leg (3 of 7 carried) and a leg carried at reduced
           chain fidelity with none of its rows on the page at all. The second one has to
           say the sources exist, or a scored leg reads as an unsourced assertion. */
        rows += '<div class="evli muted">' + (items.length
          ? "showing " + items.length + " of " + total + " cited source(s)"
          : "none of this leg's " + total + " cited source(s) are carried on this page") +
          "; the rest, with each one's verbatim excerpt, are in <span class='mono'>" +
          esc(chainPath(c)) + "</span></div>";
      }
      return rows;
    }
    function block(title, o) {
      if (!o) return "";
      /* `esc(o.rationale || "")` drew an empty paragraph for a leg whose reasoning this
         build did not carry, which reads as "nobody wrote one". A scored leg with no
         rationale on the page says so and names the file. */
      var why = o.rationale != null
        ? "<div class='small'>" + esc(o.rationale) + "</div>"
        : (o.score != null
          ? "<div class='small muted'>Scored " + o.score +
            "/100. The written reasoning for this score is not on this page — it is in <span class='mono'>" +
            esc(chainPath(c)) + "</span>.</div>"
          : "");
      return "<h3>" + title + "</h3>" + why + ev(o);
    }
    var chips = '<div class="row">' + (h.verdict ? chip(h.verdict.replace("_", " "), h.verdict) : chip("unscored")) + (h.money_corner ? '<span class="chip UNDISCOVERED">★ money corner</span>' : "") + chip((l.investability || "").replace(/_/g, " ")) + chip((l.bottleneck || {}).criticality === "CHOKE_POINT" ? "choke point" : "bottleneck " + ((l.bottleneck || {}).criticality || "").toLowerCase()) + "</div>";
    var analysis =
      scoreBar("Impact", h.impact, "--accent") + scoreBar("Crowdedness", h.crowdedness, "--crd") + scoreBar("Value capture", h.capture, "--und") +
      block("Impact", h.impact) + block("Crowdedness", h.crowdedness) + block("Value capture", h.capture) +
      (h.repricing_check && h.repricing_check.note ? "<h3>Repricing check</h3><div class='small'>" + (h.repricing_check.legs_met != null ? "<span class='num'>" + h.repricing_check.legs_met + "/4 legs</span> · " : "") + esc(h.repricing_check.note) +
        (h.repricing_check.legs_detail_held ? " <span class='muted'>· the " + h.repricing_check.legs_detail_held + " per-leg basis note(s) are in <span class='mono'>" + esc(chainPath(c)) + "</span></span>" : "") + "</div>" : "") +
      /* The three capture judgments Ember records per link (supply concentration,
         substitutability, who posted the margin). They reach no template and were 66 KB
         of the 2026-08-30 page, so app/build.py carries the count; without this line a
         reader would have no way to know the capture score rests on anything written. */
      (l.capture_inputs_count ? "<h3>Capture inputs</h3><div class='small muted'>" +
        l.capture_inputs_count + " recorded judgment(s) behind the capture score, in <span class='mono'>" +
        esc(chainPath(c)) + "</span></div>" : "") +
      (((l.bottleneck || {}).note_held) ? "<div class='small muted' style='margin-top:8px'>The bottleneck note for this link is not on this page — it is in <span class='mono'>" + esc(chainPath(c)) + "</span>.</div>" : "") +
      "<h3>Feeds</h3><div class='small'>" + ((l.upstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "none") + "</div>" +
      "<h3>Fed by</h3><div class='small'>" + ((l.downstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "none") + "</div>" +
      // The link's own citation bar, the one tools/check_chain.py enforces on every link
      // and edge. app/build.py carries its COUNT, not its rows, so the count is printed
      // with the file that holds the citations rather than rendering nowhere.
      (l.evidence_count == null ? "" :
        "<h3>Map citations</h3><div class='small'>" + l.evidence_count +
        " dated source(s) behind this link, in data/chains/" + esc(c.id) + ".json</div>") +
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
        return '<span class="mv"><span class="' + (m.direction === "UP" ? "up" : "down") + '">' + (m.direction === "UP" ? "↑" : "↓") + "</span>" + esc(shortName(linkName(c, m.link_id))) + " · " + esc(m.magnitude.toLowerCase()) + "</span>";
      }).join("");
      var inds = (s.leading_indicators || []).map(function (i) {
        var trip = ((D.indicators || {}).trips || []).filter(function (t) {
          return t.chain === c.id && t.scenario === s.id && t.indicator === indText(i);
        })[0];
        var trippedAt = i.tripped_at || (trip && trip.tripped_at);
        var badge = trippedAt ? chip("tripped " + trippedAt, "OVER_CROWDED") : i.armed ? chip("armed", "accent") : "";
        return "<li>" + esc(indText(i)) + (i.where_to_watch ? " <span class='muted'>(" + esc(i.where_to_watch) + ")</span>" : "") + " " + badge + "</li>";
      }).join("");
      return '<div class="card scen"><div class="head"><span class="t">' + esc(s.id) + " — " + esc(s.title) + "</span>" +
        chip(s.status, s.status === "SCREENED" ? "accent" : "neutral") + (s.clock && s.clock !== c.clock ? chip(s.clock) : "") +
        '<span class="p">' + esc(s.probability_pct) + "<small>%</small></span></div>" +
        '<div class="pbar"><i style="width:' + esc(s.probability_pct) + '%"></i></div>' +
        "<div class='small'>" + esc(s.narrative) + "</div>" +
        "<div style='margin:10px 0 2px'>" + mv + "</div>" +
        /* At reduced chain fidelity the per-moved-link `why` and each indicator's
           check basis are not carried. Say it, rather than showing a shorter scenario
           that looks like a thinner one. */
        (s.why_held ? "<div class='muted small'>the reason each of these " + s.why_held +
          " link move(s) was written down is in <span class='mono'>" + esc(chainPath(c)) + "</span></div>" : "") +
        "<details><summary>Leading indicators & invalidation</summary><div class='body'><ul class='bullets'>" + inds + "</ul>" +
        "<div class='small' style='margin-top:8px'><b>Invalidation:</b> " + (s.invalidation_signs || []).map(esc).join(" · ") + "</div>" +
        (s.evidence_count ? "<div class='muted small' style='margin-top:8px'>" + s.evidence_count +
          " cited source(s) behind this scenario, in <span class='mono'>" + esc(chainPath(c)) + "</span></div>" : "") +
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
            '<span class="muted" title="' + esc((r.theme_revenue_exposure || {}).basis || "") + '">null</span>';
          var nug = (r.earnings_nuggets || []).length ? "<details><summary>" + r.earnings_nuggets.length + " earnings nugget(s)</summary>" +
            r.earnings_nuggets.map(function (n) { return "<blockquote>“" + esc(n.quote) + "”<div class='muted'>" + esc(n.form || "") + " · <a href='" + esc(n.url) + "'>" + esc(n.accession) + "</a></div></blockquote>"; }).join("") + "</details>" : "";
          var act = r.status === "DIVED" ? '<a class="chip accent" href="#/stock/' + esc(r.ticker) + "/" + esc(chainId) + '">Dive →</a>' :
            r.status === "CANDIDATE" ? "" : chip(r.status);
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
    /* A finished dive is ~55 KB of prose and nearly all of it renders, so app/build.py
       carries whole dives newest-first up to STOCK_DETAIL_BUDGET_BYTES and stops. This is
       the page for one that did not fit. It shows the verdict, the clock, the levels, the
       review date and the real price chart — everything the index row carries — and then
       says plainly that the written case is in the file rather than drawing empty Bull,
       Bear and Red team cards, which would read as "never written" instead of "not on
       this page". */
    if (st.detail_inlined === false) {
      var cn = (D.carried || {}).stocks || {};
      return topbar("chain") + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: ticker }]) + "<main>" +
        '<div class="pagehead"><h1>' + esc(st.ticker) + ' <span style="font-weight:400;font-size:16px;color:var(--ink-3)">' + esc(st.name || "") + "</span></h1></div>" + hero +
        seclabel("Price") + "<div class='card'>" + priceChart(mk, st) + "</div>" +
        seclabel("The case") +
        '<div class="statgrid"><div><h3 style="color:var(--good)">Bull</h3><ul class="bullets good">' + (st.bull || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div>" +
        '<div><h3 style="color:var(--bad)">Bear</h3><ul class="bullets bad">' + (st.bear || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div></div>" +
        ((st.red_team || {}).surviving_bear_case
          ? "<div class='card redteam' style='margin-top:14px'><div class='rt-label'>Red team — attacked " +
            esc(st.red_team.attacked_at) + " · " + (st.red_team.verdict_survived ? "verdict survived" : "verdict overturned") +
            " · " + esc(st.red_team.challenge_count) + " challenge(s), not on this page</div>" +
            "<div class='small' style='margin-top:10px'><b>Surviving bear case:</b> " + esc(st.red_team.surviving_bear_case) + "</div></div>"
          : "") +
        seclabel("The rest of the writeup") +
        "<div class='card'><h3>Not carried on this page</h3><div class='small'>" +
        "Everything above is this dive's own. " +
        ((st.bull || []).length
          ? "Its expectations gap, priced-in table, valuation lines, filing quotes, data gaps, red-team challenges and history are"
          : "Its bull and bear cases, expectations gap, priced-in table, valuation lines, filing quotes, data gaps, red team and history are") +
        " not on this page — they are in <span class='mono'>data/stocks/" +
        esc(ticker) + "__" + esc(chainId) + ".json</span>.</div>" +
        (cn.total ? "<div class='small muted' style='margin-top:8px'>Of " + esc(cn.total) +
          " dives on file this build carried " + esc(cn.carried) + " in full, " +
          esc(cn.summary) + " down to bull, bear and the surviving bear case, and " +
          esc(cn.index_only) + " as verdict and levels only — " + esc(cn.order) +
          ". A finished dive is tens of kilobytes of prose and the whole page has to " +
          "open on a phone.</div>" : "") +
        "</div>" + footer() + "</main>";
    }
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
      "<dt>Market cap</dt><dd class='num'>" + esc(((st.valuation_snapshot.market_cap || {}).value) || "—") + "</dd>" +
      (st.valuation_snapshot.lines || []).map(function (l) { return "<dt>" + esc(l.name) + "</dt><dd class='num'>" + esc(l.value) + " <span class='muted'>[" + esc(l.tag) + "]</span></dd>"; }).join("") +
      (st.entry_zone ? "<dt>Entry basis</dt><dd class='small'>" + esc(st.entry_zone.basis) + "</dd>" : "") + "</div></div>";
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
  function pct(v) { return v == null ? "—" : (v * 100).toFixed(1) + "%"; }
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
  /* What this page is holding of a price series, in the page's own words.
     app/build.py inlines the full daily file only for tickers that have a dive, and
     downsamples even those to method.page.series_points evenly spaced points — the chart
     is 940px wide and drew 420 at most anyway. A downsample rendered as if it were the
     whole file is the same defect class as an invented number, so every chart states
     `inlined_rows` against `row_count` and names the file that has the rest. */
  function seriesNote(mk, ticker) {
    var s = (mk || {}).series;
    if (!s || s.row_count == null) return "";
    var path = "data/market/" + String(ticker || "").replace(/\./g, "-") + ".json";
    if (s.sampling === "HEADER_ONLY") {
      return s.row_count + " price points exist in " + path +
        "; this page carries none of them — the series is inlined only for tickers with a dive";
    }
    if (s.sampling === "EVEN" && s.inlined_rows != null && s.inlined_rows < s.row_count) {
      return s.inlined_rows + " of " + s.row_count + " price points, evenly spaced · full daily series in " + path;
    }
    return "all " + s.row_count + " price points on file";
  }
  function priceChart(mk, st) {
    if (!mk || !mk.series) {
      return '<div class="emptystate">No price series yet.<div class="runwrap">' + runButton("request data " + st.ticker, "the fetch workflow fills data/market in ~5 minutes") + "</div></div>";
    }
    if (!(mk.series.rows || []).length) {
      /* Two different states that must never be shown as one: the fetch has never run,
         and the fetch has run but this build did not carry the series onto the page. */
      return mk.series.row_count
        ? '<div class="emptystate">This page is not carrying this price series.<div class="small muted" style="margin-top:8px">' + esc(seriesNote(mk, st.ticker)) + "</div></div>"
        : '<div class="emptystate">No price series yet.<div class="runwrap">' + runButton("request data " + st.ticker, "the fetch workflow fills data/market in ~5 minutes") + "</div></div>";
    }
    var rows = mk.series.rows;
    var step = Math.max(1, Math.floor(rows.length / 420));
    var pts = rows.filter(function (_, i) { return i % step === 0 || i === rows.length - 1; });
    var W = 940, H = 360, P = { l: 52, r: 76, t: 30, b: 34 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    var vals = pts.map(function (r) { return r[1]; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (st.entry_zone) { lo = Math.min(lo, st.entry_zone.low); hi = Math.max(hi, st.no_entry_above || hi); }
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
    if (st.no_entry_above) {
      s += '<line x1="' + P.l + '" y1="' + Y(st.no_entry_above) + '" x2="' + (W - P.r) + '" y2="' + Y(st.no_entry_above) + '" stroke="var(--ovr)" stroke-width="0.9" stroke-dasharray="2 5"/>' +
        '<text x="' + (W - P.r + 6) + '" y="' + (Y(st.no_entry_above) + 4) + '" font-size="10" class="mono-t" fill="var(--ovr)">' + esc(st.no_entry_above) + " no entry</text>";
    }
    for (var t = 0; t <= 4; t++) {
      var v = lo + ((hi - lo) * t) / 4;
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)"/>';
      // top tick carries the axis name inline; the rest keep bare numbers (Evidence axis grammar)
      // sits just inside the plot on the top gridline, so the event-label band above stays clear
      if (t === 4) s += '<text x="' + (P.l + 6) + '" y="' + (Y(v) + 13) + '" font-size="10" class="mono-t" fill="var(--ink-3)">' + v.toFixed(0) + "   price" + (mk.series.currency ? ", " + esc(mk.series.currency) : "") + "</text>";
      else s += '<text x="' + (P.l - 8) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t" fill="var(--chart-axis)">' + v.toFixed(0) + "</text>";
    }
    var lbl = Math.max(1, Math.floor(pts.length / 6));
    pts.forEach(function (r, i) { if (i % lbl === 0 && i < pts.length - 3) s += '<text x="' + X(i) + '" y="' + (H - P.b + 16) + '" text-anchor="middle" font-size="9.5" class="mono-t" fill="var(--chart-axis)">' + esc(r[0].slice(0, 7)) + "</text>"; });
    // An event outside the price window is exactly the one that must not vanish: a SCHEDULED
    // occurrence or a dated calendar entry is future by definition. Clamp it to the edge instead.
    (st.events || []).forEach(function (e, ei) {
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
    s += '<text x="' + (X(pts.length - 1) + 9) + '" y="' + (Y(last[1]) + 4) + '" font-size="11.5" font-weight="650" class="mono-t" fill="var(--ink)">' + last[1] + "</text>";
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
  /* v3: the whole machine as one field. d3-force (vendored, ISC) lays out every
     research object — signals, chains, links, scenarios, screens, names, dives,
     shadow rows, candidates, known future events — plus a dust halo of raw feed
     headlines. One graph<->screen transform; zoom, pan, drag, neighbour-dim. */
  var CXP = {
    bg0: "#08080d", bg1: "#0f0f16",
    ink: "#dde0ef", ink2: "#9296ad", ink3: "#7a7e99",
    accent: "#8687f0",
    verd: { QUIET: "#7b8496", UNDISCOVERED: "#34c77e", EMERGING: "#e3b23c", CROWDED: "#e97f4e", OVER_CROWDED: "#c14a62" },
    dive: { INVESTABLE: "#34c77e", WATCH: "#e3b23c", TOO_LATE: "#c14a62" },
    none: "#6a6e86", gold: "#d4a017", bad: "#e05a72",
    fam: { POLICY: "#e3b23c", CORPORATE: "#8687f0", TECH: "#34c77e", PHYSICAL: "#e97f4e", GEO: "#c14a62", MACRO: "#e3b23c", LEGAL: "#e3b23c" }
  };
  // In the radial, a node with no seat on the ranked circle used to fade to nothing, so
  // companies, verdicts and the raw feed vanished the moment you ranked. These kinds keep a
  // faint resting opacity instead, so the ranking still shows the field it ranks. Structural
  // kinds (sig/chain/link/scen) are absent here → floor 0, so a FOCUSED radial still
  // collapses to its one chain.
  // Floor multiplies each kind's own base node alpha, so it is calibrated per base to land
  // every backdrop kind at roughly the same faint level: dust's base is ~0.4 (needs a high
  // floor to read), a verdict's is 0.92 (needs less).
  var CX_RADIAL_FLOOR = { co: 0.30, dive: 0.26, screen: 0.16, shadow: 0.15, cand: 0.13, evt: 0.17, dust: 0.42 };
  var CX_W = 1200, CX_H = 760, CX_NOWX = 720, CX_CLAMP = 1460;
  var CX_CHARGE = { sig: -900, chain: -700, scen: -420, screen: -380, dive: -320, shadow: -260, link: -180, evt: -110, cand: -70, co: -60, socket: 0, dust: -3 };
  var CX_EDGE = {
    "sig-chain":   { d: 90,  s: 0.55, a: 0.16 },
    "chain-link":  { d: 120, s: 0.22, a: 0.07 },
    "link-link":   { d: 58,  s: 0.30, a: 0.13 },
    "chain-scen":  { d: 150, s: 0.35, a: 0.10 },
    "scen-link":   { d: 105, s: 0.06, a: 0.035 },
    "scen-screen": { d: 80,  s: 0.50, a: 0.14 },
    "screen-co":   { d: 60,  s: 0.35, a: 0.09 },
    "co-link":     { d: 34,  s: 0.10, a: 0.04 },
    "dive-co":     { d: 40,  s: 0.60, a: 0.18 },
    "dive-shadow": { d: 30,  s: 0.70, a: 0.18 }
  };
  var CX_ORDER = { sig: 0, chain: 1, link: 2, scen: 3, screen: 4, co: 5, dive: 6, shadow: 7, cand: 8, evt: 9, socket: 10 };
  function cxTrim(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function cxTimeX(dateStr) {
    var dx = daysBetween(TODAY, dateStr);
    if (isNaN(dx)) dx = 0;
    var t = Math.sqrt(Math.min(Math.abs(dx), CX_CLAMP) / CX_CLAMP);
    return dx < 0 ? CX_NOWX - t * 560 : CX_NOWX + t * 380;
  }

  /* ---- graph model: every research object in the payload ---- */
  function cxBuild() {
    var nodes = [], edges = [], byKey = {}, i;
    function add(n) {
      n.key = n.kind + ":" + n.id;
      if (byKey[n.key]) return byKey[n.key];
      n.x = n.x0; n.y = n.y0;
      byKey[n.key] = n; nodes.push(n); return n;
    }
    function join(aKey, bKey, type) {
      if (!byKey[aKey] || !byKey[bKey] || aKey === bKey) return;
      edges.push({ source: aKey, target: bKey, type: type, alpha: (CX_EDGE[type] || {}).a || 0.06 });
    }
    var cx0 = CX_W / 2, cy0 = CX_H / 2;
    function seedX(t) { return cx0 + (t - 0.5) * 460 + (Math.random() - 0.5) * 90; }
    function seedY(t) { return cy0 + (t - 0.5) * 300 + (Math.random() - 0.5) * 90; }

    // signals
    var sigs = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; });
    sigs.forEach(function (sg, si) {
      var occ = sg.occurrence || null;
      var anchor = (occ && occ.anchor_date) || sg.created_at || TODAY;
      var un = (sg.unmappedness || {}).score || 0;
      var tx = cxTimeX(anchor);
      add({
        kind: "sig", id: sg.id, ref: sg, rSem: 10 + 42 * Math.pow(un / 100, 1.8),
        tier: un >= 80 ? "MAJOR" : un >= 60 ? "MID" : "TAIL", color: CXP.accent,
        dashed: !!(occ && occ.kind === "SCHEDULED"), tx: tx, txw: 0.012,
        label: cxTrim(sg.title, 34),
        sub: occ ? cxTrim(occ.label, 40) + " · " + anchor : "occurrence undated",
        tip: sg.title + " — " + (occ ? occ.kind + " · " + anchor : "undated"),
        x0: tx + (Math.random() - 0.5) * 60,
        y0: CX_H * (0.26 + 0.5 * ((si % 3) / 2)) + (Math.random() - 0.5) * 70
      });
    });

    // chains (iterate directly; attach via signal_id so an orphaned chain still draws)
    (D.chains || []).forEach(function (c, ci) {
      var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
      var scored = links.filter(function (l) { return l.heat && l.heat.crowdedness && l.heat.crowdedness.score != null; }).length;
      var host = c.signal_id && byKey["sig:" + c.signal_id];
      var hx = host ? host.x0 : seedX(ci / Math.max(1, (D.chains || []).length));
      var hy = host ? host.y0 + 120 : cy0;
      add({
        kind: "chain", id: c.id, ref: c, rSem: 10 + 6 * (links.length ? scored / links.length : 0),
        color: CXP.accent, label: cxTrim(c.title, 30),
        sub: links.length + " links · " + scored + " scored",
        tip: c.title + " — " + links.length + " links, " + scored + " scored",
        arcs: links.map(function (l) { return (l.heat || {}).verdict || null; }),
        x0: hx + (Math.random() - 0.5) * 40, y0: hy + (Math.random() - 0.5) * 40
      });
      if (host) { host.sigChain = c.id; join("chain:" + c.id, "sig:" + c.signal_id, "sig-chain"); }
      links.forEach(function (l, li) {
        var a = (-90 + li * (360 / Math.max(1, links.length))) * Math.PI / 180;
        var h = l.heat || {};
        add({
          kind: "link", id: c.id + "/" + l.id, ref: l, chainId: c.id,
          rSem: (3.0 + (h.impact && h.impact.score != null ? h.impact.score : 0) / 50) * ((h.money_corner || (l.bottleneck || {}).criticality === "CHOKE_POINT") ? 1.4 : 1),
          color: h.verdict ? CXP.verd[h.verdict] : CXP.none,
          gold: !!h.money_corner, choke: (l.bottleneck || {}).criticality === "CHOKE_POINT",
          label: cxTrim(l.name, 22),
          sub: (h.verdict ? h.verdict.replace(/_/g, " ").toLowerCase() : "unscored") +
               (h.money_corner ? " · ★ money corner" : "") +
               ((l.bottleneck || {}).criticality === "CHOKE_POINT" ? " · choke point" : ""),
          tip: l.name + (h.verdict ? " — " + h.verdict.replace("_", " ") : " — unscored") + (h.money_corner ? " · ★ money corner" : ""),
          x0: hx + 130 * Math.cos(a), y0: hy + 130 * Math.sin(a)
        });
        join("link:" + c.id + "/" + l.id, "chain:" + c.id, "chain-link");
      });
      links.forEach(function (l) {
        (l.upstream_of || []).forEach(function (u) {
          join("link:" + c.id + "/" + l.id, "link:" + c.id + "/" + u, "link-link");
        });
      });
      (c.scenarios || []).forEach(function (s, si2) {
        var col = s.status === "SCREENED" ? CXP.verd.UNDISCOVERED
          : (s.status === "INVALIDATED" || s.status === "PLAYED_OUT") ? CXP.ink3 : CXP.accent;
        add({
          kind: "scen", id: c.id + "/" + s.id, ref: s, chainId: c.id,
          rSem: 4.5 + 7 * ((s.probability_pct || 0) / 100), color: col,
          label: cxTrim(s.title, 26), sub: s.id + (s.probability_pct != null ? " · " + s.probability_pct + "%" : " · unscored"),
          ty: CX_H * 0.16, tyw: 0.006,
          tip: s.id + " · " + s.title + " — " + s.probability_pct + "%",
          x0: hx + (si2 - 1.5) * 90, y0: hy - 180 + (Math.random() - 0.5) * 40
        });
        join("scen:" + c.id + "/" + s.id, "chain:" + c.id, "chain-scen");
        (s.links_moved || []).forEach(function (m) {
          join("scen:" + c.id + "/" + s.id, "link:" + c.id + "/" + m.link_id, "scen-link");
        });
      });
    });

    // companies: union of example tickers, screened names, dived and shadowed tickers
    var coSeen = {};
    function co(ticker, seedNear) {
      var t = String(ticker || "").trim();
      if (!t) return null;
      var k = "co:" + t;
      if (coSeen[t]) return byKey[k];
      coSeen[t] = 1;
      var near = seedNear || { x0: cx0, y0: cy0 };
      return add({
        kind: "co", id: t, rSem: 2.0, color: CXP.ink3, label: t,
        tip: t, hollow: !marketFor(t),
        x0: near.x0 + (Math.random() - 0.5) * 90, y0: near.y0 + (Math.random() - 0.5) * 90
      });
    }
    (D.chains || []).forEach(function (c) {
      (c.links || []).forEach(function (l) {
        var ln = byKey["link:" + c.id + "/" + l.id];
        (l.example_tickers || []).forEach(function (t) {
          var n = co(t, ln);
          if (!n) return;
          join("co:" + n.id, "link:" + c.id + "/" + l.id, "co-link");
          n.chainIds = n.chainIds || []; n.chains = n.chains || [];
          if (n.chainIds.indexOf(c.id) < 0) { n.chainIds.push(c.id); n.chains.push(cxTrim(c.title, 26)); }
        });
      });
    });

    // screens
    (D.screens || []).forEach(function (sc) {
      var names = [];
      ["pure_play", "picks_and_shovels", "second_order", "hedge"].forEach(function (b) {
        ((sc.buckets || {})[b] || []).forEach(function (r) { names.push(r); });
      });
      var anchorScen = byKey["scen:" + sc.chain_id + "/" + sc.scenario_id];
      add({
        kind: "screen", id: sc.id, ref: sc, rSem: 6 + 0.9 * Math.sqrt(names.length || 1),
        color: CXP.ink2, label: "SCREEN " + sc.scenario_id,
        sub: names.length + " names", tip: sc.id + " — " + names.length + " names screened",
        x0: (anchorScen ? anchorScen.x0 : cx0) + 60, y0: (anchorScen ? anchorScen.y0 : cy0) - 60
      });
      if (anchorScen) join("scen:" + sc.chain_id + "/" + sc.scenario_id, "screen:" + sc.id, "scen-screen");
      names.forEach(function (r) {
        var n = co(r.ticker, byKey["screen:" + sc.id]);
        if (!n) return;
        n.tier = r.tier; n.screened = true; n.coName = r.name || n.coName;
        n.color = r.tier === "T1" ? CXP.ink : r.tier === "T2" ? CXP.ink2 : CXP.ink3;
        n.tip = r.ticker + (r.name ? " · " + r.name : "") + " — " + (r.tier || "") + " " + (r.status || "");
        join("screen:" + sc.id, "co:" + n.id, "screen-co");
      });
    });

    // dives
    (D.stocks || []).forEach(function (st) {
      var w = st.verdict === "INVESTABLE" ? 1 : st.verdict === "WATCH" ? 0.6 : 0.3;
      var target = byKey["co:" + st.ticker];
      add({
        kind: "dive", id: st.ticker + "__" + st.chain_id, ref: st,
        rSem: 7 + 4 * w, color: CXP.dive[st.verdict] || CXP.none,
        dashed: st.status === "DRAFT", label: st.ticker,
        sub: (st.verdict || "").replace("_", " ").toLowerCase(),
        tip: st.ticker + " — " + st.verdict + " (" + st.clock + ", " + st.status + ")",
        ty: CX_H * 0.86, tyw: 0.008,
        x0: target ? target.x0 : cx0, y0: CX_H * 0.84
      });
      var cn = co(st.ticker, byKey["dive:" + st.ticker + "__" + st.chain_id]);
      if (cn) { cn.dived = true; join("dive:" + st.ticker + "__" + st.chain_id, "co:" + cn.id, "dive-co"); }
    });

    // shadow rows
    var sres = (D.shadow || {}).results || {};
    (((D.shadow || {}).book || {}).rows || []).forEach(function (r, ri) {
      var call = (sres[r.id] || {}).call;
      add({
        kind: "shadow", id: r.id, ref: r, rSem: 4,
        color: call === "RIGHT" ? CXP.verd.UNDISCOVERED : call === "WRONG" ? CXP.verd.OVER_CROWDED : CXP.none,
        label: r.ticker, tip: r.ticker + " — shadow " + (call || "awaiting +90d"),
        ty: CX_H * 0.9, tyw: 0.008, x0: CX_W * 0.8 + ri * 26, y0: CX_H * 0.9
      });
      co(r.ticker, byKey["shadow:" + r.id]);
      (D.stocks || []).forEach(function (st) {
        if (st.shadow_ref && st.shadow_ref.indexOf(r.id) > -1) join("dive:" + st.ticker + "__" + st.chain_id, "shadow:" + r.id, "dive-shadow");
      });
    });

    // ambient candidates + known future events
    (((D.candidates || {}).candidates) || []).forEach(function (cd) {
      if (cd.status !== "AMBIENT") return;
      var tx = cxTimeX(cd.date);
      add({
        kind: "cand", id: cd.id, ref: cd, rSem: 3.5, color: CXP.fam[cd.family] || CXP.none,
        tx: tx, txw: 0.010, label: cxTrim(cd.title, 20),
        tip: cd.title + " — " + cd.family + " · " + cd.date,
        x0: tx + (Math.random() - 0.5) * 90, y0: CX_H * (0.62 + Math.random() * 0.26)
      });
    });
    (((D.calendar || {}).events) || []).forEach(function (ev) {
      if (ev.status !== "WATCHING") return;
      var tx = cxTimeX(ev.date);
      add({
        kind: "evt", id: ev.id, ref: ev, rSem: 4, dashed: true, color: CXP.fam[ev.kind] || CXP.none,
        tx: tx, txw: 0.014, label: cxTrim(ev.title, 20),
        tip: ev.title + " — " + ev.kind + " · " + ev.date,
        x0: tx + (Math.random() - 0.5) * 60, y0: CX_H * (0.2 + Math.random() * 0.3)
      });
    });

    // sockets: an empty funnel stage still shows its shape
    function socket(id, label, gx, gy, nav, note) {
      add({ kind: "socket", id: id, rSem: 16, color: CXP.ink3, dashed: true, socket: true,
            label: label + " · 0", nav: nav, tip: note, fx: gx, fy: gy, x0: gx, y0: gy });
    }
    if (!(D.screens || []).length) socket("screen", "SCREEN", CX_W * 0.80, CX_H * 0.30, null, "no scenario screened yet");
    if (!(D.stocks || []).length) socket("dive", "DIVE", CX_W * 0.72, CX_H * 0.86, null, "no deep dive yet");
    if (!(((D.shadow || {}).book || {}).rows || []).length) socket("shadow", "SHADOW", CX_W * 0.90, CX_H * 0.86, "#/shadow", "no TOO LATE verdict graded yet");
    if (!(((D.candidates || {}).candidates) || []).filter(function (c) { return c.status === "AMBIENT"; }).length)
      socket("cand", "AMBIENT", CX_NOWX - 300, CX_H * 0.80, null, "run radar to triage the feed");
    if (!(((D.calendar || {}).events) || []).filter(function (e) { return e.status === "WATCHING"; }).length)
      socket("evt", "KNOWN FUTURE", CX_NOWX + 230, CX_H * 0.22, null, "no dated future event tracked yet");

    // adjacency + degree-weighted radii (bounded, additive: degree never swamps semantics)
    var adj = {}, deg = {};
    nodes.forEach(function (n) { adj[n.key] = {}; deg[n.key] = 0; });
    edges.forEach(function (e) {
      adj[e.source][e.target] = 1; adj[e.target][e.source] = 1;
      deg[e.source]++; deg[e.target]++;
    });
    nodes.forEach(function (n) {
      n.r = n.rSem + (n.kind === "socket" ? 0 : Math.min(6, 1.25 * Math.sqrt(Math.max(0, deg[n.key] - 1))));
    });

    // feed dust: real headlines, laid out on an annulus (seats filled after warm-up)
    var dust = ((D.feeds || {}).items || []);
    for (i = 0; i < dust.length; i++) {
      var it = dust[i];
      add({
        kind: "dust", id: "d" + i, ref: it, rSem: 1.5, r: 1.5,
        color: CXP.fam[it.f] || CXP.ink3, seat: i,
        tip: cxTrim(it.t, 84) + " — " + (it.s || "") + " · " + ((it.d || "").slice(0, 10)),
        x0: cx0 + (Math.random() - 0.5) * 900, y0: cy0 + (Math.random() - 0.5) * 620
      });
    }
    // z = time (depth axis): sqrt-compressed days from today, past negative, future positive
    function zOf(dateStr) {
      var dx = daysBetween(TODAY, dateStr);
      if (isNaN(dx)) dx = 0;
      var zt = Math.sqrt(Math.min(Math.abs(dx), CX_CLAMP) / CX_CLAMP) * 480;
      return dx < 0 ? -zt : zt;
    }
    nodes.forEach(function (n) {
      if (n.kind !== "sig") return;
      var occ = (n.ref || {}).occurrence || {};
      n.z = zOf(occ.anchor_date || (n.ref || {}).created_at || TODAY);
    });
    nodes.forEach(function (n) {
      if (n.kind !== "chain") return;
      var host = byKey["sig:" + ((n.ref || {}).signal_id || "")];
      n.z = (host ? host.z : 0) + (Math.random() - 0.5) * 20;
    });
    nodes.forEach(function (n) {
      if (n.z != null) return;
      var chz = n.chainId && byKey["chain:" + n.chainId] ? byKey["chain:" + n.chainId].z : null;
      if (n.kind === "link") n.z = (chz || 0) + (Math.random() - 0.5) * 40;
      else if (n.kind === "scen") n.z = (chz || 0) + 70 + Math.random() * 25;
      else if (n.kind === "screen") n.z = ((byKey["chain:" + ((n.ref || {}).chain_id || "")] || {}).z || 0) + 85;
      else if (n.kind === "co") {
        var c0 = (n.chainIds || [])[0];
        n.z = (c0 && byKey["chain:" + c0] ? byKey["chain:" + c0].z : 0) + (Math.random() - 0.5) * 40;
      }
      else if (n.kind === "dive" || n.kind === "shadow") n.z = zOf((n.ref || {}).as_of || (n.ref || {}).verdict_date || TODAY);
      else if (n.kind === "cand") n.z = zOf((n.ref || {}).date);
      else if (n.kind === "evt") n.z = Math.max(60, zOf((n.ref || {}).date));
      else if (n.kind === "dust") n.z = (Math.random() - 0.5) * 70;
      else n.z = 0;
    });
    return { nodes: nodes, edges: edges, byKey: byKey, adj: adj, deg: deg, ring: null, builtAt: D.built_at };
  }

  /* ---- simulation (vendored d3-force; we own the clock) ---- */
  function cxRingForce(g) {
    var ns;
    function force(alpha) {
      if (!g.ring) return;
      for (var i = 0; i < ns.length; i++) {
        var n = ns[i];
        if (n.kind === "cand" && n.hx != null) {
          n.vx += (n.hx - n.x) * 0.05 * alpha;
          n.vy += (n.hy - n.y) * 0.05 * alpha;
          continue;
        }
        if (n.kind !== "dust" || n.sx == null) continue;
        n.vx += (n.sx - n.x) * 0.08 * alpha;
        n.vy += (n.sy - n.y) * 0.08 * alpha;
      }
    }
    force.initialize = function (_) { ns = _; };
    return force;
  }
  function cxSeats(g) {
    var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    g.nodes.forEach(function (n) {
      if (n.kind === "dust") return;
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    });
    if (minX > maxX) { minX = 0; maxX = CX_W; minY = 0; maxY = CX_H; }
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    var r0 = 0.62 * Math.max(maxX - minX, maxY - minY), band = 0.28 * r0;
    // one arc per family, sized by nothing but order: the halo reads as five labelled
    // sectors so a headline's family is legible from its position, not only its colour.
    var span = Math.PI * 2 / CX_FAMS.length, gap = 0.055;
    var arcOf = {}, seen = {};
    CX_FAMS.forEach(function (f, fi) {
      arcOf[f] = { i: fi, a0: -Math.PI / 2 + fi * span + gap, a1: -Math.PI / 2 + (fi + 1) * span - gap, n: 0 };
      seen[f] = 0;
    });
    g.nodes.forEach(function (n) { if (n.kind === "dust" && arcOf[(n.ref || {}).f]) arcOf[(n.ref || {}).f].n++; });
    g.ring = { cx: cx, cy: cy, r0: r0, band: band, arcs: arcOf, span: span, gap: gap };
    g.nodes.forEach(function (n) {
      if (n.kind !== "dust") return;
      var f = (n.ref || {}).f, arc = arcOf[f], ang, rad;
      if (arc && arc.n > 0) {
        var j = seen[f]++;
        ang = arc.a0 + (arc.a1 - arc.a0) * ((j + 0.5) / arc.n);
        rad = r0 + band * ((j * 0.6180339) % 1);
      } else {
        // no family on the item: it keeps the old free seat rather than being filed by guess
        ang = n.seat * 2.399963 + 0.13;
        rad = r0 + band * ((n.seat * 0.6180339) % 1);
      }
      n.sx = cx + rad * Math.cos(ang);
      n.sy = cy + rad * Math.sin(ang) * 0.92;
      n.famArc = arc ? f : null;
    });
    // ambient candidates ride the outer halo, named, in their own family's arc
    var candSeen = {};
    g.nodes.forEach(function (n) {
      if (n.kind !== "cand") return;
      var f = (n.ref || {}).family, arc = arcOf[f];
      if (!arc) return;
      var k = candSeen[f] = (candSeen[f] || 0) + 1;
      var ang2 = arc.a0 + (arc.a1 - arc.a0) * (k / (1 + countCand(g, f)));
      var rad2 = r0 + band * 1.32;
      n.hx = cx + rad2 * Math.cos(ang2);
      n.hy = cy + rad2 * Math.sin(ang2) * 0.92;
    });
  }
  function countCand(g, f) {
    var n = 0;
    g.nodes.forEach(function (x) { if (x.kind === "cand" && (x.ref || {}).family === f) n++; });
    return n;
  }
  function cxSim(g) {
    var sim = d3.forceSimulation(g.nodes)
      .velocityDecay(0.55)
      .force("charge", d3.forceManyBody().theta(0.9).distanceMin(2).distanceMax(420)
        .strength(function (n) { return CX_CHARGE[n.kind] != null ? CX_CHARGE[n.kind] : -80; }))
      .force("link", d3.forceLink(g.edges).id(function (n) { return n.key; })
        .distance(function (e) { return (CX_EDGE[e.type] || {}).d || 80; })
        .strength(function (e) { return (CX_EDGE[e.type] || {}).s || 0.2; }))
      .force("collide", d3.forceCollide(function (n) { return n.kind === "dust" ? 1.8 : n.r + 2; }).strength(0.65))
      .force("x", d3.forceX(CX_W / 2).strength(function (n) { return n.kind === "dust" ? 0 : 0.0022; }))
      .force("y", d3.forceY(CX_H / 2).strength(function (n) { return n.kind === "dust" ? 0 : 0.0035; }))
      .force("ring", cxRingForce(g));
    sim.stop();
    var i;
    for (i = 0; i < 70; i++) sim.tick();     // structure first, dust free
    cxSeats(g);                               // annulus from the settled core
    for (i = 0; i < 20; i++) sim.tick();     // dust snaps toward its seats
    var bad = false;
    g.nodes.forEach(function (n) { if (!isFinite(n.x) || !isFinite(n.y)) bad = true; });
    if (bad) {
      g.nodes.forEach(function (n) { n.x = n.x0; n.y = n.y0; n.vx = n.vy = 0; });
      for (i = 0; i < 60; i++) sim.tick();
    }
    return sim;
  }

  /* ---- view: one graph<->screen transform for everything ---- */
  var CX_CACHE = null;
  var CX_F = 900; // focal length; effective zoom k = CX_F / cam.dist
  function cxCam(g, w, h) {
    // frame the research core; the dust halo may bleed off-frame
    var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    g.nodes.forEach(function (n) {
      if (n.kind === "dust") return;
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    });
    if (minX > maxX) { minX = 0; maxX = CX_W; minY = 0; maxY = CX_H; }
    var gw = Math.max(maxX - minX, 300), gh = Math.max(maxY - minY, 240);
    var s = Math.min((w * 0.78) / gw, (h * 0.8) / gh);
    return {
      yaw: -0.45, pitch: -0.22,
      dist: Math.max(CX_F / 4, Math.min(3600, CX_F / Math.max(0.2, s))),
      tx: (minX + maxX) / 2, ty: (minY + maxY) / 2, tz: 0
    };
  }

  /* ---- instrument rails: every register is real data ---- */
  function cxReg(k, v, dim) { return '<div class="cx-reg"><span class="k">' + esc(k) + '</span><span class="v' + (dim ? " dim" : "") + '">' + esc(v) + "</span></div>"; }
  function cxBar(share, color) { return '<div class="cx-bar"><i style="width:' + Math.round(share * 100) + "%;background:" + color + '"></i></div>'; }
  function cxAgeDays(ts) { if (!ts) return null; var d = daysBetween(String(ts).slice(0, 10), TODAY); return isNaN(d) ? null : d; }
  function cxRailLeft() {
    var sigs = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; });
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
    var fam = {}, items = (D.feeds || {}).items || [];
    items.forEach(function (it) { fam[it.f] = (fam[it.f] || 0) + 1; });
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
    var h = '<div class="cx-grp">FUNNEL</div>' +
      cxReg("signals", sigs.length) + cxReg("chains", (D.chains || []).length) +
      cxReg("links", links.length) + cxReg("scenarios", scens) +
      cxReg("screens", (D.screens || []).length) + cxReg("names", nNames) +
      cxReg("dives", (D.stocks || []).length) +
      cxReg("shadow", ((((D.shadow || {}).book) || {}).rows || []).length) +
      cxReg("ambient", cands) + cxReg("known future", evts);
    var scoredN = links.length - unscored;
    h += '<div class="cx-grp">HEAT · ' + scoredN + "/" + links.length + " SCORED</div>";
    [["undiscovered", vc.UNDISCOVERED, "var(--und)"], ["emerging", vc.EMERGING, "var(--emg)"],
     ["crowded", vc.CROWDED, "var(--crd)"], ["over-crowded", vc.OVER_CROWDED, "var(--ovr)"],
     ["quiet", vc.QUIET, "var(--quiet)"]].forEach(function (r) {
      h += cxReg(r[0], r[1]) + cxBar(links.length ? r[1] / links.length : 0, r[2]);
    });
    h += cxReg("unscored", unscored, !unscored) + cxReg("money corner", money) + cxReg("choke point", choke);
    // MAP register: the cartographer's own report card, on the same principle as the sockets.
    // A link that has never produced a name is a finding about the map, so it is rendered.
    var mcal = (D.map || {}).calibration;
    if (mcal) {
      var mly = mcal.link_yield || {}, mper = mcal.per_chain || {};
      var mFind = 0, mThin = 0, mUncited = 0;
      Object.keys(mper).forEach(function (k) {
        var st = mper[k].structure || {};
        mFind += (st.orphans || []).length + (st.one_way_edges || []).length +
                 (st.wrong_direction_edges || []).length + (st.verdict_mismatches || []).length;
        mThin += (st.thin_choke_points || []).length;
        mUncited += (st.links_without_evidence || []).length;
      });
      h += '<div class="cx-grp">MAP · ' + (mly.yielded_a_name || 0) + "/" + (mly.links_total || 0) + " YIELDED</div>";
      h += cxReg("dead links", (mly.links_total || 0) - (mly.yielded_a_name || 0)) +
           cxBar(mly.links_total ? (mly.yielded_a_name || 0) / mly.links_total : 0, "var(--und)") +
           cxReg("structural findings", mFind, !mFind) +
           cxReg("thin choke points", mThin, !mThin) +
           cxReg("uncited links", mUncited, !mUncited) +
           cxReg("archetypes", ((D.map || {}).archetypes || []).filter(function (a) { return a.status === "HARDENED"; }).length);
    }
    var feedTotal = (D.feeds || {}).total;
    h += '<div class="cx-grp">FEED · ' + (feedTotal == null || feedTotal === items.length
      ? items.length + " HELD"
      : items.length + " OF " + feedTotal + " HELD") + "</div>";
    Object.keys(fam).sort(function (a, b) { return fam[b] - fam[a]; }).forEach(function (f) {
      h += cxReg(f.toLowerCase(), fam[f]) + cxBar(items.length ? fam[f] / items.length : 0, CXP.fam[f] || CXP.ink3);
    });
    h += '<div class="cx-grp">SYSTEM</div>' +
      /* The page carries only the PENDING and FAILED rows of data/requests.json — the two
         states it filters for. `settled` is the count of the rows it did not carry, so
         the register prints the whole store rather than implying 134 requests were 18. */
      cxReg("requests", pendingCount() + "P / " + failedCount() + "F" +
        ((D.requests || {}).settled ? " / " + D.requests.settled + " done" : ""),
        !pendingCount() && !failedCount()) +
      cxReg("radar", (rs.radar || "?").toLowerCase()) + cxReg("digest", (rs.digest || "?").toLowerCase()) +
      cxReg("feeds", (fs.sources_ok != null ? fs.sources_ok + "/" + (fs.sources_ok + (fs.sources_failed || []).length) : "—") +
        (cxAgeDays((act.feeds || {}).last_run) != null ? " · " + cxAgeDays((act.feeds || {}).last_run) + "d" : "")) +
      cxReg("fetch", cxAgeDays((act.fetch || {}).last_run) != null ? cxAgeDays((act.fetch || {}).last_run) + "d" : "—") +
      cxReg("trips", ((D.indicators || {}).trips || []).length, !((D.indicators || {}).trips || []).length) +
      cxReg("pcs armed", pcsOk + "/" + nNames, !pcsOk) +
      cxReg("queued", (QUEUE.queue || []).length, !(QUEUE.queue || []).length) +
      cxReg("digest wk", ((D.digests || [])[0] || {}).week || "—") +
      // Adam's sweep. Rendered ONLY when the digest actually carries it: an absent machine
      // section must read as "not audited", never as a healthy zero. `machineReg` returns ""
      // rather than a register showing 0 findings over nothing examined.
      machineReg() +
      cxReg("stalest", stalest ? stalest + "d" : "—") +
      cxReg("since visit", deltaItems().items.length);
    return h;
  }
  /* Adam's weekly machine sweep, surfaced from the latest digest (CLAUDE.md postlude 1i).
     Two registers, never one: a finding count without the denominator it was found over is
     exactly the unfalsifiable number method section 9 and Rule 21 forbid. When the digest has
     no machine section this renders NOTHING, because "0 findings" over an unaudited machine
     is the false all-clear the audit itself exists to catch. */
  function machineReg() {
    var m = (((D.digests || [])[0] || {}).machine) || null;
    if (!m) return cxReg("machine", "not audited", true);
    var ex = m.examined || {};
    var denom = ex.commands != null ? ex.commands + " cmds" : "";
    return cxReg("machine", (m.findings != null ? m.findings + " findings" : "—")
                 + (denom ? " / " + denom : "")) +
      cxReg("v8 tree", m.v8_reachable === true ? "read" : "unreachable", m.v8_reachable !== true);
  }

  var CX_LTYPE = { RUN: "#8687f0", AMEND: "#e3b23c", RADAR: "#34c77e", "RADAR-DEGRADED": "#e97f4e", DIGEST: "#8687f0", NOTE: "#6a6e86" };
  function cxLedgerRows() {
    return (D.ledger || []).slice().reverse().slice(0, 26).map(function (line) {
      var f = line.split("|").map(function (x) { return x.trim(); });
      var ts = (f[0] || "").slice(11) || (f[0] || "").slice(0, 10);
      var type = f[1] || "NOTE", cmd = f[2] || "", by = "", res = "", art = "";
      for (var i = 3; i < f.length; i++) {
        var s = f[i];
        if (s.indexOf("by:") === 0) by = s.slice(3).trim();
        else if (s.indexOf("result:") === 0) res = s.slice(7).trim();
        else if (s.indexOf("artifact:") === 0) art = s.slice(9).trim();
        else if (res) res += " | " + s;
      }
      return '<div class="cx-ev" title="' + esc(res) + '"><span class="t">' + esc(ts) + '</span> <span class="k" style="color:' +
        (CX_LTYPE[type] || CXP.none) + '">' + esc(type) + "</span>" +
        '<span class="s">' + esc(cmd) + (by ? " · " + esc(by) : "") + "</span>" +
        '<span class="s" style="color:' + (art.indexOf("skipped(") === 0 ? CXP.bad : "#3e4258") + '">' + esc(res.slice(0, 74)) + "</span></div>";
    }).join("");
  }
  /* ---- v6 two-level focus: family census, then one signal's links ranked by heat ----
     A family is a real field on feed items and ambient candidates (validate.py enforces it
     there). It is NOT a field on a signal card, so a signal's family is DERIVED from the
     candidate that promoted into it and is otherwise stated as unfiled. Never inferred from
     the title: an invented tag would be the same defect class as an invented price. */
  var CX_FAMS = ["GEO", "CORPORATE", "TECH", "POLICY", "PHYSICAL"];
  function cxSigFamily(sigId) {
    var cands = (D.candidates || {}).candidates || [];
    for (var i = 0; i < cands.length; i++) {
      if (cands[i].promoted_signal_id === sigId && cands[i].family) return cands[i].family;
    }
    return null;
  }
  function cxNodeFamily(n) {
    if (n.kind === "sig") return cxSigFamily(n.id);
    var cid = n.chainId || (n.kind === "chain" && n.id) || (n.kind === "sig" && n.sigChain) || null;
    if (!cid) return null;
    var c = byId(D.chains, cid);
    return c && c.signal_id ? cxSigFamily(c.signal_id) : null;
  }
  function cxFamilies() {
    var out = {}, i;
    CX_FAMS.forEach(function (f) { out[f] = { fam: f, headlines: 0, cands: 0, sigs: 0 }; });
    // counted over the whole feed store when the build carried those totals, so the
    // chips agree with the HELD number beside them; else over what the page inlines
    var ftot = (D.feeds || {}).families;
    if (ftot) CX_FAMS.forEach(function (f) { if (ftot[f] != null) out[f].headlines = ftot[f]; });
    else ((D.feeds || {}).items || []).forEach(function (it) { if (out[it.f]) out[it.f].headlines++; });
    ((D.candidates || {}).candidates || []).forEach(function (c) {
      if (c.status === "AMBIENT" && out[c.family]) out[c.family].cands++;
    });
    (D.signals || []).forEach(function (s) {
      var f = cxSigFamily(s.id);
      if (f && out[f]) out[f].sigs++;
    });
    return CX_FAMS.map(function (f) { return out[f]; })
      .sort(function (a, b) { return b.headlines - a.headlines; });
  }
  function cxUnfiledSignals() {
    return (D.signals || []).filter(function (s) {
      return s.status !== "DISMISSED" && s.status !== "EXPIRED" && !cxSigFamily(s.id);
    }).length;
  }

  function cxOppRail() {
    var sigs = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; })
      .slice().sort(function (a, b) { return ((b.unmappedness || {}).score || 0) - ((a.unmappedness || {}).score || 0); });
    function moneyOf(sig) {
      var out = null;
      (D.chains || []).forEach(function (c) {
        if (c.signal_id !== sig.id) return;
        (c.links || []).forEach(function (l) { if (l.heat && l.heat.money_corner && !out) out = l.name; });
      });
      return out;
    }
    function row(s, i) {
      var un = (s.unmappedness || {}).score || 0;
      var m = moneyOf(s);
      // a chained signal opens its own ranking; an unchained one can only be flown to
      return '<button class="cxopp' + (CX_MODE.sig === s.id ? " on" : "") + '" ' +
        (s.chain_id ? 'data-cxradial="' + esc(s.id) + '"' : 'data-cxfly="sig:' + esc(s.id) + '"') + ">" +
        '<span class="rk">' + (i + 1) + '</span><span class="nm">' + esc(cxTrim(s.title, 26)) + '</span><span class="un">' + un + "</span>" +
        (m ? '<span class="mc">★ ' + esc(cxTrim(m, 30)) + "</span>" : '<span class="mc dim">' + esc((s.suggested_clock || "").toLowerCase()) + "</span>") +
        "</button>";
    }
    var majors = sigs.filter(function (s) { return ((s.unmappedness || {}).score || 0) >= 80; });
    var mids = sigs.filter(function (s) { var u = (s.unmappedness || {}).score || 0; return u >= 60 && u < 80; });
    var tail = sigs.length - majors.length - mids.length;
    var nC = ((D.candidates || {}).candidates || []).filter(function (x) { return x.status === "AMBIENT"; }).length;
    var nE = ((D.calendar || {}).events || []).filter(function (x) { return x.status === "WATCHING"; }).length;
    var h = '<div class="cx-grp">OPPORTUNITIES · CLICK TO FLY</div>';
    h += '<div class="cx-tierlbl">MAJOR</div>' + (majors.map(row).join("") || '<div class="cx-tiernone">none ≥ 80 unmapped</div>');
    h += '<div class="cx-tierlbl">WATCH</div>' + (mids.map(function (s, i) { return row(s, majors.length + i); }).join("") || '<div class="cx-tiernone">none</div>');
    h += '<div class="cx-tierlbl">LONG TAIL</div><div class="cx-tiernone">' + tail + " smaller signal(s) · " + nC + " candidates · " + nE + " future events · feed dust</div>";
    return h;
  }
  /* ---- v6 phase 2: the radial ranking ----
     The field answers "what is out there"; the radial answers "in what order".
     Both are the same nodes: the morph blends each node from its projected position in
     the 3D field to its seat on the circle, so nothing appears or disappears without
     being watched moving. Every ring, bar and number below is read off the payload:
     a score that is absent stays absent and says so. */
  /* The halo is not decoration: each labelled arc is one family of raw headlines, and the
     count beside it is how many the feed store actually holds for that family. */
  function cxFamilyArcs(ctx, proj3, g, gc, M) {
    var ring = g.ring;
    if (!ring || !ring.arcs) return;
    ctx.save();
    CX_FAMS.forEach(function (f) {
      var arc = ring.arcs[f];
      if (!arc || !arc.n) return;
      var col = CXP.fam[f] || CXP.ink3;
      var dim = CX_FILTER.fam && CX_FILTER.fam !== f;
      ctx.globalAlpha = (1 - M) * (dim ? 0.16 : 0.5);
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      ctx.beginPath();
      var started = false;
      for (var i = 0; i <= 24; i++) {
        var a = arc.a0 + (arc.a1 - arc.a0) * (i / 24);
        var rr = ring.r0 + ring.band + 14;
        var p = proj3(ring.cx + rr * Math.cos(a), ring.cy + rr * Math.sin(a) * 0.92, 0);
        if (!p) { started = false; continue; }
        if (!started) { ctx.moveTo(p.x, p.y); started = true; } else ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
      var am = (arc.a0 + arc.a1) / 2, lr = ring.r0 + ring.band + 42;
      var lp = proj3(ring.cx + lr * Math.cos(am), ring.cy + lr * Math.sin(am) * 0.92, 0);
      if (!lp) return;
      ctx.globalAlpha = (1 - M) * (dim ? 0.3 : 1);
      ctx.font = "8.5px 'JetBrains Mono', monospace";
      ctx.fillStyle = dim ? CXP.ink3 : col;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(f + " · " + arc.n, lp.x, lp.y);
    });
    ctx.restore();
  }
  /* One line that always says exactly what is being shown and how to leave it. */
  function cxStatusLine(ctx, w, h, g) {
    var pair = cxRadialChain();
    var txt;
    if (CX_MODE.phase === "radial" && pair) {
      var fam = cxSigFamily(pair.sig.id);
      txt = "FOCUSED · " + (fam ? fam + " ▸ " : "") + pair.sig.title.toUpperCase() +
        " · " + (pair.chain.links || []).length + " LINKS RANKED BY HEAT · ESC TO CLEAR";
    } else if (CX_MODE.phase === "radial") {
      var ns = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; }).length;
      txt = "RANKED · " + ns + " SIGNALS BY UNMAPPEDNESS · ESC FOR THE FIELD";
    } else if (CX_FILTER.fam) {
      var f = CX_FILTER.fam;
      var shown = 0, links = 0, heads = 0;
      (D.signals || []).forEach(function (s) {
        if (cxSigFamily(s.id) !== f) return;
        shown++;
        var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
        if (c) links += (c.links || []).length;
      });
      var ft = (D.feeds || {}).families;
      if (ft && ft[f] != null) heads = ft[f];
      else ((D.feeds || {}).items || []).forEach(function (it) { if (it.f === f) heads++; });
      txt = "FILTERED · " + f + " · " + shown + " SIGNALS · " + links + " LINKS · " +
        heads + " HEADLINES · ESC TO CLEAR";
    } else {
      var held = ((D.feeds || {}).total != null) ? (D.feeds || {}).total : ((D.feeds || {}).items || []).length;
      txt = "RAW FEED · " + held + " HELD · EACH DOT A HEADLINE · RUN RADAR TO TRIAGE";
    }
    ctx.save();
    ctx.font = "8px 'JetBrains Mono', monospace";
    ctx.fillStyle = "rgba(88,92,114,.85)";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(txt, w - 20, h - 22);
    ctx.restore();
  }

  var CX_MODE = { phase: "network", sig: null, morph: 0 };
  var CX_HEAT_ORDER = { UNDISCOVERED: 0, EMERGING: 1, CROWDED: 2, OVER_CROWDED: 3, QUIET: 4 };
  var CX_FACTORS = [["impact", "IMPACT", "#8687f0"], ["crowdedness", "CROWDEDNESS", "#e97f4e"], ["capture", "CAPTURE", "#34c77e"]];
  // stacking order for the global radial's spoke bars: most investable innermost
  var CX_MIX_ORDER = ["UNDISCOVERED", "EMERGING", "QUIET", "CROWDED", "OVER_CROWDED"];
  // screen-space geometry of the global radial's spoke bars, refreshed every frame the
  // radial draws, so mousemove can hit-test the bars themselves (they are not nodes)
  var CX_RADHIT = null;
  // viewer-tuned render gains: per-type size multipliers plus a contrast exponent that
  // makes big nodes bigger and small ones smaller. Per-viewer convenience only — it
  // never touches the data, and a cleared localStorage just restores the defaults.
  // TIMELINE mode: a flat screen-space layout where a node's distance from the field
  // center is |time from today| and the side is the sign — past left, future right —
  // so one ring is year +k on its right half and year -k on its left half.
  var CX_TL_MODE = false, CX_TL = 0, CX_TL_K0 = null;
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
    var k = kind === "sig" || kind === "chain" ? "sig"
      : kind === "link" ? "link"
      : kind === "scen" || kind === "screen" ? "scen"
      : kind === "co" || kind === "dive" || kind === "shadow" ? "co"
      : kind === "cand" || kind === "evt" ? "intake"
      : kind === "dust" ? "dust" : null;
    return k ? CX_GAIN[k] : 1;
  }
  function cxHeatRank(l) {
    var v = (l.heat || {}).verdict;
    return CX_HEAT_ORDER[v] != null ? CX_HEAT_ORDER[v] : 5;
  }
  function cxScore(l, leg) {
    var h = l.heat || {}, f = h[leg];
    return f && f.score != null ? f.score : null;
  }
  /* Chain-level factor read: the mean of each heat leg over the links that actually
     scored it. A leg no link has scored stays null, and an unchained signal returns
     all nulls — the empty track is the statement, never an invented midpoint. */
  function cxChainMeans(c) {
    var out = { impact: null, crowdedness: null, capture: null };
    if (!c) return out;
    CX_FACTORS.forEach(function (f) {
      var vs = (c.links || []).map(function (l) { return cxScore(l, f[0]); })
        .filter(function (v) { return v != null; });
      if (vs.length) out[f[0]] = vs.reduce(function (a, b) { return a + b; }, 0) / vs.length;
    });
    return out;
  }
  function cxRadialChain() {
    if (!CX_MODE.sig) return null;
    var s = byId(D.signals, CX_MODE.sig);
    if (!s || !s.chain_id) return null;
    var c = byId(D.chains, s.chain_id);
    return c ? { sig: s, chain: c } : null;
  }
  /* Screen-space seats for the current radial. Returns {targets, draw} where draw()
     paints the rings, spokes, meters and labels at the given alpha. */
  function cxRadial(g, w, h, ctx) {
    var pair = cxRadialChain();
    // the circle is sized so its labels fit inside the stage: a name that runs off the
    // edge is worse than a smaller ring, and the read-out is the point of the ranking
    var gutter = Math.min(250, Math.max(150, w * 0.27));
    var cx = w / 2, cy = h * 0.52;
    var R = Math.max(80, Math.min(w * 0.5 - gutter, h * 0.5 - 66));
    var lblMax = Math.max(10, Math.round((gutter - 30) / 6.6));
    var targets = {};
    function seat(key, x, y, r) { targets[key] = { x: x, y: y, r: r }; }

    if (pair) {
      var links = (pair.chain.links || []).slice().sort(function (a, b) {
        var d = cxHeatRank(a) - cxHeatRank(b);
        if (d) return d;
        var ia = cxScore(a, "impact"), ib = cxScore(b, "impact");
        return (ib == null ? -1 : ib) - (ia == null ? -1 : ia);
      });
      var n = links.length || 1;
      var scens = pair.chain.scenarios || [];
      links.forEach(function (l, i) {
        var a = -Math.PI / 2 + i * 2 * Math.PI / n;
        var imp = cxScore(l, "impact");
        // radius reads impact; an unscored link gets the floor size, never an invented one
        var rr = imp == null ? 3.2 : 4 + 12 * Math.pow(Math.max(0, imp - 45) / 50, 1.2);
        l._ca = a;
        seat("link:" + pair.chain.id + "/" + l.id, cx + R * 0.86 * Math.cos(a), cy + R * 0.86 * Math.sin(a), rr);
      });
      scens.forEach(function (s, j) {
        var a2 = [-Math.PI * 0.11, Math.PI * 0.89, -Math.PI * 0.89, Math.PI * 0.11, -Math.PI * 0.5, Math.PI * 0.5][j % 6];
        var p = s.probability_pct;
        seat("scen:" + pair.chain.id + "/" + s.id, cx + R * 0.34 * Math.cos(a2), cy + R * 0.34 * Math.sin(a2),
             p == null ? 2.4 : 2.5 + 4 * p / 50);
        s._ca = a2;
      });
      seat("sig:" + pair.sig.id, cx, cy, 11);
      seat("chain:" + pair.chain.id, cx, cy, 0);
      CX_RADHIT = null;                     // the focused ranking has no spoke bars

      return { targets: targets, focus: pair, draw: function (al) {
        ctx.save(); ctx.globalAlpha = al;
        ctx.setLineDash([2, 6]); ctx.strokeStyle = "rgba(88,92,114,0.38)"; ctx.lineWidth = 1;
        [0.34, 0.86].forEach(function (f) { ctx.beginPath(); ctx.arc(cx, cy, R * f, 0, 7); ctx.stroke(); });
        ctx.setLineDash([]);
        ctx.font = "7.5px 'JetBrains Mono', monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(88,92,114,0.85)";
        ctx.fillText("LINKS · RANKED BY HEAT", cx, cy - R * 0.86 - 24);
        if (scens.length) ctx.fillText("SCENARIOS", cx, cy - R * 0.34 - 12);
        // per-link factor meters, rank numbers and read-out
        links.forEach(function (l, i) {
          var a = l._ca, ca = Math.cos(a), sa = Math.sin(a);
          var t = targets["link:" + pair.chain.id + "/" + l.id];
          var b0 = R * 0.48, b1 = R * 0.78;
          ctx.save(); ctx.translate(cx, cy); ctx.rotate(a); ctx.lineCap = "round";
          CX_FACTORS.forEach(function (f, q) {
            var v = cxScore(l, f[0]), off = (q - 1) * 6;
            ctx.strokeStyle = "#16161f"; ctx.lineWidth = 3;
            ctx.beginPath(); ctx.moveTo(b0, off); ctx.lineTo(b1, off); ctx.stroke();
            if (v == null) return;             // no fill for an unscored leg: the empty track is the statement
            ctx.strokeStyle = f[2];
            ctx.beginPath(); ctx.moveTo(b0, off); ctx.lineTo(b0 + (b1 - b0) * v / 100, off); ctx.stroke();
          });
          ctx.restore();
          if (!t) return;
          ctx.font = "8.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3; ctx.textAlign = "center";
          ctx.fillText((i < 9 ? "0" : "") + (i + 1), cx + R * 0.42 * ca, cy + R * 0.42 * sa - 10);
          var side = ca >= 0 ? 1 : -1, align = side > 0 ? "left" : "right";
          var lx = t.x + (t.r + 13) * side;
          var vd = (l.heat || {}).verdict, col = vd ? CXP.verd[vd] : CXP.none;
          var choke = (l.bottleneck || {}).criticality === "CHOKE_POINT";
          ctx.textAlign = align;
          ctx.font = "600 12px 'Baloo 2', sans-serif"; ctx.fillStyle = CXP.ink;
          ctx.fillText(cxTrim(l.name, lblMax) + ((l.heat || {}).money_corner ? " ★" : ""), lx, t.y - 13);
          ctx.font = "7.5px 'JetBrains Mono', monospace";
          ctx.fillStyle = choke ? CXP.bad : col;
          ctx.fillText((vd ? vd.replace(/_/g, " ") : "UNSCORED") + (choke ? " · CHOKE POINT" : ""), lx, t.y);
          ctx.fillStyle = CXP.ink3; ctx.font = "8px 'JetBrains Mono', monospace";
          ctx.fillText("IMP " + num(cxScore(l, "impact")) + " · CRW " + num(cxScore(l, "crowdedness")) +
                       " · CAP " + num(cxScore(l, "capture")), lx, t.y + 12);
        });
        // scenarios by their real names
        scens.forEach(function (s) {
          var t2 = targets["scen:" + pair.chain.id + "/" + s.id];
          if (!t2) return;
          var ca2 = Math.cos(s._ca), align2 = ca2 >= 0 ? "left" : "right";
          ctx.font = "7.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink2; ctx.textAlign = align2;
          ctx.fillText(cxTrim(s.title, 28).toUpperCase() + " " + num(s.probability_pct, "unscored") +
                       (s.probability_pct == null ? "" : "%"), t2.x + 14 * (ca2 >= 0 ? 1 : -1), t2.y);
        });
        cxRadialCore(ctx, cx, cy, R, pair, links.length, al);
        cxRadialLegend(ctx, w, h, al, "FACTORS · PER LINK", "DOT SIZE — IMPACT");
        ctx.restore();
      } };
    }

    // global radial: every live signal ranked by how unmapped it still is
    var sigs = (D.signals || []).filter(function (s) { return s.status !== "DISMISSED" && s.status !== "EXPIRED"; })
      .slice().sort(function (a, b) {
        var ua = (a.unmappedness || {}).score, ub = (b.unmappedness || {}).score;
        return (ub == null ? -1 : ub) - (ua == null ? -1 : ua);
      });
    var m = sigs.length || 1;
    sigs.forEach(function (s, i) {
      var a = -Math.PI / 2 + (i + 0.5) * 2 * Math.PI / m;
      var un = (s.unmappedness || {}).score;
      s._ca = a;
      seat("sig:" + s.id, cx + R * 0.93 * Math.cos(a), cy + R * 0.93 * Math.sin(a),
           un == null ? 4.5 : 5 + 7 * un / 100);
      var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
      if (!c) return;
      seat("chain:" + c.id, cx + R * 0.72 * Math.cos(a), cy + R * 0.72 * Math.sin(a), 0);
      var ls = (c.links || []).slice().sort(function (p, q) { return cxHeatRank(p) - cxHeatRank(q); });
      var ln = ls.length || 1, spread = (2 * Math.PI / m) * 0.78;
      ls.forEach(function (l, j) {
        var la = a + (j - (ln - 1) / 2) * spread / ln;
        var lr = R * (0.50 + (j % 2) * 0.06);
        seat("link:" + c.id + "/" + l.id, cx + lr * Math.cos(la), cy + lr * Math.sin(la), 3.4);
      });
      (c.scenarios || []).forEach(function (sc, j, arr) {
        var sa2 = a + (j - (arr.length - 1) / 2) * 0.30;
        var sr = R * (j % 2 ? 0.30 : 0.24);
        var p = sc.probability_pct;
        seat("scen:" + c.id + "/" + sc.id, cx + sr * Math.cos(sa2), cy + sr * Math.sin(sa2),
             p == null ? 2.2 : 2.2 + 3.5 * p / 50);
      });
    });
    CX_RADHIT = { cx: cx, cy: cy, r0: R * 0.60, r1: R * 0.84,
                  spokes: sigs.map(function (s) { return { a: s._ca, id: s.id }; }) };
    return { targets: targets, focus: null, draw: function (al) {
      ctx.save(); ctx.globalAlpha = al;
      ctx.setLineDash([2, 6]); ctx.strokeStyle = "rgba(88,92,114,0.38)"; ctx.lineWidth = 1;
      [[0.27, "SCENARIOS"], [0.54, "LINKS"], [0.97, "SIGNALS"]].forEach(function (r) {
        ctx.beginPath(); ctx.arc(cx, cy, R * r[0], 0, 7); ctx.stroke();
      });
      ctx.setLineDash([]);
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      [[0.27, "SCENARIOS"], [0.54, "LINKS"], [0.97, "SIGNALS"]].forEach(function (r) {
        var ly = cy - R * r[0];
        ctx.fillStyle = "rgba(8,8,13,0.85)"; ctx.fillRect(cx - 32, ly - 6, 64, 12);
        ctx.font = "7.5px 'JetBrains Mono', monospace"; ctx.fillStyle = "rgba(88,92,114,0.8)";
        ctx.fillText(r[1], cx, ly);
      });
      sigs.forEach(function (s, i) {
        var a = s._ca, ca = Math.cos(a), sa = Math.sin(a), t = targets["sig:" + s.id];
        var un = (s.unmappedness || {}).score;
        var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
        var hm = cxChainMeans(c);
        // the spoke bar is the chain's link mix by heat verdict, stacked most-investable
        // first: factor MEANS cluster in a 43-77 band and every spoke looked identical,
        // while the verdict mix is what actually differs chain to chain. The dark
        // remainder at the outer end is the unscored share, never an invented segment.
        var b0 = R * 0.60, b1 = R * 0.84;
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(a); ctx.lineCap = "butt";
        ctx.strokeStyle = "#16161f"; ctx.lineWidth = 4.5;
        ctx.beginPath(); ctx.moveTo(b0, 0); ctx.lineTo(b1, 0); ctx.stroke();
        var links2 = c ? (c.links || []) : [];
        if (links2.length) {
          var span = b1 - b0, x0 = b0;
          CX_MIX_ORDER.forEach(function (k) {
            var cnt = links2.filter(function (l) { return (l.heat || {}).verdict === k; }).length;
            if (!cnt) return;
            var seg = span * cnt / links2.length;
            ctx.strokeStyle = CXP.verd[k];
            ctx.beginPath(); ctx.moveTo(x0 + 0.75, 0); ctx.lineTo(x0 + seg - 0.75, 0); ctx.stroke();
            x0 += seg;
          });
        }
        ctx.restore();
        if (!t) return;
        var side = ca >= 0 ? 1 : -1, align = side > 0 ? "left" : "right";
        var lx = t.x + (t.r + 13) * side;
        ctx.textAlign = align; ctx.textBaseline = "middle";
        ctx.font = "600 12.5px 'Baloo 2', sans-serif"; ctx.fillStyle = CXP.ink;
        ctx.fillText(cxTrim(s.title, lblMax), lx, t.y - 13);
        ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
        ctx.fillText((i < 9 ? "0" : "") + (i + 1) + " · UN " + num(un, "unscored") + " · " +
                     (c ? (c.links || []).length + " LINKS" : "UNCHAINED"), lx, t.y);
        // the chain means cluster in a narrow band, so the bars alone read as identical:
        // print the numbers, the same read-out the per-link view carries
        if (hm.impact != null || hm.crowdedness != null || hm.capture != null) {
          ctx.fillText("IMP " + num(hm.impact == null ? null : Math.round(hm.impact)) +
                       " · CRW " + num(hm.crowdedness == null ? null : Math.round(hm.crowdedness)) +
                       " · CAP " + num(hm.capture == null ? null : Math.round(hm.capture)), lx, t.y + 12);
        }
      });
      ctx.strokeStyle = "rgba(134,135,240,0.30)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(cx, cy, 34, 0, 7); ctx.stroke();
      ctx.textAlign = "center";
      ctx.font = "600 14px 'Baloo 2', sans-serif"; ctx.fillStyle = CXP.ink;
      ctx.fillText("UPSTREAM", cx, cy + 56);
      var nl = 0;
      (D.chains || []).forEach(function (c) { nl += (c.links || []).length; });
      ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
      ctx.fillText(sigs.length + " SIGNALS · " + nl + " LINKS · NOW", cx, cy + 72);
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink2;
      ctx.fillText("SPOKE — SHARE OF LINKS BY HEAT VERDICT", 20, h - 40);
      cxRadialLegend(ctx, w, h, al, null, "DOT SIZE — UNMAPPEDNESS");
      ctx.restore();
    } };
  }
  function cxRadialCore(ctx, cx, cy, R, pair, nLinks, al) {
    ctx.strokeStyle = "rgba(134,135,240,0.30)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, 34, 0, 7); ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "600 14px 'Baloo 2', sans-serif"; ctx.fillStyle = CXP.ink;
    ctx.fillText(cxTrim(pair.sig.title, 34), cx, cy + 52);
    var un = (pair.sig.unmappedness || {}).score;
    var fam = cxSigFamily(pair.sig.id);
    ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
    ctx.fillText((fam ? fam + " ▸ " : "") + "UN " + num(un, "unscored") + " · " + nLinks +
                 " LINKS · " + pair.sig.id, cx, cy + 68);
  }
  /* The flat TIMELINE read-out: concentric screen-space rings around the field center,
     one per year of distance from today, with the vertical NOW line through the middle.
     A ring reads as year +k on its right half and year -k on its left half, which is
     the whole trick: distance is |time|, the side is the sign. */
  function cxTimeRings(ctx, w, h, cx, cy, Rmax, al) {
    if (al <= 0.01) return;
    ctx.save(); ctx.globalAlpha = al;
    var y0 = parseInt(TODAY.slice(0, 4), 10);
    // NOW: the vertical axis
    ctx.strokeStyle = "rgba(167,168,246,0.6)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx, cy - Rmax - 18); ctx.lineTo(cx, cy + Rmax + 18); ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "600 11px 'JetBrains Mono', monospace";
    ctx.fillStyle = "rgba(8,8,13,0.85)"; ctx.fillRect(cx - 24, cy - Rmax - 34, 48, 14);
    ctx.fillStyle = "rgba(167,168,246,0.95)"; ctx.fillText("N O W", cx, cy - Rmax - 27);
    ctx.font = "600 10px 'JetBrains Mono', monospace";
    ctx.fillStyle = "rgba(134,135,240,0.7)";
    ctx.fillText("« PAST", cx - Rmax * 0.55, cy + Rmax + 12);
    ctx.fillText("FUTURE »", cx + Rmax * 0.55, cy + Rmax + 12);
    for (var k = 1; k <= 3; k++) {
      var rr = Math.sqrt(Math.min(365 * k, CX_CLAMP) / CX_CLAMP) * Rmax;
      ctx.strokeStyle = "rgba(134,135,240," + (k === 1 ? 0.34 : 0.24) + ")";
      ctx.lineWidth = k === 1 ? 1.6 : 1.2;
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, 7); ctx.stroke();
      // the same ring is a future year on the right and its mirror past year on the left
      ctx.font = "600 10.5px 'JetBrains Mono', monospace";
      [[cx + rr, y0 + k], [cx - rr, y0 - k]].forEach(function (lab) {
        ctx.fillStyle = "rgba(8,8,13,0.85)"; ctx.fillRect(lab[0] - 18, cy - 8, 36, 15);
        ctx.fillStyle = "rgba(167,168,246,0.9)"; ctx.fillText(String(lab[1]), lab[0], cy);
      });
    }
    ctx.textBaseline = "alphabetic";
    ctx.restore();
  }
  function cxRadialLegend(ctx, w, h, al, factorsLabel, sizeLabel) {
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    if (factorsLabel) {
      ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = "#3e4258";
      ctx.fillText(factorsLabel, 20, h - 96);
      CX_FACTORS.forEach(function (f, q) {
        var ly = h - 76 + q * 17;
        ctx.strokeStyle = f[2]; ctx.lineWidth = 3; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(20, ly); ctx.lineTo(42, ly); ctx.stroke();
        ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink2;
        ctx.fillText(f[1], 50, ly + 0.5);
      });
    }
    if (sizeLabel) {
      ctx.font = "8px 'JetBrains Mono', monospace";
      ctx.fillStyle = CXP.ink2; ctx.fillText(sizeLabel, 20, h - 22);
    }
    // verdict legend, bottom right
    var keys = ["UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED", "QUIET"];
    ctx.textAlign = "right";
    ctx.font = "8px 'JetBrains Mono', monospace"; ctx.fillStyle = "#3e4258";
    ctx.fillText("HEAT", w - 20, h - 96);
    keys.forEach(function (k, q) {
      var ly = h - 76 + q * 15;
      ctx.fillStyle = CXP.verd[k];
      ctx.beginPath(); ctx.arc(w - 26, ly, 3.2, 0, 7); ctx.fill();
      ctx.fillStyle = CXP.ink2; ctx.textAlign = "right";
      ctx.fillText(k.replace(/_/g, " "), w - 34, ly + 0.5);
    });
  }

  function cxTopRail() {
    var fams = cxFamilies();
    var h = '<div class="cx-toprail"><div class="cx-famrow">';
    h += '<button class="cx-famchip' + (CX_FILTER.fam ? "" : " on") + '" data-cxfam="">ALL</button>';
    fams.forEach(function (f) {
      var on = CX_FILTER.fam === f.fam;
      h += '<button class="cx-famchip' + (on ? " on" : "") + '" data-cxfam="' + esc(f.fam) + '"' +
        (on ? ' style="border-color:' + (CXP.fam[f.fam] || CXP.ink3) + '"' : "") + '>' +
        '<i class="dot" style="background:' + (CXP.fam[f.fam] || CXP.ink3) + '"></i>' +
        esc(f.fam) + " " + f.headlines + (on ? " ✕" : "") + "</button>";
    });
    h += "</div>" + cxSubRail() + "</div>";
    return h;
  }
  function cxSubRail() {
    var f = CX_FILTER.fam;
    if (!f) return "";
    var sigs = (D.signals || []).filter(function (s) {
      return s.status !== "DISMISSED" && s.status !== "EXPIRED" && cxSigFamily(s.id) === f;
    }).sort(function (a, b) {
      var ua = (a.unmappedness || {}).score, ub = (b.unmappedness || {}).score;
      return (ub == null ? -1 : ub) - (ua == null ? -1 : ua);
    });
    var cands = ((D.candidates || {}).candidates || []).filter(function (c) {
      return c.status === "AMBIENT" && c.family === f;
    });
    var h = '<div class="cx-subrail"><span class="lbl" style="color:' + (CXP.fam[f] || CXP.ink3) + '">IN ' + esc(f) + " · PRIORITIZED</span>";
    if (sigs.length) {
      sigs.forEach(function (s, i) {
        var un = (s.unmappedness || {}).score;
        var chained = !!s.chain_id;
        h += '<button class="cx-subchip' + (CX_MODE.sig === s.id ? " on" : "") + '"' +
          (chained ? ' data-cxradial="' + esc(s.id) + '"' : ' data-cxfly="sig:' + esc(s.id) + '"') +
          ' title="' + esc(s.title) + '">' +
          (i < 9 ? "0" : "") + (i + 1) + " " + esc(cxTrim(s.title, 26).toUpperCase()) +
          " · " + (un == null ? "unscored" : un) + (CX_MODE.sig === s.id ? " ✕" : "") + "</button>";
      });
    } else {
      h += '<span class="cx-subnone">no signal is filed to ' + esc(f) +
        " · a signal takes its family from the candidate it was promoted from, and " +
        cxUnfiledSignals() + " of " + (D.signals || []).length + " are unfiled</span>";
    }
    cands.forEach(function (c) {
      h += '<button class="cx-subchip dim" data-cxfly="cand:' + esc(c.id) + '" title="' + esc(c.title) + '">' +
        esc(cxTrim(c.title, 24).toUpperCase()) + "</button>";
    });
    return h + "</div>";
  }
  function cxPhaseBar() {
    var s = CX_MODE.sig ? byId(D.signals, CX_MODE.sig) : null;
    return '<div class="cx-phase">' +
      '<button class="cx-phbtn' + (CX_MODE.phase === "network" && !CX_TL_MODE ? " on" : "") + '" data-cxphase="network">◉ NETWORK</button>' +
      '<button class="cx-phbtn' + (CX_MODE.phase === "radial" ? " on" : "") + '" data-cxphase="radial"' +
      ' title="every signal ranked by unmappedness; click a chained signal for its own ranking">◎ RADIAL' +
      (s ? " · " + esc(cxTrim(s.title, 22).toUpperCase()) : "") + "</button>" +
      '<button class="cx-phbtn' + (CX_MODE.phase === "network" && CX_TL_MODE ? " on" : "") + '" data-cxphase="timeline"' +
      ' title="flat time rings: distance from center is time from today, past on the left, future on the right">◔ TIMELINE</button></div>';
  }

  function cxFilterRail() {
    var h = '<div class="cx-grp">FILTER <button id="cxFReset" class="cxfreset" title="clear all filters">reset</button></div>';
    h += '<input id="cxSearch" class="cxsearch" type="text" placeholder="search the field…" aria-label="search the field">';
    h += '<div class="cxchips">' + CX_KIND_CHIPS.map(function (kc) {
      return '<button class="cxfchip' + (CX_FILTER.kinds[kc[0]] === false ? " off" : "") + '" data-cxk="' + kc[0] + '">' + kc[1] + "</button>";
    }).join("") + "</div>";
    h += '<div class="cxchips">' + (D.chains || []).map(function (c) {
      return '<button class="cxfchip sel' + (CX_FILTER.chain === c.id ? " on" : "") + '" data-cxchain="' + esc(c.id) + '">' + esc(cxTrim(c.id.replace(/-/g, " "), 18)) + "</button>";
    }).join("") + "</div>";
    return h;
  }
  function cxUIHidden() {
    try { return localStorage.getItem("upstream.cxUI") === "hidden"; } catch (e) { return false; }
  }
  /* The floating chrome clears the topbar by measurement, never by a guessed constant:
     the topbar wraps to roughly twice its height under 760px, and anything pinned to a
     hardcoded 64px disappears underneath it on a phone. */
  var CX_TOP_OBS = null;
  function cxMeasureTop() {
    var tb = document.querySelector(".topbar");
    if (!tb) return;
    document.documentElement.style.setProperty("--cx-top", Math.round(tb.getBoundingClientRect().height) + 12 + "px");
    // the webfonts land after first paint and change that height, so watch the bar
    // itself rather than measuring once and trusting it
    if (window.ResizeObserver) {
      if (CX_TOP_OBS) CX_TOP_OBS.disconnect();
      CX_TOP_OBS = new ResizeObserver(function () {
        var b = document.querySelector(".topbar");
        if (b) document.documentElement.style.setProperty("--cx-top", Math.round(b.getBoundingClientRect().height) + 12 + "px");
      });
      CX_TOP_OBS.observe(tb);
    }
  }
  function cxToggleUI() {
    var full = document.getElementById("cxFull");
    if (!full) return;
    var hid = full.classList.toggle("ui-hidden");
    document.body.classList.toggle("cx-ui-hidden", hid);
    var b = document.getElementById("cxUIBtn");
    if (b) { b.textContent = hid ? "◱ SHOW UI" : "◲ HIDE UI"; b.setAttribute("aria-pressed", hid ? "true" : "false"); }
    try { localStorage.setItem("upstream.cxUI", hid ? "hidden" : "shown"); } catch (e) {}
  }
  function cxGaugeRail() {
    function row(key, label, title) {
      var v = CX_GAIN[key];
      var lo = key === "contrast" ? 0.6 : key === "labels" ? 0 : 0.3;
      var hi2 = key === "contrast" ? 1.8 : key === "labels" ? 1 : 2.5;
      return '<div class="row" title="' + esc(title) + '"><label for="cxg-' + key + '">' + esc(label) + "</label>" +
        '<input type="range" id="cxg-' + key + '" data-cxgain="' + key + '" min="' + lo + '" max="' + hi2 +
        '" step="0.05" value="' + v + '">' +
        '<span class="val" id="cxgv-' + key + '">×' + v.toFixed(2) + "</span></div>";
    }
    return '<div class="cx-gaugerail" id="cxGauges"><div class="lbl">GAUGES · SIZE &amp; VISIBILITY</div>' +
      row("contrast", "CONTRAST", "above 1: the biggest nodes grow, the smallest shrink; below 1 they even out") +
      row("sig", "SIGNALS", "signals and their chain hubs") +
      row("link", "LINKS", "value-chain links") +
      row("scen", "SCENARIOS", "scenarios and screens") +
      row("co", "COMPANIES", "companies, dives, shadow rows") +
      row("intake", "INTAKE", "candidates and calendar events") +
      row("dust", "FEED", "raw feed dust") +
      row("labels", "LABELS", "label density: left shows only the most important names, right shows everything") +
      '<button class="rst" id="cxGainReset" title="back to defaults">RESET</button></div>';
  }
  function cortexView() {
    var hid = cxUIHidden();
    return topbar("cortex") +
      '<div class="cxfull' + (hid ? " ui-hidden" : "") + '" id="cxFull">' +
      '<div class="cx-stage">' +
      '<canvas id="cortexCanvas" tabindex="0" role="img" aria-label="Cortex field"></canvas>' +
      '<div class="cx-brackets" aria-hidden="true"><i></i><i></i><i></i><i></i></div>' +
      cxTopRail() + cxPhaseBar() + cxGaugeRail() +
      "</div>" +
      '<div class="cx-ovl-left">' +
      '<div class="cx-head"><span class="cx-title">CORTEX</span>' +
      '<button class="cxinfo-btn" id="cxInfoBtn" aria-expanded="false" aria-controls="cxInfo" title="what am I looking at?">' +
      '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M1.6 12S5.3 5.2 12 5.2 22.4 12 22.4 12 18.7 18.8 12 18.8 1.6 12 1.6 12Z"/><circle cx="12" cy="12" r="3.1"/></svg>' +
      '<span>How to read this</span></button></div>' +
      '<div class="cx-rail-l">' + cxOppRail() + cxFilterRail() + cxRailLeft() + "</div>" +
      '<div class="cx-note">Analytical outputs from public data · not investment advice</div>' +
      "</div>" +
      '<div class="cxinfo" id="cxInfo" hidden><p>The machine as a constellation. Bright hubs are signals, sized by how unmapped they still are; around each, its value chain, scenarios, names and verdicts; the halo is the raw feed. Depth is time: past sinks away, the future comes toward you, the NOW ring marks today. Drag to orbit the field in 3D, scroll to fly closer (detail appears as you approach), shift-drag to pan, ⟲ ⟳ or Q / E to spin. Hover any dot for its story, click to open it. The ranked opportunities on the left fly you straight to them. H hides the interface.</p></div>' +
      '<div class="cx-strip" aria-live="polite"><span class="cx-s1">CORTEX</span><span class="cx-s2" id="cxCounts"></span><span class="cx-s3" id="cxZoom"></span><span class="cx-read" id="cxRead"></span><button class="cx-rbtn" data-crot="-1" title="rotate left (Q)">⟲</button><button class="cx-rbtn" data-crot="1" title="rotate right (E)">⟳</button><span class="cx-s4">BUILT ' + esc(D.built_at || TODAY) + "</span></div>" +
      '<button class="cx-uibtn" id="cxUIBtn" aria-pressed="' + (hid ? "true" : "false") + '" title="hide or show the interface (H)">' + (hid ? "◱ SHOW UI" : "◲ HIDE UI") + "</button>" +
      "</div>" +
      "<div id='drawerHost'></div>";
  }

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
    if (n.kind === "chain") {
      var money = (o.links || []).filter(function (l) { return l.heat && l.heat.money_corner; });
      return cxDrawerShell(o.title,
        chip(o.clock) + chip(o.status) + staleChip(o.heat_as_of),
        "<p class='small'>" + (o.links || []).length + " links · " + (o.scenarios || []).length + " scenarios" +
        (money.length ? " · ★ money corner: " + esc(money.map(function (l) { return l.name; }).join(", ")) : "") + "</p>" +
        (o.map_limitation ? "<div class='muted small'>" + esc(o.map_limitation) + "</div>" : "") +
        (o.chain_fidelity && o.chain_fidelity !== "FULL"
          ? "<div class='muted small' style='margin-top:8px'>Reduced fidelity on this page — the written analysis is in <span class='mono'>" + esc(chainPath(o)) + "</span>.</div>" : "") +
        '<div class="row" style="margin-top:14px"><a class="chip accent" href="#/chain/' + esc(o.id) + '">Open chain →</a>' +
        '<a class="chip accent" href="#/chain/' + esc(o.id) + '/heat">Heat map →</a>' +
        '<a class="chip accent" href="#/chain/' + esc(o.id) + '/scen">Scenarios →</a></div>');
    }
    if (n.kind === "scen") {
      var ch = byId(D.chains, n.chainId);
      return cxDrawerShell(o.id + " — " + o.title,
        chip(o.status) + chip(o.probability_pct + "%", "accent") + (o.clock ? chip(o.clock) : ""),
        "<p class='small'>" + esc(o.narrative) + "</p>" +
        "<h3>Leading indicators</h3><ul class='bullets'>" + (o.leading_indicators || []).map(function (x) {
          return "<li>" + esc(x.indicator) + (x.armed ? " " + chip("armed", "accent") : "") + "</li>";
        }).join("") + "</ul>" +
        "<div class='small'><b>Invalidation:</b> " + (o.invalidation_signs || []).map(esc).join(" · ") + "</div>" +
        '<div style="margin-top:14px">' + (o.screen_ref
          ? '<a class="chip accent" href="#/screen/' + esc(n.chainId) + "/" + esc(o.id) + '">Open screen →</a>'
          : runButton("run screen " + n.chainId + " " + o.id, "screens stocks for this scenario")) + "</div>");
    }
    if (n.kind === "screen") { location.hash = "#/screen/" + o.chain_id + "/" + o.scenario_id; return ""; }
    if (n.kind === "dive") { location.hash = "#/stock/" + o.ticker + "/" + o.chain_id; return ""; }
    if (n.kind === "shadow") { location.hash = "#/shadow"; return ""; }
    if (n.kind === "socket") { if (n.nav) location.hash = n.nav; return ""; }
    if (n.kind === "co") {
      var t = n.id, where = [], mk = marketFor(t), srow = null;
      (D.chains || []).forEach(function (c) {
        (c.links || []).forEach(function (l) {
          if ((l.example_tickers || []).indexOf(t) > -1) where.push(c.title + " · " + l.name);
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

  /* ---- renderer + interaction ---- */
  /* ---- semantic info + rich hover card ---- */
  function cxInfo(n) {
    var o = n.ref || {};
    if (n.kind === "sig") return "unmapped " + num((o.unmappedness || {}).score, "?") + " · " + (o.evidence || []).length + " ev · " + (o.horizon_years || []).join("–") + "y";
    if (n.kind === "link") { var h = o.heat || {}; return h.verdict ? "i" + num((h.impact || {}).score) + " · c" + num((h.crowdedness || {}).score) + " · v" + num((h.capture || {}).score) : "unscored"; }
    if (n.kind === "scen") return num(o.probability_pct, "?") + "% · " + (o.status || "").toLowerCase();
    if (n.kind === "cand") return (o.family || "") + " · " + (o.date || "");
    if (n.kind === "evt") return (o.kind || "") + " · " + (o.date || "");
    if (n.kind === "dive") return (o.verdict || "").replace("_", " ").toLowerCase() + " · " + (o.clock || "").toLowerCase();
    if (n.kind === "co") return n.tier || "";
    return "";
  }
  function cxCardRow(k, v) { return "<div class='r'><span class='k'>" + esc(k) + "</span><span>" + esc(v) + "</span></div>"; }
  /* The spec card for a spoke bar in the global radial: what each segment is, counted
     from the chain's links on disk — the same arithmetic that sized the segments. */
  function cxSpokeCard(sigId) {
    var s = byId(D.signals, sigId) || {};
    var c = s.chain_id ? byId(D.chains, s.chain_id) : null;
    var links = c ? (c.links || []) : [];
    var h = "<div class='hd'><span class='pip' style='background:" + CXP.accent + "'></span>" +
            "<span class='kind'>spoke bar · link mix by heat verdict</span></div>" +
            "<div class='tt'>" + esc(s.title || sigId) + "</div>";
    if (!links.length) return h + "<div class='bd'>unchained — no links to score yet, so the bar stays an empty track</div>";
    var n = links.length, seen = 0;
    h += "<div class='bd'>each segment is the share of this chain's " + n + " links at that heat verdict, most investable at the center</div>";
    CX_MIX_ORDER.forEach(function (k) {
      var cnt = links.filter(function (l) { return (l.heat || {}).verdict === k; }).length;
      if (!cnt) return;
      seen += cnt;
      h += "<div class='r'><span class='k'><span class='pip' style='background:" + CXP.verd[k] + "'></span> " +
           esc(k.replace(/_/g, " ")) + "</span><span>" + cnt + " of " + n + " · " + Math.round(100 * cnt / n) + "%</span></div>";
    });
    if (seen < n) {
      h += "<div class='r'><span class='k'><span class='pip' style='background:#3a3a4a'></span> UNSCORED</span>" +
           "<span>" + (n - seen) + " of " + n + " · dark remainder</span></div>";
    }
    var hm = cxChainMeans(c);
    if (hm.impact != null || hm.crowdedness != null || hm.capture != null) {
      h += cxCardRow("mean scores", "impact " + num(hm.impact == null ? null : Math.round(hm.impact)) +
                     " · crowded " + num(hm.crowdedness == null ? null : Math.round(hm.crowdedness)) +
                     " · capture " + num(hm.capture == null ? null : Math.round(hm.capture)));
    }
    return h;
  }
  function cxHoverCard(n) {
    var o = n.ref || {}, h = "";
    function head(kind, title, color) {
      return "<div class='hd'><span class='pip' style='background:" + (color || n.color) + "'></span><span class='kind'>" + esc(kind) + "</span></div><div class='tt'>" + esc(title) + "</div>";
    }
    if (n.kind === "sig") {
      var occ = o.occurrence || {};
      h = head("signal · " + (occ.kind || "undated"), o.title) +
        "<div class='bd'>" + esc(cxTrim(o.thesis, 200)) + "</div>" +
        cxCardRow("occurrence", (occ.label ? cxTrim(occ.label, 34) + " · " : "") + (occ.anchor_date || "?")) +
        cxCardRow("unmapped", num((o.unmappedness || {}).score, "?") + " / 100") +
        cxCardRow("evidence", (o.evidence || []).length + " cited · horizon " + (o.horizon_years || []).join("–") + "y") +
        cxCardRow("review by", o.review_by || "—");
    } else if (n.kind === "chain") {
      var money = (o.links || []).filter(function (l) { return l.heat && l.heat.money_corner; });
      h = head("value chain", o.title) +
        cxCardRow("links", (o.links || []).length + " · " + (o.scenarios || []).length + " scenarios") +
        (money.length ? cxCardRow("★ money corner", money.map(function (l) { return l.name; }).join(", ")) : "") +
        cxCardRow("heat as of", o.heat_as_of || "unscored");
    } else if (n.kind === "link") {
      var ht = o.heat || {};
      h = head("chain link" + (ht.money_corner ? " · ★ money corner" : ""), o.name) +
        "<div class='bd'>" + esc(cxTrim(o.role, 160)) + "</div>" +
        (ht.verdict ? cxCardRow("verdict", ht.verdict.replace("_", " ")) +
          cxCardRow("scores", "impact " + num((ht.impact || {}).score) + " · crowded " + num((ht.crowdedness || {}).score) + " · capture " + num((ht.capture || {}).score))
          : cxCardRow("verdict", "unscored")) +
        ((o.bottleneck || {}).criticality === "CHOKE_POINT" ? cxCardRow("bottleneck", "CHOKE POINT") : "") +
        ((o.example_tickers || []).length ? cxCardRow("names", o.example_tickers.slice(0, 5).join(" · ")) : "");
    } else if (n.kind === "scen") {
      h = head("scenario " + o.id, o.title) +
        "<div class='bd'>" + esc(cxTrim(o.narrative, 180)) + "</div>" +
        cxCardRow("probability", num(o.probability_pct, "?") + "% · " + (o.status || "")) +
        cxCardRow("indicators", (o.leading_indicators || []).length + " (" + (o.leading_indicators || []).filter(function (x) { return x.armed; }).length + " armed)");
    } else if (n.kind === "screen") {
      h = head("stock screen", o.id) + cxCardRow("scenario", o.scenario_id || "") + cxCardRow("as of", o.as_of || "");
    } else if (n.kind === "co") {
      h = head("company", n.id + (n.coName ? " — " + n.coName : "")) +
        (n.tier ? cxCardRow("tier", n.tier) : "") +
        cxCardRow("market data", n.hollow ? "none yet" : "fetched") +
        (n.chains && n.chains.length ? cxCardRow("named in", n.chains.join(", ")) : "");
    } else if (n.kind === "dive") {
      h = head("deep dive", o.ticker) +
        cxCardRow("verdict", (o.verdict || "") + " · " + (o.clock || "") + (o.status === "DRAFT" ? " · draft" : "")) +
        (o.entry_zone ? cxCardRow("entry zone", o.entry_zone.low + "–" + o.entry_zone.high) : "") +
        cxCardRow("review by", o.review_by || "—");
    } else if (n.kind === "shadow") {
      h = head("shadow row", o.ticker) + cxCardRow("origin", (o.origin || "").replace(/_/g, " ")) + cxCardRow("reprice at", o.review_at || "");
    } else if (n.kind === "cand") {
      var ca = impactFor(o.id);
      h = head("ambient candidate", o.title) +
        "<div class='bd'>" + esc(cxTrim(o.why, 200)) + "</div>" +
        cxCardRow("family", (o.family || "") + " · " + (o.date || "")) +
        (ca ? cxCardRow("impact", (ca.impact_band || "").toLowerCase() +
          (ca.impact_score != null ? " " + ca.impact_score : "") + " · " + impactTitle(ca)) : "") +
        cxCardRow("source", cxTrim(o.source_name, 44));
    } else if (n.kind === "evt") {
      h = head("known future event", o.title) +
        "<div class='bd'>" + esc(cxTrim(o.why_it_matters, 200)) + "</div>" +
        cxCardRow("when", o.date + (o.window ? " · " + o.window : "")) +
        cxCardRow("source", cxTrim(o.source_name, 44));
    } else if (n.kind === "dust") {
      h = head("raw feed", o.t) + cxCardRow("source", (o.s || "") + " · " + ((o.d || "").slice(0, 10))) + cxCardRow("family", o.f || "?") +
        "<div class='bd dim'>untriaged — the radar sweep judges promotion</div>";
    } else if (n.kind === "socket") {
      h = head("empty stage", n.label) + "<div class='bd dim'>" + esc(n.tip || "") + "</div>";
    }
    return h + "<div class='ft'>click to open</div>";
  }

  /* ---- filters (visual only; layout never moves) ---- */
  var CX_FILTER = { kinds: {}, chain: null, fam: null, q: "" };
  var CX_KIND_CHIPS = [["sig", "signals"], ["chain", "chains"], ["link", "links"], ["scen", "scenarios"], ["co", "names"], ["dive", "verdicts"], ["cand", "ambient"], ["evt", "future"], ["dust", "feed"]];
  function cxChipKind(n) {
    if (n.kind === "screen" || n.kind === "shadow") return "dive";
    return n.kind;
  }
  function cxMatch(n) {
    if (n.kind === "socket") return true;
    var ck = cxChipKind(n);
    if (CX_FILTER.kinds[ck] === false) return false;
    if (CX_FILTER.chain) {
      var ch = n.chainId || (n.kind === "chain" && n.id) || (n.kind === "sig" && n.sigChain) ||
        (n.kind === "screen" && (n.ref || {}).chain_id) || (n.kind === "dive" && (n.ref || {}).chain_id) ||
        (n.kind === "co" && n.chains && n.chains.length === 1 && n.chainIds && n.chainIds[0]);
      if (n.kind === "co") { if (!(n.chainIds || []).length || n.chainIds.indexOf(CX_FILTER.chain) < 0) return false; }
      else if (ch !== CX_FILTER.chain) return false;
    }
    if (CX_FILTER.fam) {
      var f = n.kind === "dust" ? (n.ref || {}).f
        : n.kind === "cand" ? (n.ref || {}).family
        : n.kind === "evt" ? (n.ref || {}).kind
        : cxNodeFamily(n);
      if (f !== CX_FILTER.fam) return false;
    }
    if (CX_FILTER.q) {
      var hay = ((n.label || "") + " " + (n.tip || "") + " " + (n.sub || "")).toLowerCase();
      if (hay.indexOf(CX_FILTER.q) < 0) return false;
    }
    return true;
  }

  /* ---- renderer + interaction (v5: 3D orbit — depth is time; hierarchy tiers) ---- */
  function initCortex() {
    var canvas = document.getElementById("cortexCanvas");
    if (!canvas || canvas.__cxRunning) return;
    canvas.__cxRunning = true;
    var ctx = canvas.getContext("2d");
    var REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var g, sim, cam;
    if (CX_CACHE && CX_CACHE.builtAt === D.built_at) {
      g = CX_CACHE.g; sim = CX_CACHE.sim; cam = CX_CACHE.cam;
    } else {
      g = cxBuild(); sim = cxSim(g); cam = null;
      CX_CACHE = { builtAt: D.built_at, g: g, sim: sim, cam: null };
    }
    var hover = null, focus = null, drag = null, orbit = null, panMove = null, raf = 0, frames = 0;
    var viewTween = null;   // eased yaw/pitch target for the FIELD / TIMELINE view buttons
    var pinned = null, fly = null;
    var userView = !!(CX_CACHE && CX_CACHE.userView), settled = !!(CX_CACHE && CX_CACHE.settled);
    var card = document.getElementById("cxcard");
    if (!card) { card = document.createElement("div"); card.className = "cxcard"; card.id = "cxcard"; card.style.display = "none"; document.body.appendChild(card); }
    var readEl = document.getElementById("cxRead"), zoomEl = document.getElementById("cxZoom"), cntEl = document.getElementById("cxCounts");
    if (cntEl) {
      var nd = g.nodes.filter(function (n) { return n.kind !== "dust"; }).length;
      cntEl.textContent = "OBJECTS " + nd + " · EDGES " + g.edges.length + " · DUST " + (g.nodes.length - nd);
      canvas.setAttribute("aria-label", "Cortex field in 3D: " + nd + " research objects on a past-to-future depth axis; drag to orbit");
    }
    var gc = { x: 0, y: 0 };
    (function () {
      var sx = 0, sy = 0, n0 = 0;
      g.nodes.forEach(function (n) { if (n.kind !== "dust") { sx += n.x; sy += n.y; n0++; } });
      if (n0) { gc.x = sx / n0; gc.y = sy / n0; }
    })();

    var sprites = {}, spriteN = 0;
    function quant(r) { return r <= 8 ? Math.round(r * 2) / 2 : r <= 32 ? Math.round(r) : Math.round(r / 4) * 4; }
    function sprite(color, r, soft) {
      var key = color + "/" + r + "/" + soft;
      if (sprites[key]) return sprites[key];
      if (spriteN > 600) { sprites = {}; spriteN = 0; }
      var s = Math.min(Math.ceil(r * soft), 256), c = document.createElement("canvas");
      c.width = c.height = s * 2;
      var x = c.getContext("2d");
      var gr = x.createRadialGradient(s, s, 0, s, s, s);
      gr.addColorStop(0, color); gr.addColorStop(soft > 3 ? 0.34 : 0.5, color); gr.addColorStop(1, "rgba(0,0,0,0)");
      x.globalAlpha = soft > 3 ? 0.18 : 1;
      x.fillStyle = gr; x.fillRect(0, 0, s * 2, s * 2);
      spriteN++; sprites[key] = { c: c, s: s };
      return sprites[key];
    }

    var T = {}; // per-frame trig + viewport
    function setT(w, h) {
      T.cy = Math.cos(cam.yaw); T.sy = Math.sin(cam.yaw);
      T.cp = Math.cos(cam.pitch); T.sp = Math.sin(cam.pitch);
      T.w = w; T.h = h; T.k = CX_F / cam.dist;
    }
    function proj3(x, y, z) {
      var dx = x - cam.tx, dy = y - cam.ty, dz = (z || 0) - cam.tz;
      var x1 = dx * T.cy + dz * T.sy, z1 = -dx * T.sy + dz * T.cy, y1 = dy;
      var y2 = y1 * T.cp - z1 * T.sp, z2 = y1 * T.sp + z1 * T.cp;
      var d = cam.dist - z2;
      if (d < 60) return null;
      var s = CX_F / d;
      return { x: T.w / 2 + x1 * s, y: T.h / 2 + y2 * s, s: s, d: d };
    }
    function camBasis() {
      return {
        rx: T.cy, ry: 0, rz: -T.sy,
        ux: T.sy * T.sp, uy: T.cp, uz: T.cy * T.sp
      };
    }
    function pick(px, py) {
      var best = null, bd = 1e9;
      for (var i = 0; i < g.nodes.length; i++) {
        var n = g.nodes[i];
        if (n._px == null) continue;
        var dx = n._px - px, dy = n._py - py, d = Math.sqrt(dx * dx + dy * dy);
        if (d < n.r * n._s + 6 && d < bd) { bd = d; best = n; }
      }
      return best;
    }
    /* Frame a whole section (a chain and everything hanging off it) rather than a point:
       a 12-link chain read at the field's default distance is a pile of overlapping names. */
    function flyToFit(ns, lead) {
      var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, minZ = 1e9, maxZ = -1e9, k = 0;
      ns.forEach(function (n) {
        if (!n || n.x == null) return;
        k++;
        if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
        var z = n.z || 0;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
      });
      if (!k || !cam) return;
      var w2 = canvas.clientWidth, h2 = canvas.clientHeight;
      var gw = Math.max(maxX - minX, 120), gh = Math.max(maxY - minY, 100);
      var sc = Math.min((w2 * 0.62) / gw, (h2 * 0.62) / gh);
      fly = {
        t0: Date.now(), ms: 700,
        from: { tx: cam.tx, ty: cam.ty, tz: cam.tz, dist: cam.dist },
        to: { tx: (minX + maxX) / 2, ty: (minY + maxY) / 2, tz: (minZ + maxZ) / 2,
              dist: Math.max(240, Math.min(3600, CX_F / Math.max(0.2, sc))) },
        n: lead || null
      };
      focus = lead || null; pinned = null;
      userView = true; CX_CACHE.userView = true; CX_CACHE.touched = true;
    }
    function camHome() {
      if (!cam) return;
      cam = cxCam(g, canvas.clientWidth, canvas.clientHeight);
      cam.tx = gc.x; cam.ty = gc.y;
      focus = null; pinned = null; userView = false; CX_CACHE.userView = false;
    }
    function flyTo(n, dist) {
      if (!cam) return;
      fly = {
        t0: Date.now(), ms: 650,
        from: { tx: cam.tx, ty: cam.ty, tz: cam.tz, dist: cam.dist },
        to: { tx: n.x, ty: n.y, tz: n.z || 0, dist: dist || Math.max(300, CX_F / 1.7) },
        n: n
      };
      focus = n; pinned = null;
      userView = true; CX_CACHE.userView = true; CX_CACHE.touched = true;
    }

    function draw() {
      if (!canvas.isConnected) { canvas.__cxRunning = false; cancelAnimationFrame(raf); card.style.display = "none"; return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      }
      if (!cam) { cam = cxCam(g, w, h); cam.tx = gc.x; cam.ty = gc.y; }
      CX_CACHE.cam = cam;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var t = Date.now() / 1000;
      var parked = sim.alpha() < sim.alphaMin();
      if (!parked) sim.tick();
      if (parked && !settled) { settled = true; CX_CACHE.settled = true; if (!userView) { cam = cxCam(g, w, h); cam.tx = gc.x; cam.ty = gc.y; } }
      if (fly) {
        // eased by wall clock, but with a per-frame floor: a throttled tab (hidden,
        // battery saver) may render only a handful of frames during fly.ms, and a
        // purely time-based ease then strands the camera mid-flight forever
        var ft = Math.min(1, (Date.now() - fly.t0) / fly.ms);
        fly.fp = (fly.fp || 0) + 1 / 40;
        if (fly.fp > ft) ft = Math.min(1, fly.fp);
        var e = ft < 0.5 ? 2 * ft * ft : 1 - Math.pow(-2 * ft + 2, 2) / 2;
        cam.tx = fly.from.tx + (fly.to.tx - fly.from.tx) * e;
        cam.ty = fly.from.ty + (fly.to.ty - fly.from.ty) * e;
        cam.tz = fly.from.tz + (fly.to.tz - fly.from.tz) * e;
        cam.dist = fly.from.dist + (fly.to.dist - fly.from.dist) * e;
        if (ft >= 1) { pinned = fly.n; fly = null; }
      } else if (!CX_CACHE.touched && !REDUCED && !hover && !focus) {
        cam.yaw += 0.0012; // idle drift until first touch
      }
      if (viewTween) {
        cam.yaw += (viewTween.yaw - cam.yaw) * 0.12;
        cam.pitch += (viewTween.pitch - cam.pitch) * 0.12;
        if (Math.abs(cam.yaw - viewTween.yaw) < 0.004 && Math.abs(cam.pitch - viewTween.pitch) < 0.004) {
          cam.yaw = viewTween.yaw; cam.pitch = viewTween.pitch; viewTween = null;
        }
      }
      setT(w, h);

      // phase morph: nodes travel between the field and the circle, never teleport
      var mTarget = CX_MODE.phase === "radial" ? 1 : 0;
      if (REDUCED) CX_MODE.morph = mTarget;
      else if (CX_MODE.morph !== mTarget) {
        var step = 0.055;
        CX_MODE.morph += Math.sign(mTarget - CX_MODE.morph) * step;
        if (Math.abs(mTarget - CX_MODE.morph) < step) CX_MODE.morph = mTarget;
      }
      var M = CX_MODE.morph, RAD = M > 0.001 ? cxRadial(g, w, h, ctx) : null;
      // timeline morph, same easing pattern as the radial phase
      var tlTarget = CX_TL_MODE && CX_MODE.phase !== "radial" ? 1 : 0;
      if (REDUCED) CX_TL = tlTarget;
      else if (CX_TL !== tlTarget) {
        var tstep = 0.055;
        CX_TL += Math.sign(tlTarget - CX_TL) * tstep;
        if (Math.abs(tlTarget - CX_TL) < tstep) CX_TL = tlTarget;
      }
      var TL = CX_TL;
      // the timeline layout is screen-space, so the camera zoom must scale it by hand:
      // otherwise scrolling only fattens the dots while the layout stays condensed
      if (TL > 0.001 && CX_TL_K0 == null) CX_TL_K0 = T.k;
      if (TL <= 0.001) CX_TL_K0 = null;
      var tlZoom = CX_TL_K0 ? Math.max(0.4, T.k / CX_TL_K0) : 1;
      var tlCx = w / 2, tlCy = h * 0.52, tlR = Math.min(w, h) * 0.44 * tlZoom;

      var bg = ctx.createRadialGradient(w / 2, h / 2, 40, w / 2, h / 2, Math.max(w, h) * 0.72);
      bg.addColorStop(0, CXP.bg1); bg.addColorStop(1, CXP.bg0);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

      // NOW plane (translucent disc at z = 0) + the time axis with year ticks
      // (the 3D read-out; it yields to the flat TIMELINE rings as they morph in)
      ctx.globalAlpha = (1 - M) * (1 - TL);
      if (M < 0.995 && TL < 0.995) {
      var planeR = (g.ring ? g.ring.r0 * 1.02 : 520);
      ctx.strokeStyle = "rgba(134,135,240,0.38)"; ctx.lineWidth = 1.4;
      ctx.beginPath();
      var first = true, topPt = null;
      for (var ai = 0; ai <= 64; ai++) {
        var aa = ai * Math.PI * 2 / 64;
        var pp = proj3(gc.x + planeR * Math.cos(aa), gc.y + planeR * Math.sin(aa), 0);
        if (!pp) { first = true; continue; }
        if (ai === 48) topPt = pp;
        if (first) { ctx.moveTo(pp.x, pp.y); first = false; } else ctx.lineTo(pp.x, pp.y);
      }
      ctx.stroke();
      ctx.setLineDash([2, 8]);
      ctx.beginPath();
      [[-planeR, 0, planeR, 0], [0, -planeR, 0, planeR]].forEach(function (ln) {
        var p1 = proj3(gc.x + ln[0], gc.y + ln[1], 0), p2 = proj3(gc.x + ln[2], gc.y + ln[3], 0);
        if (p1 && p2) { ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); }
      });
      ctx.stroke(); ctx.setLineDash([]);
      ctx.font = "600 11px 'JetBrains Mono', monospace"; ctx.textAlign = "center";
      if (topPt) { ctx.fillStyle = "rgba(167,168,246,0.95)"; ctx.fillText("N O W", topPt.x, topPt.y - 7); }
      // time axis through the center: year rings and ticks along z. This is the depth
      // read-out for the whole field, so it is drawn to be seen, not inferred.
      var y0 = parseInt(TODAY.slice(0, 4), 10);
      ctx.strokeStyle = "rgba(134,135,240,0.65)"; ctx.lineWidth = 2.5;
      var pA = proj3(gc.x, gc.y, -480), pB = proj3(gc.x, gc.y, 480);
      if (pA && pB) { ctx.beginPath(); ctx.moveTo(pA.x, pA.y); ctx.lineTo(pB.x, pB.y); ctx.stroke(); }
      for (var yy = y0 - 3; yy <= y0 + 3; yy++) {
        var dxDays = daysBetween(TODAY, yy + "-01-01");
        if (Math.abs(dxDays) > CX_CLAMP) continue;
        var zz = Math.sqrt(Math.abs(dxDays) / CX_CLAMP) * 480 * (dxDays < 0 ? -1 : 1);
        var tp = proj3(gc.x, gc.y, zz);
        if (!tp) continue;
        // year ticks on the axis only — the in-field year rings looked like clutter
        // and were removed (Ron, 2026-09-01); the TIMELINE phase carries the ring
        // read-out instead
        ctx.fillStyle = "rgba(167,168,246,0.95)";
        ctx.fillRect(tp.x - 2.5, tp.y - 2.5, 5, 5);
        ctx.font = "600 10.5px 'JetBrains Mono', monospace";
        ctx.fillText(String(yy), tp.x, tp.y - 9);
      }
      ctx.fillStyle = "rgba(167,168,246,0.95)"; ctx.font = "600 11px 'JetBrains Mono', monospace";
      if (pA) ctx.fillText("« PAST", pA.x, pA.y + 14);
      if (pB) ctx.fillText("FUTURE »", pB.x, pB.y + 14);
      cxFamilyArcs(ctx, proj3, g, gc, M);
      }
      ctx.globalAlpha = 1;
      if (RAD) RAD.draw(M);
      if (TL > 0.001) cxTimeRings(ctx, w, h, tlCx, tlCy, tlR, TL * (1 - M));

      // project all nodes
      g.nodes.forEach(function (n, ni) {
        var pp = proj3(n.x, n.y, n.z || 0);
        var tg = RAD ? RAD.targets[n.key] : null;
        // TIMELINE seat: radius = |time from today|, side = its sign. The node keeps
        // its layout direction, folded into the correct half so past stays left and
        // future right; one ring is +k years on the right and -k on the left.
        var tls = null;
        if (TL > 0.001 && !RAD) {
          var rt = Math.min(1, Math.abs(n.z || 0) / 480) * tlR;
          var dxL = (n.x - gc.x) || 0.001, dyL = (n.y - gc.y) || 0.001;
          var nL = Math.sqrt(dxL * dxL + dyL * dyL);
          var ux = Math.abs(dxL / nL) * ((n.z || 0) >= 0 ? 1 : -1), uy = dyL / nL;
          tls = { x: tlCx + rt * ux, y: tlCy + rt * uy };
        }
        if (!pp) {
          // Clipped by the near plane. A node holding a seat on the ring still has a
          // place to be: the seat is screen-space and owes nothing to the camera, so a
          // camera drift that carries a node behind the eye must not delete it from the
          // ranking. Without this the idle yaw ate the radial one node at a time.
          if (tg) {
            n._px = tg.x; n._py = tg.y;
            n._s = tg.r / Math.max(0.001, n.r); n._d = cam.dist;
            n._fade = M;                   // arrives with the morph instead of popping
            n._radial = true;
            return;
          }
          if (tls) {                       // a timeline seat also owes nothing to the camera
            n._px = tls.x; n._py = tls.y;
            n._s = T.k; n._d = cam.dist; n._fade = TL; n._radial = false;
            return;
          }
          n._px = null; return;
        }
        n._px = pp.x + (n.kind === "dust" || REDUCED ? 0 : Math.sin(t * 0.4 + ni));
        n._py = pp.y + (n.kind === "dust" || REDUCED ? 0 : Math.cos(t * 0.33 + ni * 2) * 0.8);
        n._s = pp.s; n._d = pp.d; n._fade = 1;
        if (tls) {
          var eT = TL < 0.5 ? 2 * TL * TL : 1 - Math.pow(-2 * TL + 2, 2) / 2;
          n._px += (tls.x - n._px) * eT;
          n._py += (tls.y - n._py) * eT;
          n._d += (cam.dist - n._d) * eT;  // flat read-out: even fog at full morph
          n._radial = false;
        }
        if (!RAD) return;
        if (tg) {
          var e2 = M < 0.5 ? 2 * M * M : 1 - Math.pow(-2 * M + 2, 2) / 2;
          n._px += (tg.x - n._px) * e2;
          n._py += (tg.y - n._py) * e2;
          n._s += (tg.r / Math.max(0.001, n.r) - n._s) * e2;
          // depth travels with the node. Alpha is fogged by _d, and a seat that kept the
          // field's depth kept the field's fog: idle camera drift swung a ring node's
          // world position away, fog hit its 0.3 floor, and the dot faded to nothing
          // while sitting still on the circle. The ring is a flat screen-space read-out,
          // so at full morph every seat is lit the same.
          n._d += (cam.dist - n._d) * e2;
          n._radial = true;
        } else {
          // no seat on the circle: ambient kinds (co/dive/dust/…) settle at a faint floor
          // rather than snapping to the middle or vanishing; structural kinds floor at 0
          var fl = CX_RADIAL_FLOOR[n.kind] || 0;
          n._fade = Math.max(fl, 1 - M);
          n._radial = false;
          if (fl && M > 0.001) {                       // recede in place: shrink to a small backdrop dot
            var e3 = M < 0.5 ? 2 * M * M : 1 - Math.pow(-2 * M + 2, 2) / 2;
            n._s += (n._s * 0.6 - n._s) * e3;
          }
        }
      });

      var hi = hover || focus;
      var hadj = hi ? g.adj[hi.key] : null;
      var anyFilter = CX_FILTER.q || CX_FILTER.chain || CX_FILTER.fam ||
        CX_KIND_CHIPS.some(function (kc) { return CX_FILTER.kinds[kc[0]] === false; });
      function fog(n) { return Math.max(0.3, Math.min(1.15, cam.dist / n._d)); }
      function nodeAlpha(n) {
        var base = n.kind === "dust" ? 0.34 + 0.14 * Math.sin(t * 0.8 + n.seat * 1.7) : n.kind === "co" ? 0.72 : n.kind === "socket" ? 0.32 : 0.92;
        base *= fog(n);
        base *= n._fade == null ? 1 : n._fade;
        var gv = cxGainOf(n.kind);
        if (gv < 1) base *= Math.max(0.08, 0.35 + 0.65 * gv);   // a turned-down type also fades
        if (anyFilter && !cxMatch(n)) base *= 0.07;
        if (!hi) return base;
        return (n === hi || (hadj && hadj[n.key])) ? Math.max(base, anyFilter && !cxMatch(n) ? 0.3 : 0.95) : base * 0.2;
      }

      // edges (under nodes), alpha by mean depth
      var buckets = {};
      g.edges.forEach(function (e) {
        if (e.source._px == null || e.target._px == null) return;
        var near = hi && (e.source === hi || e.target === hi);
        var a = !hi ? e.alpha : near ? Math.min(0.55, e.alpha * 5) : e.alpha * 0.2;
        a *= Math.max(0.3, Math.min(1.1, cam.dist / ((e.source._d + e.target._d) / 2))) * (1 - M);
        if (anyFilter && (!cxMatch(e.source) || !cxMatch(e.target))) a *= 0.12;
        var b = Math.max(1, Math.round(a * 40));
        (buckets[b] = buckets[b] || []).push(e);
      });
      Object.keys(buckets).forEach(function (b) {
        ctx.strokeStyle = "rgba(205,210,235," + (b / 40).toFixed(3) + ")";
        ctx.lineWidth = b / 40 > 0.3 ? 1.15 : 0.7;
        ctx.beginPath();
        buckets[b].forEach(function (e) {
          ctx.moveTo(e.source._px, e.source._py);
          ctx.lineTo(e.target._px, e.target._py);
        });
        ctx.stroke();
      });

      // nodes, painter-sorted far to near
      var order = [];
      g.nodes.forEach(function (n) { if (n._px != null) order.push(n); });
      order.sort(function (a, b) { return b._d - a._d; });
      order.forEach(function (n) {
        var x = n._px, y = n._py;
        if (x < -80 || x > w + 80 || y < -80 || y > h + 80) return;
        // viewer gauges: per-type size gain, then the contrast exponent around a 5px
        // pivot so big nodes grow while small ones shrink (or the reverse under 1)
        var gain = cxGainOf(n.kind);
        var rr = n.r * gain;
        if (CX_GAIN.contrast !== 1) rr = 5 * Math.pow(Math.max(0.1, rr) / 5, CX_GAIN.contrast);
        var R = quant(Math.max((n.kind === "dust" ? 0.9 : 2.2) * Math.min(1, gain), 0.4, rr * n._s));
        var a = nodeAlpha(n);
        ctx.globalAlpha = a;
        if (n.kind === "dust") {
          var sp = sprite(n.color, 1.6, 3);
          ctx.drawImage(sp.c, x - sp.s / 2, y - sp.s / 2, sp.s, sp.s);
        } else {
          if (R > 26) {
            var grd = ctx.createRadialGradient(x, y, 0, x, y, R * 2);
            grd.addColorStop(0, n.color); grd.addColorStop(0.28, n.color); grd.addColorStop(1, "rgba(0,0,0,0)");
            ctx.globalAlpha = a * 0.15; ctx.fillStyle = grd;
            ctx.beginPath(); ctx.arc(x, y, R * 2, 0, 7); ctx.fill();
            ctx.globalAlpha = a;
          } else {
            var soft = n.kind === "sig" || n.kind === "chain" ? 3.4 : 2.8;
            if (n._radial && M > 0.5) soft = 2.8;
            var halo = sprite(n.color, R, soft);
            ctx.drawImage(halo.c, x - halo.s, y - halo.s, halo.s * 2, halo.s * 2);
          }
          if (n.hollow) {
            ctx.strokeStyle = n.color; ctx.lineWidth = 1.2;
            ctx.beginPath(); ctx.arc(x, y, Math.max(2, R * 0.8), 0, 7); ctx.stroke();
          } else if (!n.socket) {
            var core = sprite(n.kind === "sig" || n.kind === "chain" ? "#c9cde0" : n.color, Math.max(1, quant(R * 0.56)), 2.6);
            ctx.drawImage(core.c, x - core.s / 2, y - core.s / 2, core.s, core.s);
          }
          if (n.gold) { ctx.strokeStyle = CXP.gold; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.arc(x, y, R + 3, 0, 7); ctx.stroke(); }
          if (n.choke) { ctx.strokeStyle = CXP.bad; ctx.lineWidth = 1.1; ctx.beginPath(); ctx.arc(x, y, R + 6, 0, 7); ctx.stroke(); }
          if (n.dashed) {
            ctx.strokeStyle = n.color; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.2;
            ctx.beginPath(); ctx.arc(x, y, R + 4, 0, 7); ctx.stroke(); ctx.setLineDash([]);
          }
          if (n.arcs && R > 8) {
            var seg = (Math.PI * 2) / n.arcs.length;
            n.arcs.forEach(function (v, aj) {
              ctx.strokeStyle = v ? CXP.verd[v] : CXP.none; ctx.lineWidth = 2.2;
              ctx.beginPath(); ctx.arc(x, y, R + 5, -Math.PI / 2 + aj * seg + 0.04, -Math.PI / 2 + (aj + 1) * seg - 0.04); ctx.stroke();
            });
          }
          if (n.pinned) { ctx.strokeStyle = CXP.ink3; ctx.lineWidth = 1; ctx.strokeRect(x + R + 4, y - 2, 3, 3); }
          if (n === focus) { ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, R + 8, 0, 7); ctx.stroke(); }
        }
        n._R = R;
      });

      // labels last, near-to-far so close ones win visually; per-node semantic ramp
      ctx.textAlign = "center";
      var dustLabels = 0;
      for (var oi = order.length - 1; oi >= 0; oi--) {
        var n = order[oi];
        if (n._px < -60 || n._px > w + 60 || n._py < -40 || n._py > h + 40) continue;
        var matched = !anyFilter || cxMatch(n);
        var rampN = Math.min(1, Math.max((n._s - 1) / 3.75, 0));
        var ramp2 = Math.min(1, Math.max((n._s - 1.9) / 1.1, 0));
        var lden = typeof CX_GAIN.labels === "number" ? CX_GAIN.labels : 1;
        if (n.kind === "dust") {
          var ramp3 = Math.min(1, Math.max((n._s - 2.6) / 1.2, 0));
          if (n !== hi && lden < 0.85) continue;   // dust headlines are the first to go
          if (ramp3 <= 0.03 || dustLabels > 60 || !matched || (M > 0.35 && n !== hi)) continue;
          if (hi && n !== hi) continue;
          dustLabels++;
          ctx.globalAlpha = ramp3 * 0.55 * (n._fade == null ? 1 : n._fade);
          ctx.font = "9px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
          ctx.fillText(cxTrim((n.ref || {}).t, 30), n._px, n._py + 10);
          continue;
        }
        if (!n.label) continue;
        if (n._radial && M > 0.35) continue;
        // ambient kinds floored back into the radial keep their dot but not their label,
        // unless hovered — the ranking's own labels stay the ones that read
        if (!n._radial && M > 0.35 && n !== hi) continue;
        var major = n.tier === "MAJOR", mid = n.tier === "MID";
        var big = n.kind === "sig" || n.kind === "chain";
        // the LABELS gauge is a priority cut, not a fade: at the left end only the
        // most important names survive, at the right end everything current shows.
        // Hovered nodes and filtered-to links are always named — narrowing the field
        // exists to read what survived.
        if (n !== hi && !(anyFilter && matched && (n.kind === "link" || n.kind === "scen"))) {
          var pri = major ? 1 : big ? 0.85 : mid ? 0.7 : n.kind === "dive" ? 0.6 : n.kind === "co" ? 0.45 : 0.25;
          if (pri < 1 - lden) continue;
        }
        var base = major ? 1 : mid ? 0.9 : big || n.kind === "dive" || n.kind === "socket" ? Math.max(0.55, rampN) : n.kind === "co" ? Math.max(0, rampN - 0.35) : Math.max(0, rampN - 0.1);
        // focus spells the section out: a filtered-to link is named at any zoom, because
        // the point of narrowing the field is to read what survived, not to hunt for it
        if (anyFilter && matched && (n.kind === "link" || n.kind === "scen") && rampN > 0.12) base = 1;
        var la = (n === hi) ? 1 : base * (hi ? ((n === hi || (hadj && hadj[n.key])) ? 1 : 0.2) : 1) * (matched ? 1 : 0.07) * Math.max(0.4, Math.min(1, fog(n)));
        // a node with no seat on the circle takes its label with it as it fades out
        la *= n._fade == null ? 1 : n._fade;
        if (la <= 0.03) continue;
        ctx.globalAlpha = la;
        ctx.font = (major ? "700 14px" : big ? "600 12px" : "500 10.5px") + " 'Baloo 2', 'JetBrains Mono', sans-serif";
        ctx.fillStyle = n === hi ? "#ffffff" : big ? CXP.ink : CXP.ink2;
        ctx.fillText(n.label, n._px, n._py + n._R + (big ? 19 : 13));
        if (n.sub && (big || n === hi || (anyFilter && matched && n.kind === "link"))) {
          ctx.font = "9.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
          ctx.fillText(n.sub, n._px, n._py + n._R + (major ? 35 : 32));
        }
        if ((major || ramp2 > 0.25 || n === hi || (anyFilter && matched && n.kind === "link")) && n.kind !== "socket") {
          var info = cxInfo(n);
          if (info) {
            ctx.globalAlpha = la * (major ? 0.85 : Math.max(0.4, ramp2));
            ctx.font = "9.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
            ctx.fillText(info, n._px, n._py + n._R + (major ? 49 : big ? 45 : 26));
          }
        }
      }
      ctx.globalAlpha = 1;

      if (pinned && pinned._px != null) {
        card.innerHTML = cxHoverCard(pinned);
        card.style.display = "block";
        var r0 = canvas.getBoundingClientRect();
        var lx = r0.left + pinned._px + 22, ly = r0.top + pinned._py - 30;
        if (lx + 310 > innerWidth - 8) lx = r0.left + pinned._px - 322;
        if (ly < 8) ly = 8; if (ly + 260 > innerHeight) ly = innerHeight - 268;
        card.style.left = lx + "px"; card.style.top = ly + "px";
      }
      cxStatusLine(ctx, w, h, g);
      if (zoomEl) zoomEl.textContent = "ZOOM " + T.k.toFixed(2) + "× · YAW " + Math.round(cam.yaw * 180 / Math.PI) % 360 + "°";
      raf = requestAnimationFrame(draw);
    }

    function setRead(n) {
      if (!readEl) return;
      readEl.textContent = n ? (n.kind.toUpperCase() + " · " + (n.tip || n.label || "")) : "";
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
    function showCard(n, cx2, cy2) { placeCard(cxHoverCard(n), cx2, cy2); }
    /* Hit-test the global radial's spoke bars: inside the bar's radial band and within
       a few pixels of the nearest spoke's ray. Bars are drawn geometry, not nodes, so
       pick() cannot see them. */
    function pickSpoke(px2, py2) {
      var H = CX_RADHIT;
      if (!H || CX_MODE.phase !== "radial" || CX_MODE.morph < 0.95) return null;
      var dx = px2 - H.cx, dy = py2 - H.cy, r = Math.sqrt(dx * dx + dy * dy);
      if (r < H.r0 - 7 || r > H.r1 + 7) return null;
      var ang = Math.atan2(dy, dx), best = null, bd = 1e9;
      H.spokes.forEach(function (sp) {
        var d = Math.abs(Math.atan2(Math.sin(ang - sp.a), Math.cos(ang - sp.a)));
        if (d < bd) { bd = d; best = sp; }
      });
      return best && bd * r <= 9 ? best : null;
    }
    function touched() { CX_CACHE.touched = true; userView = true; CX_CACHE.userView = true; }

    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (drag) {
        drag.moved += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0);
        var B = camBasis(), s = drag.n._s || T.k;
        var wx = (B.rx * (e.movementX || 0) + B.ux * (e.movementY || 0)) / s;
        var wy = (B.ry * (e.movementX || 0) + B.uy * (e.movementY || 0)) / s;
        drag.n.fx = (drag.n.fx == null ? drag.n.x : drag.n.fx) + wx;
        drag.n.fy = (drag.n.fy == null ? drag.n.y : drag.n.fy) + wy;
        return;
      }
      if (orbit) {
        cam.yaw = orbit.yaw0 + (e.clientX - orbit.sx) * 0.005;
        cam.pitch = Math.max(-1.4, Math.min(1.4, orbit.pitch0 - (e.clientY - orbit.sy) * 0.005));
        return;
      }
      if (panMove) {
        var B2 = camBasis(), sc = T.k;
        cam.tx = panMove.tx0 - (B2.rx * (e.clientX - panMove.sx) + B2.ux * (e.clientY - panMove.sy)) / sc;
        cam.ty = panMove.ty0 - (B2.ry * (e.clientX - panMove.sx) + B2.uy * (e.clientY - panMove.sy)) / sc;
        cam.tz = panMove.tz0 - (B2.rz * (e.clientX - panMove.sx) + B2.uz * (e.clientY - panMove.sy)) / sc;
        return;
      }
      hover = pick(px, py);
      var spoke = hover ? null : pickSpoke(px, py);
      canvas.style.cursor = hover ? "pointer" : spoke ? "default" : "grab";
      setRead(hover || focus);
      if (hover) { pinned = null; showCard(hover, e.clientX, e.clientY); }
      else if (spoke) { pinned = null; placeCard(cxSpokeCard(spoke.id), e.clientX, e.clientY); }
      else if (!pinned) card.style.display = "none";
    });
    canvas.addEventListener("mousedown", function (e) {
      touched(); pinned = null;
      var r = canvas.getBoundingClientRect();
      var n = pick(e.clientX - r.left, e.clientY - r.top);
      if (n && n.kind !== "dust" && !n.socket && !e.shiftKey) {
        drag = { n: n, t0: Date.now(), moved: 0, alt: e.altKey };
        n.fx = n.x; n.fy = n.y;
        sim.alphaTarget(0.3).alpha(Math.max(sim.alpha(), 0.12));
      } else if (n) {
        drag = { n: n, t0: Date.now(), moved: 0, tapOnly: true };
      } else if (e.shiftKey) {
        panMove = { sx: e.clientX, sy: e.clientY, tx0: cam.tx, ty0: cam.ty, tz0: cam.tz };
        canvas.style.cursor = "move";
      } else {
        orbit = { sx: e.clientX, sy: e.clientY, yaw0: cam.yaw, pitch0: cam.pitch };
        canvas.style.cursor = "grabbing";
      }
    });
    function endDrag() {
      if (drag) {
        var quick = (Date.now() - drag.t0) < 500 && drag.moved < 6;
        var n = drag.n;
        if (!drag.tapOnly) {
          sim.alphaTarget(0);
          if (drag.alt) { n.pinned = true; } else { n.fx = null; n.fy = null; n.pinned = false; }
        }
        if (quick) {
          card.style.display = "none";
          // a chained signal unfolds into its own ranking; everything else opens its record
          if (n.kind === "sig" && (n.ref || {}).chain_id && CX_MODE.phase !== "radial") enterRadial(n.id);
          else cxShowDrawer(cxNodeDrawer(n));
        }
        drag = null;
      }
      orbit = null; panMove = null;
      canvas.style.cursor = "grab";
    }
    canvas.addEventListener("mouseup", endDrag);
    canvas.addEventListener("mouseleave", function () {
      endDrag(); hover = null; if (!pinned) card.style.display = "none"; setRead(focus);
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault(); touched(); pinned = null;
      var dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1);
      cam.dist = Math.max(CX_F / 4, Math.min(3600, cam.dist * Math.pow(1.0015, dy)));
    }, { passive: false });
    canvas.addEventListener("keydown", function (e) {
      var order2 = g.nodes.filter(function (n) { return n.kind !== "dust"; }).sort(function (a, b) {
        return (CX_ORDER[a.kind] - CX_ORDER[b.kind]) || (a.x - b.x);
      });
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault(); touched();
        var idx = focus ? order2.indexOf(focus) : -1;
        idx = (idx + (e.key === "ArrowRight" ? 1 : -1) + order2.length) % order2.length;
        focus = order2[idx]; setRead(focus);
      } else if (e.key === "Enter" || e.key === " ") {
        if (focus) { e.preventDefault(); cxShowDrawer(cxNodeDrawer(focus)); }
      } else if (e.key === "h" || e.key === "H") { e.preventDefault(); cxToggleUI(); }
      else if (e.key === "q" || e.key === "Q") { e.preventDefault(); touched(); cam.yaw -= Math.PI / 30; }
      else if (e.key === "e" || e.key === "E") { e.preventDefault(); touched(); cam.yaw += Math.PI / 30; }
      else if (e.key === "+" || e.key === "=" ) { e.preventDefault(); touched(); cam.dist = Math.max(CX_F / 4, cam.dist * 0.8); }
      else if (e.key === "-") { e.preventDefault(); touched(); cam.dist = Math.min(3600, cam.dist * 1.25); }
      else if (e.key === "0" || e.key === "r" || e.key === "R") {
        e.preventDefault();
        cam = cxCam(g, canvas.clientWidth, canvas.clientHeight); cam.tx = gc.x; cam.ty = gc.y;
        userView = false; CX_CACHE.userView = false; pinned = null; sim.alpha(0.2);
      } else if (e.key === "Escape") {
        focus = null; pinned = null; card.style.display = "none"; setRead(null);
        // one level at a time: the signal radial, then the family, then the bare field
        if (CX_MODE.sig) { CX_MODE.sig = null; CX_MODE.phase = "network"; refreshChrome(); }
        else if (CX_MODE.phase === "radial") { CX_MODE.phase = "network"; refreshChrome(); }
        else if (CX_FILTER.fam) { CX_FILTER.fam = null; refreshChrome(); camHome(); }
      }
    });

    // opportunities register: fly the camera to a ranked node
    document.querySelectorAll("[data-cxfly]").forEach(function (b) {
      b.addEventListener("click", function () {
        var n = g.byKey[b.getAttribute("data-cxfly")];
        if (n) flyTo(n);
      });
    });
    // filter rail wiring (state lives in CX_FILTER; rail re-renders per view)
    function bindRail() {
    document.querySelectorAll("[data-cxk]").forEach(function (b) {
      b.addEventListener("click", function () {
        var k = b.getAttribute("data-cxk");
        CX_FILTER.kinds[k] = CX_FILTER.kinds[k] === false;
        b.classList.toggle("off", CX_FILTER.kinds[k] === false);
      });
    });
    document.querySelectorAll("[data-cxchain]").forEach(function (b) {
      b.addEventListener("click", function () {
        var c = b.getAttribute("data-cxchain");
        CX_FILTER.chain = CX_FILTER.chain === c ? null : c;
        document.querySelectorAll("[data-cxchain]").forEach(function (x) { x.classList.toggle("on", x.getAttribute("data-cxchain") === CX_FILTER.chain); });
        // narrowing to a section is only useful if you can read it: fly in, or fly back out
        if (CX_FILTER.chain) {
          // frame the chain's own structure. Its screened names and dives are seated
          // across the width of the field, and including them frames everything, which
          // is the same as framing nothing.
          var SECT = { sig: 1, chain: 1, link: 1, scen: 1 };
          var sect = g.nodes.filter(function (n2) { return SECT[n2.kind] && cxMatch(n2); });
          flyToFit(sect, g.byKey["chain:" + CX_FILTER.chain]);
        } else camHome();
      });
    });
    function enterRadial(sigId) {
      CX_MODE.sig = CX_MODE.sig === sigId ? null : sigId;
      CX_MODE.phase = CX_MODE.sig ? "radial" : "network";
      focus = null; pinned = null; card.style.display = "none";
      refreshChrome();
    }
    function refreshChrome() {
      var top = document.querySelector(".cx-toprail");
      if (top) top.outerHTML = cxTopRail();
      var opp = document.querySelector(".cx-rail-l");
      if (opp) { opp.innerHTML = cxOppRail() + cxFilterRail() + cxRailLeft(); bindRail(); }
      var ph = document.querySelector(".cx-phase");
      if (ph) ph.outerHTML = cxPhaseBar();
      bindChrome();
    }
    function bindChrome() {
      document.querySelectorAll(".cx-toprail [data-cxfam]").forEach(function (b) {
        b.addEventListener("click", function () {
          var f = b.getAttribute("data-cxfam");
          CX_FILTER.fam = (!f || CX_FILTER.fam === f) ? null : f;
          if (!CX_FILTER.fam) { CX_MODE.sig = null; CX_MODE.phase = "network"; }
          refreshChrome();
          // the click narrows AND focuses: fly to the family's members (signals, their
          // chains, ambient candidates), or back out to the whole field on clear
          if (CX_FILTER.fam) {
            // the click makes a focal point, not just a filter: fly to the family's
            // strongest member. Fitting every member is honest but useless here — a
            // family's signals are laid out by time, so their fit is nearly the whole
            // field and the camera barely moves. Its prioritized #1 IS its focal point;
            // the sub-rail lists the rest. Memberless family: focus its halo arc.
            var focal = null, bestUn = -2;
            g.nodes.forEach(function (n2) {
              if (n2.kind !== "sig" || !cxMatch(n2)) return;
              var u2 = ((n2.ref || {}).unmappedness || {}).score;
              if ((u2 == null ? -1 : u2) > bestUn) { bestUn = u2 == null ? -1 : u2; focal = n2; }
            });
            if (!focal) g.nodes.forEach(function (n2) {
              if (!focal && n2.kind === "cand" && cxMatch(n2)) focal = n2;
            });
            if (focal) flyTo(focal, 1050);
            else {
              var arc = g.ring && g.ring.arcs && g.ring.arcs[CX_FILTER.fam];
              var arcPts = [];
              if (arc) for (var ai = 0; ai <= 6; ai++) {
                var aa = arc.a0 + (arc.a1 - arc.a0) * (ai / 6);
                var rr2 = g.ring.r0 + g.ring.band * 0.5;
                arcPts.push({ x: g.ring.cx + rr2 * Math.cos(aa), y: g.ring.cy + rr2 * Math.sin(aa) * 0.92, z: 0 });
              }
              if (arcPts.length) flyToFit(arcPts, null);
            }
          } else camHome();
        });
      });
      document.querySelectorAll("[data-cxradial]").forEach(function (b) {
        b.addEventListener("click", function () { enterRadial(b.getAttribute("data-cxradial")); });
      });
      document.querySelectorAll("[data-cxphase]").forEach(function (b) {
        b.addEventListener("click", function () {
          if (b.hasAttribute("disabled")) return;
          var ph = b.getAttribute("data-cxphase");
          if (ph === "timeline") {
            // timeline is the network phase in its flat time layout
            touched();                              // stop the idle drift fighting the view
            CX_MODE.phase = "network"; CX_TL_MODE = true; CX_TL_K0 = null;
            viewTween = { yaw: 0, pitch: 0 };       // face-on: the flat rings are the read-out
          } else {
            if (CX_TL_MODE && ph === "network") viewTween = { yaw: -0.45, pitch: -0.22 };
            CX_TL_MODE = false;
            CX_MODE.phase = ph;
          }
          refreshChrome();
        });
      });
      document.querySelectorAll(".cx-toprail [data-cxfly]").forEach(function (b) {
        b.addEventListener("click", function () {
          var n2 = g.byKey[b.getAttribute("data-cxfly")];
          if (n2) { CX_MODE.phase = "network"; refreshChrome(); flyTo(n2); }
        });
      });
    }
    bindChrome();
    // gauge rail: bound once — refreshChrome never rebuilds it, so the sliders keep
    // their positions and these listeners never stack
    function syncGaugeUI() {
      document.querySelectorAll("[data-cxgain]").forEach(function (sl) {
        var k = sl.getAttribute("data-cxgain");
        sl.value = CX_GAIN[k];
        var el = document.getElementById("cxgv-" + k);
        if (el) el.textContent = "×" + CX_GAIN[k].toFixed(2);
      });
    }
    document.querySelectorAll("[data-cxgain]").forEach(function (sl) {
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
      syncGaugeUI(); cxSaveGain();
    });
    var cxReset = document.getElementById("cxFReset");
    if (cxReset) cxReset.addEventListener("click", function () {
      CX_FILTER = { kinds: {}, chain: null, fam: null, q: "" };
      CX_MODE.sig = null; CX_MODE.phase = "network";
      refreshChrome();
      var si0 = document.getElementById("cxSearch"); if (si0) si0.value = "";
      document.querySelectorAll("[data-cxk]").forEach(function (x) { x.classList.remove("off"); });
      document.querySelectorAll("[data-cxchain],[data-cxfam]").forEach(function (x) { x.classList.remove("on"); });
    });
    var si = document.getElementById("cxSearch");
    if (si) {
      si.value = CX_FILTER.q;
      si.addEventListener("input", function () { CX_FILTER.q = si.value.trim().toLowerCase(); });
    }
    }
    bindRail();
    document.querySelectorAll("[data-crot]").forEach(function (b) {
      b.addEventListener("click", function () { touched(); cam.yaw += parseInt(b.getAttribute("data-crot"), 10) * Math.PI / 12; });
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
  /* Notes and changelogs are append-only, so they grow without bound while the page only
     ever shows a tail. app/build.py carries the last method.page.history_rows of each and
     ships the full count beside it; these two print the denominator whenever the page is
     holding fewer rows than the file does. Same rule as the ledger's 60 lines and the
     occurrence log's 900 rows: truncating is fine, reporting the truncated count as the
     total is not. */
  function carriedTail(shown, total, unit, where) {
    if (total == null || total <= shown) return "";
    return "<div class='muted small' style='margin-top:6px'>showing the last " + shown +
      " of " + total + " " + esc(unit) + " · the rest are in " + esc(where) + "</div>";
  }
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
    }).join("") + carriedTail(n.length, obj.notes_total, "notes", objPath(obj))
      : '<div class="muted">None — add one from any session: <span class="mono">note ' + esc(obj.id || obj.ticker || "") + ' "…"</span></div>');
  }
  function changelogBlock(obj) {
    var c = (obj.changelog || []).slice().reverse();
    if (!c.length) return "";
    return seclabel("History") + "<div class='timeline'>" + c.map(function (x) {
      return '<div class="t"><span class="when">' + esc((x.ts || "").slice(0, 10)) + " · " + esc(x.by) + "</span><br>" + esc(x.change) + (x.prior ? " <span class='muted'>(was: " + esc(x.prior) + ")</span>" : "") + "</div>";
    }).join("") + "</div>" + carriedTail(c.length, obj.changelog_total, "history entries", objPath(obj));
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
    if (!weeks.length) return "";
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
      ((TH && TH.total > (TH.rows || []).length)
        ? " This page carries the newest " + esc((TH.rows || []).length) + " of " +
          esc(TH.total) + " rows; the counts above are over the whole store."
        : "") +
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
          (led ? "last seen in the ledger " + esc(led.slice(0, 10)) : "no ledger line names " + esc(a.name) + " in the last 60") +
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

  function notFound(what) {
    return topbar() + "<main><div class='emptystate' style='margin-top:40px'>Not found: " + esc(what) + '<br><br><a href="#/radar">back to radar</a></div>' + footer() + "</main>";
  }

  /* ---------------- router & events ---------------- */
  function route() {
    var h = location.hash || "#/";
    var p = h.replace(/^#\//, "").split("/").map(decodeURIComponent);
    var html;
    if (!p[0]) html = cortexView();
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
    else html = cortexView();
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
