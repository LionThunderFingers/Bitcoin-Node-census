#!/usr/bin/env python3
"""Fetch cesspool.lol per-block grade files into a validated, sanitised cache.

Runs unprivileged (system user census-fetch) and writes only the cache directory. Per block it
keeps five numeric/hex fields and nothing else; no text from cesspool reaches the cache, except
grader_version after it matches a strict pattern. Cache layout:
  CACHE/<n>.json   {"from", "to", "fetched", "blocks": [{height, hash, time, sewage_share, weight}]}
  CACHE/state.json {"grader_version", "last_full_refresh", "tip_height"}
Normal hourly run = 2 requests (tip + newest batch). Every batch is re-read when grader_version
changes and once every 24 h. A full refresh writes nothing until every file has downloaded and
validated. Requests are spaced 2.5 s apart. Exit 0 on success, 1 on any failure (cache untouched).

Run: `python3 cesspool-fetch.py [--cache DIR]` (default /var/lib/census-cesspool).
Test: `python3 cesspool-fetch.py --self-test`.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request

BASE = "https://cesspool.lol/api/v1/"
FIRST_BATCH = 961
MAX_BATCH = 2000          # sanity cap on the newest batch number (height 2,000,000)
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20
SPACING = 2.5
FULL_REFRESH_SECS = 24 * 3600
USER_AGENT = "BitcoinNodeCensus/1 (+https://github.com/LionThunderFingers/Bitcoin-Node-census)"
DEFAULT_CACHE = "/var/lib/census-cesspool"
KEYS = ("height", "hash", "time", "sewage_share", "weight")

_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9.+_-]{1,64}")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_block(obj, lo, hi):
    """The five kept fields of a block, or None if anything is off. Extra keys are dropped."""
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


def _load(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("response is not bytes")
    if len(raw) > MAX_BYTES:
        raise ValueError("response too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ValueError("not JSON: %s" % type(e).__name__)


def parse_batch(raw_bytes, n):
    """Validated, height-sorted, de-duplicated blocks of batch n. ValueError on a bad shape."""
    d = _load(raw_bytes)
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError("wrong shape")
    lo, hi = n * 1000, n * 1000 + 999
    f, t = d.get("from"), d.get("to")
    if not _is_int(f) or not _is_int(t) or not (lo <= f <= t <= hi):
        raise ValueError("from/to outside batch %d" % n)
    if len(d["blocks"]) > 1000:
        raise ValueError("too many blocks")
    out = {}
    for b in d["blocks"]:
        v = validate_block(b, f, t)
        if v is not None:
            out[v["height"]] = v
    return [out[k] for k in sorted(out)]


def parse_tip(raw_bytes):
    d = _load(raw_bytes)
    if not isinstance(d, dict):
        raise ValueError("wrong shape")
    h, gv = d.get("height"), d.get("grader_version")
    if not _is_int(h) or h < 0:
        raise ValueError("bad tip height")
    if not isinstance(gv, str) or not _VERSION.fullmatch(gv):
        raise ValueError("bad grader_version")
    return h, gv


def _read_state(cache_dir):
    try:
        with open(os.path.join(cache_dir, "state.json"), "rb") as f:
            s = json.loads(f.read(MAX_BYTES))
        if (isinstance(s, dict) and isinstance(s.get("grader_version"), str)
                and _VERSION.fullmatch(s["grader_version"])
                and _is_int(s.get("last_full_refresh")) and _is_int(s.get("tip_height"))):
            return s
    except (OSError, ValueError):
        pass
    return None


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, path)


def run(cache_dir, opener, now, sleep):
    try:
        return _run(cache_dir, opener, now, sleep)
    except Exception as e:  # any failure: leave the cache alone, report once
        print("cesspool-fetch: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1


def _run(cache_dir, opener, now, sleep):
    os.makedirs(cache_dir, exist_ok=True)
    state = _read_state(cache_dir)
    requested = [0]

    def get(url):
        if requested[0]:
            sleep(SPACING)
        requested[0] += 1
        return opener(url)

    tip, gv = parse_tip(get(BASE + "tip.json"))
    newest = tip // 1000
    if not (FIRST_BATCH <= newest <= MAX_BATCH):
        raise ValueError("tip height %d out of range" % tip)
    full = (state is None or state["grader_version"] != gv
            or now - state["last_full_refresh"] >= FULL_REFRESH_SECS
            or state["last_full_refresh"] > now
            or any(not os.path.exists(os.path.join(cache_dir, "%d.json" % n))
                   for n in range(FIRST_BATCH, newest)))
    if full:
        first = FIRST_BATCH
    else:
        # the previously newest file was partial; re-read it if the tip moved to a new batch
        first = max(FIRST_BATCH, min(newest, state["tip_height"] // 1000))
    fetched = {}
    for n in range(first, newest + 1):
        blocks = parse_batch(get("%sblocks/%d.json" % (BASE, n)), n)
        if blocks:
            lo, hi = blocks[0]["height"], blocks[-1]["height"]
        else:
            lo = hi = n * 1000
        fetched[n] = {"from": lo, "to": hi, "fetched": now, "blocks": blocks}
    for n, obj in fetched.items():
        _write(os.path.join(cache_dir, "%d.json" % n), obj)
    _write(os.path.join(cache_dir, "state.json"), {
        "grader_version": gv,
        "last_full_refresh": now if full else state["last_full_refresh"],
        "tip_height": tip,
    })
    return 0


def _urlopen(url):
    if not url.startswith(BASE):
        raise ValueError("refusing URL outside " + BASE)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return data


# ---------------------------------------------------------------- self-test

def _run_self_test():
    import tempfile
    failures = []

    def check(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    H = "a" * 64

    def blk(h, **kw):
        b = {"height": h, "hash": H, "time": 1700000000, "grade": "A", "grade_label": "x",
             "sewage_share": 0.25, "sewage_tx": 1, "gray_tx": 2, "weight": 3990000,
             "url": "https://cesspool.lol/block/%d/" % h, "grader_version": "v1"}
        b.update(kw)
        return b

    def batch(n, hi=None, **kw):
        hi = n * 1000 + 999 if hi is None else hi
        lo = 961640 if n == 961 else n * 1000
        return json.dumps({"api": 1, "from": lo, "to": hi,
                           "blocks": [blk(h, **kw) for h in (lo, hi)]}).encode()

    def tip(h, gv="v1"):
        return json.dumps({"api": 1, "height": h, "hash": H, "grader_version": gv}).encode()

    def test_block_validation():
        lo, hi = 1000, 1999
        check("valid block kept", validate_block(blk(1500), lo, hi) is not None)
        for label, bad in [("share string", blk(1500, sewage_share="0.5")),
                           ("share negative", blk(1500, sewage_share=-0.1)),
                           ("share >1", blk(1500, sewage_share=1.5)),
                           ("share nan", blk(1500, sewage_share=float("nan"))),
                           ("weight 0", blk(1500, weight=0)),
                           ("weight too big", blk(1500, weight=5_000_000)),
                           ("bad hash", blk(1500, hash="xyz")),
                           ("height below", blk(999)), ("height above", blk(2000)),
                           ("bool share", blk(1500, sewage_share=True)),
                           ("hash trailing newline", blk(1500, hash="a" * 64 + "\n")),
                           ("not a dict", [1, 2])]:
            check("rejects " + label, validate_block(bad, lo, hi) is None)

    def test_only_numeric_fields_cached():
        b = blk(1500, grade_label="<img onerror=x>", url="javascript:alert(1)")
        v = validate_block(b, 1000, 1999)
        check("exactly five keys", v is not None and sorted(v) == sorted(KEYS))
        raw = json.dumps({"from": 1000, "to": 1999, "blocks": [b]}).encode()
        got = parse_batch(raw, 1)
        check("parse_batch keeps no text", bool(got) and "<img" not in json.dumps(got)
              and "javascript" not in json.dumps(got))

    def make_opener(table, log):
        def opener(url):
            log.append(url)
            r = table[url]
            if isinstance(r, Exception):
                raise r
            return r
        return opener

    def seeded(d, tip_h=963500, gv="v1", last=1000):
        full = {BASE + "tip.json": tip(tip_h, gv)}
        for n in range(961, tip_h // 1000 + 1):
            full["%sblocks/%d.json" % (BASE, n)] = batch(n, hi=tip_h if n == tip_h // 1000 else None)
        log, sl = [], []
        rc = run(d, make_opener(full, log), last, sl.append)
        return rc, log, sl, full

    def snap(d):
        return {f: open(os.path.join(d, f), "rb").read() for f in sorted(os.listdir(d))}

    def test_bad_responses_leave_cache():
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, full = seeded(d)
            check("seed run ok", rc == 0)
            before = snap(d)
            later = 1000 + 7200
            newest = BASE + "blocks/963.json"
            for label, resp in [("html", b"<html>"), ("timeout", TimeoutError("t")),
                                ("oversize", b"x" * (2 * 1024 * 1024 + 1)),
                                ("bad from/to", json.dumps({"from": 5, "to": 6, "blocks": []}).encode()),
                                ("truncated", batch(963, hi=963500)[:50])]:
                t = dict(full)
                t[newest] = resp
                rc = run(d, make_opener(t, []), later, lambda s: None)
                check("bad newest (%s) -> nonzero, cache identical" % label,
                      rc != 0 and snap(d) == before)
            t = dict(full)
            t[BASE + "tip.json"] = b"<html>"
            check("bad tip -> nonzero", run(d, make_opener(t, []), later, lambda s: None) != 0
                  and snap(d) == before)
            t = dict(full)
            t[BASE + "tip.json"] = tip(963500, "v2")
            t[BASE + "blocks/962.json"] = b"<html>"
            check("full refresh all-or-nothing", run(d, make_opener(t, []), later, lambda s: None) != 0
                  and snap(d) == before)
            t = dict(full)
            t[BASE + "tip.json"] = tip(963500, "v2\n")
            check("grader_version trailing newline -> tip rejected",
                  run(d, make_opener(t, []), later, lambda s: None) != 0 and snap(d) == before)
            st = json.load(open(os.path.join(d, "state.json")))
            st["grader_version"] = "v1\n"
            _write(os.path.join(d, "state.json"), st)
            check("state with newline grader_version ignored", _read_state(d) is None)
            with open(os.path.join(d, "state.json"), "wb") as f:
                f.write(before["state.json"])
            check("no tmp files left", not [f for f in os.listdir(d) if f.endswith(".tmp")])

    def test_hourly_run_two_requests():
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, full = seeded(d, last=1000)
            log = []
            rc = run(d, make_opener(full, log), 1000 + 3600, lambda s: None)
            check("hourly ok", rc == 0)
            check("exactly tip + newest", log == [BASE + "tip.json", BASE + "blocks/963.json"])

    def test_regrade_refetches_all():
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, full = seeded(d, last=1000)
            full[BASE + "tip.json"] = tip(963500, "v2")
            log, sl = [], []
            rc = run(d, make_opener(full, log), 5000, sl.append)
            want = [BASE + "tip.json"] + ["%sblocks/%d.json" % (BASE, n) for n in range(961, 964)]
            check("regrade ok", rc == 0)
            check("every file requested", log == want)
            check("sleep 2.5 between requests", sl == [2.5] * (len(want) - 1))
            st = json.load(open(os.path.join(d, "state.json")))
            check("state updated", st == {"grader_version": "v2", "last_full_refresh": 5000,
                                          "tip_height": 963500})
            first = json.load(open(os.path.join(d, "961.json")))
            check("cached file shape", sorted(first) == ["blocks", "fetched", "from", "to"]
                  and first["from"] == 961640 and first["fetched"] == 5000
                  and all(sorted(b) == sorted(KEYS) for b in first["blocks"]))

    def test_daily_refresh():
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, full = seeded(d, last=1000)
            log = []
            rc = run(d, make_opener(full, log), 1000 + 25 * 3600, lambda s: None)
            check("daily ok", rc == 0 and len(log) == 4)
            st = json.load(open(os.path.join(d, "state.json")))
            check("last_full_refresh advanced", st["last_full_refresh"] == 1000 + 25 * 3600)
            log = []
            run(d, make_opener(full, log), 1000 + 25 * 3600 + 60, lambda s: None)
            check("then hourly again", len(log) == 2)

    def test_new_batch_rereads_previous():
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, full = seeded(d, tip_h=963990, last=1000)
            full[BASE + "tip.json"] = tip(964010)
            full[BASE + "blocks/963.json"] = batch(963)
            full[BASE + "blocks/964.json"] = batch(964, hi=964010)
            log = []
            rc = run(d, make_opener(full, log), 1000 + 3600, lambda s: None)
            check("batch rollover rereads partial predecessor",
                  rc == 0 and log[1:] == [BASE + "blocks/963.json", BASE + "blocks/964.json"])

    for t in (test_block_validation, test_only_numeric_fields_cached, test_bad_responses_leave_cache,
              test_hourly_run_two_requests, test_regrade_refetches_all, test_daily_refresh,
              test_new_batch_rereads_previous):
        try:
            t()
        except Exception as e:
            check("%s raised %s: %s" % (t.__name__, type(e).__name__, e), False)
    print("self-test: ok" if not failures else "self-test: %d FAILED" % len(failures))
    return 1 if failures else 0


def main(argv):
    if "--self-test" in argv:
        return _run_self_test()
    cache = DEFAULT_CACHE
    args = argv[1:]
    if args:
        if len(args) == 2 and args[0] == "--cache":
            cache = args[1]
        else:
            print("usage: cesspool-fetch.py [--cache DIR] | --self-test", file=sys.stderr)
            return 2
    return run(cache, _urlopen, int(time.time()), time.sleep)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
