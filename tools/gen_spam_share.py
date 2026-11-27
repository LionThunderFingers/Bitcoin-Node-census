#!/usr/bin/env python3
"""Join the sanitised cesspool.lol block-grade cache with census.jsonl into spam-share.json.

Everything the page's "spam share and node versions" panel draws is aggregated here, not in the
browser. Two separate measurements on one UTC-day axis, never merged:
  spam:     per complete UTC day, the weight-weighted share of block weight cesspool.lol grades as
            sewage (sum(share*weight)/sum(weight), never a mean of per-block shares), plus the
            plain per-block mean and the block count;
  versions: per UTC day, the last valid census sample's rated-good nodes grouped by software
            version (newest few official versions, Plumb anywhere in the user agent, the rest as
            "other"), as percentages that sum to 100.0.
Eras start on the UTC day a FINAL official Knots release was first seen by the census (from
releases.json; release candidates and builds already running when the census began are
excluded); each era gets its weighted average and per-block mean.
The partial UTC day of the newest block is never a daily point; `last24h` is its own figure.
Every time reference is the newest block's time, never the wall clock, so the same inputs
always give the same output. The cache is treated as untrusted input and re-validated here.

Spam by type (additive): when cached blocks carry cesspool's `sewage_by_type` map ({type id:
sewage weight}), a day with EVERY block mapped gets `t`: each type's share of that day's whole
block weight in percent, in the order of the top-level `types` (up to 6 types by peak daily share,
`unknown` always kept when present, the rest folded into "other", last). A day with any
unmapped block has no `t` (partial coverage would understate). `last24h.t` works the same way.
Without any maps `types` is [] and no `t` appears.

Output (stdout): {source, grader_version, latest_block, last24h, first_partial, small_below,
groups, types, days, releases, eras}. A missing or empty cache is not an error (days: []); an
unreadable census.jsonl exits non-zero; a missing or unreadable releases.json just means no
release markers.

Run: `python3 gen_spam_share.py CACHE_DIR CENSUS_JSONL [RELEASES_JSON]` writes JSON to stdout.
Test: `python3 gen_spam_share.py --self-test`.
"""
import json
import math
import os
import re
import stat
import sys
from datetime import date, timedelta

_DAY = 86400
_EPOCH = date(1970, 1, 1)
MAX_VERSION_GROUPS = 5    # newest official versions drawn as their own band; older ones fold into "other"
GROUP_MIN_PEAK = 2.0      # a version needs this peak share (percent of one day's nodes) or a final release
SMALL_SAMPLE_NODES = 250  # census days with fewer rated-good nodes are drawn hatched
_MAX_BYTES = 2 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9.+_-]{1,64}")
_TYPE_ID = re.compile(r"[a-z0-9_-]{1,40}")
MAX_TYPE_BANDS = 6        # spam types drawn as their own band; the rest fold into "other"
_BATCH_NAME = re.compile(r"([0-9]{1,7})\.json")
_PLUMB = re.compile(r"plumb", re.I)
# first version-looking token (two-digit major, so dates like 11.16.2026 never match)
_UA_VERSION = re.compile(r"[:]([0-9]{2}\.[0-9]{1,2}\.[0-9]{1,2})(?![0-9])")
_FINAL_UA = re.compile(r"/Satoshi:([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/Knots:[0-9]{8}/")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _valid_block(obj, lo, hi):
    if not isinstance(obj, dict):
        return None
    h, hsh, t = obj.get("height"), obj.get("hash"), obj.get("time")
    share, w = obj.get("sewage_share"), obj.get("weight")
    if not _is_int(h) or not (lo <= h <= hi):
        return None
    if not isinstance(hsh, str) or not _HASH.fullmatch(hsh):
        return None
    if not _is_int(t) or t <= 0:
        return None
    if isinstance(share, bool) or not isinstance(share, (int, float)):
        return None
    if not math.isfinite(share) or not (0 <= share <= 1):
        return None
    if not _is_int(w) or not (1 <= w <= 4000000):
        return None
    out = {"height": h, "hash": hsh, "time": t, "sewage_share": float(share), "weight": w}
    if "sewage_by_type" in obj:
        m = _valid_type_map(obj["sewage_by_type"], w)
        if m is not None:
            out["sewage_by_type"] = m
    return out


def _valid_type_map(m, weight):
    """sewage_by_type ({type id: integer weight}) re-validated like tools/cesspool-fetch.py does,
    or None (the block is still used, just without a type split)."""
    if not isinstance(m, dict) or len(m) > 64:
        return None
    total = 0
    for k, v in m.items():
        if not isinstance(k, str) or not _TYPE_ID.fullmatch(k):
            return None
        if not _is_int(v) or not (0 <= v <= 4000000):
            return None
        total += v
    return dict(m) if total <= weight else None


_MAX_FILES = 5000


