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
      na("#/", "Radar", "radar") +
      na("#/cortex", "Cortex", "cortex") +
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
    var h = '<div class="crumbs"><a href="#/">Radar</a>';
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
      { n: nSig, l: "Signals", href: "#/" },
      { n: nCh, l: nCh === 1 ? "Chain" : "Chains", href: navHrefChains() },
      { n: nScen, l: "Scenarios", href: one ? "#/chain/" + one.id + "/scen" : "#/" },
      { n: nScr, l: "Screens", href: nScr && one ? "#/screen/" + D.screens[0].chain_id + "/" + D.screens[0].scenario_id : "#/" },
      { n: nVer, l: "Verdicts", href: nVer ? "#/stock/" + D.stocks[0].ticker + "/" + D.stocks[0].chain_id : "#/" },
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
    return '<div class="sigrow" data-nav="#/signal/' + esc(s.id) + '" tabindex="0" role="link" aria-label="' + esc(s.title) + '">' +
      "<div>" +
      '<div class="meta">' + chip(laneName) + chip(s.suggested_clock) +
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
  function screenView(chainId, scenId) {
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
    var hero = '<div class="vhero ' + esc(st.verdict) + '">' +
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
    if (st.entry_zone) s += '<rect x="' + P.l + '" y="' + Y(st.entry_zone.high) + '" width="' + iw + '" height="' + (Y(st.entry_zone.low) - Y(st.entry_zone.high)) + '" fill="var(--band-good)"/><text x="' + (P.l + 8) + '" y="' + (Y(st.entry_zone.high) + 14) + '" font-size="10" font-weight="600" fill="var(--und)">ENTRY ZONE</text>';
    if (st.no_entry_above) s += '<rect x="' + P.l + '" y="' + P.t + '" width="' + iw + '" height="' + Math.max(0, Y(st.no_entry_above) - P.t) + '" fill="var(--band-bad)"/><text x="' + (P.l + 8) + '" y="' + (P.t + 14) + '" font-size="10" font-weight="600" fill="var(--ovr)">NO ENTRY</text>';
    for (var t = 0; t <= 4; t++) {
      var v = lo + ((hi - lo) * t) / 4;
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)"/>';
      s += '<text x="' + (P.l - 8) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t" fill="var(--chart-axis)">' + v.toFixed(0) + "</text>";
    }
    var lbl = Math.max(1, Math.floor(pts.length / 6));
    pts.forEach(function (r, i) { if (i % lbl === 0 && i < pts.length - 3) s += '<text x="' + X(i) + '" y="' + (H - P.b + 16) + '" text-anchor="middle" font-size="9.5" class="mono-t" fill="var(--chart-axis)">' + esc(r[0].slice(0, 7)) + "</text>"; });
    (st.events || []).forEach(function (e, ei) {
      var idx = -1;
      pts.forEach(function (r, i) { if (idx < 0 && r[0] >= e.date) idx = i; });
      if (idx < 0) return;
      var lyy = ei % 2 === 0 ? P.t - 6 : P.t - 18;
      s += '<line x1="' + X(idx) + '" y1="' + (P.t - 2) + '" x2="' + X(idx) + '" y2="' + (H - P.b) + '" stroke="var(--chart-axis)" stroke-dasharray="2 4"/>' +
        '<text x="' + X(idx) + '" y="' + lyy + '" text-anchor="middle" font-size="9" fill="var(--ink-3)">' + esc(e.label.length > 26 ? e.label.slice(0, 25) + "…" : e.label) + "</text>";
    });
    var path = pts.map(function (r, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(r[1]).toFixed(1); }).join("");
    s += '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>';
    var last = pts[pts.length - 1];
    s += '<circle cx="' + X(pts.length - 1) + '" cy="' + Y(last[1]) + '" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>';
    s += '<text x="' + (X(pts.length - 1) + 9) + '" y="' + (Y(last[1]) + 4) + '" font-size="11.5" font-weight="650" class="mono-t" fill="var(--ink)">' + last[1] + "</text>";
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
  /* v2: always-dark canvas constellation. Force-directed hubs (signals) with
     link halos and ticker leaves, soft past/future gravity around a NOW glow,
     ambient candidates/events, and an outer dust ring of real feed headlines. */
  var CXP = {
    bg0: "#0a0a0f", bg1: "#14141c",
    ink: "#dde0ef", ink2: "#9296ad", ink3: "#585c72",
    accent: "#8687f0",
    verd: { UNDISCOVERED: "#34c77e", EMERGING: "#e3b23c", CROWDED: "#e97f4e", OVER_CROWDED: "#c14a62" },
    none: "#6a6e86", gold: "#d4a017", bad: "#e05a72",
    fam: { POLICY: "#e3b23c", CORPORATE: "#8687f0", TECH: "#34c77e", PHYSICAL: "#e97f4e", GEO: "#c14a62", MACRO: "#e3b23c", LEGAL: "#e3b23c" }
  };
  function cxTrim(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  var CX_NOWX = 720, CX_W = 1200, CX_H = 760, CX_CLAMP = 1460;
  function cxTimeX(dateStr) {
    var dx = daysBetween(TODAY, dateStr);
    if (isNaN(dx)) dx = 0;
    var t = Math.sqrt(Math.min(Math.abs(dx), CX_CLAMP) / CX_CLAMP);
    return dx < 0 ? CX_NOWX - t * 600 : CX_NOWX + t * 400;
  }
  function cxGraph() {
    var nodes = [], edges = [], idx = {};
    function add(n) { idx[n.kind + ":" + n.id] = nodes.length; nodes.push(n); return n; }
    function link(aKey, bKey, alpha, rest) {
      var a = idx[aKey], b = idx[bKey];
      if (a == null || b == null) return;
      edges.push({ a: a, b: b, alpha: alpha, rest: rest });
    }
    (D.signals || []).forEach(function (sg, si) {
      if (sg.status === "DISMISSED" || sg.status === "EXPIRED") return;
      var occ = sg.occurrence || null;
      var anchor = (occ && occ.anchor_date) || sg.created_at || TODAY;
      var un = (sg.unmappedness || {}).score || 0;
      var tx = cxTimeX(anchor);
      add({
        kind: "sig", id: sg.id, ref: sg, r: 14 + 16 * un / 100, color: CXP.accent,
        dashed: occ && occ.kind === "SCHEDULED", tx: tx, txw: 0.012,
        label: cxTrim(sg.title, 38),
        desc: occ ? cxTrim(occ.label, 44) + " · " + anchor : "occurrence undated",
        x: tx + (Math.random() - 0.5) * 40, y: CX_H * (0.32 + 0.36 * (si % 2)) + (Math.random() - 0.5) * 60
      });
      var c = sg.chain_id ? byId(D.chains, sg.chain_id) : null;
      if (!c) return;
      var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
      links.forEach(function (l, i) {
        var hub = nodes[idx["sig:" + sg.id]];
        var a = -Math.PI / 2 + i * (2 * Math.PI / links.length);
        var v = (l.heat || {}).verdict;
        add({
          kind: "link", id: l.id, ref: l, chainId: c.id, r: 3.2 + ((l.heat && l.heat.impact && l.heat.impact.score) || 40) / 55,
          color: v ? CXP.verd[v] : CXP.none, gold: !!(l.heat && l.heat.money_corner),
          choke: (l.bottleneck || {}).criticality === "CHOKE_POINT",
          tip: l.name + (v ? " — " + v.replace("_", " ") : " — unscored"),
          x: hub.x + (hub.r + 60) * Math.cos(a), y: hub.y + (hub.r + 60) * Math.sin(a)
        });
        link("link:" + l.id, "sig:" + sg.id, 0.05, hub.r + 52);
        (l.example_tickers || []).slice(0, 4).forEach(function (t, ti) {
          var me = nodes[idx["link:" + l.id]];
          var key = l.id + "/" + t;
          add({
            kind: "tick", id: key, tip: t, r: 1.8, color: CXP.ink3,
            x: me.x + (Math.random() - 0.5) * 40, y: me.y + (Math.random() - 0.5) * 40
          });
          link("tick:" + key, "link:" + l.id, 0.045, 20 + ti * 3);
        });
      });
      links.forEach(function (l) {
        (l.upstream_of || []).forEach(function (u) { link("link:" + l.id, "link:" + u, 0.13, 52); });
      });
    });
    (((D.candidates || {}).candidates) || []).forEach(function (cd) {
      if (cd.status !== "AMBIENT") return;
      var tx = cxTimeX(cd.date);
      add({ kind: "cand", id: cd.id, ref: cd, r: 3.5, color: CXP.fam[cd.family] || CXP.none, tx: tx, txw: 0.01,
            tip: cxTrim(cd.title, 60) + " · " + cd.family, x: tx + (Math.random() - 0.5) * 80, y: CX_H * (0.2 + Math.random() * 0.6) });
    });
    (((D.calendar || {}).events) || []).forEach(function (ev) {
      if (ev.status !== "WATCHING") return;
      var tx = cxTimeX(ev.date);
      add({ kind: "evt", id: ev.id, ref: ev, r: 4, dashed: true, color: CXP.fam[ev.kind] || CXP.none, tx: tx, txw: 0.014,
            tip: cxTrim(ev.title, 60) + " · " + ev.date, x: tx + (Math.random() - 0.5) * 60, y: CX_H * (0.25 + Math.random() * 0.5) });
    });
    return { nodes: nodes, edges: edges };
  }
  function cxSettle(g, ticks) {
    for (var t = 0; t < ticks; t++) cxTick(g, 1);
    return g;
  }
  function cxTick(g, alpha) {
    var N = g.nodes, E = g.edges, i, j;
    for (i = 0; i < N.length; i++) {
      var a = N[i];
      for (j = i + 1; j < N.length; j++) {
        var b = N[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 90000) continue;
        var f = alpha * 260 * (a.r + b.r) / d2;
        if (f > 4) f = 4;
        var d = Math.sqrt(d2);
        a.vx = (a.vx || 0) + f * dx / d; a.vy = (a.vy || 0) + f * dy / d;
        b.vx = (b.vx || 0) - f * dx / d; b.vy = (b.vy || 0) - f * dy / d;
      }
    }
    for (i = 0; i < E.length; i++) {
      var e = E[i], p = N[e.a], q = N[e.b];
      var ex = q.x - p.x, ey = q.y - p.y;
      var el = Math.sqrt(ex * ex + ey * ey) + 0.01;
      var s = alpha * 0.02 * (el - (e.rest || 50)) / el;
      p.vx = (p.vx || 0) + s * ex; p.vy = (p.vy || 0) + s * ey;
      q.vx = (q.vx || 0) - s * ex; q.vy = (q.vy || 0) - s * ey;
    }
    for (i = 0; i < N.length; i++) {
      if (N[i].kind !== "sig") continue;
      for (j = i + 1; j < N.length; j++) {
        if (N[j].kind !== "sig") continue;
        var hx = N[i].x - N[j].x, hy = N[i].y - N[j].y;
        var hd = Math.sqrt(hx * hx + hy * hy) + 0.01;
        var want = (N[i].r + N[j].r) * 3 + 240;
        if (hd < want) {
          var hf = alpha * 0.028 * (want - hd);
          N[i].vx += hf * hx / hd; N[i].vy += hf * hy / hd;
          N[j].vx -= hf * hx / hd; N[j].vy -= hf * hy / hd;
        }
      }
    }
    for (i = 0; i < N.length; i++) {
      var n = N[i];
      if (n.tx != null) n.vx = (n.vx || 0) + alpha * (n.txw || 0.01) * (n.tx - n.x);
      n.vx = (n.vx || 0) + alpha * 0.0022 * (CX_W / 2 - n.x) * (n.tx != null ? 0.25 : 1);
      n.vy = (n.vy || 0) + alpha * 0.004 * (CX_H / 2 - n.y);
      n.x += (n.vx *= 0.82); n.y += (n.vy *= 0.82);
    }
  }
  function cortexView() {
    var nSig = (D.signals || []).filter(function (x) { return x.status !== "DISMISSED" && x.status !== "EXPIRED"; }).length;
    var nL = 0; (D.chains || []).forEach(function (c) { nL += (c.links || []).length; });
    var nC = ((D.candidates || {}).candidates || []).filter(function (x) { return x.status === "AMBIENT"; }).length;
    var nE = ((D.calendar || {}).events || []).filter(function (x) { return x.status === "WATCHING"; }).length;
    var nF = ((D.feeds || {}).items || []).length;
    return topbar("cortex") + "<main>" +
      '<div class="pagehead"><h1>Cortex</h1><p class="sub">The occurrence field. Bright hubs are analyzed signals (size = how unmapped) with their value chains clustered around them; the dust ring is the raw feed — every dot a real headline. Past drifts left of the NOW seam, known future events sit right of it, dashed.</p></div>' +
      '<div class="card cxpanel"><div class="cxwrap">' +
      '<canvas id="cortexCanvas" role="img" aria-label="Cortex constellation — signals, chains, candidates, future events and feed dust on a past/future field"></canvas>' +
      '<div class="cx-hud"><b>CORTEX</b> · occurrence field<br>' +
      "signals <b>" + nSig + "</b> · chain links <b>" + nL + "</b> · candidates <b>" + nC + "</b><br>" +
      "known future <b>" + nE + "</b> · feed dust <b>" + nF + "</b> · built <b>" + esc(TODAY) + "</b></div>" +
      '<div class="cx-legend">● hub = signal · halo dots = chain links (verdict color, gold ring = money corner) · faint dots = tickers · ◆ dashed = known future · outer dust = raw feed (hover any dot) · drag nothing, click everything</div>' +
      "</div></div>" +
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
  }
  function cxDustDrawer(it) {
    return '<div class="scrim" data-closedrawer></div><div class="drawer" role="dialog" aria-label="feed item"><button class="x" data-closedrawer>✕</button>' +
      '<div class="row">' + chip(it.f || "?") + chip((it.d || "").slice(0, 10), "neutral") + chip("raw feed") + "</div>" +
      "<h2>" + esc(it.t) + "</h2>" +
      "<div class='muted small'>[" + esc(it.s || "?") + "] · untriaged feed item — the weekday radar sweep judges whether it becomes a candidate (method §0.1)</div>" +
      "<div style='margin-top:16px'>" + runButton("run radar", "triage the feed into candidates and signals") + "</div></div>";
  }
  function initCortex() {
    var canvas = document.getElementById("cortexCanvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var g = cxSettle(cxGraph(), 170);
    var dust = ((D.feeds || {}).items || []).slice(0, 300);
    var hover = null, mx = -1, my = -1, t0 = Date.now();
    var tip = document.getElementById("cxtip");
    if (!tip) { tip = document.createElement("div"); tip.className = "tooltip"; tip.id = "cxtip"; tip.style.display = "none"; document.body.appendChild(tip); }

    var sprites = {};
    function sprite(color, r, soft) {
      var key = color + "/" + r + "/" + soft;
      if (sprites[key]) return sprites[key];
      var s = Math.ceil(r * (soft || 3)), c = document.createElement("canvas");
      c.width = c.height = s * 2;
      var x = c.getContext("2d");
      var gr = x.createRadialGradient(s, s, 0, s, s, s);
      gr.addColorStop(0, color);
      gr.addColorStop(soft > 3 ? 0.2 : 0.5, color);
      gr.addColorStop(1, "rgba(0,0,0,0)");
      x.globalAlpha = soft > 3 ? 0.5 : 1;
      x.fillStyle = gr;
      x.fillRect(0, 0, s * 2, s * 2);
      sprites[key] = { c: c, s: s };
      return sprites[key];
    }
    function fit(w, h) {
      var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
      g.nodes.forEach(function (n) {
        if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
      });
      if (minX > maxX) { minX = 0; maxX = CX_W; minY = 0; maxY = CX_H; }
      var gw = Math.max(maxX - minX, 300), gh = Math.max(maxY - minY, 240);
      var k = Math.min((w * 0.64) / gw, (h * 0.66) / gh);
      return { k: k, ox: w / 2 - k * (minX + maxX) / 2, oy: h / 2 - k * (minY + maxY) / 2 };
    }
    function draw() {
      if (!canvas.isConnected) { tip.style.display = "none"; return; }
      var dpr = window.devicePixelRatio || 1;
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var t = (Date.now() - t0) / 1000;
      cxTick(g, 0.06);
      var F = fit(w, h);
      function SX(x) { return F.ox + F.k * x; }
      function SY(y) { return F.oy + F.k * y; }
      // ground
      var bg = ctx.createRadialGradient(w / 2, h / 2, 40, w / 2, h / 2, Math.max(w, h) * 0.7);
      bg.addColorStop(0, CXP.bg1); bg.addColorStop(1, CXP.bg0);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
      // NOW seam
      var nx = SX(CX_NOWX);
      var seam = ctx.createLinearGradient(nx - 46, 0, nx + 46, 0);
      seam.addColorStop(0, "rgba(134,135,240,0)"); seam.addColorStop(0.5, "rgba(134,135,240,0.10)"); seam.addColorStop(1, "rgba(134,135,240,0)");
      ctx.fillStyle = seam; ctx.fillRect(nx - 46, 0, 92, h);
      ctx.strokeStyle = "rgba(134,135,240,0.30)"; ctx.setLineDash([4, 6]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(nx, 26); ctx.lineTo(nx, h - 20); ctx.stroke(); ctx.setLineDash([]);
      ctx.font = "10px 'JetBrains Mono', monospace"; ctx.textAlign = "center";
      ctx.fillStyle = "rgba(134,135,240,0.75)"; ctx.fillText("N O W", nx, 18);
      ctx.fillStyle = CXP.ink3;
      ctx.fillText("« PAST", nx - 58, 18); ctx.fillText("AHEAD »", nx + 62, 18);
      // dust ring
      var cxc = w / 2, cyc = h / 2, minR = Math.min(w, h) / 2;
      for (var di = 0; di < dust.length; di++) {
        var it = dust[di];
        var ang = di * 2.399963 + 0.13;
        var rad = minR * (0.74 + 0.2 * ((di * 0.6180339) % 1));
        var dxp = cxc + rad * Math.cos(ang), dyp = cyc + rad * Math.sin(ang) * 0.92;
        var tw = 0.30 + 0.16 * Math.sin(t * 0.8 + di * 1.7);
        var col = CXP.fam[it.f] || CXP.ink3;
        ctx.globalAlpha = tw;
        var sp = sprite(col, 1.5, 0);
        ctx.drawImage(sp.c, dxp - sp.s / 2, dyp - sp.s / 2, sp.s, sp.s);
        it._x = dxp; it._y = dyp;
      }
      ctx.globalAlpha = 1;
      // edges
      g.edges.forEach(function (e) {
        var p = g.nodes[e.a], q = g.nodes[e.b];
        var hi = hover && (hover === p || hover === q);
        ctx.strokeStyle = "rgba(205,210,235," + (hi ? Math.min(0.5, e.alpha * 4) : e.alpha) + ")";
        ctx.lineWidth = hi ? 1.2 : 0.7;
        ctx.beginPath(); ctx.moveTo(SX(p.x), SY(p.y)); ctx.lineTo(SX(q.x), SY(q.y)); ctx.stroke();
      });
      // nodes
      g.nodes.forEach(function (n, ni) {
        var x = SX(n.x) + 1.6 * Math.sin(t * 0.4 + ni), y = SY(n.y) + 1.2 * Math.cos(t * 0.33 + ni * 2);
        n._x = x; n._y = y;
        var R = Math.max(1.2, F.k * n.r);
        if (n.kind === "sig") R = Math.max(11, R);
        R = Math.round(R * 2) / 2; // quantize so glow sprites cache instead of exploding
        var hi = hover === n;
        var halo = sprite(n.color, R, n.kind === "sig" ? 7 : 4.2);
        ctx.globalAlpha = n.kind === "tick" ? 0.4 : hi ? 1 : 0.85;
        ctx.drawImage(halo.c, x - halo.s, y - halo.s, halo.s * 2, halo.s * 2);
        var core = sprite(n.kind === "sig" ? "#eceefc" : n.color, Math.max(1, R * (n.kind === "sig" ? 0.6 : 0.72)), 0);
        ctx.drawImage(core.c, x - core.s / 2, y - core.s / 2, core.s, core.s);
        if (n.gold) { ctx.strokeStyle = CXP.gold; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(x, y, R + 2.5, 0, 7); ctx.stroke(); }
        if (n.choke) { ctx.strokeStyle = CXP.bad; ctx.lineWidth = 1.1; ctx.beginPath(); ctx.arc(x, y, R + 5, 0, 7); ctx.stroke(); }
        if (n.dashed) {
          ctx.strokeStyle = n.kind === "sig" ? CXP.accent : n.color;
          ctx.setLineDash([3, 3]); ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.arc(x, y, R + 4, 0, 7); ctx.stroke(); ctx.setLineDash([]);
        }
        n._R = R;
      });
      // labels last, on top of every dot
      g.nodes.forEach(function (n) {
        if (n.kind !== "sig") return;
        var hi = hover === n;
        ctx.textAlign = "center";
        ctx.font = "600 11px 'JetBrains Mono', monospace";
        ctx.fillStyle = hi ? "#ffffff" : CXP.ink;
        ctx.fillText(n.label, n._x, n._y + n._R + 22);
        ctx.font = "10px 'JetBrains Mono', monospace";
        ctx.fillStyle = CXP.ink2;
        ctx.fillText(n.desc, n._x, n._y + n._R + 36);
      });
      requestAnimationFrame(draw);
    }
    function pick(px, py) {
      var best = null, bd = 1e9;
      g.nodes.forEach(function (n) {
        if (n._x == null) return;
        var d = Math.abs(n._x - px) + Math.abs(n._y - py);
        var reach = Math.max(9, n.r * 1.2 + 6);
        if (d < reach && d < bd) { bd = d; best = n; }
      });
      if (!best) {
        for (var i = 0; i < dust.length; i++) {
          var it = dust[i];
          if (it._x != null && Math.abs(it._x - px) + Math.abs(it._y - py) < 7) return { kind: "dust", ref: it, tip: cxTrim(it.t, 90) + " — " + (it.s || "") + " · " + ((it.d || "").slice(0, 10)) };
        }
      }
      return best;
    }
    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect();
      mx = e.clientX - r.left; my = e.clientY - r.top;
      hover = pick(mx, my);
      canvas.style.cursor = hover ? "pointer" : "default";
      if (hover) {
        var txt = hover.kind === "sig" ? hover.label + " — " + hover.desc : hover.tip || "";
        tip.innerHTML = "<span class='num'>" + esc(txt) + "</span>";
        tip.style.display = "block"; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY - 12 + "px";
      } else tip.style.display = "none";
    });
    canvas.addEventListener("mouseleave", function () { hover = null; tip.style.display = "none"; canvas.style.cursor = "default"; });
    canvas.addEventListener("click", function (e) {
      var r = canvas.getBoundingClientRect();
      hover = pick(e.clientX - r.left, e.clientY - r.top);
      if (!hover) return;
      tip.style.display = "none";
      if (hover.kind === "sig") cxShowDrawer(cortexDrawer("sig", hover.id));
      else if (hover.kind === "link") { var c = byId(D.chains, hover.chainId); if (c) cxShowDrawer(drawer(c, hover.ref)); }
      else if (hover.kind === "cand") cxShowDrawer(cortexDrawer("cand", hover.id));
      else if (hover.kind === "evt") cxShowDrawer(cortexDrawer("evt", hover.id));
      else if (hover.kind === "dust") cxShowDrawer(cxDustDrawer(hover.ref));
    });
    requestAnimationFrame(draw);
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
    return topbar() + "<main><div class='emptystate' style='margin-top:40px'>Not found: " + esc(what) + '<br><br><a href="#/">back to radar</a></div>' + footer() + "</main>";
  }

  /* ---------------- router & events ---------------- */
  function route() {
    var h = location.hash || "#/";
    var p = h.replace(/^#\//, "").split("/").map(decodeURIComponent);
    var html;
    if (!p[0]) html = homeView();
    else if (p[0] === "signal") html = signalView(p[1]);
    else if (p[0] === "chains") html = chainsView();
    else if (p[0] === "chain") html = chainView(p[1], p[2]);
    else if (p[0] === "screen") html = screenView(p[1], p[2]);
    else if (p[0] === "stock") html = stockView(p[1], p[2]);
    else if (p[0] === "cortex") html = cortexView();
    else if (p[0] === "book") html = bookView();
    else if (p[0] === "shadow") html = shadowView();
    else html = homeView();
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
