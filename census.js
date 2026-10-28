const fmt = n => Number(n).toLocaleString("en-GB");
const DASH = "—";
// Every value that reaches the DOM goes through these, so a missing or malformed field
// degrades to an em-dash instead of printing NaN / undefined / Infinity.
const numOr = (v, fb) => {
  // Number(null), Number("") and Number([]) are all 0, so a bare Number.isFinite test
  // turns "this field is absent" into a published zero. Those are different claims and
  // the difference is the whole point of the height table, so reject them up front.
  if (v === null || v === undefined || v === "" ||
      typeof v === "boolean" || Array.isArray(v)) return fb;
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
};
const fmtOr = v => { const n = numOr(v, null); return n === null ? DASH : fmt(n); };
const clamp = (n, lo, hi) => n < lo ? lo : n > hi ? hi : n;
const days = b => (Math.abs(b) < 0.05 ? 0 : b * 10 / 60 / 24).toFixed(1);
const esc = v => String(v).replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = id => document.getElementById(id);
// Builds an element from a tag, attributes and children. Children that are strings are
// inserted as text, so a value from the data files can never become markup.
const el = (tag, attrs, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "style") e.style.cssText = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null && k !== undefined) e.append(k);
  return e;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const p2 = n => String(n).padStart(2, "0");
// Everything on this page is stated in UTC, including the stamp in the masthead.
const dayLabel = d => d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
const timeLabel = d => p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes());
const utcLabel = ts => { const d = new Date(ts * 1000);
  return dayLabel(d) + " " + timeLabel(d) + " UTC"; };

// The adoption chart is fed by two independent fetches: history.json supplies the series,
// data.json supplies the activation height used for the marker. Either may land first.
let ADOPT_SERIES = null;
let ACTIVATION_HEIGHT = null;

function showError(msg) {
  const e = document.getElementById("err");
  e.style.display = "block";
  // msg carries a thrown Error's message, and a JSON parse failure quotes the response
  // body back inside it, so it is added as text, never as markup.
  e.replaceChildren(el("strong", null, "Could not load the latest figures."), " " + String(msg));
}

// Headline: every post-fork node that answered the crawler recently, archival AND pruned.
// The seeder only rates a node "good" if it advertises NODE_NETWORK, so b2b_reachable (still
// published unchanged for other consumers) leaves pruned nodes out. A sample without the
// storage_* fields falls back to the old figure and wording rather than showing a partial sum.
function renderHeadlineCount(d) {
  const a = numOr(d.storage_archival, null), p = numOr(d.storage_pruned, null),
        o = numOr(d.storage_other, null), good = numOr(d.b2b_reachable, null);
  const k = document.getElementById("figUpK"), note = document.getElementById("figUpNote");
  if (a === null || p === null || o === null) {
    document.getElementById("figUp").textContent = fmtOr(d.b2b_reachable);
    k.textContent = "post-fork nodes rated good";
    note.textContent = "by the seeder’s own quality gate";
    return;
  }
  document.getElementById("figUp").textContent = fmt(a + p + o);
  k.textContent = "post-fork nodes reachable";
  note.textContent = fmt(a) + " archival · " + fmt(p) + " pruned" +
    (o > 0 ? " · " + fmt(o) + " other" : "") + ". The sections below count only the " +
    (good === null ? DASH : fmt(good)) + " rated good by the seeder, which excludes pruned nodes.";
}

function render(d) {
  const stampTs = numOr(d.ts, null);
  document.getElementById("stamp").textContent = stampTs === null ? DASH
    : new Date(stampTs * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  renderHeadlineCount(d);
  document.getElementById("figB2").textContent = fmtOr(d.b2b);
  document.getElementById("figCrawl").textContent = fmtOr(d.total);
  document.getElementById("figTip").textContent = fmtOr(d.max_height);

  const versions = (d.versions && typeof d.versions === "object") ? d.versions : {};
  const entries = Object.entries(versions)
    .map(([name, n]) => [String(name), numOr(n, 0)])
    .sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(e => e[1]), 1);
  const enforcing = d.enforcing_versions || [];
  document.getElementById("versionBars").replaceChildren(...entries.map(([name, n]) => {
    const isRc = /rc\d/i.test(name);
    const isOdd = !/Knots/i.test(name);
    const cls = isOdd ? "odd" : isRc ? "rc" : "";
    const tag = isOdd ? "  (not Knots, but claims the post-fork bit)"
              : isRc ? "  (release candidate)" : "";
    return el("div", { class: cls ? "bar-row " + cls : "bar-row" },
      el("span", { class: "bar-label" }, name + tag),
      el("span", { class: "bar-val" }, String(n)),
      el("span", { class: "bar-track" },
        el("span", { class: "bar-fill", style: "width:" + (n / max * 100).toFixed(1) + "%" })));
  }));

  renderActivation(d, entries, enforcing);
  renderHeights(d);
  renderNetworks(d);
  renderStorage(d);
  renderShareTiles(d);

  // The adoption chart needs the activation height to place its marker, but that value
  // arrives on this fetch while the series arrives on another. Stash it and redraw; the
  // chart draws the marker only once both halves are present, in whichever order.
  ACTIVATION_HEIGHT = numOr(d.activation_height, null);
  if (ADOPT_SERIES) drawAdoption();
}

// ---- Where the reachable nodes are: clearnet vs Tor vs I2P ----
// Only this seed publishes reachable Tor and I2P fork-node counts; most crawlers never
// reach those networks. The field is absent in samples written before it existed, in which
// case the whole section hides rather than rendering a misleading empty or zeroed panel.
function renderNetworks(d) {
  const sec = $("networksSection");
  if (!sec) return;
  const net = (d.networks && typeof d.networks === "object" && !Array.isArray(d.networks))
    ? d.networks : null;
  const rows = net ? [
    ["Tor (v3 onion)", "tor"],
    ["I2P", "i2p"],
    ["IPv4 (clearnet)", "ipv4"],
    ["IPv6 (clearnet)", "ipv6"]
  ].map(([label, k]) => [label, numOr(net[k], 0)]) : [];
  const total = rows.reduce((s, r) => s + r[1], 0);
  // No field, or a field that sums to zero (nothing reachable yet): nothing to show.
  if (!net || total <= 0) { sec.hidden = true; return; }
  sec.hidden = false;
  rows.sort((a, b) => b[1] - a[1]);
  const max = Math.max(...rows.map(r => r[1]), 1);
  $("networkBars").replaceChildren(...rows.map(([label, n]) => el("div", { class: "bar-row" },
    el("span", { class: "bar-label" }, label),
    el("span", { class: "bar-val" }, fmt(n) + "  ·  " + (n / total * 100).toFixed(1) + "%"),
    el("span", { class: "bar-track" },
      el("span", { class: "bar-fill", style: "width:" + (n / max * 100).toFixed(1) + "%" })))));
}

