/* Upstream SPA — vanilla JS over window.UPSTREAM_DATA. Hash-routed, no external libs. */
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
    if (d > 90) return '<span class="chip verystale">stale · as of ' + esc(asOf) + "</span>";
    if (d > 30) return '<span class="chip stale">stale · as of ' + esc(asOf) + "</span>";
    if (d > 7) return '<span class="chip neutral">as of ' + esc(asOf) + "</span>";
    return "";
  }
  function chip(text, cls) { return '<span class="chip ' + esc(cls || "neutral") + '">' + esc(text) + "</span>"; }
  function tierChip(t) { return t ? '<span class="chip tier">' + esc(t) + "</span>" : ""; }
  function cmdPill(cmd, caption) {
    return '<div><span class="cmd"><span>' + esc(cmd) + '</span><button data-copy="' + esc(cmd) + '">copy</button></span>' +
      '<div class="cmd-caption">' + esc(caption || "paste into a Claude session on Ronkimhi/upstream") + "</div></div>";
  }
  function verdColor(v) { return { UNDISCOVERED: "var(--und)", EMERGING: "var(--emg)", CROWDED: "var(--crd)", OVER_CROWDED: "var(--ovr)" }[v] || "var(--border-strong)"; }
  function marketFor(t) { return (D.market || {})[String(t).replace(/\./g, "-")] || (D.market || {})[t] || null; }
  function fmtMoney(x) { return typeof x === "number" ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : esc(x); }
  function slug(s) { return String(s); }

  /* ---------------- shell ---------------- */
  function pendingCount() {
    var r = (D.requests && D.requests.requests) || [];
    return r.filter(function (x) { return x.status === "PENDING"; }).length;
  }
  function failedCount() {
    var r = (D.requests && D.requests.requests) || [];
    return r.filter(function (x) { return x.status === "FAILED"; }).length;
  }
  function healthState() {
    if (failedCount() > 0) return ["var(--bad)", "data requests FAILED — check data/requests.json"];
    var rs = ((D.health || {}).sessions || {}).routine_status || {};
    if (rs.radar === "LIVE") {
      var lastRadar = null;
      (D.ledger || []).forEach(function (l) { if (l.indexOf("| RADAR") > -1) lastRadar = l.slice(0, 10); });
      if (lastRadar && daysBetween(lastRadar, TODAY) > 4) return ["var(--bad)", "radar silent since " + lastRadar];
    }
    if (pendingCount() > 0) return ["var(--warn)", pendingCount() + " data request(s) pending"];
    return ["var(--good)", "all quiet"];
  }
  function topbar() {
    var hs = healthState();
    var p = pendingCount();
    return '<div class="topbar">' +
      '<a href="#/" class="wordmark"><span class="tick">▲</span>UPSTREAM</a>' +
      '<span class="builtat">built ' + esc(D.built_at || "?") + "</span>" +
      '<span class="spacer"></span>' +
      (p ? '<span class="tb-chip"><span class="healthdot" style="background:var(--warn)"></span>' + p + " pending</span>" : "") +
      '<span class="tb-chip" title="' + esc(hs[1]) + '"><span class="healthdot" style="background:' + hs[0] + '"></span>health</span>' +
      '<button class="tb-btn" data-nav="#/shadow">shadow</button>' +
      '<button class="tb-btn" data-nav="#/book">book</button>' +
      '<button class="tb-btn" id="themeBtn">theme</button>' +
      "</div>";
  }
  function crumbs(parts) {
    var h = '<div class="crumbs"><a href="#/">Radar</a>';
    parts.forEach(function (p, i) {
      h += '<span class="sep">›</span>';
      if (p.href && i < parts.length - 1) h += '<a href="' + p.href + '">' + esc(p.label) + "</a>";
      else if (p.cmd) h += '<span class="cmd" style="padding:2px 8px;font-size:11px">' + esc(p.cmd) + '<button data-copy="' + esc(p.cmd) + '">copy</button></span>';
      else h += '<span class="here">' + esc(p.label) + "</span>";
    });
    return h + "</div>";
  }
  function footer() {
    return '<div class="footer">Upstream is a private research tool. Verdicts and zones are analytical outputs from public data with stated methods and gaps — not investment advice. Canonical copy: <span class="mono">app/index.html</span> in the repo; if this page looks stale, a newer build may not have been republished yet.</div>';
  }

  /* ---------------- what changed ---------------- */
  function lastVisit() { try { return localStorage.getItem("upstream.lastVisit"); } catch (e) { return null; } }
  function stampVisit() { try { localStorage.setItem("upstream.lastVisit", new Date().toISOString()); } catch (e) {} }
  function whatChanged() {
    var since = lastVisit();
    var items = [];
    (D.signals || []).forEach(function (s) {
      if (!since || (s.created_at && s.created_at > since.slice(0, 10))) items.push("New signal: <a href='#/signal/" + s.id + "'>" + esc(s.title) + "</a>");
    });
    (D.stocks || []).forEach(function (st) {
      (st.changelog || []).forEach(function (c) {
        if (since && c.ts > since) items.push("Deep dive updated: <a href='#/stock/" + st.ticker + "/" + st.chain_id + "'>" + esc(st.ticker) + "</a> — " + esc(c.change));
      });
      if (st.review_by && st.review_by <= TODAY && st.status !== "ARCHIVED") items.push("Review due: <a href='#/stock/" + st.ticker + "/" + st.chain_id + "'>" + esc(st.ticker) + "</a> (review_by " + esc(st.review_by) + ")");
    });
    (D.chains || []).forEach(function (c) {
      (c.scenarios || []).forEach(function (sc) {
        (sc.leading_indicators || []).forEach(function (ind) {
          if (ind.tripped_at) items.push("Indicator TRIPPED: " + esc(ind.indicator) + " → scenario <a href='#/chain/" + c.id + "'>" + esc(sc.id + " " + sc.title) + "</a>");
        });
      });
    });
    ((D.indicators || {}).trips || []).forEach(function (t) {
      if (!since || t.tripped_at >= since.slice(0, 10)) items.push("Indicator TRIPPED " + esc(t.tripped_at) + ": " + esc(t.indicator) + " (" + esc(t.ticker) + " " + esc(t.op) + " " + esc(t.level) + ", seen " + esc(t.seen) + ") → <a href='#/chain/" + t.chain + "'>" + esc(t.chain + " " + t.scenario) + "</a>");
    });
    if (!since && !items.length) items.push("First visit on this device — everything below is new to you.");
    if (since && !items.length) items.push("Nothing changed since your last visit (" + esc(since.slice(0, 10)) + ").");
    return '<div class="card brief"><h3>What changed' + (since ? " since " + esc(since.slice(0, 10)) : "") + "</h3><ul>" +
      items.slice(0, 12).map(function (i) { return "<li>" + i + "</li>"; }).join("") + "</ul></div>";
  }

  /* ---------------- home ---------------- */
  function signalCard(s) {
    var next = s.status === "NEW" ? cmdPill("run chain " + s.id, "build this signal's value chain") :
      s.status === "CHAINED" ? '<a class="chip accent" href="#/chain/' + esc(s.chain_id) + '">open chain →</a>' : "";
    return '<a class="card sigcard" href="#/signal/' + esc(s.id) + '">' +
      '<div class="row">' + chip(s.status, s.status === "NEW" ? "accent" : "neutral") +
      chip(s.suggested_clock) + '<span class="muted num">' + esc((s.horizon_years || []).join("-")) + "y</span>" +
      staleChip(s.updated_at) + "</div>" +
      "<h3>" + esc(s.title) + "</h3>" +
      '<div class="thesis">' + esc(s.thesis) + "</div>" +
      '<div class="row"><span class="muted">unmapped ' + '<span class="num">' + esc((s.unmappedness || {}).score) + "</span>/100</span>" +
      '<span class="muted">' + (s.evidence || []).length + " evidence</span></div></a>" +
      (next ? '<div style="margin:-2px 0 12px">' + next + "</div>" : "");
  }
  function homeView() {
    var lanes = { MACRO: [], INDUSTRY: [], USE_CASE: [] };
    (D.signals || []).forEach(function (s) { (lanes[s.lane] = lanes[s.lane] || []).push(s); });
    var digest = (D.digests || [])[0];
    var lh = { MACRO: "Macro & geopolitics", INDUSTRY: "Industry inflections", USE_CASE: "Emerging use cases" };
    var ledgerTail = (D.ledger || []).slice(-6).reverse();
    return topbar() + "<main>" +
      "<h1>Radar</h1><p class='lead'>Known events whose chain consequences are still unmapped. Nothing below was analyzed until someone clicked it; everything analyzed is saved forever.</p>" +
      whatChanged() +
      (digest ? '<div class="card" style="margin-top:12px"><h3>Saturday digest · ' + esc(digest.week || "") + "</h3><div class='small'>" + esc(digest.summary || "") + "</div></div>" : "") +
      "<h2>Signals</h2><div class='lanes'>" +
      Object.keys(lanes).map(function (k) {
        return "<div><div class='lane-h'>" + esc(lh[k]) + " · " + lanes[k].length + "</div>" +
          (lanes[k].map(signalCard).join("") || '<div class="emptystate">nothing yet — the radar routine fills this lane</div>') + "</div>";
      }).join("") + "</div>" +
      "<h2>Recent activity</h2><div class='card'><div class='mono' style='font-size:11.5px;white-space:pre-wrap;color:var(--ink-2)'>" +
      ledgerTail.map(esc).join("\n") + "</div></div>" +
      footer() + "</main>";
  }

  /* ---------------- signal ---------------- */
  function signalView(id) {
    var s = byId(D.signals, id);
    if (!s) return notFound("signal " + id);
    var evid = (s.evidence || []).map(function (e) {
      return '<div class="evli">' + chip(e.tag, "neutral") + " " + esc(e.claim) +
        (e.source_name ? ' <span class="muted">[' + esc(e.source_name) + (e.source_date ? ", " + esc(e.source_date) : "") + "]</span>" : "") + "</div>";
    }).join("");
    return topbar() + crumbs([{ label: s.title }]) + "<main>" +
      '<div class="row">' + chip(s.lane) + chip(s.status, s.status === "NEW" ? "accent" : "neutral") + chip(s.suggested_clock) + staleChip(s.updated_at) + "</div>" +
      "<h1>" + esc(s.title) + "</h1><p class='lead'>" + esc(s.thesis) + "</p>" +
      "<div class='grid' style='grid-template-columns:repeat(auto-fit,minmax(280px,1fr))'>" +
      "<div class='card'><h3>Why now</h3><div class='small'>" + esc(s.why_now) + "</div></div>" +
      "<div class='card'><h3>The retail gap</h3><div class='small'>" + esc(s.retail_gap) + "</div></div>" +
      "<div class='card'><h3>Unmapped-ness · <span class='num'>" + esc((s.unmappedness || {}).score) + "</span>/100</h3><div class='small'>" + esc((s.unmappedness || {}).rationale) + "</div></div></div>" +
      "<h2>Evidence</h2><div class='card'>" + (evid || "<div class='muted'>none</div>") + "</div>" +
      "<h2>Next</h2>" + (s.chain_id ? "<a class='chip accent' href='#/chain/" + esc(s.chain_id) + "'>open the value chain →</a>" :
        s.status === "NEW" ? cmdPill("run chain " + s.id, "builds the 8-15 link value chain for this signal") : "<span class='muted'>" + esc(s.status) + "</span>") +
      notesBlock(s) + changelogBlock(s) + footer() + "</main>";
  }

  /* ---------------- chain ---------------- */
  var chainTab = "flow";
  function chainView(id, tab) {
    var c = byId(D.chains, id);
    if (!c) return notFound("chain " + id);
    chainTab = tab || chainTab || "flow";
    var links = (c.links || []).slice().sort(function (a, b) { return a.position - b.position; });
    var scored = links.filter(function (l) { return l.heat && l.heat.crowdedness && l.heat.crowdedness.score != null; });
    var money = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    var body = chainTab === "heat" ? heatTab(c, links, scored) : chainTab === "scen" ? scenTab(c) : flowTab(c, links);
    return topbar() + crumbs([{ label: sigTitle(c.signal_id), href: "#/signal/" + c.signal_id }, { label: c.title }]) + "<main>" +
      '<div class="row">' + chip(c.clock) + chip(c.status) + staleChip(c.heat_as_of) +
      (money.length ? chip("★ money corner: " + money.map(function (l) { return l.name; }).join(", "), "UNDISCOVERED") : "") + "</div>" +
      "<h1>" + esc(c.title) + "</h1>" +
      "<p class='lead section-note'>" + esc(c.map_limitation || "") + "</p>" +
      '<div class="tabs">' +
      ["flow:Flow", "heat:Heat 2x2", "scen:Scenarios"].map(function (t) {
        var k = t.split(":");
        return '<button class="' + (chainTab === k[0] ? "on" : "") + '" data-tab="' + k[0] + '" data-chain="' + esc(c.id) + '">' + k[1] + "</button>";
      }).join("") + "</div>" + body +
      "<div class='healthline'>heat coverage: " + scored.length + "/" + links.length + " links scored · as of " + esc(c.heat_as_of || "never") + "</div>" +
      notesBlock(c) + changelogBlock(c) + footer() + "</main>";
  }
  function sigTitle(id) { var s = byId(D.signals, id); return s ? s.title : id; }
  function flowTab(c, links) {
    var h = '<div class="flow">';
    links.forEach(function (l, i) {
      var hv = l.heat && l.heat.verdict;
      var cr = l.heat && l.heat.crowdedness && l.heat.crowdedness.score;
      var im = l.heat && l.heat.impact && l.heat.impact.score;
      var cp = l.heat && l.heat.capture && l.heat.capture.score;
      h += '<div class="fnode ' + (hv ? "v-" + hv : "") + '" data-drawer="' + esc(l.id) + '" tabindex="0" role="button" aria-label="' + esc(l.name) + '">' +
        (l.heat && l.heat.money_corner ? '<span class="star" title="money corner">★</span>' : "") +
        (l.bottleneck && l.bottleneck.criticality === "CHOKE_POINT" ? '<span class="choke" title="choke point"></span>' : "") +
        '<div class="pos">' + (i + 1) + "</div><div class='nm'>" + esc(l.name) + "</div>" +
        '<div class="sc">' + (hv ? "i" + im + " · c" + cr + " · v" + cp : "unscored") + "</div></div>";
      if (i < links.length - 1) h += '<div class="farrow">→</div>';
    });
    h += "</div>";
    h += '<div class="legend">' +
      [["UNDISCOVERED", "--und"], ["EMERGING", "--emg"], ["CROWDED", "--crd"], ["OVER_CROWDED", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") +
      "<span>★ money corner</span><span><span class='choke' style='position:static;display:inline-block;vertical-align:-1px'></span> choke point</span>" +
      "<span class='mono'>i impact · c crowdedness · v value capture</span></div>";
    h += "<div id='drawerHost'></div>";
    return h;
  }
  function scoreBar(name, obj, colorVar) {
    if (!obj || obj.score == null) return '<div class="scorebar"><span class="small">' + esc(name) + '</span><div class="tk"></div><span class="num muted">null</span></div>';
    return '<div class="scorebar"><span class="small">' + esc(name) + '</span><div class="tk"><i style="width:' + obj.score + "%;background:var(" + colorVar + ')"></i></div><span class="num">' + obj.score + "</span></div>";
  }
  function drawer(c, l) {
    var h = l.heat || {};
    function ev(o) { return ((o || {}).evidence || []).map(function (e) { return '<div class="evli">' + chip(e.tag) + " " + esc(e.claim) + (e.source_name ? " <span class='muted'>[" + esc(e.source_name) + "]</span>" : "") + "</div>"; }).join(""); }
    return '<div class="drawer" role="dialog" aria-label="' + esc(l.name) + '"><button class="x" data-closedrawer>✕</button>' +
      '<div class="row">' + (h.verdict ? chip(h.verdict, h.verdict) : chip("unscored")) + (h.money_corner ? chip("★ money corner", "UNDISCOVERED") : "") + chip(l.investability) + chip("bottleneck: " + (l.bottleneck || {}).criticality) + "</div>" +
      "<h2 style='margin-top:10px'>" + esc(l.name) + "</h2><p class='small'>" + esc(l.role) + "</p>" +
      scoreBar("Impact", h.impact, "--accent") + scoreBar("Crowdedness", h.crowdedness, "--crd") + scoreBar("Value capture", h.capture, "--und") +
      (h.impact ? "<h3 style='margin-top:12px'>Impact</h3><div class='small'>" + esc((h.impact || {}).rationale) + "</div>" + ev(h.impact) : "") +
      (h.crowdedness ? "<h3 style='margin-top:10px'>Crowdedness</h3><div class='small'>" + esc((h.crowdedness || {}).rationale) + "</div>" + ev(h.crowdedness) : "") +
      (h.capture ? "<h3 style='margin-top:10px'>Value capture</h3><div class='small'>" + esc((h.capture || {}).rationale) + "</div>" + ev(h.capture) : "") +
      (h.repricing_check && h.repricing_check.note ? "<h3 style='margin-top:10px'>Repricing check</h3><div class='small'>" + (h.repricing_check.legs_met != null ? "<span class='num'>" + h.repricing_check.legs_met + "/4 legs</span> · " : "") + esc(h.repricing_check.note) + "</div>" : "") +
      "<h3 style='margin-top:12px'>Feeds</h3><div class='small'>" + ((l.upstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "—") + "</div>" +
      "<h3>Fed by</h3><div class='small'>" + ((l.downstream_of || []).map(function (x) { return esc(linkName(c, x)); }).join(", ") || "—") + "</div>" +
      "<h3 style='margin-top:12px'>Example names</h3><div class='row'>" + (l.example_tickers || []).map(function (t) { return chip(t, "neutral"); }).join("") + "</div>" +
      (h.verdict ? "" : "<div style='margin-top:14px'>" + cmdPill("run heat " + c.id, "scores every unscored link with fetched evidence") + "</div>") +
      "</div>";
  }
  function linkName(c, id) { var l = byId(c.links, id); return l ? l.name : id; }
  function heatTab(c, links, scored) {
    if (!scored.length) return '<div class="emptystate">No links scored yet.<br><br>' + cmdPill("run heat " + c.id) + "</div>";
    var W = 880, H = 520, P = { l: 60, r: 30, t: 26, b: 46 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    function X(v) { return P.l + (v / 100) * iw; }
    function Y(cr) { return P.t + (cr / 100) * ih; } // crowd 0 at TOP (money corner top-right)
    var s = '<div class="card"><div class="chartwrap"><svg viewBox="0 0 ' + W + " " + H + '" width="100%" style="max-width:' + W + 'px" role="img" aria-label="Impact vs crowdedness scatter">';
    s += '<rect x="' + X(60) + '" y="' + Y(0) + '" width="' + (X(100) - X(60)) + '" height="' + (Y(40) - Y(0)) + '" fill="var(--band-good)" rx="8"/>';
    s += '<text x="' + (X(80)) + '" y="' + (Y(8)) + '" text-anchor="middle" font-size="11" font-weight="600" fill="var(--und)">money corner</text>';
    [0, 20, 40, 60, 80, 100].forEach(function (v) {
      s += '<line x1="' + X(v) + '" y1="' + P.t + '" x2="' + X(v) + '" y2="' + (H - P.b) + '" stroke="var(--chart-grid)" stroke-width="1"/>';
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)" stroke-width="1"/>';
      s += '<text x="' + X(v) + '" y="' + (H - P.b + 18) + '" text-anchor="middle" font-size="10" class="mono-t">' + v + "</text>";
      s += '<text x="' + (P.l - 10) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t">' + v + "</text>";
    });
    s += '<line x1="' + X(60) + '" y1="' + P.t + '" x2="' + X(60) + '" y2="' + (H - P.b) + '" stroke="var(--ink-3)" stroke-dasharray="4 4" stroke-width="1"/>';
    s += '<line x1="' + P.l + '" y1="' + Y(40) + '" x2="' + (W - P.r) + '" y2="' + Y(40) + '" stroke="var(--ink-3)" stroke-dasharray="4 4" stroke-width="1"/>';
    s += '<text x="' + (P.l + iw / 2) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="11">Impact →</text>';
    s += '<text transform="rotate(-90)" x="' + (-(P.t + ih / 2)) + '" y="16" text-anchor="middle" font-size="11">Crowdedness (quieter is higher) →</text>';
    scored.forEach(function (l) {
      var im = l.heat.impact ? l.heat.impact.score : null, cr = l.heat.crowdedness.score, cp = l.heat.capture ? l.heat.capture.score : 40;
      if (im == null) return;
      var r = 5 + (cp || 0) / 11;
      s += '<circle cx="' + X(im) + '" cy="' + Y(cr) + '" r="' + r.toFixed(1) + '" fill="' + verdColor(l.heat.verdict) + '" fill-opacity="0.82" stroke="var(--surface)" stroke-width="2" data-dot="' + esc(l.id) + '"><title>' + esc(l.name) + ": impact " + im + ", crowdedness " + cr + ", capture " + cp + "</title></circle>";
      s += '<text x="' + X(im) + '" y="' + (Y(cr) - r - 5) + '" text-anchor="middle" font-size="10.5" font-weight="600" fill="var(--ink)">' + esc(l.name.split(" ")[0] === "AI" ? l.name : l.name.split("(")[0].trim()) + "</text>";
    });
    s += "</svg></div>";
    s += '<div class="legend"><span>dot size = value capture</span>' +
      [["UNDISCOVERED", "--und"], ["EMERGING", "--emg"], ["CROWDED", "--crd"], ["OVER_CROWDED", "--ovr"]].map(function (v) {
        return '<span><span class="sw" style="background:var(' + v[1] + ')"></span>' + v[0] + "</span>";
      }).join("") + "</div>";
    var un = links.filter(function (l) { return !l.heat || !l.heat.crowdedness || l.heat.crowdedness.score == null; });
    if (un.length) s += '<div class="muted" style="margin-top:8px">not scored: ' + un.map(function (l) { return esc(l.name); }).join(", ") + "</div>";
    s += "</div>";
    var mc = links.filter(function (l) { return l.heat && l.heat.money_corner; });
    s += "<h2>Reading it</h2><div class='card small'>" +
      (mc.length ? "<b>Money corner:</b> " + mc.map(function (l) { return esc(l.name) + " (i" + l.heat.impact.score + " c" + l.heat.crowdedness.score + " v" + l.heat.capture.score + ")"; }).join(" · ") + ". " : "No link currently meets all three thresholds (impact ≥ 60, crowdedness ≤ 40, capture ≥ 60). ") +
      "High-impact low-crowd links with small dots are the trap: un-crowded because capture is capped — see each link's capture rationale.</div>";
    return s;
  }
  function scenTab(c) {
    var scens = c.scenarios || [];
    if (!scens.length) return '<div class="emptystate">No scenarios yet.<br><br>' + cmdPill("run scenarios " + c.id) + "</div>";
    return scens.map(function (s) {
      var mv = (s.links_moved || []).map(function (m) {
        return '<span class="mv"><span class="' + (m.direction === "UP" ? "up" : "down") + '">' + (m.direction === "UP" ? "↑" : "↓") + "</span>" + esc(linkName(c, m.link_id)) + " · " + esc(m.magnitude.toLowerCase()) + "</span>";
      }).join("");
      var inds = (s.leading_indicators || []).map(function (i) {
        var trip = ((D.indicators || {}).trips || []).filter(function (t) {
          return t.chain === c.id && t.scenario === s.id && t.indicator === i.indicator;
        })[0];
        var trippedAt = i.tripped_at || (trip && trip.tripped_at);
        var badge = trippedAt ? chip("TRIPPED " + trippedAt, "OVER_CROWDED") : i.armed ? chip("armed", "accent") : "";
        return "<li>" + esc(i.indicator) + " <span class='muted'>(" + esc(i.where_to_watch) + ")</span> " + badge + "</li>";
      }).join("");
      return '<div class="card scen"><div class="row"><b>' + esc(s.id) + " · " + esc(s.title) + "</b>" + chip(s.status, s.status === "SCREENED" ? "accent" : "neutral") + (s.clock ? chip(s.clock) : "") +
        '<span class="num muted">p ' + esc(s.probability_pct) + "%</span></div>" +
        '<div class="pbar"><i style="width:' + esc(s.probability_pct) + '%"></i></div>' +
        "<div class='small'>" + esc(s.narrative) + "</div>" +
        "<div style='margin:8px 0 4px'>" + mv + "</div>" +
        "<details><summary>Leading indicators & invalidation</summary><ul class='bullets'>" + inds + "</ul>" +
        "<div class='small' style='margin-top:6px'><b>Invalidation:</b> " + (s.invalidation_signs || []).map(esc).join(" · ") + "</div></details>" +
        "<div style='margin-top:10px'>" + (s.screen_ref ? '<a class="chip accent" href="#/screen/' + esc(c.id) + "/" + esc(s.id) + '">open stock screen →</a>' : cmdPill("run screen " + c.id + " " + s.id, "screens stocks for this scenario")) + "</div></div>";
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
      return "<h2>" + names[k] + " · " + rows.length + '</h2><div class="tablewrap"><table><thead><tr><th>Name</th><th>Tier</th><th>Thesis</th><th>Exposure</th><th>Fundamentals</th><th>Attention</th><th>Status</th></tr></thead><tbody>' +
        rows.map(function (r) {
          var f = r.fundamentals === "PENDING_DATA" ? chip("pending data", "stale") :
            (typeof r.fundamentals === "object" && r.fundamentals ? "<span class='num small'>" + esc(r.fundamentals.summary || JSON.stringify(r.fundamentals).slice(0, 60)) + "</span>" : "—");
          var cw = r.crowdedness === "PENDING_DATA" ? chip("pending", "stale") :
            (r.crowdedness && r.crowdedness.state ? chip(r.crowdedness.state + (r.crowdedness.pcs_score != null ? " " + r.crowdedness.pcs_score : ""), r.crowdedness.state === "DARK" ? "UNDISCOVERED" : r.crowdedness.state === "CROWDED" ? "CROWDED" : "neutral") : "—");
          var ex = r.theme_revenue_exposure && r.theme_revenue_exposure.pct != null ? "<span class='num'>" + esc(r.theme_revenue_exposure.pct) + "%</span>" :
            '<span class="chip neutral" title="' + esc((r.theme_revenue_exposure || {}).basis || "") + '">NULL</span>';
          var nug = (r.earnings_nuggets || []).length ? "<details><summary>" + r.earnings_nuggets.length + " nugget(s)</summary>" +
            r.earnings_nuggets.map(function (n) { return "<blockquote>“" + esc(n.quote) + "”<div class='muted'>" + esc(n.form || "") + " · <a href='" + esc(n.url) + "'>" + esc(n.accession) + "</a></div></blockquote>"; }).join("") + "</details>" : "";
          var dived = r.status === "DIVED" ? '<a class="chip accent" href="#/stock/' + esc(r.ticker) + "/" + esc(chainId) + '">dive →</a>' : chip(r.status);
          return "<tr><td><b>" + esc(r.ticker) + "</b><div class='muted'>" + esc(r.name || "") + " · " + esc(r.exchange || "") + "</div>" + nug + "</td><td>" + tierChip(r.tier) + "</td><td class='small' style='max-width:260px'>" + esc(r.thesis_1line) + "</td><td>" + ex + "</td><td>" + f + "</td><td>" + cw + "</td><td>" + dived + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).join("");
    var gaps = (sc.data_gaps || []);
    return topbar() + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: "Screen · " + scenId }]) + "<main>" +
      "<h1>" + esc(scen ? scen.title : scenId) + " — stock screen</h1>" +
      "<p class='lead section-note'>" + esc(sc.universe_note) + "</p>" + body +
      ((sc.taste_filtered || []).length ? "<div class='card small' style='margin-top:12px'><b>Filtered by taste:</b> " + sc.taste_filtered.map(esc).join(", ") + "</div>" : "") +
      (gaps.length ? "<div style='margin-top:14px'>" + cmdPill("run screen " + chainId + " " + scenId, "re-run once fetched data lands (" + gaps.length + " names pending)") + "</div>" : "") +
      "<div class='healthline'>examined " + esc((sc.health || {}).tickers_examined) + " · fully scored " + esc((sc.health || {}).fully_scored) + " · pending " + esc((sc.health || {}).pending) + " · errors " + esc((sc.health || {}).errors) + "</div>" +
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
    var vb = '<div class="verdictbar ' + esc(st.verdict) + '"><span class="v">' + esc(st.verdict.replace("_", " ")) + "</span>" +
      chip(st.clock) + chip(st.status, st.status === "FINAL" ? "accent" : "stale") + tierChip(st.tier) +
      (pos.length ? chip("in book: " + pos.map(function (t) { return t.action + " @ " + t.price; }).join(", "), "accent") : "") +
      '<span class="spacer" style="flex:1"></span><span class="muted">review by <span class="num">' + esc(st.review_by) + "</span></span></div>";
    var zones = st.verdict === "INVESTABLE" && st.entry_zone ?
      "<div class='card'><h3>Entry logic</h3><div class='kv'><dt>Entry zone</dt><dd class='num'>" + fmtMoney(st.entry_zone.low) + " – " + fmtMoney(st.entry_zone.high) + "</dd>" +
      "<dt>No entry above</dt><dd class='num'>" + fmtMoney(st.no_entry_above) + "</dd><dt>Basis</dt><dd class='small'>" + esc(st.entry_zone.basis) + "</dd></div></div>" :
      st.verdict === "WATCH" ? "<div class='card'><h3>Watch triggers</h3><ul class='bullets'>" + (st.watch_triggers || []).map(function (t) { return "<li>" + esc(t.metric) + " " + esc(t.direction || "") + " <span class='num'>" + esc(t.level) + "</span></li>"; }).join("") + "</ul></div>" :
      "<div class='card'><h3>Shadow tracking</h3><div class='small'>TOO LATE calls are graded: this name was added to the <a href='#/shadow'>shadow book</a> and is repriced at +90 days vs SPY.</div></div>";
    var rt = st.red_team ? "<div class='card redteam'><h3>Red team · attacked " + esc(st.red_team.attacked_at) + " · " + (st.red_team.verdict_survived ? "verdict survived" : "verdict overturned") + "</h3>" +
      (st.red_team.challenges || []).map(function (ch) { return "<div class='small' style='margin:6px 0'><b>" + esc(ch.dimension) + ":</b> " + esc(ch.attack) + " <span class='muted'>→ " + esc(ch.outcome) + "</span></div>"; }).join("") +
      (st.red_team.amendments ? "<div class='small'><b>Amendments:</b> " + esc(st.red_team.amendments) + "</div>" : "") +
      "<div class='small' style='margin-top:8px'><b>Surviving bear case:</b> " + esc(st.red_team.surviving_bear_case) + "</div></div>" :
      "<div class='card redteam'><h3>Red team</h3><div class='small'>This dive is DRAFT — it becomes FINAL only after a fresh-context attack.</div><div style='margin-top:8px'>" + cmdPill("run redteam " + ticker + " " + chainId) + "</div></div>";
    var priced = "<div class='card'><h3>What is already priced in</h3>" +
      (st.what_is_priced_in || []).map(function (p) { return "<div class='evli'>" + chip(p.tag) + " " + esc(p.expectation) + "</div>"; }).join("") +
      "<div class='small' style='margin-top:8px'>" + esc(st.priced_in_summary || "") + "</div></div>";
    var val = "<div class='card'><h3>Valuation snapshot</h3><div class='kv'>" +
      "<dt>Price</dt><dd class='num'>" + fmtMoney((st.valuation_snapshot.price || {}).value) + " <span class='muted'>[" + esc((st.valuation_snapshot.price || {}).source) + ", as of " + esc((st.valuation_snapshot.price || {}).as_of) + "]</span></dd>" +
      "<dt>Market cap</dt><dd class='num'>" + esc(((st.valuation_snapshot.market_cap || {}).value) || "—") + "</dd>" +
      (st.valuation_snapshot.lines || []).map(function (l) { return "<dt>" + esc(l.name) + "</dt><dd class='num'>" + esc(l.value) + " <span class='muted'>[" + esc(l.tag) + ", " + esc(l.as_of) + "]</span></dd>"; }).join("") + "</div></div>";
    return topbar() + crumbs([{ label: c ? c.title : chainId, href: "#/chain/" + chainId }, { label: ticker }]) + "<main>" +
      (st.fixture ? '<div class="fixturebanner">FIXTURE PAGE — synthetic demo data so the UI can be reviewed; deleted when the first real deep dive lands.</div>' : "") +
      "<h1 style='margin-top:10px'>" + esc(st.ticker) + " <span class='muted' style='font-weight:400;font-size:15px'>" + esc(st.name || "") + "</span></h1>" + vb +
      "<h2>Price</h2><div class='card'>" + priceChart(mk, st) + "</div>" +
      "<div class='twocol' style='margin-top:12px'>" + zones + val + "</div>" +
      "<div class='twocol' style='margin-top:12px'><div class='card'><h3 style='color:var(--good)'>Bull</h3><ul class='bullets'>" + (st.bull || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div>" +
      "<div class='card'><h3 style='color:var(--bad)'>Bear</h3><ul class='bullets'>" + (st.bear || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul></div></div>" +
      "<div style='margin-top:12px'>" + priced + "</div>" +
      "<div style='margin-top:12px'>" + rt + "</div>" +
      notesBlock(st) + changelogBlock(st) + footer() + "</main>";
  }
  function priceChart(mk, st) {
    if (!mk || !mk.series || !(mk.series.rows || []).length) {
      return '<div class="emptystate">No price series yet.<br><br>' + cmdPill("request data " + st.ticker, "queues a fetch; the workflow fills data/market in ~5 minutes") + "</div>";
    }
    var rows = mk.series.rows;
    var step = Math.max(1, Math.floor(rows.length / 420));
    var pts = rows.filter(function (_, i) { return i % step === 0 || i === rows.length - 1; });
    var W = 900, H = 380, P = { l: 56, r: 74, t: 18, b: 34 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    var vals = pts.map(function (r) { return r[1]; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (st.entry_zone) { lo = Math.min(lo, st.entry_zone.low); hi = Math.max(hi, st.no_entry_above || hi); }
    var pad = (hi - lo) * 0.07; lo -= pad; hi += pad;
    function X(i) { return P.l + (i / (pts.length - 1)) * iw; }
    function Y(v) { return P.t + (1 - (v - lo) / (hi - lo)) * ih; }
    var s = '<div class="chartwrap"><svg id="pxchart" viewBox="0 0 ' + W + " " + H + '" width="100%" style="max-width:' + W + 'px" role="img" aria-label="price chart">';
    if (st.entry_zone) s += '<rect x="' + P.l + '" y="' + Y(st.entry_zone.high) + '" width="' + iw + '" height="' + (Y(st.entry_zone.low) - Y(st.entry_zone.high)) + '" fill="var(--band-good)"/><text x="' + (P.l + 6) + '" y="' + (Y(st.entry_zone.high) + 13) + '" font-size="10" fill="var(--und)">entry zone</text>';
    if (st.no_entry_above) s += '<rect x="' + P.l + '" y="' + P.t + '" width="' + iw + '" height="' + Math.max(0, Y(st.no_entry_above) - P.t) + '" fill="var(--band-bad)"/><text x="' + (P.l + 6) + '" y="' + (P.t + 13) + '" font-size="10" fill="var(--ovr)">no entry</text>';
    var ticks = 5;
    for (var t = 0; t <= ticks; t++) {
      var v = lo + ((hi - lo) * t) / ticks;
      s += '<line x1="' + P.l + '" y1="' + Y(v) + '" x2="' + (W - P.r) + '" y2="' + Y(v) + '" stroke="var(--chart-grid)"/>';
      s += '<text x="' + (P.l - 8) + '" y="' + (Y(v) + 3) + '" text-anchor="end" font-size="10" class="mono-t">' + v.toFixed(0) + "</text>";
    }
    var lbl = Math.max(1, Math.floor(pts.length / 6));
    pts.forEach(function (r, i) { if (i % lbl === 0) s += '<text x="' + X(i) + '" y="' + (H - P.b + 16) + '" text-anchor="middle" font-size="9.5" class="mono-t">' + esc(r[0].slice(0, 7)) + "</text>"; });
    (st.events || []).forEach(function (e) {
      var idx = -1;
      pts.forEach(function (r, i) { if (idx < 0 && r[0] >= e.date) idx = i; });
      if (idx < 0) return;
      s += '<line x1="' + X(idx) + '" y1="' + P.t + '" x2="' + X(idx) + '" y2="' + (H - P.b) + '" stroke="var(--ink-3)" stroke-dasharray="2 4"/>' +
        '<text x="' + X(idx) + '" y="' + (P.t - 4 + (((st.events.indexOf(e)) % 2) * 0)) + '" text-anchor="middle" font-size="9" fill="var(--ink-3)">' + esc(e.label.length > 24 ? e.label.slice(0, 23) + "…" : e.label) + "</text>";
    });
    var path = pts.map(function (r, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(r[1]).toFixed(1); }).join("");
    s += '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>';
    var last = pts[pts.length - 1];
    s += '<circle cx="' + X(pts.length - 1) + '" cy="' + Y(last[1]) + '" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>';
    s += '<text x="' + (X(pts.length - 1) + 8) + '" y="' + (Y(last[1]) + 4) + '" font-size="11" font-weight="600" class="mono-t" fill="var(--ink)">' + last[1] + "</text>";
    s += '<rect id="pxhover" x="' + P.l + '" y="' + P.t + '" width="' + iw + '" height="' + ih + '" fill="transparent"/>';
    s += "</svg></div>";
    s += '<div class="muted num" style="margin-top:6px">prices [' + esc(mk.series.source) + ", as of " + esc(mk.series.as_of) + "] · " + esc(mk.price_status) +
      (mk.price_status === "DISPUTED" ? " — both prints shown in data/market, never averaged" : "") + "</div>";
    window.__px = { pts: pts, X: X, Y: Y, P: P, W: W };
    return s;
  }

  /* ---------------- book & shadow ---------------- */
  function bookView() {
    var trades = D.trades || [];
    var rows = trades.map(function (t) {
      var st = null;
      (D.stocks || []).forEach(function (s) { if (s.ticker === t.ticker) st = s; });
      return "<tr><td class='num'>" + esc((t.ts || "").slice(0, 10)) + "</td><td><b>" + esc(t.ticker) + "</b></td><td>" + chip(t.action) + "</td><td class='num'>" + fmtMoney(t.price) + "</td><td>" + esc(t.by) + "</td><td>" +
        (st ? '<a href="#/stock/' + esc(st.ticker) + "/" + esc(st.chain_id) + '">' + chip(st.verdict, st.verdict) + "</a>" : "<span class='muted'>no dive</span>") + "</td><td class='small'>" + esc(t.note || "") + "</td></tr>";
    }).join("");
    return topbar() + crumbs([{ label: "Book" }]) + "<main><h1>Book</h1><p class='lead'>Real positions, logged one line at a time. Calibration measures your money, not hypotheticals.</p>" +
      (trades.length ? '<div class="tablewrap"><table><thead><tr><th>Date</th><th>Ticker</th><th>Action</th><th>Price</th><th>By</th><th>Machine call</th><th>Note</th></tr></thead><tbody>' + rows + "</tbody></table></div>" :
        '<div class="emptystate">No trades logged.<br><br>' + cmdPill('log trade VRT bought 112 "starter position"') + "</div>") +
      footer() + "</main>";
  }
  function shadowView() {
    var rows = ((D.shadow || {}).book || {}).rows || [];
    var res = ((D.shadow || {}).results) || {};
    var right = 0, graded = 0;
    var body = rows.map(function (r) {
      var x = res[r.id];
      if (x && x.call) { graded++; if (x.call === "RIGHT") right++; }
      return "<tr><td class='num'>" + esc(r.verdict_date) + "</td><td><b>" + esc(r.ticker) + "</b></td><td>" + chip(r.origin) + "</td><td class='num'>" + fmtMoney((r.spot || {}).value) + "</td><td class='num'>" + esc(r.review_at) + "</td>" +
        "<td>" + (x ? "<span class='num'>" + esc(x.delta_pct) + "% vs SPY</span> " + chip(x.call, x.call) : chip("awaiting +90d", "neutral")) + "</td></tr>";
    }).join("");
    return topbar() + crumbs([{ label: "Shadow book" }]) + "<main><h1>Shadow book</h1><p class='lead'>Every TOO LATE verdict and dismissed signal, repriced at +90 days vs SPY. RIGHT means skipping was correct. The machine's \"no\" gets graded here.</p>" +
      (graded ? "<div class='card' style='max-width:340px'><h3>TOO LATE hit rate</h3><div class='num' style='font-size:26px;font-weight:700'>" + Math.round((100 * right) / graded) + "%</div><div class='muted'>" + right + " of " + graded + " graded calls were right</div></div>" : "") +
      (rows.length ? '<div class="tablewrap" style="margin-top:12px"><table><thead><tr><th>Verdict date</th><th>Ticker</th><th>Origin</th><th>Spot</th><th>Reprice at</th><th>Result</th></tr></thead><tbody>' + body + "</tbody></table></div>" :
        '<div class="emptystate" style="margin-top:12px">Empty — it fills automatically from TOO LATE verdicts and dismissed signals.</div>') +
      footer() + "</main>";
  }

  /* ---------------- shared blocks ---------------- */
  function notesBlock(obj) {
    var n = obj.notes || [];
    return "<h2>Notes</h2>" + (n.length ? n.map(function (x) {
      return '<div class="note"><span class="who">' + esc(x.by) + " · " + esc((x.ts || "").slice(0, 10)) + "</span><br>" + esc(x.text) + "</div>";
    }).join("") : "<div class='muted'>none — add one with <span class='mono'>note " + esc(obj.id || obj.ticker || "") + ' "..."</span></div>');
  }
  function changelogBlock(obj) {
    var c = (obj.changelog || []).slice().reverse();
    if (!c.length) return "";
    return "<h2>History</h2><div class='timeline'>" + c.map(function (x) {
      return '<div class="t"><span class="when">' + esc((x.ts || "").slice(0, 10)) + " · " + esc(x.by) + "</span><br>" + esc(x.change) + (x.prior ? " <span class='muted'>(was: " + esc(x.prior) + ")</span>" : "") + "</div>";
    }).join("") + "</div>";
  }
  function notFound(what) {
    return topbar() + "<main><div class='emptystate'>Not found: " + esc(what) + '<br><br><a href="#/">back to radar</a></div>' + footer() + "</main>";
  }

  /* ---------------- router & events ---------------- */
  function route() {
    var h = location.hash || "#/";
    var p = h.replace(/^#\//, "").split("/").map(decodeURIComponent);
    var html;
    if (!p[0]) html = homeView();
    else if (p[0] === "signal") html = signalView(p[1]);
    else if (p[0] === "chain") html = chainView(p[1], p[2]);
    else if (p[0] === "screen") html = screenView(p[1], p[2]);
    else if (p[0] === "stock") html = stockView(p[1], p[2]);
    else if (p[0] === "book") html = bookView();
    else if (p[0] === "shadow") html = shadowView();
    else html = homeView();
    app.innerHTML = html;
    window.scrollTo(0, 0);
    wire();
  }
  function wire() {
    app.querySelectorAll("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        var txt = b.getAttribute("data-copy");
        function done() { b.textContent = "copied"; setTimeout(function () { b.textContent = "copy"; }, 1400); }
        try { navigator.clipboard.writeText(txt).then(done, function () { fallbackCopy(txt); done(); }); }
        catch (err) { fallbackCopy(txt); done(); }
      });
    });
    app.querySelectorAll("[data-nav]").forEach(function (b) { b.addEventListener("click", function () { location.hash = b.getAttribute("data-nav"); }); });
    app.querySelectorAll("[data-tab]").forEach(function (b) {
      b.addEventListener("click", function () { chainTab = b.getAttribute("data-tab"); location.hash = "#/chain/" + b.getAttribute("data-chain") + "/" + chainTab; });
    });
    app.querySelectorAll("[data-drawer]").forEach(function (n) {
      function open() {
        var chainId = (location.hash.split("/")[2] || "");
        var c = byId(D.chains, chainId);
        var l = c && byId(c.links, n.getAttribute("data-drawer"));
        if (!l) return;
        var host = document.getElementById("drawerHost");
        host.innerHTML = drawer(c, l);
        host.querySelector("[data-closedrawer]").addEventListener("click", function () { host.innerHTML = ""; });
        host.querySelectorAll("[data-copy]").forEach(function (b) {
          b.addEventListener("click", function () { try { navigator.clipboard.writeText(b.getAttribute("data-copy")); b.textContent = "copied"; } catch (e) { fallbackCopy(b.getAttribute("data-copy")); } });
        });
      }
      n.addEventListener("click", open);
      n.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
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
        tip.style.display = "block"; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY - 10 + "px";
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
  setTimeout(stampVisit, 4000);
})();