def _read_json(path):
    """Read a cache file only if it is a regular file: no symlinks (the directory is writable by
    another user and this runs as root), no FIFOs or devices (they could block the read)."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            with os.fdopen(fd, "rb") as f:
                fd = -1
                raw = f.read(_MAX_BYTES + 1)
        finally:
            if fd >= 0:
                os.close(fd)
        if len(raw) > _MAX_BYTES:
            return None
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def load_cache(cache_dir):
    """(blocks sorted by height, grader_version or None). Deduped by height, newest `fetched`
    wins (ties: the higher batch number). Missing, corrupt or odd files are skipped."""
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return [], None
    best = {}  # height -> (fetched, batch n, block)
    for name in sorted(names)[:_MAX_FILES]:
        m = _BATCH_NAME.fullmatch(name)
        if not m:
            continue
        n = int(m.group(1))
        d = _read_json(os.path.join(cache_dir, name))
        if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
            continue
        f, t = d.get("from"), d.get("to")
        lo, hi = n * 1000, n * 1000 + 999
        if not _is_int(f) or not _is_int(t) or not (lo <= f <= t <= hi):
            continue
        if len(d["blocks"]) > 1000:
            continue
        fetched = d.get("fetched")
        fetched = fetched if _is_int(fetched) else 0
        for b in d["blocks"]:
            v = _valid_block(b, f, t)
            if v is None:
                continue
            key = (fetched, n)
            cur = best.get(v["height"])
            if cur is None or key > cur[:2]:
                best[v["height"]] = (fetched, n, v)
    blocks = [best[h][2] for h in sorted(best)]
    gv = None
    st = _read_json(os.path.join(cache_dir, "state.json"))
    if isinstance(st, dict):
        s = st.get("grader_version")
        if isinstance(s, str) and _VERSION.fullmatch(s):
            gv = s
    return blocks, gv


def _weighted(blocks):
    w = sum(b["weight"] for b in blocks)
    return sum(b["sewage_share"] * b["weight"] for b in blocks) / w


def _mean(blocks):
    return sum(b["sewage_share"] for b in blocks) / len(blocks)


def _pct(x):
    """Fraction -> percent, one decimal."""
    return round(x * 100, 1)


def _newest(blocks):
    return max(blocks, key=lambda b: (b["height"], b["time"]))


def _iso(day):
    return (_EPOCH + timedelta(days=day)).isoformat()


def blocks_by_day(blocks):
    """{UTC day number: [blocks]} for COMPLETE days only. The UTC day of the newest block is
    partial and is left out."""
    if not blocks:
        return {}
    cur = _newest(blocks)["time"] // _DAY
    days = {}
    for b in blocks:
        d = b["time"] // _DAY
        if d < cur:
            days.setdefault(d, []).append(b)
    return days


def _last_24h_blocks(blocks):
    ref = _newest(blocks)["time"]
    return [b for b in blocks if b["time"] >= ref - _DAY]


def last_24h(blocks):
    """Weighted share of blocks with time >= newest_time - 86400, or None."""
    if not blocks:
        return None
    win = _last_24h_blocks(blocks)
    return {"blocks": len(win), "share": round(_weighted(win), 4)}


def type_weights(blocks):
    """{type id: summed sewage weight} over `blocks`, or None unless EVERY block carries a
    sewage_by_type map (partial coverage would understate the shares)."""
    tw = {}
    for b in blocks:
        m = b.get("sewage_by_type")
        if m is None:
            return None
        for k, v in m.items():
            tw[k] = tw.get(k, 0) + v
    return tw


def type_bands(days):
    """Band order for the type split from a list of (type weights, day block weight) pairs, one
    per fully covered day: up to MAX_TYPE_BANDS types ranked by PEAK daily share (max over days
    of type weight / day weight), highest first (ties by id), so a type that spikes for a couple
    of days gets its own band even if small in total. `unknown` is always included when it has
    any weight; the rest fold into "other", last, when used. A real type literally called
    "other" is folded too."""
    peak = {}
    used = set()
    for tw, dw in days:
        for k, v in tw.items():
            if v > 0:
                used.add(k)
                peak[k] = max(peak.get(k, 0.0), v / dw)
    cand = sorted((k for k in peak if k != "other"), key=lambda k: (-peak[k], k))
    keep = cand[:MAX_TYPE_BANDS]
    if "unknown" in cand and "unknown" not in keep:
        keep = sorted(keep[:MAX_TYPE_BANDS - 1] + ["unknown"], key=lambda k: (-peak[k], k))
    if any(k not in keep for k in used):
        keep.append("other")
    return keep


def type_shares(tw, total_weight, types):
    """Each band's share of `total_weight` in percent (_pct), in `types` order; everything not
    named in `types` goes to "other"."""
    named = set(types)
    out = []
    for g in types:
        if g == "other":
            v = sum(x for k, x in tw.items() if k not in named or k == "other")
        else:
            v = tw.get(g, 0)
        out.append(_pct(v / total_weight))
    return out


def _as_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def census_samples(lines):
    """({UTC day number: {user agent: count}}, ts of the first valid sample or None). Each day
    keeps its last valid sample. A sample is valid when ts > 0, b2b_reachable > 0 and the
    version counts sum to it (torn samples are skipped)."""
    samples = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(rec, dict):
            continue
        ts, reach = _as_int(rec.get("ts")), _as_int(rec.get("b2b_reachable"))
        versions = rec.get("versions")
        if ts <= 0 or reach <= 0 or not isinstance(versions, dict) or not versions:
            continue
        vs = {}
        for ua, count in versions.items():
            n = _as_int(count)
            if n > 0:
                vs[str(ua)] = n
        if sum(vs.values()) != reach:
            continue
        samples.append((ts, vs))
    samples.sort(key=lambda p: p[0])
    out = {}
    for ts, vs in samples:
        out[ts // _DAY] = vs
    return out, (samples[0][0] if samples else None)


def ua_group(ua):
    """"Plumb" (anywhere in the user agent), else the first X.Y.Z version token, else None."""
    if _PLUMB.search(ua):
        return "Plumb"
    m = _UA_VERSION.search(ua)
    return m.group(1) if m else None


def _vkey(v):
    return tuple(int(p) for p in v.split("."))


def version_groups(day_counts, finals):
    """Band order for the version stack: up to MAX_VERSION_GROUPS official versions, oldest
    first, then "Plumb" and "other". A version is a candidate when it is a final release or
    reached GROUP_MIN_PEAK percent of some day's nodes; the newest candidates win."""
    cand = set(finals)
    for counts in day_counts.values():
        tot = sum(counts.values())
        for g, c in counts.items():
            if g is not None and g != "Plumb" and c * 100.0 >= GROUP_MIN_PEAK * tot:
                cand.add(g)
    keep = sorted(cand, key=_vkey)[-MAX_VERSION_GROUPS:]
    return keep + ["Plumb", "other"]