// ---- Archival vs pruned (storage_* from the advertised service bits) ----
function renderStorage(d) {
  const sec = $("storageSection");
  if (!sec) return;
  const keys = [["Archival (full history)", "storage_archival"],
                ["Pruned (recent blocks only)", "storage_pruned"],
                ["Advertises neither", "storage_other"]];
  // Absent field = an older sample that predates it: hide, never show zeros.
  if (!keys.every(([, k]) => numOr(d[k], null) !== null)) { sec.hidden = true; return; }
  const rows = keys.map(([label, k]) => [label, numOr(d[k], 0)]).filter(r => r[1] > 0);
  const total = rows.reduce((s, r) => s + r[1], 0);
  if (total <= 0) { sec.hidden = true; return; }
  sec.hidden = false;
  rows.sort((a, b) => b[1] - a[1]);
  const max = Math.max(...rows.map(r => r[1]), 1);
  $("storageNote").textContent = fmt(total) + " post-fork nodes completed a handshake in the last " +
    fmt(POLL_FRESH_SECONDS / 60 | 0) + " minutes. A lower bound measured by one crawler, from the service bits " +
    "each node advertises.";
  $("storageBars").replaceChildren(...rows.map(([label, n]) => el("div", { class: "bar-row" },
    el("span", { class: "bar-label" }, label),
    el("span", { class: "bar-val" }, fmt(n) + "  ·  " + (n / total * 100).toFixed(1) + "%"),
    el("span", { class: "bar-track" },
      el("span", { class: "bar-fill", style: "width:" + (n / max * 100).toFixed(1) + "%" })))));
}

// ---- Are nodes agreeing on the chain? (aggregate + split by enforcement status) ----
function renderHeights(d) {
  const h = (d.heights && typeof d.heights === "object") ? d.heights : {};
  const hbe = (d.heights_by_enforcement && typeof d.heights_by_enforcement === "object" &&
               !Array.isArray(d.heights_by_enforcement)) ? d.heights_by_enforcement : null;
  // The two groups are read independently: one can be published while the other is not.
  const grp = k => (hbe && hbe[k] && typeof hbe[k] === "object" && !Array.isArray(hbe[k]))
    ? hbe[k] : null;
  const en = grp("enforcing"), ne = grp("not_enforcing");
  // An absent group renders as an em-dash, never as a zero: "not published" and
  // "published as none" are different claims.
  const cell = (g, k) => g ? fmtOr(g[k]) : DASH;

  $("heightRows").replaceChildren(...[
    ["At the highest block seen", "tip"],
    ["Within 6 blocks", "near"],
    ["Within 144 blocks (about a day)", "day"],
    ["More than 144 blocks behind", "behind"]
  ].map(([l, k]) => el("tr", null,
    el("td", null, l),
    el("td", { class: "n" }, fmtOr(h[k])),
    el("td", { class: "n" }, cell(en, k)),
    el("td", { class: "n" }, cell(ne, k)))));

  // Conclusion derived from this render's data — nothing about it is hardcoded.
  const nb = ne ? numOr(ne.behind, null) : null;
  const eb = en ? numOr(en.behind, null) : null;
  let msg;
  if (!hbe) {
    msg = "This sample does not publish the height breakdown split by enforcement status, " +
      "so the two groups cannot be compared from it. The figures above are the aggregate " +
      "over every post-fork node, as before.";
  } else if (nb === null) {
    msg = "The non-enforcing group has no height breakdown in this sample, so no comparison " +
      "between the two groups can be drawn from it. Read the aggregate column only.";
  } else if (nb === 0) {
    msg = "No divergence observed between the two groups: no non-enforcing node is more than " +
      "144 blocks behind the highest block seen" +
      (eb === 0 ? ", and neither is any enforcing node" : "") +
      ". Read that narrowly. It is the absence of a signal in one crawler's snapshot, built " +
      "from heights each node reported at its last handshake, and it is not proof that " +
      "consensus is safe. A split can exist among nodes this crawler never reached, or " +
      "appear later. A real one would show as a persistent cluster in the bottom row across " +
      "several updates.";
  } else {
    msg = fmt(nb) + " non-enforcing node" + (nb === 1 ? " is" : "s are") + " more than 144 " +
      "blocks behind the highest block seen, against " +
      (eb === null ? "an unpublished number of" : fmt(eb)) + " enforcing node" +
      (eb === 1 ? "" : "s") + ". A single snapshot cannot tell a chain split apart from a " +
      "stale or slow crawl, so this warrants watching as a persistent cluster across several " +
      "updates before it is read as divergence.";
  }
  $("divergeNote").textContent = msg;
}

// ---- Two context tiles: post-fork share of the crawl, and measurement freshness ----
const POLL_FRESH_SECONDS = 4980;
function pctOf(a, b) {
  const x = numOr(a, null), y = numOr(b, null);
  if (x === null || y === null || y <= 0) return null;
  return clamp(x / y * 100, 0, 100);
}
// A null percentage is a missing measurement and prints as an em-dash, never "0%".
// Values under 1% keep one decimal so a small-but-real share never reads as zero.
const pctStr = p => p === null ? DASH
  : (p > 0 && p < 1 ? p.toFixed(1) : String(Math.round(p))) + "%";

