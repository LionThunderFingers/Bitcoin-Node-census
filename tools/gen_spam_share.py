#!/usr/bin/env python3
"""Join the sanitised cesspool.lol block-grade cache with census.jsonl into spam-share.json.

Two separate measurements, drawn side by side by the page and never merged:
  blocks: per complete UTC day, the weight-weighted share of block weight cesspool.lol grades
          as sewage (sum(share*weight)/sum(weight), never a mean of per-block shares);
  nodes:  per UTC day, the last census sample's count of rated-good nodes running filtering
          software (Plumb anywhere in the user agent, or official Knots >= FILTER_MIN_KNOTS).
The partial UTC day of the newest block is never a daily point; `last24h` is its own figure.
Every time reference is the newest block's time, never the wall clock, so the same inputs
always give the same output. The cache is treated as untrusted input and re-validated here.

Output (stdout): {source, grader_version, filter_min_knots, latest_block, last24h, blocks, nodes}.
A missing or empty cache is not an error (blocks: []); an unreadable census.jsonl exits non-zero.

Run: `python3 gen_spam_share.py CACHE_DIR CENSUS_JSONL` writes the JSON object to stdout.
Test: `python3 gen_spam_share.py --self-test`.
"""
import json
import math
import os
import re
import sys

try:
    from policy_model import canonicalize, classify_enforcing, parse_min_version
except ImportError:
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    from policy_model import canonicalize, classify_enforcing, parse_min_version

FILTER_MIN_KNOTS = "29.5.1"
_DAY = 86400
_MAX_BYTES = 2 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9.+_-]{1,64}")
_BATCH_NAME = re.compile(r"([0-9]{1,7})\.json")
_PLUMB = re.compile(r"plumb", re.I)


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
    return {"height": h, "hash": hsh, "time": t, "sewage_share": float(share), "weight": w}


def _read_json(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(_MAX_BYTES + 1)
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
    for name in names:
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
    return round(sum(b["sewage_share"] * b["weight"] for b in blocks) / w, 4)


def _newest(blocks):
    return max(blocks, key=lambda b: (b["height"], b["time"]))


def day_shares(blocks):
    """One entry per COMPLETE UTC day, oldest first. The UTC day of the newest block is
    partial and is left out."""
    if not blocks:
        return []
    cur = _newest(blocks)["time"] // _DAY
    days = {}
    for b in blocks:
        d = b["time"] // _DAY
        if d < cur:
            days.setdefault(d, []).append(b)
    return [{"day": d, "blocks": len(days[d]), "share": _weighted(days[d])} for d in sorted(days)]


def last_24h(blocks):
    """Weighted share of blocks with time >= newest_time - 86400, or None."""
    if not blocks:
        return None
    ref = _newest(blocks)["time"]
    win = [b for b in blocks if b["time"] >= ref - _DAY]
    return {"blocks": len(win), "share": _weighted(win)}


def _as_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def node_series(lines, min_version):
    """Last valid sample of each UTC day, oldest first (whole history). Plumb is tested
    first and counted once; official Knots >= min_version counts as knots_filter."""
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
        plumb = kf = total = 0
        for ua, count in versions.items():
            n = _as_int(count)
            if n <= 0:
                continue
            total += n
            ua = str(ua)
            if _PLUMB.search(ua):
                plumb += n
            elif classify_enforcing(canonicalize(ua), min_version):
                kf += n
        if total != reach:
            continue
        samples.append({"ts": ts, "rated_good": reach, "plumb": plumb, "knots_filter": kf,
                        "share": round((plumb + kf) / reach, 4)})
    samples.sort(key=lambda p: p["ts"])
    return [p for i, p in enumerate(samples)
            if i + 1 == len(samples) or samples[i + 1]["ts"] // _DAY != p["ts"] // _DAY]


def build(cache_dir, census_lines):
    blocks, gv = load_cache(cache_dir)
    latest = _newest(blocks) if blocks else None
    return {
        "source": {"name": "cesspool.lol", "url": "https://cesspool.lol/"},
        "grader_version": gv,
        "filter_min_knots": FILTER_MIN_KNOTS,
        "latest_block": {"height": latest["height"], "time": latest["time"]} if latest else None,
        "last24h": last_24h(blocks),
        "blocks": day_shares(blocks),
        "nodes": node_series(census_lines, parse_min_version(FILTER_MIN_KNOTS)),
    }


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
        check("weighted share (not mean)", day_shares(bl),
              [{"day": 20000, "blocks": 2, "share": 0.25}])

    def test_partial_day_excluded():
        newest = D0 + 3 * _DAY + 5000
        bl = [blk(1, D0 + 100, 0.2, 10), blk(2, newest - 90000, 0.4, 10),
              blk(3, newest - 86400, 1.0, 10), blk(4, newest - 100, 0.0, 10),
              blk(5, newest, 0.5, 20)]
        days = [d["day"] for d in day_shares(bl)]
        check("partial day not in blocks", (newest // _DAY) in days, False)
        check("complete days kept", days, [20000, 20002])
        # window is inclusive at exactly newest-86400: blocks 3,4,5
        check("last24h window", last_24h(bl), {"blocks": 3, "share": round((10 + 0 + 10) / 40, 4)})
        check("last24h empty", last_24h([]), None)

    def test_node_series_classification():
        mv = parse_min_version("29.5.1")
        UA = {"kf": "/Satoshi:29.5.1/Knots:20260508/", "no": "/Satoshi:29.5.0/Knots:20260508/",
              "pl": "/Satoshi:29.5.1/Knots:20260508/Plumb:14/", "ol": "/OracleKnots:29.5.0/plumb12/",
              "fu": "/Satoshi:29.6.0(foo)/Knots:20270101/"}

        def line(ts, reach, vs):
            return json.dumps({"ts": ts, "b2b_reachable": reach, "versions": vs})
        lines = [
            line(D0 + 10, 99, {UA["kf"]: 99}),                     # earlier same day: replaced
            line(D0 + 50, 5, {UA["kf"]: 1, UA["no"]: 1, UA["pl"]: 1, UA["ol"]: 1, UA["fu"]: 1}),
            line(D0 + 60, 7, {UA["kf"]: 1}),                       # torn (sum != reach): skipped
            "garbage",
            line(D0 + _DAY + 1, 2, {UA["no"]: 2}),
        ]
        got = node_series(lines, mv)
        check("one point per day", len(got), 2)
        check("classification", got[0], {"ts": D0 + 50, "rated_good": 5, "plumb": 2,
                                         "knots_filter": 2, "share": 0.8})
        check("neither -> zero", got[1]["share"], 0.0)

    def test_no_cache():
        d = tempfile.mkdtemp()
        try:
            out = build(d, [])
            check("no cache blocks", out["blocks"], [])
            check("no cache latest", out["latest_block"], None)
            check("no cache last24h", out["last24h"], None)
            check("missing dir ok", build(os.path.join(d, "nope"), [])["blocks"], [])
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

    for t in (test_weighted_share, test_partial_day_excluded, test_node_series_classification,
              test_no_cache, test_grader_version_rejected, test_cache_dedupe_and_junk):
        t()
    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


def main(argv):
    if "--self-test" in argv:
        return _run_self_test()
    if len(argv) != 3:
        print("usage: gen_spam_share.py CACHE_DIR CENSUS_JSONL | --self-test", file=sys.stderr)
        return 2
    try:
        with open(argv[2], encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print("gen_spam_share: cannot read census: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(build(argv[1], lines), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