def _tenths(counts, groups):
    """Shares of one day in percent, one decimal, in `groups` order, summing to exactly 100.0
    (largest remainder on tenths of a percent)."""
    tot = sum(counts.values())
    raw = [1000.0 * counts.get(g, 0) / tot for g in groups]
    fl = [int(math.floor(v)) for v in raw]
    left = 1000 - sum(fl)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - fl[i]), i))
    for i in order[:max(0, left)]:
        fl[i] += 1
    return [v / 10.0 for v in fl]


def release_markers(releases, first_ts):
    """[{"v": version, "ts": first_ts}], oldest first, for final official Knots builds
    (/Satoshi:X.Y.Z/Knots:DATE/: no release candidates, no Plumb or renamed builds), earliest
    sighting per version. A build first seen within an hour of the first census sample was
    already running when the census began, so it is not a release event and is skipped."""
    best = {}
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict):
            continue
        ua, ts = r.get("ua"), r.get("first_ts")
        m = _FINAL_UA.fullmatch(ua) if isinstance(ua, str) else None
        if not m or not _is_int(ts) or ts <= 0:
            continue
        if first_ts is not None and ts <= first_ts + 3600:
            continue
        v = m.group(1)
        if v not in best or ts < best[v]:
            best[v] = ts
    return sorted(({"v": v, "ts": t} for v, t in best.items()),
                  key=lambda r: (r["ts"], _vkey(r["v"])))


def build_eras(byday, first, last, markers):
    """Era averages over the complete block days first..last (day numbers). `markers` is
    [{"v", "day"}] oldest first, each inside first..last. Eras without blocks are dropped."""
    starts = [(None, first)] + [(m["v"], m["day"]) for m in markers]
    eras = []
    for k, (v, start) in enumerate(starts):
        end = starts[k + 1][1] - 1 if k + 1 < len(starts) else last
        if k > 0:
            name = v
        else:
            name = "before " + starts[1][0] if len(starts) > 1 else "all days"
        bs = [b for d in range(start, end + 1) for b in byday.get(d, [])]
        if not bs:
            continue
        eras.append({"name": name, "start": _iso(start), "end": _iso(end),
                     "w": _pct(_weighted(bs)), "m": _pct(_mean(bs)), "n": len(bs)})
    return eras