function renderShareTiles(d) {
  const shareAll = pctOf(d.b2b, d.total);
  const shareReach = pctOf(d.b2b_reachable, d.reachable);
  $("figShare").textContent = pctStr(shareAll);
  $("figShareNote").textContent =
    (shareReach === null
      ? "The reachable-node share is not available in this sample. "
      : pctStr(shareReach) + " of the nodes rated good are post-fork nodes. ") +
    "The crawler bootstraps from post-fork seeds, so this is not an unbiased sample of the " +
    "whole Bitcoin network. It overstates the post-fork share of it.";

  const fresh = pctOf(d.b2b_polled_recently, d.b2b);
  const med = numOr(d.b2b_poll_age_median, null);
  const p90 = numOr(d.b2b_poll_age_p90, null);
  $("figFresh").textContent = pctStr(fresh);
  $("figFreshNote").textContent =
    "“Recently” means within " + fmt(POLL_FRESH_SECONDS) + " seconds (83 minutes), " +
    "the age at which a node decays out of the seeder's good set. Median age " +
    (med === null ? DASH : fmt(Math.round(Math.abs(med) < 0.5 ? 0 : med)) + "s") +
    ", 90th percentile " +
    (p90 === null ? DASH : fmt(Math.round(Math.abs(p90) < 0.5 ? 0 : p90)) + "s") + ".";
}

function renderActivation(d, entries, enforcing) {
  // 979920 is the height at which NORMAL coinbase maturity resumes; 979919 is the last
  // block under the long maturity rule. data.json now publishes activation_end_height and
  // that value is preferred; this constant is only a last-resort fallback for an older
  // sample that predates the field.
  const MATURITY_RELEASE_HEIGHT = 979920;
  const relH = numOr(d.activation_end_height ?? MATURITY_RELEASE_HEIGHT, MATURITY_RELEASE_HEIGHT);
  const act = numOr(d.activation_height, null);
  const tip = numOr(d.max_height, null);
  const reach = Math.max(0, numOr(d.b2b_reachable, 0));

  const title = $("actTitle"), windowLine = $("actWindowLine");
  const progress = $("actProgress"), risk = $("actRisk");

  const setTile = (id, big, lbl, sub) => {
    $(id).textContent = big;
    $(id + "Lbl").textContent = lbl;
    const s = $(id + "Sub");
    s.textContent = sub || "";
    s.hidden = !sub;
  };

  // --- Neutral fallback: without both heights there is no honest state to show. ---
  if (act === null || tip === null) {
    title.replaceChildren("Long coinbase maturity, activation height ",
      el("span", { class: "num", id: "actHeight" }, DASH));
    windowLine.hidden = true;
    progress.hidden = true;
    risk.hidden = true;
    setTile("cdBlocks", DASH, "blocks remaining", "");
    setTile("cdDays", DASH, "days, at ~10 min/block", "");
    setTile("cdReady", DASH, "of reachable post-fork nodes on a release containing it", "");
    return;
  }

  // Sum of nodes whose user agent is listed as containing the rule.
  const ready = entries
    .filter(([v]) => enforcing.includes(v))
    .reduce((a, [, n]) => a + n, 0);
  const pct = reach > 0 ? Math.round(ready / reach * 100) + "%" : DASH;
  const pctSub = reach > 0
    ? fmt(ready) + " of " + fmt(reach) + " reachable post-fork nodes"
    : "no reachable post-fork nodes counted";

  // ---------------- STATE A: not yet activated ----------------
  if (tip < act) {
    title.replaceChildren("Long coinbase maturity activates at block ",
      el("span", { class: "num", id: "actHeight" }, fmt(act)));
    windowLine.hidden = true;
    progress.hidden = true;
    risk.hidden = true;
    const remaining = act - tip;
    setTile("cdBlocks", fmt(remaining), "blocks remaining", "");
    setTile("cdDays", days(remaining), "days, at ~10 min/block", "");
    setTile("cdReady", pct, "of reachable post-fork nodes on a release containing it", pctSub);
    return;
  }

  // ---------------- STATE B: in force (or window complete) ----------------
  const span = relH - act;                       // window length in blocks
  const lastBlock = span > 0 ? relH - 1 : null;  // last block under the long maturity rule
  const complete = span > 0 && tip >= relH;
  const elapsed = tip - act;
  const toRelease = span > 0 ? Math.max(0, relH - tip) : null;

  title.textContent = complete
    ? "Long coinbase maturity window is complete"
    : "Long coinbase maturity is in force";

  if (span > 0) {
    windowLine.textContent =
      "Took effect at block " + fmt(act) + " and is in force through block " + fmt(lastBlock) +
      " — " + fmt(span) + " blocks, about " + Math.round(span * 10 / 60 / 24) +
      " days. Normal maturity resumes at block " + fmt(relH) + ".";
  } else {
    windowLine.textContent = "Took effect at block " + fmt(act) + ".";
  }
  windowLine.hidden = false;

  setTile("cdBlocks", fmt(elapsed), "blocks since it took effect",
    "about " + days(elapsed) + " days, at ~10 min/block");

  if (toRelease === null) {
    setTile("cdDays", DASH, "blocks until normal maturity resumes",
      "window end height unavailable");
  } else if (complete) {
    setTile("cdDays", "0", "blocks until normal maturity resumes at block " + fmt(relH),
      "window complete — normal maturity has resumed");
  } else {
    setTile("cdDays", fmt(toRelease),
      "blocks until normal maturity resumes at block " + fmt(relH),
      "about " + days(toRelease) + " days, at ~10 min/block");
  }

  setTile("cdReady", pct, "of reachable post-fork nodes on a release containing it", pctSub);

  // ---- progress through the window ----
  if (span > 0) {
    const now = clamp(tip, act, relH);
    const frac = clamp((now - act) / span, 0, 1);
    const bar = $("actBar");
    bar.setAttribute("aria-valuemin", String(act));
    bar.setAttribute("aria-valuemax", String(relH));
    bar.setAttribute("aria-valuenow", String(now));
    bar.setAttribute("aria-valuetext",
      "block " + fmt(now) + " of " + fmt(relH) + ", " + Math.round(frac * 100) + " per cent");
    $("actFill").style.width = (frac * 100).toFixed(1) + "%";
    $("actProgressText").textContent = complete
      ? "Highest block seen is " + fmt(tip) + ", past the end of the window. All " +
        fmt(span) + " blocks (" + fmt(act) + " through " + fmt(lastBlock) + ") are behind us."
      : "Block " + fmt(tip) + " of the " + fmt(span) + "-block window (" + fmt(act) +
        " through " + fmt(lastBlock) + "): " + fmt(elapsed) + " blocks in, " + fmt(toRelease) +
        " to go, " + Math.round(frac * 100) + "% through.";
    progress.hidden = false;
  } else {
    progress.hidden = true;
  }

  // ---- non-enforcing breakdown ----
  const nonEnf = entries.filter(([v]) => !enforcing.includes(v));
  const listed = nonEnf.reduce((a, [, n]) => a + n, 0);
  const headline = clamp(reach - ready, 0, Number.MAX_SAFE_INTEGER);
  $("neCount").textContent = reach > 0 ? fmt(headline) : DASH;
  $("neCountLbl").textContent = reach > 0
    ? "of " + fmt(reach) + " reachable post-fork nodes are not on a release containing the rule"
    : "no reachable post-fork nodes counted, so there is nothing to measure against";

  const rows = nonEnf.slice();
  const unrecorded = headline - listed;
  if (reach > 0 && unrecorded > 0) rows.push(["software not recorded", unrecorded]);
  const rowMax = Math.max(...rows.map(r => r[1]), 1);
  $("neList").replaceChildren(...(rows.length
    ? rows.map(([name, n]) => {
        // OracleKnots is a third-party build whose source cannot be verified from a user
        // agent string. It is listed as unverified, never as failing to enforce.
        const tag = /oracle/i.test(name) ? "  — unverified third-party build" : "";
        return el("div", { class: "bar-row" },
          el("span", { class: "bar-label" }, String(name) + tag),
          el("span", { class: "bar-val" }, fmt(n)),
          el("span", { class: "bar-track" },
            el("span", { class: "bar-fill", style: "width:" + (n / rowMax * 100).toFixed(1) + "%" })));
      })
    : [el("p", { class: "ne-note", style: "margin-top:0" }, reach > 0
        ? "Every reachable post-fork node the crawler identified is on a release containing the rule."
        : "No reachable post-fork node was counted this crawl, so there is nothing to break down.")]));

  $("neNote").textContent = unrecorded > 0
    ? "Counts come from the user agent each node reported. " + fmt(unrecorded) +
      " node(s) are in the reachable total but reported no software this crawl, so they are " +
      "shown as not recorded rather than attributed to a release."
    : "Counts come from the user agent each node reported. A node on an older release is " +
      "only on a different chain if a block spends a coinbase output younger than the long " +
      "maturity.";
  risk.hidden = false;
}

