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

// Three independent fetches feed the merged software table and the activation section:
// data.json (the current sample), releases.json (first-seen and peak per release) and
// policies.json (policy names, used only as a fallback for a sample that predates
// data.policy / data.next_policy). Any of them may land first, so each stashes its result
// and re-runs the renderers that depend on it.
let DATA = null;
let RELEASE_ROWS = null;         // releases.json rows: first/last seen and peak per release
let POLICY_LIST = null;          // policies.json, verbatim

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
    (o > 0 ? " · " + fmt(o) + " other" : "") + ". The software table and the readiness figure " +
    "count all of them; the height table, the network split and the policy chart count only " +
    "the " + (good === null ? DASH : fmt(good)) + " the seeder rates good, which excludes " +
    "pruned nodes.";
}

function render(d) {
  DATA = d;
  const stampTs = numOr(d.ts, null);
  document.getElementById("stamp").textContent = stampTs === null ? DASH
    : new Date(stampTs * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  renderHeadlineCount(d);
  document.getElementById("figB2").textContent = fmtOr(d.b2b);
  document.getElementById("figCrawl").textContent = fmtOr(d.total);
  document.getElementById("figTip").textContent = fmtOr(d.max_height);

  renderSoftware();
  renderActivation();
  renderHeights(d);
  renderNetworks(d);
  renderContextTiles(d);
}

// ---- which population a figure is counted over ----
// Two bases, in preference order:
//   "storage"  -- versions_storage: every post-fork node polled inside the freshness window,
//                 archival and pruned, which is the population the headline count uses, so
//                 the software table's totals row equals the headline figure;
//   "versions" -- the older per-release map, over the seeder's rated-good set only, which
//                 excludes pruned nodes.
// null when neither is usable, so callers print an em-dash instead of inventing a
// denominator. The per-release keys of both fields are canonicalized by the publisher, so
// exact-string membership in enforcing_versions works against either.
const obj = v => (v && typeof v === "object" && !Array.isArray(v)) ? v : null;
function softwareBasis(d) {
  const vs = obj(d.versions_storage);
  if (vs) {
    const rows = [];
    let a = 0, p = 0, o = 0;
    for (const [ua, raw] of Object.entries(vs)) {
      const e = obj(raw);
      if (!e) continue;
      const ar = Math.max(0, numOr(e.archival, 0)),
            pr = Math.max(0, numOr(e.pruned, 0)),
            ot = Math.max(0, numOr(e.other, 0));
      rows.push({ ua: String(ua), archival: ar, pruned: pr, total: ar + pr + ot });
      a += ar; p += pr; o += ot;
    }
    const total = a + p + o;
    if (rows.length && total > 0) {
      return { kind: "storage", rows, archival: a, pruned: p, other: o, total,
               label: "post-fork nodes polled in the last " +
                      fmt(POLL_FRESH_SECONDS / 60 | 0) + " minutes" };
    }
  }
  const ver = obj(d.versions);
  if (ver) {
    const rows = [];
    let total = 0;
    for (const [ua, n] of Object.entries(ver)) {
      const c = Math.max(0, numOr(n, 0));
      rows.push({ ua: String(ua), archival: null, pruned: null, total: c });
      total += c;
    }
    if (rows.length && total > 0) {
      return { kind: "versions", rows, archival: null, pruned: null, other: null, total,
               label: "post-fork nodes the seeder rates good" };
    }
  }
  return null;
}

// ---- which rule the activation section and the Enforces column are about ----
// Names come from the published sample (data.policy / data.next_policy). A sample that
// predates those fields falls back to matching heights against policies.json -- but for
// NAMES ONLY. Enforcement is never re-derived from user-agent strings here: that
// classification has one home, in the publisher, and a policy with no published enforcing
// list scores as an em-dash rather than a guess.
function policyTargets(d) {
  const tip = numOr(d.max_height, null);
  const pol = (Array.isArray(POLICY_LIST) ? POLICY_LIST : [])
    .filter(p => obj(p) && numOr(p.activation, null) !== null);
  const nameOf = p => String((p && (p.short || p.name)) || "").trim();
  const act = numOr(d.activation_height, null);

  const fromFile = pol.find(p => numOr(p.activation, null) === act) || null;
  const cur = {
    name: nameOf(obj(d.policy)) || nameOf(fromFile),
    activation: act,
    end: numOr(d.activation_end_height, null),
    list: Array.isArray(d.enforcing_versions) ? d.enforcing_versions : null
  };

  const dn = obj(d.next_policy);
  let next = null;
  if (dn && numOr(dn.activation, null) !== null) {
    next = { name: nameOf(dn), activation: numOr(dn.activation, null),
             end: numOr(dn.end, null),
             list: Array.isArray(dn.enforcing_versions) ? dn.enforcing_versions : null };
  } else if (!("next_policy" in d) && tip !== null) {
    // The field being absent means "this publisher does not know about next policies";
    // the field being present and null means "there is no next policy", which is
    // authoritative and must not be second-guessed from policies.json.
    const fut = pol.filter(p => numOr(p.activation, 0) > tip)
                   .sort((x, y) => numOr(x.activation, 0) - numOr(y.activation, 0));
    if (fut.length) {
      next = { name: nameOf(fut[0]), activation: numOr(fut[0].activation, null),
               end: numOr(fut[0].end, null), list: null };
    }
  }
  return { cur, next };
}

// Nodes on a release the sample lists as enforcing `list`, over `basis`. null when either
// half is missing, which the callers render as an em-dash.
function readiness(basis, list) {
  if (!basis || !Array.isArray(list)) return null;
  const set = new Set(list.map(String));
  let ready = 0;
  for (const r of basis.rows) if (set.has(r.ua)) ready += r.total;
  return { ready, notReady: Math.max(0, basis.total - ready),
           pct: basis.total > 0 ? clamp(ready / basis.total * 100, 0, 100) : null };
}

// Release-name tags. OracleKnots is checked FIRST: its user agent contains "Knots", so the
// not-Knots test would miss it, and it is a third-party build whose source cannot be
// verified from a user-agent string -- listed as unverified, never as failing to enforce.
function releaseTag(ua) {
  if (/oracle/i.test(ua)) return "unverified third-party build";
  if (!/Knots/i.test(ua)) return "not Knots, but claims the post-fork bit";
  if (/rc\d/i.test(ua)) return "release candidate";
  return "";
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
  $("networkNote").textContent = "Counts the " + fmt(total) + " post-fork nodes the seeder " +
    "rates good, which excludes pruned nodes, not the wider recently-polled base used by " +
    "the software table.";
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

  // Which population this table is counted over, stated with the count rather than left to
  // the reader to infer from the headline tile.
  const good = numOr(d.b2b_reachable, null);
  $("heightsBase").textContent = (good === null
    ? "Counts the post-fork nodes the seeder rates good"
    : "Counts the " + fmt(good) + " post-fork nodes the seeder rates good") +
    ", which excludes pruned nodes, not the wider recently-polled base used by the software " +
    "table above.";
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

// The post-fork share of the whole crawl used to be its own tile saying the same thing as
// this one with a percentage instead of a count. It is now the note under the count.
function renderContextTiles(d) {
  const shareAll = pctOf(d.b2b, d.total);
  const shareReach = pctOf(d.b2b_reachable, d.reachable);
  $("figB2Note").textContent = shareAll === null
    ? "seen by the crawler"
    : pctStr(shareAll) + " of the " + fmtOr(d.total) + " nodes crawled";
  $("figB2Bias").textContent =
    (shareAll === null ? "" : pctStr(shareAll) + " of every node this crawler reached " +
      "advertises the post-fork bit. ") +
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

// ---- Activation section: generic over whatever policies.json / the publisher say ----
// Nothing here names a rule. The title, the labels and the readiness list all come from the
// published sample, so a new policy epoch moves this section by a policies.json edit on the
// seed rather than by an edit to this page.
function renderActivation() {
  const d = DATA;
  if (!d) return;
  const tip = numOr(d.max_height, null);
  const basis = softwareBasis(d);
  const { cur, next } = policyTargets(d);

  const title = $("actTitle"), windowLine = $("actWindowLine"), risk = $("actRisk");
  const setTile = (id, big, lbl, sub) => {
    $(id).textContent = big;
    $(id + "Lbl").textContent = lbl;
    const s = $(id + "Sub");
    s.textContent = sub || "";
    s.hidden = !sub;
  };

  // The next rule is the one worth counting down to, and only while it really is in the
  // future. Otherwise the section describes the rule in force.
  const pending = next && next.activation !== null && tip !== null && tip < next.activation;
  const target = pending ? next : cur;
  const named = target.name || "";

  // Readiness is membership in the list the sample publishes for THIS rule. A rule with no
  // published list (for instance a next policy derived from policies.json by an older
  // publisher) scores as an em-dash: guessing it from user-agent strings in the browser
  // would be a second, divergent copy of a consensus-sensitive classifier.
  const rd = readiness(basis, target.list);
  const readyPct = rd && rd.pct !== null ? pctStr(rd.pct) : DASH;
  const readyLbl = named ? "on a release enforcing " + named : "on a release enforcing it";
  const readySub = !basis
    ? "no population to measure against in this sample"
    : rd === null
      ? "this sample publishes no list of the releases enforcing it"
      : fmt(rd.ready) + " of " + fmt(basis.total) + " " + basis.label;

  // --- Neutral fallback: without a height and an activation there is no honest state. ---
  if (tip === null || target.activation === null) {
    title.replaceChildren(named ? named + ", activation height " : "Activation height ",
      el("span", { class: "num" }, DASH));
    windowLine.hidden = true;
    setTile("cdBlocks", DASH, "blocks remaining", "");
    setTile("cdDays", DASH, "days, at ~10 min/block", "");
    setTile("cdReady", readyPct, readyLbl, readySub);
    renderNotEnforcing(basis, rd, named, risk);
    return;
  }

  if (pending) {
    // ---------------- STATE A: the next rule has not activated yet ----------------
    title.replaceChildren((named ? named : "The next consensus rule") + " activates at block ",
      el("span", { class: "num" }, fmt(target.activation)));
    // The rule in force meanwhile, if the sample says what it is.
    if (cur.activation !== null && tip >= cur.activation) {
      windowLine.textContent = (cur.name ? cur.name : "The current rule") +
        " is in force now, from block " + fmt(cur.activation) +
        (cur.end !== null && cur.end > cur.activation
          ? " through block " + fmt(cur.end - 1) + "."
          : ".");
      windowLine.hidden = false;
    } else {
      windowLine.hidden = true;
    }
    const remaining = target.activation - tip;
    setTile("cdBlocks", fmt(remaining), "blocks remaining", "");
    setTile("cdDays", days(remaining), "days, at ~10 min/block", "");
    setTile("cdReady", readyPct, readyLbl, readySub);
    renderNotEnforcing(basis, rd, named, risk);
    return;
  }

  // ---------------- STATE B: the rule is in force ----------------
  // No "normal maturity resumes" anywhere: a rolling rule does not end, it is simply in
  // force through the last block of its epoch, and the next epoch takes over there.
  const act = target.activation, end = target.end;
  const lastBlock = end !== null && end > act ? end - 1 : null;
  const span = lastBlock === null ? null : end - act;
  const elapsed = tip - act;

  title.textContent = named ? named + " is in force" : "A consensus rule is in force";
  windowLine.textContent = lastBlock === null
    ? "Took effect at block " + fmt(act) + "."
    : "Took effect at block " + fmt(act) + " and is in force through block " + fmt(lastBlock) +
      " — " + fmt(span) + " blocks, about " + Math.round(span * 10 / 60 / 24) + " days.";
  windowLine.hidden = false;

  setTile("cdBlocks", fmt(elapsed), "blocks since it took effect",
    "about " + days(elapsed) + " days, at ~10 min/block");

  if (lastBlock === null) {
    setTile("cdDays", DASH, "blocks until the end of its window",
      "the window end height is not published in this sample");
  } else if (tip > lastBlock) {
    setTile("cdDays", "0", "blocks until the end of its window",
      "block " + fmt(lastBlock) + ", the last block it covers, is behind us");
  } else {
    const toEnd = lastBlock - tip;
    setTile("cdDays", fmt(toEnd),
      "blocks until block " + fmt(lastBlock) + ", the last it covers",
      "about " + days(toEnd) + " days, at ~10 min/block");
  }

  setTile("cdReady", readyPct, readyLbl, readySub);
  renderNotEnforcing(basis, rd, named, risk);
}

// The headline not-enforcing count. The per-version list that used to sit under it is gone:
// it is a column in the software table now, over the same population.
function renderNotEnforcing(basis, rd, named, risk) {
  if (!basis || rd === null) { risk.hidden = true; return; }
  risk.hidden = false;
  $("actRiskHead").textContent = named
    ? "Post-fork nodes not on a release enforcing " + named
    : "Post-fork nodes not on a release enforcing the rule";
  $("neCount").textContent = basis.total > 0 ? fmt(rd.notReady) : DASH;
  $("neCountLbl").textContent = basis.total > 0
    ? "of the " + fmt(basis.total) + " " + basis.label
    : "no post-fork nodes counted, so there is nothing to measure against";
  $("neNote").textContent = "Counts come from the user agent each node reported, matched " +
    "against the list of enforcing releases in the published sample. Which release each " +
    "node runs is in the software table below. A node on an older release is only on a " +
    "different chain once a block actually breaks the rule.";
}

// ---- Software in use post-fork: ONE table, one row per canonical release ----
// Merges what used to be four panels: the version bars, the archival/pruned split, the
// release-adoption table and the per-version not-enforcing list. Current counts come from
// data.json (versions_storage when published, else versions), first-seen and peak from
// releases.json; either fetch may be missing, and each column degrades to an em-dash on its
// own rather than taking the section down.
function renderSoftware() {
  const d = DATA;
  if (!d) return;
  const basis = softwareBasis(d);
  const { cur, next } = policyTargets(d);
  // The Enforces column follows the countdown: the next rule when the sample published a
  // list for it, otherwise the rule in force. The header names whichever it is, so the
  // column is never ambiguous about which rule it answers for.
  const enf = (next && Array.isArray(next.list)) ? next
            : (Array.isArray(cur.list) ? cur : null);
  const enfSet = enf ? new Set(enf.list.map(String)) : null;
  $("verEnfHead").textContent = enf
    ? "Enforces " + (enf.name || "the rule")
    : "Enforces";

  // first-seen / last-seen / peak, keyed by the same canonical release string
  const rel = new Map();
  if (Array.isArray(RELEASE_ROWS)) {
    for (const r of RELEASE_ROWS) {
      if (!obj(r) || typeof r.ua !== "string") continue;
      rel.set(r.ua, r);
    }
  }

  const rows = new Map();
  if (basis) {
    for (const r of basis.rows) {
      const prev = rows.get(r.ua);
      // One line per canonical release even if the publisher ever emits two keys that
      // canonicalize together: sum rather than overwrite.
      rows.set(r.ua, prev
        ? { ua: r.ua, archival: prev.archival === null ? r.archival : prev.archival + r.archival,
            pruned: prev.pruned === null ? r.pruned : prev.pruned + r.pruned,
            total: prev.total + r.total }
        : { ua: r.ua, archival: r.archival, pruned: r.pruned, total: r.total });
    }
  }
  // Releases the crawler has seen before but not in this sample: kept, at zero, and
  // collapsed below so the main table stays the size of the live network.
  const gone = [];
  for (const ua of rel.keys()) if (!rows.has(ua)) gone.push(ua);

  const dayOf = ts => { const t = numOr(ts, null);
    return t === null || t <= 0 ? DASH : dayLabel(new Date(t * 1000)); };
  const peakCell = r => {
    // nodes_* is the node-count view; peak_*/now_* keep their original share meaning for
    // other readers of releases.json and are not used here.
    const c = r ? numOr(r.nodes_peak, null) : null;
    if (c === null) return DASH;
    const when = dayOf(r.nodes_peak_ts);
    return fmt(c) + (when === DASH ? "" : " · " + when);
  };

  const nameCell = ua => {
    const tag = releaseTag(ua);
    return el("td", { class: "ua" }, ua,
      tag ? el("span", { class: "tag" }, " — " + tag) : null);
  };
  const enfCell = ua => enfSet ? (enfSet.has(ua) ? "yes" : "no") : DASH;

  const body = [...rows.values()].sort((a, b) => b.total - a.total ||
    (a.ua < b.ua ? -1 : a.ua > b.ua ? 1 : 0));
  $("versionRows").replaceChildren(...(body.length
    ? body.map(r => {
        const x = rel.get(r.ua) || null;
        return el("tr", null,
          nameCell(r.ua),
          el("td", { class: "n" }, r.archival === null ? DASH : fmt(r.archival)),
          el("td", { class: "n" }, r.pruned === null ? DASH : fmt(r.pruned)),
          el("td", { class: "n" }, fmt(r.total)),
          el("td", { class: "n" }, pctStr(pctOf(r.total, basis.total))),
          el("td", { class: "n" }, enfCell(r.ua)),
          el("td", { class: "n" }, dayOf(x && x.first_ts)),
          el("td", { class: "n" }, peakCell(x)));
      })
    : [el("tr", null, el("td", { colspan: "8", style: "color:var(--text-3)" },
        "This sample publishes no per-release counts, so there is nothing to break down."))]));

  // Totals row. With versions_storage published it equals the headline count by
  // construction (the publisher sums the same population into storage_*).
  $("versionTotals").replaceChildren(...(basis
    ? [el("td", null, "All releases"),
       el("td", { class: "n" }, basis.archival === null ? DASH : fmt(basis.archival)),
       el("td", { class: "n" }, basis.pruned === null ? DASH : fmt(basis.pruned)),
       el("td", { class: "n" }, fmt(basis.total)),
       el("td", { class: "n" }, "100%"),
       el("td", { class: "n" }, DASH),
       el("td", { class: "n" }, DASH),
       el("td", { class: "n" }, DASH)]
    : []));

  // Caption: what the numbers are counted over, and the two honest caveats.
  const mixed = [...rel.values()].some(r => r.nodes_peak_basis && r.nodes_now_basis &&
    r.nodes_peak_basis !== r.nodes_now_basis);
  const head = !basis
    ? "This sample publishes no per-release counts."
    : basis.kind === "storage"
      ? fmt(basis.total) + " post-fork nodes completed a handshake in the last " +
        fmt(POLL_FRESH_SECONDS / 60 | 0) + " minutes: " + fmt(basis.archival) + " archival, " +
        fmt(basis.pruned) + " pruned" +
        (basis.other > 0 ? ", " + fmt(basis.other) + " advertising neither" : "") +
        ". The totals row is the headline count at the top of the page."
      : "This sample does not publish the per-release archival/pruned split, so those two " +
        "columns are em-dashes and Total is the count of nodes the seeder rates good (" +
        fmt(basis.total) + "), which excludes pruned nodes. The split appears here as soon " +
        "as the seed publishes it.";
  $("versionNote").textContent = head +
    " Peak is the highest number of nodes this crawler has ever seen on a release, and the " +
    "UTC day it saw it; the peak share behind it is left out below 50 nodes, where a share " +
    "off a handful of nodes says nothing." +
    (mixed ? " Peaks dated before the seed began publishing the archival/pruned split were " +
      "measured over the narrower rated-good set, so a release's peak can step up at that " +
      "point: that is a change of measurement, not adoption." : "") +
    " A lower bound measured by one crawler.";

  // ---- releases no longer seen ----
  const wrap = $("goneWrap");
  const GONE_CAP = 100;
  if (!gone.length) { wrap.hidden = true; return; }
  wrap.hidden = false;
  gone.sort((a, b) => numOr(rel.get(b).nodes_peak, 0) - numOr(rel.get(a).nodes_peak, 0));
  const shown = gone.slice(0, GONE_CAP);
  $("goneSummary").textContent = fmt(gone.length) +
    (gone.length === 1 ? " release no longer seen" : " releases no longer seen");
  $("goneRows").replaceChildren(...shown.map(ua => {
    const x = rel.get(ua);
    return el("tr", null,
      nameCell(ua),
      el("td", { class: "n" }, peakCell(x)),
      el("td", { class: "n" }, dayOf(x.first_ts)),
      el("td", { class: "n" }, dayOf(x.last_ts)));
  }));
  $("goneNote").textContent = (shown.length < gone.length
    ? "Showing the " + fmt(shown.length) + " of " + fmt(gone.length) + " with the highest peak. "
    : "") + "Seen by this crawler at some point, but not in the current sample. Full " +
    "machine-readable list: releases.json.";
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

// ---- policy adoption over time (policy-history.json): exclusive buckets ----
// Reachable post-fork nodes grouped by the newest policy their software enforces, each as a
// share of the reachable total, over time. Absent/short series hide the whole section.
let POLICY_SERIES = null;        // {pts:[{ts,reach,buckets}], ids:[...], labels:{}}
let POLICY_LABELS = {};
let POLICY_MIN = {};             // id -> enforcing_min_version, from policies.json

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
    // A policy's line starts at the first sample with a node in it; a flat 0% before that would
    // read as "available all along, unused". The pre-fork bucket runs from the start.
    const from = id === "pre" ? 0 : pts.findIndex(p => numOr(p.buckets[id], 0) > 0);
    if (from >= 0) for (const p of pts.slice(from)) dpath += (dpath ? " L" : "M") + n1(X(p.ts)) + " " + n1(Y(shareAt(p, id)));
    const last = pts[pts.length - 1];
    if (dpath) lines += '<path class="pc-line" d="' + dpath + '" style="stroke:' + col + '"/>' +
      '<circle class="pc-dot" cx="' + n1(X(last.ts)) + '" cy="' + n1(Y(shareAt(last, id))) +
      '" r="3.5" style="fill:' + col + '"/>';
    if (markTs && markTs > t0 && markTs <= t1) {
      markers += '<line class="ch-mark" x1="' + n1(X(markTs)) + '" y1="' + padT + '" x2="' +
        n1(X(markTs)) + '" y2="' + n1(padT + ph) + '"/>';
    }
    legend.push({ id, col, pct: shareAt(last, id) });
  });

  const aria = "Multi-line chart: rated-good post-fork nodes grouped by the newest policy their " +
    "software enforces, each a percentage of the rated-good total, over time, the groups summing to 100.";
  host.insertAdjacentHTML("afterbegin",
    '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="' +
    esc(aria) + '">' + grid + xax + markers + lines + "</svg>");

  $("pcLegend").replaceChildren(...legend.map(it => el("span", { class: "pc-leg" },
    el("span", { class: "pc-sw", style: "background:" + it.col }),
    el("span", null, pcLabel(it.id) + " "),
    el("span", { class: "pv" }, Math.round(it.pct) + "%"))));
  const last = pts[pts.length - 1];
  $("pcCap").textContent = fmt(pts.length) + " samples, " + utcLabel(pts[0].ts) + " to " +
    utcLabel(last.ts) + ". Each node counted once, in the newest policy its software " +
    "enforces, as a share of the " + fmt(last.reach) + " post-fork nodes the seeder rated " +
    "good at the latest sample — not the wider recently-polled base used by the software " +
    "table, so pruned nodes are not in this chart. A lower bound measured by one crawler. " +
    "Dashed lines mark the first time the crawler saw a final (non-candidate) build of each " +
    "policy's release.";
}

let redrawPending = false;
window.addEventListener("resize", () => {
  if (redrawPending) return;
  if (!POLICY_SERIES) return;
  redrawPending = true;
  requestAnimationFrame(() => {
    redrawPending = false;
    drawPolicyChart();
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

// history.json is deliberately NOT fetched any more. The single-series adoption chart it
// fed duplicated the per-policy chart below, which is drawn from policy-history.json; the
// file is still published for other consumers.

// Donate: copy the address to the clipboard. Independent of every fetch above, so it works
// even if the census file never loads. The address is read from the button's own data
// attribute, so a copy never depends on how the <code> element happened to render. Covers
// both the strip under the figures and the full section at the bottom.
for (const btn of document.querySelectorAll("button.donate-copy")) {
  btn.addEventListener("click", () => {
    const addr = btn.getAttribute("data-addr") || "";
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(addr).then(() => {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("done");
      setTimeout(() => { btn.textContent = prev; btn.classList.remove("done"); }, 1600);
    }).catch(() => {});
  });
}

// releases.json: first seen, last seen and peak for the software table, plus the first-seen
// marker times on the policy chart. Independent of the census fetch; on failure the table
// still renders from data.json with em-dashes in those three columns.
fetch("releases.json?t=" + Date.now())
  .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then(r => {
    RELEASE_ROWS = Array.isArray(r) ? r : null;
    if (DATA) renderSoftware();             // data.json may have landed before this
    if (POLICY_SERIES) drawPolicyChart();   // the chart may have drawn before this arrived
  })
  .catch(() => { RELEASE_ROWS = null; });

// policies.json: the policy chart's id->label names, and the fallback source of policy NAMES
// for the activation section when the published sample predates data.policy/next_policy. It
// is never used to decide which releases enforce a rule. On any failure the ids fall back to
// readable defaults and the activation section falls back to neutral wording.
fetch("policies.json?t=" + Date.now())
  .then(r => (r.ok ? r.json() : []))
  .then(pol => {
    if (Array.isArray(pol)) {
      POLICY_LIST = pol;
      for (const p of pol) if (p && p.id) {
        POLICY_LABELS[p.id] = p.short || p.name || p.id;
        POLICY_MIN[p.id] = p.enforcing_min_version;
      }
      if (DATA) { renderActivation(); renderSoftware(); }
    }
  })
  .catch(() => {})
  .finally(() => fetch("policy-history.json?t=" + Date.now())
    .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(h => renderPolicyHistory(h, POLICY_LABELS))
    .catch(() => { POLICY_SERIES = null; $("policyChartSection").hidden = true; }));