def build(cache_dir, census_lines, releases=None):
    blocks, gv = load_cache(cache_dir)
    latest = _newest(blocks) if blocks else None
    byday = blocks_by_day(blocks)
    out = {
        "source": {"name": "cesspool.lol", "url": "https://cesspool.lol/"},
        "grader_version": gv,
        "latest_block": {"height": latest["height"], "time": latest["time"]} if latest else None,
        "last24h": last_24h(blocks),
        "first_partial": False,
        "small_below": SMALL_SAMPLE_NODES,
        "groups": [],
        "types": [],
        "days": [],
        "releases": [],
        "eras": [],
    }
    if not byday:
        return out
    first, last = min(byday), max(byday)
    out["first_partial"] = min(b["time"] for b in byday[first]) % _DAY > 3600
    samples, first_ts = census_samples(census_lines)

    day_counts = {}   # census day -> {version group or None: nodes}
    for d in range(first, last + 1):
        if d in samples:
            c = {}
            for ua, n in samples[d].items():
                g = ua_group(ua)
                c[g] = c.get(g, 0) + n
            day_counts[d] = c
    marks = release_markers(releases, first_ts)
    for m in marks:
        m["day"] = m["ts"] // _DAY
    marks = [m for m in marks if first <= m["day"] <= last]
    groups = version_groups(day_counts, [m["v"] for m in marks])
    vset = set(groups)

    # spam by type: only days where every block carries sewage_by_type
    dtw = {d: type_weights(bs) for d, bs in byday.items()}
    types = type_bands([(tw, sum(b["weight"] for b in byday[d])) for d, tw in dtw.items() if tw is not None])
    if types and out["last24h"] is not None:
        win = _last_24h_blocks(blocks)
        tw = type_weights(win)
        if tw is not None:
            out["last24h"]["t"] = type_shares(tw, sum(b["weight"] for b in win), types)

    days = []
    for d in range(first, last + 1):
        row = {"d": _iso(d)}
        bs = byday.get(d)
        if bs:
            row.update(w=_pct(_weighted(bs)), m=_pct(_mean(bs)), n=len(bs))
            if types and dtw[d] is not None:
                row["t"] = type_shares(dtw[d], sum(b["weight"] for b in bs), types)
        else:
            row["n"] = 0
        if d in day_counts:
            folded = {}
            for g, n in day_counts[d].items():
                k = g if g in vset else "other"
                folded[k] = folded.get(k, 0) + n
            row["s"] = _tenths(folded, groups)
            row["c"] = sum(day_counts[d].values())
        days.append(row)
    out["groups"] = groups
    out["types"] = types
    out["days"] = days
    out["releases"] = [{"v": m["v"], "d": _iso(m["day"])} for m in marks]
    out["eras"] = build_eras(byday, first, last, marks)
    return out