function renderStable(s) {
  if (!s || typeof s !== "object") throw new Error("stable-nodes.json is not an object");
  $("stQual").textContent = fmtOr(s.qualifying_count);
  $("stSeen").textContent = fmtOr(s.observed_count);
  const ageD = numOr(s.crawler_age_days, null);
  $("stAge").textContent = ageD === null ? DASH : ageD.toFixed(1);
  const crit = (s.criterion && typeof s.criterion === "object") ? s.criterion : {};
  const critPct = numOr(crit.threshold_pct, null);
  $("stCrit").textContent = (critPct === null || !crit.window)
    ? DASH
    : "≥" + critPct + "% over " + String(crit.window);

  // Addresses and window labels come off the wire; they are escaped like any other
  // remote string, and every uptime figure is guarded so a missing one cannot print NaN.
  const pctCell = v => {
    const x = numOr(v, null);
    if (x === null) return DASH;
    // clamp passes -0 straight through and (-0).toFixed(1) is "-0.0".
    const y = clamp(x, 0, 100);
    return (y === 0 ? 0 : y).toFixed(1) + "%";
  };
  const rows = Array.isArray(s.nodes) ? s.nodes.slice(0, 25) : [];
  $("stableRows").replaceChildren(...(rows.length
    ? rows.map(n => {
        const u = (n && n.uptime && typeof n.uptime === "object") ? n.uptime : {};
        return el("tr", null,
          el("td", { style: "font-family:var(--sans);font-size:12.5px;word-break:break-all" },
            String((n && n.address) != null ? n.address : DASH)),
          el("td", { class: "n" }, pctCell(u["2h"])),
          el("td", { class: "n" }, pctCell(u["1d"])),
          el("td", { class: "n" }, pctCell(u["7d"])));
      })
    : [el("tr", null, el("td", { colspan: "4", style: "color:var(--text-3)" },
        "No node clears the bar yet. The crawler needs more observation time before any " +
        "longer-window figure is trustworthy."))]));
}

// ------------------------- adoption curve (history.json) -------------------------

