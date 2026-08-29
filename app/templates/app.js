/* Upstream SPA v2 — vanilla JS over window.UPSTREAM_DATA. Hash-routed, no external libs. */
(function () {
  "use strict";
  var D = window.UPSTREAM_DATA || {};
  var app = document.getElementById("app");
  var TODAY = (D.built_at || new Date().toISOString()).slice(0, 10);

  /* ---------------- helpers ---------------- */
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
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
  function verdColor(v) { return { UNDISCOVERED: "var(--und)", EMERGING: "var(--emg)", CROWDED: "var(--crd)", OVER_CROWDED: "var(--ovr)" }[v] || "var(--border-strong)"; }
  function marketFor(t) { return (D.market || {})[String(t).replace(/\./g, "-")] || (D.market || {})[t] || null; }
  function fmtMoney(x) { return typeof x === "number" ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : esc(x); }
  function seclabel(t) { return '<div class="seclabel">' + esc(t) + "</div>"; }

  /* ---------------- click queue (artifact capability) ----------------
     A Run click publishes a new version of this page with the command queued
     in the #upstream-queue block. Any live Claude session watching the
     artifact is notified, executes the command against the repo, and
     republishes the page with the results baked in. Where queueing is
     unavailable (local preview, read-only viewer) the button degrades to a
     copy affordance automatically. */
  var QUEUE = { v: 1, queue: [] };
  try { QUEUE = JSON.parse(document.getElementById("upstream-queue").textContent) || QUEUE; } catch (e) {}
  var QSTATE = { readonly: false, busy: false };
  var SCRIPT_END = "</scr" + "ipt>";
  function canQueue() { return !!(window.claude && typeof window.claude.use === "function") && !QSTATE.readonly; }
  function isQueued(cmd) { return (QUEUE.queue || []).some(function (q) { return q.cmd === cmd; }); }
  function toast(msg, ms) {
    var t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ms || 5600);
  }
  function fetchSelfSource() {
    function ok(r) { return r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status)); }
    return fetch("index.html", { cache: "no-store" }).then(ok)
      .catch(function () { return fetch(location.href.split("#")[0], { cache: "no-store" }).then(ok); })
      .then(function (src) {
        if (src.indexOf('id="upstream-queue"') < 0) throw new Error("queue block not found in source");
        return src;
      });
  }
  function replaceQueueBlock(src, queueObj) {
    var open = src.indexOf('id="upstream-queue"');
    var start = src.indexOf(">", open) + 1;
    var end = src.indexOf(SCRIPT_END, start);
    return src.slice(0, start) + JSON.stringify(queueObj).replace(/<\//g, "<\\/") + src.slice(end);
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
      return fetchSelfSource().then(function (src) {
        var cur = { v: 1, queue: [] };
        try {
          var o = src.indexOf('id="upstream-queue"');
          var s = src.indexOf(">", o) + 1;
          cur = JSON.parse(src.slice(s, src.indexOf(SCRIPT_END, s))) || cur;
        } catch (e) {}
        if ((cur.queue || []).some(function (q) { return q.cmd === cmd; })) {
          QSTATE.busy = false; QUEUE = cur; toast("Already queued: " + cmd); route(); return;
        }
        cur.queue = (cur.queue || []).concat([{ id: "q-" + Date.now(), cmd: cmd, ts: new Date().toISOString() }]);
        try { sessionStorage.setItem("upstream.justQueued", cmd); } catch (e) {}
        return ns.publish(replaceQueueBlock(src, cur)).catch(function (err) {
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
  var RUN_LABELS = [
    [/^run chain /, "Build chain"], [/^run heat /, "Score heat map"], [/^run scenarios /, "Write scenarios"],
    [/^run screen /, "Screen stocks"], [/^run deepdive /, "Run deep dive"], [/^run redteam /, "Red-team it"],
    [/^request data /, "Fetch data"], [/^refresh /, "Update"], [/^run radar/, "Run radar"], [/^run digest/, "Build digest"],
  ];
  function runLabel(cmd) {
    for (var i = 0; i < RUN_LABELS.length; i++) if (RUN_LABELS[i][0].test(cmd)) return RUN_LABELS[i][1];
    return "Run";
  }
  function cmdline(cmd) {
    return '<span class="cmdline"><code>' + esc(cmd) + '</code><button data-copy="' + esc(cmd) + '" title="copy command">copy</button></span>';
  }
  function runButton(cmd, caption, opts) {
    opts = opts || {};
    if (isQueued(cmd)) {
      if (opts.compact) return '<button class="btn-run q" disabled>Queued ✓</button>';
      return '<div class="runwrap"><button class="btn-run q" disabled>Queued ✓</button>' +
        '<span class="cmdline">waiting for a live Claude session · ' + cmdline(cmd) + "</span></div>";
    }
    var btn = canQueue()
      ? '<button class="btn-run" data-run="' + esc(cmd) + '">' + esc(runLabel(cmd)) + "</button>"
      : '<button class="btn-run" data-copyrun="' + esc(cmd) + '">' + esc(runLabel(cmd)) + (opts.compact ? "" : " — copy") + "</button>";
    if (opts.compact) return btn;
    return '<div class="runwrap">' + btn +
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
      (esc_.length ? '<div class="sysline"><span class="healthdot" style="background:var(--crd)"></span>' +
        esc(esc_[0].text) + "</div>" : "");
  }

  function homeView() {
    var sigs = (D.signals || []).slice().sort(function (a, b) {
      return ((b.unmappedness || {}).score || 0) - ((a.unmappedness || {}).score || 0);
    });
    var hs = healthState();
    var sys = (D.ledger || []).slice(-3).reverse();
    return topbar("radar") + "<main>" +
      '<div class="hero">' + todayCard() + funnelCard() + "</div>" +
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
    return topbar("chain") + "<main><div class='pagehead'><h1>Chains</h1></div><div class='siglist'>" +
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
      seclabel("Evidence") + "<div class='card'>" + (evid || "<div class='muted'>none</div>") + "</div>" +
      seclabel("Next step") +
      (s.chain_id ? "<a class='chip accent' href='#/chain/" + esc(s.chain_id) + "'>Open the value chain →</a>" :
        s.status === "NEW" ? runButton("run chain " + s.id, "maps this signal into an 8-15 link value chain") : "<span class='muted'>" + esc(s.status) + "</span>") +
      notesBlock(s) + changelogBlock(s) + footer() + "</main>";
  }

  /* ---------------- chain ---------------- */
  var chainTab = "flow";
  function chainView(id, tab) {
    var c = byId(D.chains, id);
    if (!c) return notFound("chain " + id);
    chainTab = (tab === "heat" || tab === "scen" || tab === "flow") ? tab : "flow";
    var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
    var scored = links.filter(function (l) { return l.heat && l.heat.crowdedness && l.heat.crowdedness.score != null; });
    var money = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    var body = chainTab === "heat" ? heatTab(c, links, scored) : chainTab === "scen" ? scenTab(c) : flowTab(c, links);
    var subtitle = scored.length
      ? (money.length ? "Money corner: " + money.map(function (l) { return l.name; }).join(", ") + ". " : "No link clears all three thresholds yet. ") +
        scored.length + " of " + links.length + " links scored, as of " + (c.heat_as_of || "—") + "."
      : "Chain mapped; heat not scored yet.";
    return topbar("chain") + crumbs([{ label: sigTitle(c.signal_id), href: "#/signal/" + c.signal_id }, { label: c.title }]) + "<main>" +
      '<div class="pagehead"><div class="row">' + chip(c.clock) + (money.length ? '<span class="chip UNDISCOVERED">★ ' + esc(money.map(function (l) { return l.name; }).join(" · ")) + "</span>" : "") + staleChip(c.heat_as_of) + "</div>" +
      "<h1>" + esc(c.title) + "</h1><p class='sub'>" + esc(subtitle) + "</p></div>" +
      chainScreenAction(c) +
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
  function drawer(c, l) {
    var h = l.heat || {};
    function ev(o) { return ((o || {}).evidence || []).map(function (e) { return '<div class="evli">' + chip(e.tag) + " " + esc(e.claim) + (e.source_name ? " <span class='muted'>[" + esc(e.source_name) + "]</span>" : "") + "</div>"; }).join(""); }
    function block(title, o) {
      if (!o) return "";
      return "<h3>" + title + "</h3><div class='small'>" + esc(o.rationale || "") + "</div>" + ev(o);
    }
    return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="' + esc(l.name) + '"><button class="x" data-closedrawer>✕</button>' +
      '<div class="row">' + (h.verdict ? chip(h.verdict.replace("_", " "), h.verdict) : chip("unscored")) + (h.money_corner ? '<span class="chip UNDISCOVERED">★ money corner</span>' : "") + chip((l.investability || "").replace(/_/g, " ")) + chip((l.bottleneck || {}).criticality === "CHOKE_POINT" ? "choke point" : "bottleneck " + ((l.bottleneck || {}).criticality || "").toLowerCase()) + "</div>" +
      "<h2>" + esc(l.name) + "</h2><p class='small'>" + esc(l.role) + "</p>" +
      scoreBar("Impact", h.impact, "--accent") + scoreBar("Crowdedness", h.crowdedness, "--crd") + scoreBar("Value capture", h.capture, "--und") +
      block("Impact", h.impact) + block("Crowdedness", h.crowdedness) + block("Value capture", h.capture) +
      (h.repricing_check && h.repricing_check.note ? "<h3>Repricing check</h3><div class='small'>" + (h.repricing_check.legs_met != null ? "<span class='num'>" + h.repricing_check.legs_met + "/4 legs</span> · " : "") + esc(h.repricing_check.note) + "</div>" : "") +
      "<h3>Feeds</h3><div class='small'>" + ((l.upstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "—") + "</div>" +
      "<h3>Fed by</h3><div class='small'>" + ((l.downstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "—") + "</div>" +
      "<h3>Example names</h3><div class='row'>" + (l.example_tickers || []).map(function (t) { return chip(t, "neutral"); }).join("") + "</div>" +
      (h.verdict ? "" : "<div style='margin-top:16px'>" + runButton("run heat " + c.id, "scores every unscored link with fetched evidence") + "</div>") +
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
      var im = l.heat.impact ? l.heat.impact.score : null, cr = l.heat.crowdedness.score, cp = l.heat.capture ? l.heat.capture.score : 40;
      if (im == null) return;
      var r = 6 + (cp || 0) / 10;
      var nm = shortName(l.name);
      var ly = labelSpot(X(im), [Y(cr) - r - 8, Y(cr) + r + 14, Y(cr) - r - 22, Y(cr) + r + 28, Y(cr) - r - 36], nm);
      s += '<circle cx="' + X(im) + '" cy="' + Y(cr) + '" r="' + r.toFixed(1) + '" fill="' + verdColor(l.heat.verdict) + '" fill-opacity="0.85" stroke="var(--surface)" stroke-width="2"><title>' + esc(l.name) + " — impact " + im + ", crowdedness " + cr + ", capture " + cp + "</title></circle>";
      s += '<text x="' + X(im) + '" y="' + ly + '" text-anchor="middle" font-size="10.5" font-weight="600" fill="var(--ink)">' + esc(nm) + "</text>";
    });
    s += "</svg></div>";
    s += '<div class="legend"><span>dot size = value capture</span>' +
      [["Undiscovered", "--und"], ["Emerging", "--emg"], ["Crowded", "--crd"], ["Over-crowded", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") + "</div></div>";
    var un = links.filter(function (l) { return !l.heat || !l.heat.crowdedness || l.heat.crowdedness.score == null; });
    if (un.length) s += '<div class="muted" style="margin-top:10px">Not scored: ' + un.map(function (l) { return esc(l.name); }).join(", ") + "</div>";
    var mc = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    s += '<div class="callout" style="margin-top:14px">' +
      (mc.length ? "<b>★ " + mc.map(function (l) { return esc(l.name); }).join(" · ") + "</b> clears all three bars (impact ≥ 60, crowdedness ≤ 40, capture ≥ 60). " :
        "<b>No money corner yet</b> — no link clears impact ≥ 60, crowdedness ≤ 40, capture ≥ 60 together. ") +
      "Quiet links with <b>small dots</b> are the trap: ignored because capture is capped, not because the market missed them.</div>";
    return s;
  }
  function scenTab(c) {
    var scens = c.scenarios || [];
    if (!scens.length) return '<div class="emptystate">No scenarios yet.<div class="runwrap">' + runButton("run scenarios " + c.id) + "</div></div>";
    return scens.map(function (s) {
      var mv = (s.links_moved || []).map(function (m) {
        return '<span class="mv"><span class="' + (m.direction === "UP" ? "up" : "down") + '">' + (m.direction === "UP" ? "↑" : "↓") + "</span>" + esc(shortName(linkName(c, m.link_id))) + " · " + esc(m.magnitude.toLowerCase()) + "</span>";
      }).join("");
      var inds = (s.leading_indicators || []).map(function (i) {
        var trip = ((D.indicators || {}).trips || []).filter(function (t) {
          return t.chain === c.id && t.scenario === s.id && t.indicator === i.indicator;
        })[0];
        var trippedAt = i.tripped_at || (trip && trip.tripped_at);
        var badge = trippedAt ? chip("tripped " + trippedAt, "OVER_CROWDED") : i.armed ? chip("armed", "accent") : "";
        return "<li>" + esc(i.indicator) + " <span class='muted'>(" + esc(i.where_to_watch) + ")</span> " + badge + "</li>";
      }).join("");
      return '<div class="card scen"><div class="head"><span class="t">' + esc(s.id) + " — " + esc(s.title) + "</span>" +
        chip(s.status, s.status === "SCREENED" ? "accent" : "neutral") + (s.clock && s.clock !== c.clock ? chip(s.clock) : "") +
        '<span class="p">' + esc(s.probability_pct) + "<small>%</small></span></div>" +
        '<div class="pbar"><i style="width:' + esc(s.probability_pct) + '%"></i></div>' +
        "<div class='small'>" + esc(s.narrative) + "</div>" +
        "<div style='margin:10px 0 2px'>" + mv + "</div>" +
        "<details><summary>Leading indicators & invalidation</summary><div class='body'><ul class='bullets'>" + inds + "</ul>" +
        "<div class='small' style='margin-top:8px'><b>Invalidation:</b> " + (s.invalidation_signs || []).map(esc).join(" · ") + "</div></div></details>" +
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
    return '<div class="chainaction"><div><span class="lbl">Stock opportunities</span>' +
      '<span class="val">not screened yet</span>' +
      '<span class="lbl">finds every name this chain touches, link by link</span></div>' +
      "<div class='row'><span data-stop>" + runButton("run screen " + c.id, null, { compact: true }) + "</span></div></div>";
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
      var sr = mk.series.rows, sn = sr.slice(-90);
      var svals = sn.map(function (r) { return r[1]; });
      var slo = Math.min.apply(null, svals), shi = Math.max.apply(null, svals);
      var rng = (shi - slo) || 1;
      var sp = sn.map(function (r, i) {
        return (i ? "L" : "M") + (i / (sn.length - 1) * 64).toFixed(1) + " " + (20 - (r[1] - slo) / rng * 18).toFixed(1);
      }).join("");
      var lastRow = sr[sr.length - 1];
      var first = sn[0][1], chg = first ? ((lastRow[1] - first) / first) * 100 : 0;
      bigval = '<div class="bigval"><div><span class="v">' + fmtMoney(lastRow[1]) + "</span>" +
        '<svg class="spark" viewBox="0 0 66 22" aria-hidden="true"><path d="' + sp + '" fill="none" stroke="' +
        (chg >= 0 ? "var(--und)" : "var(--ovr)") + '" stroke-width="1.6"/></svg></div>' +
        '<span class="l">last close · ' + esc(lastRow[0]) + " · " + (chg >= 0 ? "+" : "") + chg.toFixed(1) + "% over 90 sessions</span></div>";
    }
    var hero = bigval + '<div class="vhero ' + esc(st.verdict) + '">' +
      '<span class="vword"><span class="dot"></span>' + esc(st.verdict.replace("_", " ")) + "</span>" + zone +
      '<span class="right">' + chip(st.clock) + tierChip(st.tier) + chip(st.status, st.status === "FINAL" ? "accent" : "stale") +
      (pos.length ? chip("in book @ " + pos[pos.length - 1].price, "accent") : "") +
      '<span class="muted">review <span class="num">' + esc(st.review_by) + "</span></span></span></div>";
    var rt = st.red_team
      ? '<div class="card redteam"><div class="rt-label">Red team — attacked ' + esc(st.red_team.attacked_at) + " · " + (st.red_team.verdict_survived ? "verdict survived" : "verdict overturned") + "</div>" +
        (st.red_team.challenges || []).map(function (ch) { return "<div class='small' style='margin:9px 0'><b>" + esc(ch.dimension) + ":</b> " + esc(ch.attack) + " <span class='muted'>→ " + esc(ch.outcome) + "</span></div>"; }).join("") +
        (st.red_team.amendments ? "<div class='small'><b>Amended:</b> " + esc(st.red_team.amendments) + "</div>" : "") +
        "<div class='small' style='margin-top:10px'><b>Surviving bear case:</b> " + esc(st.red_team.surviving_bear_case) + "</div></div>"
      : '<div class="card redteam"><div class="rt-label">Red team</div><div class="small" style="margin-top:8px">This dive is DRAFT — it becomes FINAL only after a fresh-context attack.</div><div style="margin-top:12px">' + runButton("run redteam " + ticker + " " + chainId) + "</div></div>";
    var val = "<div class='card'><h3>Valuation snapshot</h3><div class='kv'>" +
      "<dt>Price</dt><dd class='num'>" + fmtMoney((st.valuation_snapshot.price || {}).value) + " <span class='muted'>[" + esc((st.valuation_snapshot.price || {}).source) + ", " + esc((st.valuation_snapshot.price || {}).as_of) + "]</span></dd>" +
      "<dt>Market cap</dt><dd class='num'>" + esc(((st.valuation_snapshot.market_cap || {}).value) || "—") + "</dd>" +
      (st.valuation_snapshot.lines || []).map(function (l) { return "<dt>" + esc(l.name) + "</dt><dd class='num'>" + esc(l.value) + " <span class='muted'>[" + esc(l.tag) + "]</span></dd>"; }).join("") +
      (st.entry_zone ? "<dt>Entry basis</dt><dd class='small'>" + esc(st.entry_zone.basis) + "</dd>" : "") + "</div></div>";
    var priced = "<div class='card'><h3>What is already priced in</h3>" +
      (st.what_is_priced_in || []).map(function (p) { return "<div class='evli'>" + chip(p.tag) + " " + esc(p.expectation) + "</div>"; }).join("") +
      "<div class='small' style='margin-top:10px'>" + esc(st.priced_in_summary || "") + "</div></div>";
    return topbar("chain") + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: ticker }]) + "<main>" +
      (st.fixture ? '<div class="fixturebanner">Fixture page — synthetic demo data so the UI can be reviewed; deleted when the first real deep dive lands.</div>' : "") +
      '<div class="pagehead"><h1>' + esc(st.ticker) + ' <span style="font-weight:400;font-size:16px;color:var(--ink-3)">' + esc(st.name || "") + "</span></h1></div>" + hero +
      seclabel("Price") + "<div class='card'>" + priceChart(mk, st) + "</div>" +
      seclabel("The case") +
      '<div class="statgrid"><div><h3 style="color:var(--good)">Bull</h3><ul class="bullets good">' + (st.bull || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div>" +
      '<div><h3 style="color:var(--bad)">Bear</h3><ul class="bullets bad">' + (st.bear || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div></div>" +
      seclabel("Diligence") +
      '<div class="statgrid">' + priced + val + "</div>" +
      "<div style='margin-top:14px'>" + rt + "</div>" +
      notesBlock(st) + changelogBlock(st) + footer() + "</main>";
  }
  function priceChart(mk, st) {
    if (!mk || !mk.series || !(mk.series.rows || []).length) {
      return '<div class="emptystate">No price series yet.<div class="runwrap">' + runButton("request data " + st.ticker, "the fetch workflow fills data/market in ~5 minutes") + "</div></div>";
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
      if (t === 4) s += '<text x="' + (P.l + 6) + '" y="' + (Y(v) + 13) + '" font-size="10" class="mono-t" fill="var(--ink-3)">' + v.toFixed(0) + "   price, " + esc((mk.series.currency || "USD")) + "</text>";
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
      (mk.price_status === "DISPUTED" ? " — both prints kept in data/market, never averaged" : "") + "</div>";
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

  /* ---------------- cortex ---------------- */
  /* v3: the whole machine as one field. d3-force (vendored, ISC) lays out every
     research object — signals, chains, links, scenarios, screens, names, dives,
     shadow rows, candidates, known future events — plus a dust halo of raw feed
     headlines. One graph<->screen transform; zoom, pan, drag, neighbour-dim. */
  var CXP = {
    bg0: "#08080d", bg1: "#14141c",
    ink: "#dde0ef", ink2: "#9296ad", ink3: "#585c72",
    accent: "#8687f0",
    verd: { UNDISCOVERED: "#34c77e", EMERGING: "#e3b23c", CROWDED: "#e97f4e", OVER_CROWDED: "#c14a62" },
    dive: { INVESTABLE: "#34c77e", WATCH: "#e3b23c", TOO_LATE: "#c14a62" },
    none: "#6a6e86", gold: "#d4a017", bad: "#e05a72",
    fam: { POLICY: "#e3b23c", CORPORATE: "#8687f0", TECH: "#34c77e", PHYSICAL: "#e97f4e", GEO: "#c14a62", MACRO: "#e3b23c", LEGAL: "#e3b23c" }
  };
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
        kind: "sig", id: sg.id, ref: sg, rSem: 13 + 13 * un / 100, color: CXP.accent,
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
          rSem: 3.0 + ((h.impact && h.impact.score) || 40) / 50,
          color: h.verdict ? CXP.verd[h.verdict] : CXP.none,
          gold: !!h.money_corner, choke: (l.bottleneck || {}).criticality === "CHOKE_POINT",
          label: cxTrim(l.name, 22),
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
          label: s.id, sub: cxTrim(s.title, 26), ty: CX_H * 0.16, tyw: 0.006,
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
    return { nodes: nodes, edges: edges, byKey: byKey, adj: adj, deg: deg, ring: null, builtAt: D.built_at };
  }

  /* ---- simulation (vendored d3-force; we own the clock) ---- */
  function cxRingForce(g) {
    var ns;
    function force(alpha) {
      if (!g.ring) return;
      for (var i = 0; i < ns.length; i++) {
        var n = ns[i];
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
    g.ring = { cx: cx, cy: cy, r0: r0, band: band };
    g.nodes.forEach(function (n) {
      if (n.kind !== "dust") return;
      var ang = n.seat * 2.399963 + 0.13;
      var rad = r0 + band * ((n.seat * 0.6180339) % 1);
      n.sx = cx + rad * Math.cos(ang);
      n.sy = cy + rad * Math.sin(ang) * 0.92;
    });
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
  function cxFit(g, w, h) {
    // frame the research core; the dust halo is allowed to bleed off-canvas
    var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    g.nodes.forEach(function (n) {
      if (n.kind === "dust") return;
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    });
    if (minX > maxX) { minX = 0; maxX = CX_W; minY = 0; maxY = CX_H; }
    var gw = Math.max(maxX - minX, 300), gh = Math.max(maxY - minY, 240);
    var k = Math.max(0.25, Math.min(4, Math.min((w * 0.86) / gw, (h * 0.88) / gh)));
    return { k: k, rot: 0, tx: w / 2 - k * (minX + maxX) / 2, ty: h / 2 - k * (minY + maxY) / 2 };
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
    var vc = { UNDISCOVERED: 0, EMERGING: 0, CROWDED: 0, OVER_CROWDED: 0 }, unscored = 0, money = 0, choke = 0;
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
     ["crowded", vc.CROWDED, "var(--crd)"], ["over-crowded", vc.OVER_CROWDED, "var(--ovr)"]].forEach(function (r) {
      h += cxReg(r[0], r[1]) + cxBar(links.length ? r[1] / links.length : 0, r[2]);
    });
    h += cxReg("unscored", unscored, !unscored) + cxReg("money corner", money) + cxReg("choke point", choke);
    h += '<div class="cx-grp">FEED · ' + items.length + " HELD</div>";
    Object.keys(fam).sort(function (a, b) { return fam[b] - fam[a]; }).forEach(function (f) {
      h += cxReg(f.toLowerCase(), fam[f]) + cxBar(items.length ? fam[f] / items.length : 0, CXP.fam[f] || CXP.ink3);
    });
    h += '<div class="cx-grp">SYSTEM</div>' +
      cxReg("requests", pendingCount() + "P / " + failedCount() + "F", !pendingCount() && !failedCount()) +
      cxReg("radar", (rs.radar || "?").toLowerCase()) + cxReg("digest", (rs.digest || "?").toLowerCase()) +
      cxReg("feeds", (fs.sources_ok != null ? fs.sources_ok + "/" + (fs.sources_ok + (fs.sources_failed || []).length) : "—") +
        (cxAgeDays((act.feeds || {}).last_run) != null ? " · " + cxAgeDays((act.feeds || {}).last_run) + "d" : "")) +
      cxReg("fetch", cxAgeDays((act.fetch || {}).last_run) != null ? cxAgeDays((act.fetch || {}).last_run) + "d" : "—") +
      cxReg("trips", ((D.indicators || {}).trips || []).length, !((D.indicators || {}).trips || []).length) +
      cxReg("pcs armed", pcsOk + "/" + nNames, !pcsOk) +
      cxReg("queued", (QUEUE.queue || []).length, !(QUEUE.queue || []).length) +
      cxReg("digest wk", ((D.digests || [])[0] || {}).week || "—") +
      cxReg("stalest", stalest ? stalest + "d" : "—") +
      cxReg("since visit", deltaItems().items.length);
    return h;
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
  function cxFilterRail() {
    var h = '<div class="cx-grp">FILTER <button id="cxFReset" class="cxfreset" title="clear all filters">reset</button></div>';
    h += '<input id="cxSearch" class="cxsearch" type="text" placeholder="search the field…" aria-label="search the field">';
    h += '<div class="cxchips">' + CX_KIND_CHIPS.map(function (kc) {
      return '<button class="cxfchip' + (CX_FILTER.kinds[kc[0]] === false ? " off" : "") + '" data-cxk="' + kc[0] + '">' + kc[1] + "</button>";
    }).join("") + "</div>";
    h += '<div class="cxchips">' + (D.chains || []).map(function (c) {
      return '<button class="cxfchip sel' + (CX_FILTER.chain === c.id ? " on" : "") + '" data-cxchain="' + esc(c.id) + '">' + esc(cxTrim(c.id.replace(/-/g, " "), 18)) + "</button>";
    }).join("") + "</div>";
    h += '<div class="cxchips">' + ["GEO", "CORPORATE", "TECH", "POLICY", "PHYSICAL"].map(function (f) {
      return '<button class="cxfchip sel' + (CX_FILTER.fam === f ? " on" : "") + '" data-cxfam="' + f + '" style="border-bottom-color:' + (CXP.fam[f] || CXP.ink3) + '">' + f.toLowerCase() + "</button>";
    }).join("") + "</div>";
    return h;
  }
  function cortexView() {
    return topbar("cortex") + "<main>" +
      '<div class="pagehead"><h1>Cortex</h1><p class="sub">The machine as a constellation. Bright hubs are signals, sized by how unmapped they still are; around each, its value chain, scenarios, names and verdicts; the halo is the raw feed. Scroll to zoom — closer in, more detail appears. Hover any dot for its story, click to open it, drag the sky to pan, ⟲ ⟳ (or Q / E) to turn it. Dashed rings are known future events. Filters live on the left.</p></div>' +
      '<div class="card cxpanel"><div class="cxframe">' +
      '<div class="cx-rail-l">' + cxFilterRail() + cxRailLeft() + "</div>" +
      '<div class="cx-stage">' +
      '<canvas id="cortexCanvas" tabindex="0" role="img" aria-label="Cortex field"></canvas>' +
      '<div class="cx-brackets" aria-hidden="true"><i></i><i></i><i></i><i></i></div>' +
      "</div>" +
      '<div class="cx-rail-r"><div class="cx-grp">EVENT LOG</div>' + cxLedgerRows() + "</div>" +
      '<div class="cx-strip" aria-live="polite"><span class="cx-s1">CORTEX</span><span class="cx-s2" id="cxCounts"></span><span class="cx-s3" id="cxZoom"></span><span class="cx-read" id="cxRead"></span><button class="cx-rbtn" data-crot="-1" title="rotate left (Q)">⟲</button><button class="cx-rbtn" data-crot="1" title="rotate right (E)">⟳</button><span class="cx-s4">BUILT ' + esc(D.built_at || TODAY) + "</span></div>" +
      "</div></div>" +
      '<div class="card cx-mobile" style="margin-top:14px">' + cxRailLeft() + "</div>" +
      "<div id='drawerHost'></div>" + footer() + "</main>";
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
    if (n.kind === "link") { var c = byId(D.chains, n.chainId); return c ? drawer(c, o) : ""; }
    if (n.kind === "dust") return cxDustDrawer(o);
    if (n.kind === "chain") {
      var money = (o.links || []).filter(function (l) { return l.heat && l.heat.money_corner; });
      return cxDrawerShell(o.title,
        chip(o.clock) + chip(o.status) + staleChip(o.heat_as_of),
        "<p class='small'>" + (o.links || []).length + " links · " + (o.scenarios || []).length + " scenarios" +
        (money.length ? " · ★ money corner: " + esc(money.map(function (l) { return l.name; }).join(", ")) : "") + "</p>" +
        (o.map_limitation ? "<div class='muted small'>" + esc(o.map_limitation) + "</div>" : "") +
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
    if (n.kind === "sig") return "unmapped " + ((o.unmappedness || {}).score || "?") + " · " + (o.evidence || []).length + " ev · " + (o.horizon_years || []).join("–") + "y";
    if (n.kind === "link") { var h = o.heat || {}; return h.verdict ? "i" + ((h.impact || {}).score || "–") + " · c" + ((h.crowdedness || {}).score || "–") + " · v" + ((h.capture || {}).score || "–") : "unscored"; }
    if (n.kind === "scen") return (o.probability_pct || "?") + "% · " + (o.status || "").toLowerCase();
    if (n.kind === "cand") return (o.family || "") + " · " + (o.date || "");
    if (n.kind === "evt") return (o.kind || "") + " · " + (o.date || "");
    if (n.kind === "dive") return (o.verdict || "").replace("_", " ").toLowerCase() + " · " + (o.clock || "").toLowerCase();
    if (n.kind === "co") return n.tier || "";
    return "";
  }
  function cxCardRow(k, v) { return "<div class='r'><span class='k'>" + esc(k) + "</span><span>" + esc(v) + "</span></div>"; }
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
        cxCardRow("unmapped", ((o.unmappedness || {}).score || "?") + " / 100") +
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
          cxCardRow("scores", "impact " + ((ht.impact || {}).score || "–") + " · crowded " + ((ht.crowdedness || {}).score || "–") + " · capture " + ((ht.capture || {}).score || "–"))
          : cxCardRow("verdict", "unscored")) +
        ((o.bottleneck || {}).criticality === "CHOKE_POINT" ? cxCardRow("bottleneck", "CHOKE POINT") : "") +
        ((o.example_tickers || []).length ? cxCardRow("names", o.example_tickers.slice(0, 5).join(" · ")) : "");
    } else if (n.kind === "scen") {
      h = head("scenario " + o.id, o.title) +
        "<div class='bd'>" + esc(cxTrim(o.narrative, 180)) + "</div>" +
        cxCardRow("probability", (o.probability_pct || "?") + "% · " + (o.status || "")) +
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
      h = head("ambient candidate", o.title) +
        "<div class='bd'>" + esc(cxTrim(o.why, 200)) + "</div>" +
        cxCardRow("family", (o.family || "") + " · " + (o.date || "")) +
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
      var f = n.kind === "dust" ? (n.ref || {}).f : n.kind === "cand" ? (n.ref || {}).family : n.kind === "evt" ? (n.ref || {}).kind : null;
      if (f !== CX_FILTER.fam) return false;
    }
    if (CX_FILTER.q) {
      var hay = ((n.label || "") + " " + (n.tip || "") + " " + (n.sub || "")).toLowerCase();
      if (hay.indexOf(CX_FILTER.q) < 0) return false;
    }
    return true;
  }

  /* ---- renderer + interaction (v4: constellation — rotate, semantic zoom, hover cards, filters) ---- */
  function initCortex() {
    var canvas = document.getElementById("cortexCanvas");
    if (!canvas || canvas.__cxRunning) return;
    canvas.__cxRunning = true;
    var ctx = canvas.getContext("2d");
    var g, sim, view;
    if (CX_CACHE && CX_CACHE.builtAt === D.built_at) {
      g = CX_CACHE.g; sim = CX_CACHE.sim; view = CX_CACHE.view;
    } else {
      g = cxBuild(); sim = cxSim(g); view = null;
      CX_CACHE = { builtAt: D.built_at, g: g, sim: sim, view: null };
    }
    var hover = null, focus = null, drag = null, pan = null, raf = 0, frames = 0;
    var userView = !!(CX_CACHE && CX_CACHE.userView), settled = !!(CX_CACHE && CX_CACHE.settled);
    var card = document.getElementById("cxcard");
    if (!card) { card = document.createElement("div"); card.className = "cxcard"; card.id = "cxcard"; card.style.display = "none"; document.body.appendChild(card); }
    var readEl = document.getElementById("cxRead"), zoomEl = document.getElementById("cxZoom"), cntEl = document.getElementById("cxCounts");
    if (cntEl) {
      var nd = g.nodes.filter(function (n) { return n.kind !== "dust"; }).length;
      cntEl.textContent = "OBJECTS " + nd + " · EDGES " + g.edges.length + " · DUST " + (g.nodes.length - nd);
      canvas.setAttribute("aria-label", "Cortex field: " + nd + " research objects, " + g.edges.length + " links, " + (g.nodes.length - nd) + " feed items");
    }

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
      gr.addColorStop(0, color); gr.addColorStop(soft > 3 ? 0.2 : 0.5, color); gr.addColorStop(1, "rgba(0,0,0,0)");
      x.globalAlpha = soft > 3 ? 0.5 : 1;
      x.fillStyle = gr; x.fillRect(0, 0, s * 2, s * 2);
      spriteN++; sprites[key] = { c: c, s: s };
      return sprites[key];
    }
    // one transform for everything: rotate → scale → translate; labels stay upright
    function SX(x, y) { return (x * view.cr - y * view.sr) * view.k + view.tx; }
    function SY(x, y) { return (x * view.sr + y * view.cr) * view.k + view.ty; }
    function G2(px, py) {
      var dx = (px - view.tx) / view.k, dy = (py - view.ty) / view.k;
      return { x: dx * view.cr + dy * view.sr, y: -dx * view.sr + dy * view.cr };
    }
    function setTrig() { view.cr = Math.cos(view.rot || 0); view.sr = Math.sin(view.rot || 0); }
    function setRot(rot) {
      var w2 = canvas.clientWidth / 2, h2 = canvas.clientHeight / 2;
      var g0 = G2(w2, h2);
      view.rot = rot; setTrig();
      view.tx = w2 - (g0.x * view.cr - g0.y * view.sr) * view.k;
      view.ty = h2 - (g0.x * view.sr + g0.y * view.cr) * view.k;
      userView = true; CX_CACHE.userView = true;
    }
    function pick(px, py) {
      var gp = G2(px, py), best = null, bd = 1e9, slop = 6 / view.k;
      for (var i = 0; i < g.nodes.length; i++) {
        var n = g.nodes[i];
        var dx = n.x - gp.x, dy = n.y - gp.y, d = Math.sqrt(dx * dx + dy * dy);
        if (d < n.r + slop && d < bd) { bd = d; best = n; }
      }
      return best;
    }
    function draw() {
      if (!canvas.isConnected) { canvas.__cxRunning = false; cancelAnimationFrame(raf); card.style.display = "none"; return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
        if (!view) { view = cxFit(g, w, h); setTrig(); }
      }
      if (!view) { view = cxFit(g, w, h); setTrig(); }
      if (view.cr == null) setTrig();
      CX_CACHE.view = view;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var t = Date.now() / 1000;
      var parked = sim.alpha() < sim.alphaMin();
      if (!parked) sim.tick();
      if (parked && !settled) {
        settled = true; CX_CACHE.settled = true;
        if (!userView) { view = cxFit(g, w, h); setTrig(); CX_CACHE.view = view; }
      }
      if (parked && !hover && !drag && (frames++ & 1)) { raf = requestAnimationFrame(draw); return; }

      var bg = ctx.createRadialGradient(w / 2, h / 2, 40, w / 2, h / 2, Math.max(w, h) * 0.72);
      bg.addColorStop(0, CXP.bg1); bg.addColorStop(1, CXP.bg0);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

      var wob = 1.0, ramp = Math.min(1, Math.max((view.k - 1) / 3.75, 0));
      var ramp2 = Math.min(1, Math.max((view.k - 1.9) / 1.1, 0));   // detail lines
      var ramp3 = Math.min(1, Math.max((view.k - 2.6) / 1.2, 0));   // dust headlines
      var hi = hover || focus;
      var hkey = hi ? hi.key : null, hadj = hi ? g.adj[hi.key] : null;
      var anyFilter = CX_FILTER.q || CX_FILTER.chain || CX_FILTER.fam ||
        CX_KIND_CHIPS.some(function (kc) { return CX_FILTER.kinds[kc[0]] === false; });
      function nodeAlpha(n) {
        var base = n.kind === "dust" ? 0.34 + 0.14 * Math.sin(t * 0.8 + n.seat * 1.7) : n.kind === "co" ? 0.72 : n.kind === "socket" ? 0.32 : 0.92;
        if (anyFilter && !cxMatch(n)) base *= 0.07;
        if (!hi) return base;
        return (n === hi || (hadj && hadj[n.key])) ? Math.max(base, 1 * (anyFilter && !cxMatch(n) ? 0.3 : 1)) : base * 0.2;
      }

      // edges, bucketed by alpha
      var buckets = {};
      g.edges.forEach(function (e) {
        var near = hi && (e.source === hi || e.target === hi);
        var a = !hi ? e.alpha : near ? Math.min(0.55, e.alpha * 5) : e.alpha * 0.2;
        if (anyFilter && (!cxMatch(e.source) || !cxMatch(e.target))) a *= 0.12;
        var b = Math.max(1, Math.round(a * 40));
        (buckets[b] = buckets[b] || []).push(e);
      });
      Object.keys(buckets).forEach(function (b) {
        ctx.strokeStyle = "rgba(205,210,235," + (b / 40).toFixed(3) + ")";
        ctx.lineWidth = b / 40 > 0.3 ? 1.15 : 0.7;
        ctx.beginPath();
        buckets[b].forEach(function (e) {
          ctx.moveTo(SX(e.source.x, e.source.y), SY(e.source.x, e.source.y));
          ctx.lineTo(SX(e.target.x, e.target.y), SY(e.target.x, e.target.y));
        });
        ctx.stroke();
      });

      // nodes
      g.nodes.forEach(function (n, ni) {
        var x = SX(n.x, n.y) + (n.kind === "dust" ? 0 : wob * Math.sin(t * 0.4 + ni));
        var y = SY(n.x, n.y) + (n.kind === "dust" ? 0 : wob * Math.cos(t * 0.33 + ni * 2));
        n._px = x; n._py = y;
        if (x < -80 || x > w + 80 || y < -80 || y > h + 80) return;
        var R = quant(Math.max(n.kind === "dust" ? 0.9 : 2.2, n.r * view.k));
        var a = nodeAlpha(n);
        ctx.globalAlpha = a;
        if (n.kind === "dust") {
          var sp = sprite(n.color, 1.6, 3);
          ctx.drawImage(sp.c, x - sp.s / 2, y - sp.s / 2, sp.s, sp.s);
        } else {
          if (R > 26) {
            var grd = ctx.createRadialGradient(x, y, 0, x, y, R * 3);
            grd.addColorStop(0, n.color); grd.addColorStop(0.2, n.color); grd.addColorStop(1, "rgba(0,0,0,0)");
            ctx.globalAlpha = a * 0.5; ctx.fillStyle = grd;
            ctx.beginPath(); ctx.arc(x, y, R * 3, 0, 7); ctx.fill();
            ctx.globalAlpha = a;
          } else {
            var halo = sprite(n.color, R, n.kind === "sig" || n.kind === "chain" ? 6 : 4.2);
            ctx.drawImage(halo.c, x - halo.s, y - halo.s, halo.s * 2, halo.s * 2);
          }
          if (n.hollow) {
            ctx.strokeStyle = n.color; ctx.lineWidth = 1.2;
            ctx.beginPath(); ctx.arc(x, y, Math.max(2, R * 0.8), 0, 7); ctx.stroke();
          } else if (!n.socket) {
            var core = sprite(n.kind === "sig" || n.kind === "chain" ? "#eceefc" : n.color, Math.max(1, quant(R * 0.62)), 2.6);
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
            n.arcs.forEach(function (v, ai) {
              ctx.strokeStyle = v ? CXP.verd[v] : CXP.none; ctx.lineWidth = 2.2;
              ctx.beginPath(); ctx.arc(x, y, R + 5, -Math.PI / 2 + ai * seg + 0.04, -Math.PI / 2 + (ai + 1) * seg - 0.04); ctx.stroke();
            });
          }
          if (n.pinned) { ctx.strokeStyle = CXP.ink3; ctx.lineWidth = 1; ctx.strokeRect(x + R + 4, y - 2, 3, 3); }
          if (n === focus) { ctx.strokeStyle = CXP.accent; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, R + 8, 0, 7); ctx.stroke(); }
        }
        n._R = R;
      });

      // labels last, on top; semantic detail grows with zoom
      ctx.textAlign = "center";
      var dustLabels = 0;
      g.nodes.forEach(function (n) {
        if (n._px == null || n._px < -60 || n._px > w + 60 || n._py < -40 || n._py > h + 40) return;
        var matched = !anyFilter || cxMatch(n);
        if (n.kind === "dust") {
          if (ramp3 <= 0.03 || dustLabels > 70 || !matched) return;
          if (hi && !(n === hi)) return;
          dustLabels++;
          ctx.globalAlpha = ramp3 * 0.55;
          ctx.font = "9px 'JetBrains Mono', monospace";
          ctx.fillStyle = CXP.ink3;
          ctx.fillText(cxTrim((n.ref || {}).t, 30), n._px, n._py + 10);
          return;
        }
        if (!n.label) return;
        var big = n.kind === "sig" || n.kind === "chain";
        var base = big || n.kind === "dive" || n.kind === "socket" ? Math.max(0.85, ramp) : n.kind === "co" ? Math.max(0, ramp - 0.35) : Math.max(0, ramp - 0.1);
        var la = (n === hi) ? 1 : base * (hi ? ((n === hi || (hadj && hadj[n.key])) ? 1 : 0.2) : 1) * (matched ? 1 : 0.07);
        if (la <= 0.03) return;
        ctx.globalAlpha = la;
        ctx.font = (big ? "600 12.5px" : "500 10.5px") + " 'Baloo 2', 'JetBrains Mono', sans-serif";
        ctx.fillStyle = n === hi ? "#ffffff" : big ? CXP.ink : CXP.ink2;
        ctx.fillText(n.label, n._px, n._py + n._R + (big ? 18 : 13));
        if (n.sub && big) {
          ctx.font = "9.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
          ctx.fillText(n.sub, n._px, n._py + n._R + 32);
        }
        if (ramp2 > 0.03 && (n === hi || ramp2 > 0.25)) {
          var info = cxInfo(n);
          if (info) {
            ctx.globalAlpha = la * ramp2;
            ctx.font = "9.5px 'JetBrains Mono', monospace"; ctx.fillStyle = CXP.ink3;
            ctx.fillText(info, n._px, n._py + n._R + (big ? 45 : 26));
          }
        }
      });
      ctx.globalAlpha = 1;
      if (zoomEl) zoomEl.textContent = "ZOOM " + view.k.toFixed(2) + "× · " + Math.round((view.rot || 0) * 180 / Math.PI) + "°";
      raf = requestAnimationFrame(draw);
    }

    function setRead(n) {
      if (!readEl) return;
      readEl.textContent = n ? (n.kind.toUpperCase() + " · " + (n.tip || n.label || "")) : "";
    }
    function showCard(n, cx, cy) {
      card.innerHTML = cxHoverCard(n);
      card.style.display = "block";
      var cw = card.offsetWidth || 300, chh = card.offsetHeight || 120;
      var lx = cx + 16, ly = cy - 12;
      if (lx + cw > innerWidth - 8) lx = cx - cw - 16;
      if (ly + chh > innerHeight - 8) ly = innerHeight - chh - 8;
      if (ly < 8) ly = 8;
      card.style.left = lx + "px"; card.style.top = ly + "px";
    }
    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (drag) {
        drag.moved += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0);
        var gp = G2(px, py);
        drag.n.fx = gp.x; drag.n.fy = gp.y;
        return;
      }
      if (pan) { view.tx = pan.tx0 + (e.clientX - pan.sx); view.ty = pan.ty0 + (e.clientY - pan.sy); userView = true; CX_CACHE.userView = true; return; }
      hover = pick(px, py);
      canvas.style.cursor = hover ? "pointer" : "grab";
      setRead(hover || focus);
      if (hover) showCard(hover, e.clientX, e.clientY);
      else card.style.display = "none";
    });
    canvas.addEventListener("mousedown", function (e) {
      var r = canvas.getBoundingClientRect();
      var n = pick(e.clientX - r.left, e.clientY - r.top);
      if (n && n.kind !== "dust" && !n.socket) {
        drag = { n: n, t0: Date.now(), moved: 0, shift: e.shiftKey };
        n.fx = n.x; n.fy = n.y;
        sim.alphaTarget(0.3).alpha(Math.max(sim.alpha(), 0.12));
      } else if (n) {
        drag = { n: n, t0: Date.now(), moved: 0, tapOnly: true };
      } else {
        pan = { sx: e.clientX, sy: e.clientY, tx0: view.tx, ty0: view.ty };
        canvas.style.cursor = "grabbing";
      }
    });
    function endDrag(e) {
      if (drag) {
        var quick = (Date.now() - drag.t0) < 500 && drag.moved < 6;
        var n = drag.n;
        if (!drag.tapOnly) {
          sim.alphaTarget(0);
          if (drag.shift) { n.pinned = true; } else { n.fx = null; n.fy = null; n.pinned = false; }
        }
        if (quick) { card.style.display = "none"; cxShowDrawer(cxNodeDrawer(n)); }
        drag = null;
      }
      pan = null;
      canvas.style.cursor = "grab";
    }
    canvas.addEventListener("mouseup", endDrag);
    canvas.addEventListener("mouseleave", function (e) {
      endDrag(e); hover = null; card.style.display = "none"; setRead(focus);
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      var dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1);
      var k2 = Math.max(0.25, Math.min(4, view.k * Math.pow(1.0015, -dy)));
      view.tx = px - (px - view.tx) * (k2 / view.k);
      view.ty = py - (py - view.ty) * (k2 / view.k);
      view.k = k2; userView = true; CX_CACHE.userView = true;
    }, { passive: false });
    canvas.addEventListener("keydown", function (e) {
      var order = g.nodes.filter(function (n) { return n.kind !== "dust"; }).sort(function (a, b) {
        return (CX_ORDER[a.kind] - CX_ORDER[b.kind]) || (a.x - b.x);
      });
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        var idx = focus ? order.indexOf(focus) : -1;
        idx = (idx + (e.key === "ArrowRight" ? 1 : -1) + order.length) % order.length;
        focus = order[idx]; setRead(focus);
      } else if (e.key === "Enter" || e.key === " ") {
        if (focus) { e.preventDefault(); cxShowDrawer(cxNodeDrawer(focus)); }
      } else if (e.key === "q" || e.key === "Q") { e.preventDefault(); setRot((view.rot || 0) - Math.PI / 30); }
      else if (e.key === "e" || e.key === "E") { e.preventDefault(); setRot((view.rot || 0) + Math.PI / 30); }
      else if (e.key === "+" || e.key === "=" || e.key === "-") {
        e.preventDefault();
        var kk = Math.max(0.25, Math.min(4, view.k * (e.key === "-" ? 0.8 : 1.25)));
        var cw = canvas.clientWidth / 2, chh2 = canvas.clientHeight / 2;
        view.tx = cw - (cw - view.tx) * (kk / view.k);
        view.ty = chh2 - (chh2 - view.ty) * (kk / view.k);
        view.k = kk; userView = true; CX_CACHE.userView = true;
      } else if (e.key === "0" || e.key === "r" || e.key === "R") {
        e.preventDefault();
        view = cxFit(g, canvas.clientWidth, canvas.clientHeight); setTrig();
        userView = false; CX_CACHE.userView = false; sim.alpha(0.25);
      } else if (e.key === "Escape") { focus = null; setRead(null); }
    });

    // filter rail wiring (rail re-renders with the view; state lives in CX_FILTER)
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
      });
    });
    document.querySelectorAll("[data-cxfam]").forEach(function (b) {
      b.addEventListener("click", function () {
        var f = b.getAttribute("data-cxfam");
        CX_FILTER.fam = CX_FILTER.fam === f ? null : f;
        document.querySelectorAll("[data-cxfam]").forEach(function (x) { x.classList.toggle("on", x.getAttribute("data-cxfam") === CX_FILTER.fam); });
      });
    });
    var cxReset = document.getElementById("cxFReset");
    if (cxReset) cxReset.addEventListener("click", function () {
      CX_FILTER = { kinds: {}, chain: null, fam: null, q: "" };
      var si = document.getElementById("cxSearch"); if (si) si.value = "";
      document.querySelectorAll("[data-cxk]").forEach(function (x) { x.classList.remove("off"); });
      document.querySelectorAll("[data-cxchain],[data-cxfam]").forEach(function (x) { x.classList.remove("on"); });
    });
    var si = document.getElementById("cxSearch");
    if (si) {
      si.value = CX_FILTER.q;
      si.addEventListener("input", function () { CX_FILTER.q = si.value.trim().toLowerCase(); });
    }
    document.querySelectorAll("[data-crot]").forEach(function (b) {
      b.addEventListener("click", function () { setRot((view.rot || 0) + parseInt(b.getAttribute("data-crot"), 10) * Math.PI / 12); });
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
    return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="' + esc(obj.title) + '"><button class="x" data-closedrawer>✕</button>' +
      '<div class="row">' + chip(isEvt ? obj.kind : obj.family) + chip(obj.date, "neutral") + chip(obj.status) + "</div>" +
      "<h2>" + esc(obj.title) + "</h2>" +
      "<p class='small'>" + esc(isEvt ? obj.why_it_matters : obj.why) + "</p>" +
      "<div class='muted small'>[" + esc(obj.source_name) + (obj.source_date ? ", " + esc(obj.source_date) : "") + "]" + (obj.window ? " · " + esc(obj.window) : "") + "</div>" +
      "<div style='margin-top:16px'>" + runButton("run radar", "the radar run judges promotion to a full signal card (method §0.1)") + "</div>" +
      "</div>";
  }

  /* ---------------- shared blocks ---------------- */
  function notesBlock(obj) {
    var n = obj.notes || [];
    return seclabel("Notes") + (n.length ? n.map(function (x) {
      return '<div class="note"><span class="who">' + esc(x.by) + " · " + esc((x.ts || "").slice(0, 10)) + "</span><br>" + esc(x.text) + "</div>";
    }).join("") : '<div class="muted">None — add one from any session: <span class="mono">note ' + esc(obj.id || obj.ticker || "") + ' "…"</span></div>');
  }
  function changelogBlock(obj) {
    var c = (obj.changelog || []).slice().reverse();
    if (!c.length) return "";
    return seclabel("History") + "<div class='timeline'>" + c.map(function (x) {
      return '<div class="t"><span class="when">' + esc((x.ts || "").slice(0, 10)) + " · " + esc(x.by) + "</span><br>" + esc(x.change) + (x.prior ? " <span class='muted'>(was: " + esc(x.prior) + ")</span>" : "") + "</div>";
    }).join("") + "</div>";
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
    else if (p[0] === "cortex") html = cortexView();
    else if (p[0] === "book") html = bookView();
    else if (p[0] === "shadow") html = shadowView();
    else html = cortexView();
    app.innerHTML = html;
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
        host.innerHTML = drawer(c, l);
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

  window.addEventListener("hashchange", route);
  route();
  try {
    var jq = sessionStorage.getItem("upstream.justQueued");
    if (jq) {
      sessionStorage.removeItem("upstream.justQueued");
      toast("Queued: " + jq + " — a live Claude session runs it and this page refreshes with the result.");
    }
  } catch (e) {}
  setTimeout(stampVisit, 4000);
})();