def _run_self_test():
    import shutil
    import tempfile
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-34s got %r want %r" % ("ok  " if ok else "FAIL", label, got, want))

    H = "a" * 64

    def blk(h, t, s, w):
        return {"height": h, "hash": H, "time": t, "sewage_share": s, "weight": w}

    def write_batch(d, n, blocks, fetched=1, lo=None, hi=None):
        with open(os.path.join(d, "%d.json" % n), "w") as f:
            json.dump({"from": n * 1000 if lo is None else lo,
                       "to": n * 1000 + 999 if hi is None else hi,
                       "fetched": fetched, "blocks": blocks}, f)

    def write_state(d, gv):
        with open(os.path.join(d, "state.json"), "w") as f:
            json.dump({"grader_version": gv, "last_full_refresh": 1, "tip_height": 1}, f)

    D0 = 20000 * _DAY

    def test_weighted_share():
        bl = [blk(1, D0 + 10, 1.0, 100), blk(2, D0 + 20, 0.0, 300), blk(3, D0 + _DAY + 5, 0.5, 10)]
        bd = blocks_by_day(bl)
        check("only the complete day", sorted(bd), [20000])
        check("weighted share (not mean)", _pct(_weighted(bd[20000])), 25.0)
        check("per-block mean", _pct(_mean(bd[20000])), 50.0)

    def test_partial_day_excluded():
        newest = D0 + 3 * _DAY + 5000
        bl = [blk(1, D0 + 100, 0.2, 10), blk(2, newest - 90000, 0.4, 10),
              blk(3, newest - 86400, 1.0, 10), blk(4, newest - 100, 0.0, 10),
              blk(5, newest, 0.5, 20)]
        days = sorted(blocks_by_day(bl))
        check("partial day not in days", (newest // _DAY) in days, False)
        check("complete days kept", days, [20000, 20002])
        # window is inclusive at exactly newest-86400: blocks 3,4,5
        check("last24h window", last_24h(bl), {"blocks": 3, "share": round((10 + 0 + 10) / 40, 4)})
        check("last24h empty", last_24h([]), None)

    def test_grouping_and_rounding():
        check("plain version", ua_group("/Satoshi:29.5.1/Knots:20260508/"), "29.5.1")
        check("rc folds into its version", ua_group("/Satoshi:29.5.1/Knots:20260508rc1/"), "29.5.1")
        check("plumb anywhere wins", ua_group("/Satoshi:29.5.1/Knots:20260508/Plumb:14/"), "Plumb")
        check("plumb, case-insensitive", ua_group("/OracleKnots:29.5.0/x.PLUMB12/"), "Plumb")
        check("date-like token is not a version", ua_group("/GrayersDad:11.16.2026=BLAKE2b/"), None)
        check("no version at all", ua_group("/btcwire:1.0/"), None)
        check("tenths sum to 100.0", _tenths({"a": 1, "b": 1, "c": 1}, ["a", "b", "c"]),
              [33.4, 33.3, 33.3])
        check("tenths, missing group", _tenths({"a": 3}, ["a", "b"]), [100.0, 0.0])
        fin = ["29.%d.0" % i for i in range(1, 8)]
        check("newest 5 versions kept", version_groups({}, fin),
              ["29.3.0", "29.4.0", "29.5.0", "29.6.0", "29.7.0", "Plumb", "other"])
        check("peak share promotes a version",
              version_groups({1: {"29.9.0": 5, "29.1.0": 99, "Plumb": 50}, 2: {"29.8.0": 1, None: 99}}, []),
              ["29.1.0", "29.9.0", "Plumb", "other"])

    def test_release_markers():
        rel = [
            {"ua": "/Satoshi:29.4.1/Knots:20260508/", "first_ts": D0 + 100},            # running at start
            {"ua": "/Satoshi:29.5.0/Knots:20260508rc1/", "first_ts": D0 + _DAY},        # candidate
            {"ua": "/Satoshi:29.5.0/Knots:20260508/", "first_ts": D0 + 2 * _DAY + 5},
            {"ua": "/Satoshi:29.5.0/Knots:20260601/", "first_ts": D0 + 2 * _DAY + 9},   # later build date
            {"ua": "/Satoshi:29.5.1/Knots:20260508/Plumb:14/", "first_ts": D0 + 3 * _DAY},
            {"ua": "/OracleKnots:29.5.1/OracleKnots:20260508/", "first_ts": D0 + 3 * _DAY},
            {"ua": "/Satoshi:29.5.1/Knots:20260508/", "first_ts": D0 + 4 * _DAY},
            {"ua": "/Satoshi:29.5.2/Knots:20260508/", "first_ts": True},
            {"ua": 7, "first_ts": D0 + _DAY}, "junk", {"ua": "/Satoshi:29.5.3/Knots:20260508/"},
        ]
        got = release_markers(rel, D0 + 100)
        check("finals only, once each", [(m["v"], m["ts"]) for m in got],
              [("29.5.0", D0 + 2 * _DAY + 5), ("29.5.1", D0 + 4 * _DAY)])
        check("not a list", release_markers({"a": 1}, D0), [])
        check("no first sample known", [m["v"] for m in release_markers(rel[:1], None)], ["29.4.1"])

    def test_full_build():
        d = tempfile.mkdtemp()
        try:
            # days 20000..20005 complete (two blocks each: share a at weight 300, share 0 at 100),
            # day 20006 holds the newest block and is partial. The cache starts at 02:00 UTC.
            a = [0.4, 0.4, 0.8, 0.8, 0.2, 0.2]
            bl = []
            for i, ai in enumerate(a):
                t = D0 + i * _DAY + (7200 if i == 0 else 600)
                bl.append(blk(100 + 2 * i, t, ai, 300))
                bl.append(blk(101 + 2 * i, t + 60, 0.0, 100))
            bl.append(blk(200, D0 + 6 * _DAY + 50, 1.0, 100))
            write_batch(d, 0, bl, lo=0, hi=999)
            U = {"x0": "/Satoshi:29.5.0/Knots:20260508/", "x1": "/Satoshi:29.5.1/Knots:20260508rc1/",
                 "pl": "/Satoshi:29.5.1/Knots:20260508/Plumb:14/", "old": "/Satoshi:29.1.0/",
                 "gd": "/GrayersDad:11.16.2026=BLAKE2b/"}

            def line(ts, vs):
                return json.dumps({"ts": ts, "b2b_reachable": sum(vs.values()), "versions": vs})
            cen = [
                line(D0 + _DAY + 10, {U["x0"]: 40, U["old"]: 60}),                 # replaced later that day
                line(D0 + _DAY + 99, {U["x0"]: 50, U["x1"]: 30, U["pl"]: 10, U["old"]: 1, U["gd"]: 9}),
                json.dumps({"ts": D0 + _DAY + 200, "b2b_reachable": 7, "versions": {U["x0"]: 1}}),
                "garbage",
                line(D0 + 2 * _DAY + 1, {U["x0"]: 4}),
                line(D0 + 4 * _DAY + 1, {U["x0"]: 1, U["x1"]: 1, U["pl"]: 1}),
                line(D0 + 5 * _DAY + 1, {U["x1"]: 2}),
                line(D0 + 6 * _DAY + 1, {U["x1"]: 9}),                             # partial day: not drawn
            ]
            rel = [{"ua": U["x0"].replace("29.5.0", "29.4.1"), "first_ts": D0 + _DAY + 10},
                   {"ua": "/Satoshi:29.5.0/Knots:20260508rc1/", "first_ts": D0 + _DAY + 99},
                   {"ua": U["x0"], "first_ts": D0 + 2 * _DAY + 1},
                   {"ua": "/Satoshi:29.5.1/Knots:20260508/", "first_ts": D0 + 4 * _DAY + 1},
                   {"ua": "/Satoshi:29.6.0/Knots:20260508/", "first_ts": D0 + 6 * _DAY + 1}]
            out = build(d, cen, rel)
            check("six complete days", [r["d"] for r in out["days"]], [_iso(20000 + i) for i in range(6)])
            check("first day is partial", out["first_partial"], True)
            check("weighted/mean/blocks", [(r["w"], r["m"], r["n"]) for r in out["days"]][:3],
                  [(30.0, 20.0, 2), (30.0, 20.0, 2), (60.0, 40.0, 2)])
            check("groups", out["groups"], ["29.5.0", "29.5.1", "Plumb", "other"])
            sv = {r["d"]: r.get("s") for r in out["days"]}
            check("no version data: first day", sv[_iso(20000)], None)
            check("no version data: gap day", sv[_iso(20003)], None)
            check("last sample of the day wins", sv[_iso(20001)], [50.0, 30.0, 10.0, 10.0])
            check("node count", [r.get("c") for r in out["days"]], [None, 100, 4, None, 3, 2])
            check("rc and 29.5.1 fold together", sv[_iso(20004)], [33.4, 33.3, 33.3, 0.0])
            check("sums to 100", all(abs(sum(s) - 100.0) < 1e-9 for s in sv.values() if s), True)
            check("markers: finals in range, not rc, not preexisting, not future",
                  out["releases"], [{"v": "29.5.0", "d": _iso(20002)}, {"v": "29.5.1", "d": _iso(20004)}])
            check("era boundaries", [(e["name"], e["start"], e["end"], e["w"], e["m"], e["n"])
                                     for e in out["eras"]],
                  [("before 29.5.0", _iso(20000), _iso(20001), 30.0, 20.0, 4),
                   ("29.5.0", _iso(20002), _iso(20003), 60.0, 40.0, 4),
                   ("29.5.1", _iso(20004), _iso(20005), 15.0, 10.0, 4)])
            check("last24h from newest block", out["last24h"]["blocks"], 1 + 2)
            # no releases at all: one era over every day
            out2 = build(d, cen)
            check("no releases -> one era", [(e["name"], e["n"]) for e in out2["eras"]], [("all days", 12)])
            check("no releases -> no markers", out2["releases"], [])
            # missing block day inside the range stays a gap, not a zero
            write_batch(d, 0, [b for b in bl if b["time"] // _DAY != 20003], lo=0, hi=999)
            gap = [r for r in build(d, cen, rel)["days"] if r["d"] == _iso(20003)][0]
            check("empty day is a gap", gap, {"d": _iso(20003), "n": 0})
        finally:
            shutil.rmtree(d)

    def test_no_cache():
        d = tempfile.mkdtemp()
        try:
            out = build(d, [])
            check("no cache days", out["days"], [])
            check("no cache eras", (out["eras"], out["releases"], out["groups"]), ([], [], []))
            check("no cache latest", out["latest_block"], None)
            check("no cache last24h", out["last24h"], None)
            check("missing dir ok", build(os.path.join(d, "nope"), [])["days"], [])
            write_batch(d, 5, [])
            check("empty batch ok", build(d, [])["latest_block"], None)
        finally:
            shutil.rmtree(d)

    def test_grader_version_rejected():
        d = tempfile.mkdtemp()
        try:
            for bad in ["<b>x</b>", "v1\n", "", "a" * 65]:
                write_state(d, bad)
                check("gv rejected %r" % bad[:8], build(d, [])["grader_version"], None)
            write_state(d, "v29.5.1.knots20260508.plumb15+g2")
            check("gv accepted", build(d, [])["grader_version"],
                  "v29.5.1.knots20260508.plumb15+g2")
        finally:
            shutil.rmtree(d)

    def test_cache_dedupe_and_junk():
        d = tempfile.mkdtemp()
        try:
            write_batch(d, 1, [blk(1000, D0 + 1, 0.1, 10), blk(1001, D0 + 2, 0.1, 10)], fetched=1)
            write_batch(d, 2, [blk(2000, D0 + 3, 0.2, 10)], fetched=5)
            # newer rewrite of batch 1 wins for 1000; 1001 is gone from it
            with open(os.path.join(d, "1.json"), "w") as f:
                json.dump({"from": 1000, "to": 1999, "fetched": 9, "blocks": [
                    blk(1000, D0 + 1, 0.9, 10), {"height": "x"},
                    blk(1500, D0 + 4, float("nan"), 10),
                    blk(5000, D0 + 4, 0.5, 10), blk(1002, D0 + 4, 1.5, 10)]}, f)
            write_batch(d, 7, [blk(7000, D0 + 4, 0.3, 10)], fetched=0)
            with open(os.path.join(d, "3.json"), "w") as f:
                f.write("{not json")
            with open(os.path.join(d, "4.json.tmp"), "w") as f:
                f.write("{}")
            write_batch(d, 6, [blk(6000, D0 + 4, 0.5, 10)], lo=6500, hi=6999)  # outside from/to
            bl, _ = load_cache(d)
            check("dedupe + validate", [(b["height"], b["sewage_share"]) for b in bl],
                  [(1000, 0.9), (2000, 0.2), (7000, 0.3)])
        finally:
            shutil.rmtree(d)

    def test_unsafe_files_skipped():
        d = tempfile.mkdtemp()
        o = tempfile.mkdtemp()
        try:
            write_batch(o, 1, [blk(1000, D0 + 1, 0.5, 10)])
            real = os.path.join(o, "1.json")
            made = False
            try:
                os.symlink(real, os.path.join(d, "1.json"))
                made = True
            except (OSError, NotImplementedError, AttributeError):
                print("skip  symlink test (cannot create symlinks here)")
            if made:
                check("symlinked batch ignored", load_cache(d)[0], [])
            if hasattr(os, "mkfifo"):
                os.mkfifo(os.path.join(d, "2.json"))
                check("FIFO batch skipped, no hang", load_cache(d)[0], [])
            else:
                print("skip  FIFO test (no mkfifo on this platform)")
            write_batch(d, 3, [blk(3000, D0 + 2, 0.5, 10)])
            check("regular file still read", [b["height"] for b in load_cache(d)[0]], [3000])
        finally:
            shutil.rmtree(d)
            shutil.rmtree(o)

    def test_corrupt_cache_exit():
        d = tempfile.mkdtemp()
        try:
            write_state(d, "v1")
            with open(os.path.join(d, "961.json"), "w") as f:
                f.write("{not json")
            cen = os.path.join(d, "census.jsonl")
            open(cen, "w").close()
            check("state ok but no blocks -> exit 1", main(["x", d, cen]), 1)
            d2 = tempfile.mkdtemp()
            try:
                check("no cache at all -> exit 0", main(["x", d2, cen]), 0)
            finally:
                shutil.rmtree(d2)
        finally:
            shutil.rmtree(d)

    def test_types():
        d = tempfile.mkdtemp()
        try:
            # days 20000..20003 complete, 20004 holds the newest block (partial).
            # day 20001 has one unmapped block; every other day is fully mapped.
            # 2 blocks per day, weight 1000 each, share 0.5 and 0.1 -> sewage weight 600/day.
            def mk(i, with_map, m):
                t = D0 + i * _DAY
                b1, b2 = blk(100 + 4 * i, t + 10, 0.5, 1000), blk(101 + 4 * i, t + 20, 0.1, 1000)
                if with_map:
                    b1["sewage_by_type"], b2["sewage_by_type"] = m
                return [b1, b2]
            bl = []
            # 8 types plus a tiny unknown: unknown must displace the 6th type; f,g,h fold to other
            m1 = {"a": 200, "b": 100, "c": 60, "d": 50, "e": 40, "f": 20, "g": 15, "h": 10, "unknown": 5}  # 500
            m2 = {"a": 60, "b": 40}                                      # 100
            for i in range(4):
                bl += mk(i, i != 1, (m1, m2))
            bl[2]["sewage_by_type"] = {"a": 1}                           # day 20001: block 0 mapped,
            bl[3].pop("sewage_by_type", None)                            # block 1 not -> partial
            bl.append(blk(500, D0 + 4 * _DAY + 5, 0.0, 1000))            # newest, unmapped
            write_batch(d, 0, bl, lo=0, hi=999)
            out = build(d, [])
            rows = {r["d"]: r for r in out["days"]}
            ty = out["types"]
            check("types: top 6 by weight + unknown + other",
                  ty, ["a", "b", "c", "d", "e", "unknown", "other"])
            check("types: unknown kept though tiny", "unknown" in ty, True)
            check("types: other last", ty[-1], "other")
            check("partial day omits t", "t" in rows[_iso(20001)], False)
            check("full days carry t", [("t" in rows[_iso(20000 + i)]) for i in (0, 2, 3)], [True] * 3)
            for i in (0, 2, 3):
                r = rows[_iso(20000 + i)]
                check("t length == types", len(r["t"]), len(ty))
                check("t sums to w within rounding", abs(sum(r["t"]) - r["w"]) <= 0.05 * len(ty), True)
            r = rows[_iso(20000)]
            check("t values (shares of 2000 weight)", r["t"],
                  [_pct(260 / 2000), _pct(140 / 2000), _pct(60 / 2000), _pct(50 / 2000), _pct(40 / 2000),
                   _pct(5 / 2000), _pct((20 + 15 + 10) / 2000)])
            check("last24h without maps has no t", "t" in out["last24h"], False)
            # no maps at all
            plain = [{k: v for k, v in b.items() if k != "sewage_by_type"} for b in bl]
            write_batch(d, 0, plain, lo=0, hi=999)
            o2 = build(d, [])
            check("no maps -> types []", o2["types"], [])
            check("no maps -> no t anywhere", any("t" in r for r in o2["days"]) or "t" in o2["last24h"], False)
            # only the last two complete days have maps, newest block mapped: last24h.t present
            part = [dict(b) for b in plain]
            for b in part:
                if b["time"] >= D0 + 3 * _DAY:
                    b["sewage_by_type"] = {"x_type": 100} if b["height"] != 500 else {}
            write_batch(d, 0, part, lo=0, hi=999)
            o3 = build(d, [])
            check("recent-only maps: one type", o3["types"], ["x_type"])
            check("recent-only maps: last24h.t present", o3["last24h"].get("t"), [_pct(200 / 3000)])
            check("recent-only maps: older days plain", [("t" in r) for r in o3["days"]],
                  [False, False, False, True])
            # a type id invalid -> map dropped for that block -> day without t
            bad = [dict(b) for b in part]
            for b in bad:
                if b["height"] == 101 + 4 * 3:
                    b["sewage_by_type"] = {"BAD": 1}
            write_batch(d, 0, bad, lo=0, hi=999)
            o4 = build(d, [])
            check("invalid map -> day loses t", [("t" in r) for r in o4["days"]][3], False)
            check("invalid map -> block still counted", o4["days"][3]["n"], 2)
            ov = [dict(b) for b in part]
            ov[-3]["sewage_by_type"] = {"x_type": 2000}                  # > block weight
            write_batch(d, 0, ov, lo=0, hi=999)
            check("map over block weight dropped", "t" in build(d, [])["days"][3], False)
            # a real type named "other" is folded, and unknown outside the top 6 still wins a slot
            fo = [dict(b) for b in plain]
            for b in fo:
                b["sewage_by_type"] = {"other": 10, "unknown": 1, "p": 9, "q": 8, "r": 7, "s": 6, "t": 5, "u": 4}
            write_batch(d, 0, fo, lo=0, hi=999)
            o5 = build(d, [])
            check("unknown kept below the cut, other folds", o5["types"],
                  ["p", "q", "r", "s", "t", "unknown", "other"])
            row = o5["days"][0]
            check("folded other share", row["t"][-1], _pct(2 * (10 + 4) / 2000))
            # peak beats total: 'spike' is tiny in total (one day) but has the highest single-day share
            sp = [dict(b) for b in plain]
            for b in sp:
                m = {c: 10 for c in "pqrstuv"}                           # steady: 7 types
                if b["time"] // _DAY == 20002:
                    m["spike"] = 400
                b["sewage_by_type"] = m
            write_batch(d, 0, sp, lo=0, hi=999)
            o6 = build(d, [])
            check("spike type gets a band, ranked first", o6["types"][0], "spike")
            check("max 6 named + other", (len(o6["types"]), o6["types"][-1]), (7, "other"))
            check("spike share on its day", o6["days"][2]["t"][0], _pct(800 / 2000))
            # real cesspool ids use hyphens
            hy = [dict(b) for b in plain]
            for b in hy:
                b["sewage_by_type"] = {"fake-multisig-padded": 30, "fake-multisig": 20, "unknown": 5}
            write_batch(d, 0, hy, lo=0, hi=999)
            o7 = build(d, [])
            check("hyphenated ids kept as bands", o7["types"], ["fake-multisig-padded", "fake-multisig", "unknown"])
            check("hyphenated ids carry t", "t" in o7["days"][0], True)
        finally:
            shutil.rmtree(d)

    for t in (test_types, test_unsafe_files_skipped,test_corrupt_cache_exit, test_weighted_share, test_partial_day_excluded,
              test_grouping_and_rounding, test_release_markers, test_full_build, test_no_cache, test_grader_version_rejected, test_cache_dedupe_and_junk):
        t()
    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


def main(argv):
    if "--self-test" in argv:
        return _run_self_test()
    if len(argv) not in (3, 4):
        print("usage: gen_spam_share.py CACHE_DIR CENSUS_JSONL [RELEASES_JSON] | --self-test",
              file=sys.stderr)
        return 2
    try:
        with open(argv[2], encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print("gen_spam_share: cannot read census: %s" % e, file=sys.stderr)
        return 1
    releases = _read_json(argv[3]) if len(argv) == 4 else None   # unreadable: no release markers
    out = build(argv[1], lines, releases)
    if out["grader_version"] is not None and out["latest_block"] is None:
        # a valid state.json but not one readable block: a corrupt cache, not a fresh install
        print("gen_spam_share: cache has state.json but no readable blocks", file=sys.stderr)
        return 1
    print(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