function renderHistory(h) {
  // A bare top-level array is the published shape. Anything else is treated as a failed
  // load and handled by the one catch, so the section hides and nothing else is touched.
  if (!Array.isArray(h)) throw new Error("history.json is not an array");

  const pts = [];
  let gaps = 0, clamped = 0;
  for (const raw of h) {
    if (!raw || typeof raw !== "object") continue;
    const ts = numOr(raw.ts, null);
    if (ts === null || ts <= 0) continue;
    const reach = Math.max(0, numOr(raw.b2b_reachable, 0));
    const enf = Math.max(0, numOr(raw.enforcing, 0));
    const mh = numOr(raw.max_height, null);
    // A sample with no reachable post-fork node has no ratio to plot. It becomes a break in
    // the line, never a division by zero and never a fabricated zero per cent.
    let pct = null;
    if (reach > 0) {
      if (enf > reach) clamped++;
      pct = clamp(enf / reach * 100, 0, 100);
    } else {
      gaps++;
    }
    pts.push({ ts, reach, enf, mh, pct });
  }
  pts.sort((a, b) => a.ts - b.ts);

  const plotted = pts.filter(p => p.pct !== null);
  if (plotted.length < 2) throw new Error("fewer than two usable samples");

  ADOPT_SERIES = pts;
  $("adoptionSection").hidden = false;

  const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts;
  const hours = (t1 - t0) / 3600;
  const spanTxt = hours >= 48 ? (hours / 24).toFixed(1) + " days"
    : hours >= 1 ? Math.round(hours) + " hours" : "under an hour";
  $("adoptCap").textContent =
    fmt(plotted.length) + " samples over " + spanTxt + ", " + utcLabel(t0) + " to " +
    utcLabel(t1) + ". Plotted raw: no smoothing and no moving average. " +
    (gaps ? fmt(gaps) + (gaps === 1
      ? " sample counted no reachable post-fork node at all and appears as a break in the line, "
      : " samples counted no reachable post-fork node at all and appear as breaks in the line, ") +
      "not as zero per cent. " : "") +
    (clamped ? fmt(clamped) + (clamped === 1
      ? " sample reported more enforcing nodes than reachable ones and is clamped to "
      : " samples reported more enforcing nodes than reachable ones and are clamped to ") +
      "100 per cent. " : "") +
    "Read this as a lower bound measured by one crawler. The denominator is the count of " +
    "post-fork nodes that accept incoming connections and currently clear the seeder's own " +
    "quality gate, and that gate decays with crawl timing, so the denominator moves on its " +
    "own. Some historical samples are also known to be truncated reads with implausibly " +
    "low totals. A dip in this line is at least as likely to be a measurement artefact as " +
    "nodes actually downgrading.";

  const MAX_ROWS = 300;
  const shown = pts.length > MAX_ROWS ? pts.slice(-MAX_ROWS) : pts;
  $("adoptRows").replaceChildren(...shown.map(p => el("tr", null,
    el("td", { class: "n", style: "text-align:left" }, utcLabel(p.ts)),
    el("td", { class: "n" }, fmtOr(p.enf)),
    el("td", { class: "n" }, fmtOr(p.reach)),
    el("td", { class: "n" }, p.pct === null ? DASH : Math.round(p.pct) + "%"))));
  $("adoptRowsNote").textContent = shown.length < pts.length
    ? "Showing the most recent " + fmt(shown.length) + " of " + fmt(pts.length) +
      " samples. The full series is in history.json."
    : "All " + fmt(pts.length) + " samples held by the crawler. A dash in the share " +
      "column is a sample with no reachable post-fork node to divide by.";

  drawAdoption();
}

function drawAdoption() {
  const host = $("adoptHost");
  if (!host || !ADOPT_SERIES || ADOPT_SERIES.length < 2) return;
  const series = ADOPT_SERIES;

  const oldSvg = host.querySelector("svg");
  if (oldSvg) oldSvg.remove();
  const tipEl = $("adoptTip");
  tipEl.hidden = true;

  // Redrawn against the real pixel width rather than scaled by preserveAspectRatio, so
  // axis text stays at its true size instead of shrinking to 6px on a narrow phone.
  const W = Math.max(280, Math.round(numOr(host.clientWidth, 0) || 600));
  const H = W < 430 ? 190 : W < 640 ? 215 : 240;
  const padL = 36, padR = W < 430 ? 38 : 54, padT = 14, padB = 34;
  const pw = Math.max(10, W - padL - padR), ph = Math.max(10, H - padT - padB);

  const t0 = series[0].ts, t1 = series[series.length - 1].ts;
  const spanT = t1 - t0;
  // Y is fixed 0-100. Auto-scaling a percentage axis exaggerates the trend.
  const Y = pct => padT + (1 - pct / 100) * ph;
  const pts = series.map((p, i) => ({
    ts: p.ts, reach: p.reach, enf: p.enf, mh: p.mh, pct: p.pct,
    x: spanT > 0 ? padL + (p.ts - t0) / spanT * pw
                 : padL + i / (series.length - 1) * pw,
    y: p.pct === null ? null : Y(p.pct)
  }));

  // Contiguous runs of plottable samples; a gap ends a run so the line breaks there.
  const runs = [];
  let cur = [];
  for (const p of pts) {
    if (p.y === null) { if (cur.length) { runs.push(cur); cur = []; } }
    else cur.push(p);
  }
  if (cur.length) runs.push(cur);

  const n1 = v => v.toFixed(1);
  const base = Y(0);
  let areaD = "", lineD = "", dots = "";
  for (const r of runs) {
    if (r.length === 1) {
      dots += '<circle class="ch-dot" cx="' + n1(r[0].x) + '" cy="' + n1(r[0].y) + '" r="3"/>';
      continue;
    }
    const seg = r.map((p, i) => (i ? "L" : "M") + n1(p.x) + " " + n1(p.y)).join(" ");
    lineD += seg + " ";
    areaD += seg + " L" + n1(r[r.length - 1].x) + " " + n1(base) +
             " L" + n1(r[0].x) + " " + n1(base) + " Z ";
  }

  let grid = "";
  for (const v of [0, 25, 50, 75, 100]) {
    grid += '<line class="ch-grid" x1="' + padL + '" y1="' + n1(Y(v)) + '" x2="' +
      n1(padL + pw) + '" y2="' + n1(Y(v)) + '"/>' +
      '<text class="ch-axis" x="' + (padL - 7) + '" y="' + n1(Y(v) + 3.5) +
      '" text-anchor="end">' + v + "%</text>";
  }

  // Tick count is set by width, so labels thin out on a narrow screen instead of colliding.
  const nTicks = W < 420 ? 3 : W < 640 ? 4 : 6;
  const seen = new Set();
  let xax = "";
  for (let k = 0; k < nTicks; k++) {
    const i = Math.round(k * (pts.length - 1) / (nTicks - 1));
    if (seen.has(i)) continue;
    seen.add(i);
    const p = pts[i], d = new Date(p.ts * 1000);
    const anchor = i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle";
    xax += '<text class="ch-axis" x="' + n1(p.x) + '" y="' + (padT + ph + 15) +
        '" text-anchor="' + anchor + '">' + esc(dayLabel(d)) + "</text>" +
      '<text class="ch-axis" x="' + n1(p.x) + '" y="' + (padT + ph + 27) +
        '" text-anchor="' + anchor + '">' + esc(timeLabel(d)) + "</text>";
  }

  // Activation marker. Drawn only when the height is known AND the series actually
  // reaches it — never at a default position, which would assert a moment we cannot see.
  let mark = "", markTs = null;
  if (ACTIVATION_HEIGHT !== null) {
    const hitPt = pts.find(p => p.mh !== null && p.mh >= ACTIVATION_HEIGHT);
    if (hitPt) {
      markTs = hitPt.ts;
      const right = hitPt.x > padL + pw * 0.62;
      mark = '<line class="ch-mark" x1="' + n1(hitPt.x) + '" y1="' + padT + '" x2="' +
        n1(hitPt.x) + '" y2="' + n1(padT + ph) + '"/>' +
        '<text class="ch-mark-t" x="' + n1(hitPt.x + (right ? -5 : 5)) + '" y="' + (padT + 9) +
        '" text-anchor="' + (right ? "end" : "start") + '">rule in force</text>';
    }
  }

  const plotted = pts.filter(p => p.y !== null);
  // Belt and braces: renderHistory already refuses a series with fewer than two
  // plottable samples, so this only fires if that guard is ever loosened. Bailing
  // here is what keeps the aria text below from reading a property off undefined.
  if (plotted.length < 2) { $("adoptionSection").hidden = true; return; }
  const first = plotted[0], last = plotted[plotted.length - 1];
  let end = "";
  if (last) {
    const lbl = Math.round(last.pct) + "%";
    const toRight = last.x + 9 + lbl.length * 7.6 <= W - 2;
    end = '<circle class="ch-dot" cx="' + n1(last.x) + '" cy="' + n1(last.y) + '" r="4"/>' +
      '<text class="ch-end" x="' + n1(last.x + (toRight ? 9 : -9)) + '" y="' +
      n1(clamp(last.y, padT + 6, padT + ph) + 4.5) + '" text-anchor="' +
      (toRight ? "start" : "end") + '">' + esc(lbl) + "</text>";
  }

  const aria = "Line chart. Share of reachable post-fork nodes on a release containing the rule: " +
    Math.round(first.pct) + " per cent at " + utcLabel(first.ts) + ", rising to " +
    Math.round(last.pct) + " per cent at " + utcLabel(last.ts) + "." +
    (markTs ? " The rule took effect at " + utcLabel(markTs) + "." : "") +
    " Vertical axis fixed from 0 to 100 per cent. Every value is also in the table " +
    "below the chart.";

  host.insertAdjacentHTML("afterbegin",
    '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'role="img" tabindex="0" aria-label="' + esc(aria) + '">' +
      grid + xax +
      '<path class="ch-area" d="' + areaD + '"/>' +
      '<path class="ch-line" d="' + lineD + '"/>' +
      dots + mark + end +
      '<line class="ch-cross" id="adoptCross" x1="0" y1="' + padT + '" x2="0" y2="' +
        n1(padT + ph) + '" style="display:none"/>' +
      '<circle class="ch-dot" id="adoptHot" cx="0" cy="0" r="4" style="display:none"/>' +
      '<rect class="ch-hit" id="adoptHit" x="' + padL + '" y="' + padT + '" width="' +
        n1(pw) + '" height="' + n1(ph) + '"/>' +
    "</svg>");

  if (!plotted.length) return;
  const svg = host.querySelector("svg");
  const cross = $("adoptCross"), hot = $("adoptHot");
  let idx = -1;

  const showAt = p => {
    cross.setAttribute("x1", n1(p.x));
    cross.setAttribute("x2", n1(p.x));
    cross.style.display = "";
    hot.setAttribute("cx", n1(p.x));
    hot.setAttribute("cy", n1(p.y));
    hot.style.display = "";
    const r = svg.getBoundingClientRect();
    const s = r.width > 0 ? r.width / W : 1;
    // textContent throughout: nothing here is ever parsed as markup.
    tipEl.textContent = "";
    const v = document.createElement("span");
    v.className = "tv";
    v.textContent = Math.round(p.pct) + "%";
    const l = document.createElement("span");
    l.className = "tl";
    l.textContent = fmt(p.enf) + " of " + fmt(p.reach) + " reachable · " + utcLabel(p.ts);
    tipEl.append(v, l);
    tipEl.hidden = false;
    // Measured rather than assumed: a fixed half-width guess overflowed the viewport
    // at ~400px and put a horizontal scrollbar on the whole page.
    const tw = numOr(tipEl.offsetWidth, 0), th = numOr(tipEl.offsetHeight, 0);
    const half = tw / 2;
    const lo = Math.min(half + 2, r.width / 2);
    const hi = Math.max(lo, r.width - half - 2);
    tipEl.style.left = clamp(p.x * s, lo, hi) + "px";
    tipEl.style.top = Math.max(p.y * s, th + 6) + "px";
  };
  const hide = () => {
    tipEl.hidden = true;
    cross.style.display = "none";
    hot.style.display = "none";
  };
  const nearest = clientX => {
    const r = svg.getBoundingClientRect();
    const s = r.width > 0 ? r.width / W : 1;
    const ux = (clientX - r.left) / s;
    let best = 0, bd = Infinity;
    plotted.forEach((p, i) => { const dd = Math.abs(p.x - ux); if (dd < bd) { bd = dd; best = i; } });
    return best;
  };

  const hitRect = $("adoptHit");
  hitRect.addEventListener("pointermove", e => { idx = nearest(e.clientX); showAt(plotted[idx]); });
  hitRect.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);
  // Keyboard parity with hover; the table below is the ungated path to every value.
  svg.addEventListener("keydown", e => {
    let step = 0;
    if (e.key === "ArrowRight") step = 1;
    else if (e.key === "ArrowLeft") step = -1;
    else if (e.key === "Home") idx = 0;
    else if (e.key === "End") idx = plotted.length - 1;
    else if (e.key === "Escape") { hide(); return; }
    else return;
    e.preventDefault();
    idx = idx < 0 ? (step < 0 ? plotted.length - 1 : 0) : clamp(idx + step, 0, plotted.length - 1);
    showAt(plotted[idx]);
  });
}

// ---- release adoption table (releases.json) ----
// Every canonical release ever seen, with first/last seen, peak share (floored at 50 reachable)
// and current share. All text goes through el() -> text nodes, so a hostile user-agent string
// in a release name cannot inject markup. Absent/empty file hides the section.
function renderReleases(r) {
  const sec = $("releasesSection");
  if (!sec) return;
  if (!Array.isArray(r) || !r.length) { sec.hidden = true; return; }
  sec.hidden = false;
  const CAP = 50;
  const shown = r.slice(0, CAP);
  const pctCount = (pct, count) => (pct == null ? DASH
    : Math.round(numOr(pct, 0)) + "% · " + fmt(numOr(count, 0)));
  $("releaseRows").replaceChildren(...shown.map(x => el("tr", null,
    el("td", { style: "font-family:var(--sans);font-size:12.5px;word-break:break-all" },
      String(x && x.ua != null ? x.ua : DASH)),
    el("td", { class: "n" }, x && x.first_ts ? utcLabel(x.first_ts) : DASH),
    el("td", { class: "n" }, x && x.last_ts ? utcLabel(x.last_ts) : DASH),
    el("td", { class: "n" }, pctCount(x && x.peak_pct, x && x.peak_count)),
    el("td", { class: "n" }, pctCount(x && x.now_pct, x && x.now_count)))));
  $("releaseNote").textContent = r.length > CAP
    ? "Showing the " + CAP + " of " + fmt(r.length) + " releases with the highest current " +
      "share. Full machine-readable list: releases.json."
    : "All " + fmt(r.length) + " releases the crawler has seen. A dash means the release was " +
      "only ever seen in samples below the 50-node floor.";
}

// ---- policy adoption over time (policy-history.json): exclusive buckets ----
// Reachable post-fork nodes grouped by the newest policy their software enforces, each as a
// share of the reachable total, over time. Absent/short series hide the whole section.
let POLICY_SERIES = null;        // {pts:[{ts,reach,buckets}], ids:[...], labels:{}}
let POLICY_LABELS = {};
let POLICY_MIN = {};             // id -> enforcing_min_version, from policies.json
let RELEASE_ROWS = null;         // releases.json rows, for the first-seen marker times

// First time the crawler saw a FINAL build (no -rcN) of an official Knots release at or above
// this policy's minimum version. Release candidates are left out on purpose: one rc node weeks
// before the release would otherwise pull the line to the wrong day. null = unknown, in which
// case no marker is drawn rather than a guessed one.
const OFFICIAL_UA = /\/Satoshi:(\d+\.\d+(?:\.\d+)?)(?:\([A-Za-z0-9 .,;_?@-]*\))?\/Knots:\d{8}(rc\d+)?\//;
function verParts(v) { const p = String(v).split(".").map(Number); while (p.length < 3) p.push(0); return p; }
function verAtLeast(a, b) { for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i]; return true; }
function policyReleaseTs(id) {
  if (!Array.isArray(RELEASE_ROWS) || !/^\d+\.\d+(\.\d+)?$/.test(POLICY_MIN[id] || "")) return null;
  const min = verParts(POLICY_MIN[id]);
  let best = null;
  for (const r of RELEASE_ROWS) {
    const ts = numOr(r && r.first_ts, null);
    if (ts === null || ts <= 0 || typeof r.ua !== "string") continue;
    const m = OFFICIAL_UA.exec(r.ua);
    if (!m || m[2] || !verAtLeast(verParts(m[1]), min)) continue;
    if (best === null || ts < best) best = ts;
  }
  return best;
}
const PC_PALETTE = ["var(--beacon)", "var(--signal)", "var(--warn)", "var(--crit)", "var(--ok)"];

function pcColor(id, i, preFirst) { return id === "pre" ? "var(--text-3)" : PC_PALETTE[(i - (preFirst ? 1 : 0) + PC_PALETTE.length) % PC_PALETTE.length]; }
function pcLabel(id) { return (POLICY_SERIES.labels && POLICY_SERIES.labels[id]) || (id === "pre" ? "Pre-fork / older" : id); }

function renderPolicyHistory(h, labels) {
  const sec = $("policyChartSection");
  if (!sec) return;
  if (!Array.isArray(h)) { sec.hidden = true; return; }
  const pts = [];
  for (const raw of h) {
    if (!raw || typeof raw !== "object") continue;
    const ts = numOr(raw.ts, null);
    if (ts === null || ts <= 0) continue;
    const reach = Math.max(0, numOr(raw.b2b_reachable, 0));
    if (reach <= 0) continue;  // no denominator to divide by -> leave it out, never a fake 0%
    const b = (raw.buckets && typeof raw.buckets === "object" && !Array.isArray(raw.buckets)) ? raw.buckets : {};
    pts.push({ ts, reach, buckets: b });
  }
  pts.sort((a, b) => a.ts - b.ts);
  if (pts.length < 2) { sec.hidden = true; return; }
  const ids = [];
  if (pts.some(p => "pre" in p.buckets)) ids.push("pre");
  for (const p of pts) for (const k of Object.keys(p.buckets)) if (!ids.includes(k)) ids.push(k);
  if (!ids.length) { sec.hidden = true; return; }
  POLICY_SERIES = { pts, ids, labels: labels || {} };
  sec.hidden = false;
  drawPolicyChart();
}

function drawPolicyChart() {
  const host = $("pcHost");
  if (!host || !POLICY_SERIES || POLICY_SERIES.pts.length < 2) return;
  const { pts, ids } = POLICY_SERIES;
  const preFirst = ids[0] === "pre";
  const old = host.querySelector("svg"); if (old) old.remove();

  const W = Math.max(280, Math.round(numOr(host.clientWidth, 0) || 600));
  const H = W < 430 ? 200 : W < 640 ? 225 : 250;
  const padL = 36, padR = W < 430 ? 16 : 24, padT = 12, padB = 34;
  const pw = Math.max(10, W - padL - padR), ph = Math.max(10, H - padT - padB);
  const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts, spanT = (t1 - t0) || 1;
  const n1 = v => Math.round(v * 10) / 10;
  const X = ts => padL + (ts - t0) / spanT * pw;
  const Y = pct => padT + (1 - clamp(pct, 0, 100) / 100) * ph;
  const shareAt = (p, id) => {
    const tot = Object.values(p.buckets).reduce((s, v) => s + Math.max(0, numOr(v, 0)), 0) || p.reach || 1;
    return Math.max(0, numOr(p.buckets[id], 0)) / tot * 100;
  };

  let grid = "";
  for (const v of [0, 25, 50, 75, 100]) {
    grid += '<line class="ch-grid" x1="' + padL + '" y1="' + n1(Y(v)) + '" x2="' + n1(padL + pw) +
      '" y2="' + n1(Y(v)) + '"/><text class="ch-axis" x="' + (padL - 7) + '" y="' + n1(Y(v) + 3.5) +
      '" text-anchor="end">' + v + "%</text>";
  }
  const nTicks = W < 420 ? 3 : W < 640 ? 4 : 6; const seen = new Set(); let xax = "";
  for (let k = 0; k < nTicks; k++) {
    const i = Math.round(k * (pts.length - 1) / (nTicks - 1));
    if (seen.has(i)) continue; seen.add(i);
    const p = pts[i], d = new Date(p.ts * 1000);
    const anchor = i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle";
    xax += '<text class="ch-axis" x="' + n1(X(p.ts)) + '" y="' + (padT + ph + 15) +
      '" text-anchor="' + anchor + '">' + esc(dayLabel(d)) + "</text>";
  }
  let lines = "", markers = "", legend = [];
  ids.forEach((id, i) => {
    const col = pcColor(id, i, preFirst);
    let dpath = "";
    // Marker = when this policy's release first appeared, not when its bucket first went above
    // zero (a lone release-candidate node would put that weeks too early).
    const markTs = id === "pre" ? null : policyReleaseTs(id);
    for (const p of pts) dpath += (dpath ? " L" : "M") + n1(X(p.ts)) + " " + n1(Y(shareAt(p, id)));
    const last = pts[pts.length - 1];
    lines += '<path class="pc-line" d="' + dpath + '" style="stroke:' + col + '"/>' +
      '<circle class="pc-dot" cx="' + n1(X(last.ts)) + '" cy="' + n1(Y(shareAt(last, id))) +
      '" r="3.5" style="fill:' + col + '"/>';
    if (markTs && markTs > t0 && markTs <= t1) {
      markers += '<line class="ch-mark" x1="' + n1(X(markTs)) + '" y1="' + padT + '" x2="' +
        n1(X(markTs)) + '" y2="' + n1(padT + ph) + '"/>';
    }
    legend.push({ id, col, pct: shareAt(last, id) });
  });

  const aria = "Multi-line chart: reachable post-fork nodes grouped by the newest policy their " +
    "software enforces, each a percentage of the reachable total, over time, the groups summing to 100.";
  host.insertAdjacentHTML("afterbegin",
    '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="' +
    esc(aria) + '">' + grid + xax + markers + lines + "</svg>");

  $("pcLegend").replaceChildren(...legend.map(it => el("span", { class: "pc-leg" },
    el("span", { class: "pc-sw", style: "background:" + it.col }),
    el("span", null, pcLabel(it.id) + " "),
    el("span", { class: "pv" }, Math.round(it.pct) + "%"))));
  const last = pts[pts.length - 1];
  $("pcCap").textContent = fmt(pts.length) + " samples, " + utcLabel(pts[0].ts) + " to " +
    utcLabel(last.ts) + ". Each node counted once, in the newest policy its software enforces; " +
    "a lower bound measured by one crawler. Dashed lines mark the first time the crawler saw a " +
    "final (non-candidate) build of each policy's release.";
}

let redrawPending = false;
window.addEventListener("resize", () => {
  if (redrawPending) return;
  if (!ADOPT_SERIES && !POLICY_SERIES) return;
  redrawPending = true;
  requestAnimationFrame(() => {
    redrawPending = false;
    if (ADOPT_SERIES) drawAdoption();
    if (POLICY_SERIES) drawPolicyChart();
  });
});

// Cache-bust so a refresh shows the newest push rather than a cached copy.
fetch("data.json?t=" + Date.now())
  .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then(render)
  .catch(e => showError("The census file could not be fetched (" + e.message +
    "). The page itself is fine; the publisher on the seed host may not have run yet."));

// Independent of the census fetch: one failing should not blank the other.
fetch("stable-nodes.json?t=" + Date.now())
  .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then(renderStable)
  .catch(() => { document.getElementById("stableSection").style.display = "none"; });

// Also independent: a missing or malformed history file hides only the adoption section.
// It deliberately does not reach showError(), which is reserved for the census file.
fetch("history.json?t=" + Date.now())
  .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then(renderHistory)
  .catch(() => { ADOPT_SERIES = null; $("adoptionSection").hidden = true; });

// Donate: copy the address to the clipboard. Independent of every fetch above, so it works
// even if the census file never loads. The address is read from the button's own data
// attribute, so a copy never depends on how the <code> element happened to render.
const donateCopy = $("donateCopy");
if (donateCopy) {
  donateCopy.addEventListener("click", () => {
    const addr = donateCopy.getAttribute("data-addr") || "";
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(addr).then(() => {
      const prev = donateCopy.textContent;
      donateCopy.textContent = "Copied";
      donateCopy.classList.add("done");
      setTimeout(() => { donateCopy.textContent = prev; donateCopy.classList.remove("done"); }, 1600);
    }).catch(() => {});
  });
}

// Release adoption table: independent, hides its own section on any failure.
fetch("releases.json?t=" + Date.now())
  .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then(r => {
    RELEASE_ROWS = Array.isArray(r) ? r : null;
    renderReleases(r);
    if (POLICY_SERIES) drawPolicyChart();   // the chart may have drawn before this arrived
  })
  .catch(() => { $("releasesSection").hidden = true; });

// Policy adoption chart needs the series plus id->label names from policies.json. The labels
// fetch never blocks the chart: on any failure the ids fall back to readable defaults.
fetch("policies.json?t=" + Date.now())
  .then(r => (r.ok ? r.json() : []))
  .then(pol => { if (Array.isArray(pol)) for (const p of pol) if (p && p.id) { POLICY_LABELS[p.id] = p.short || p.name || p.id; POLICY_MIN[p.id] = p.enforcing_min_version; } })
  .catch(() => {})
  .finally(() => fetch("policy-history.json?t=" + Date.now())
    .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(h => renderPolicyHistory(h, POLICY_LABELS))
    .catch(() => { POLICY_SERIES = null; $("policyChartSection").hidden = true; }));
