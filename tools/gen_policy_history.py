#!/usr/bin/env python3
"""Build the exclusive-bucket policy-adoption time series from census.jsonl.

One point per parsable sample, oldest first:
  {ts, b2b_reachable, buckets: {"pre": int, "<policy id>": int, ...}}
Thinned past 90 days back: full resolution inside that window, then the last point of each
UTC day. The page fetches this file in full on every load, so it cannot grow for ever. See
decimate().
Each reachable post-fork node is counted in exactly one bucket: the newest policy its
software enforces, else "pre". Counts are raw; the page divides by b2b_reachable. Buckets
always carry "pre" plus every policy id, zero-filled, so a reader can tell zero from absent.

Run: `python3 gen_policy_history.py CENSUS_JSONL POLICIES_JSON` writes the JSON array to
stdout. Test: `python3 gen_policy_history.py --self-test`.
"""
import json
import sys

try:
    from policy_model import parse_policies, policy_bucket
except ImportError:
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    from policy_model import parse_policies, policy_bucket


def _as_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# How far back the full hourly resolution is kept. Beyond it one point per UTC day is
# enough to draw the curve, and the file is fetched in full on every page load, so it
# cannot be allowed to grow without bound (about 0.9 MB a year at hourly resolution).
DECIMATE_AFTER_DAYS = 90
_DAY = 86400


def decimate(points):
    """Every point from the last DECIMATE_AFTER_DAYS days, plus the last point of each UTC
    day before that. Points must already be sorted oldest first; the order is preserved.

    The cutoff is measured from the NEWEST point in the series, not from the wall clock.
    The whole file is regenerated from census.jsonl on every publish, so a wall-clock
    cutoff would silently re-trim already-published points as the clock moved and make the
    output depend on when it ran; measured from the series itself the same census.jsonl
    always produces the same file.

    A unix timestamp divided by 86400 IS the UTC day number (the epoch is UTC midnight),
    so no timezone handling is needed to group by UTC day.
    """
    points = list(points)
    if not points:
        return points
    cutoff = points[-1]["ts"] - DECIMATE_AFTER_DAYS * _DAY
    old = [p for p in points if p["ts"] < cutoff]
    recent = [p for p in points if p["ts"] >= cutoff]
    # Keep a point only when it is the last of its UTC day among the old points. The last
    # old point is always kept, so the series never gains a gap at the join.
    kept = [p for i, p in enumerate(old)
            if i + 1 == len(old) or old[i + 1]["ts"] // _DAY != p["ts"] // _DAY]
    return kept + recent


def build_policy_history(lines, policies):
    ids = ["pre"] + [p["id"] for p in sorted(policies, key=lambda x: x["activation"])]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        ts, reach = _as_int(rec.get("ts")), _as_int(rec.get("b2b_reachable"))
        versions = rec.get("versions")
        # Only keep a sample whose version counts sum exactly to b2b_reachable (the seed-census
        # invariant). A torn sample -- missing versions, or counts that do not add up -- is
        # skipped, never reconciled into "pre", so a crawl glitch can't fabricate a tier spike.
        if ts <= 0 or reach <= 0 or not isinstance(versions, dict) or not versions:
            continue
        buckets = {k: 0 for k in ids}
        total = 0
        for ua, count in versions.items():
            n = _as_int(count)
            if n <= 0:
                continue
            buckets[policy_bucket(str(ua), policies)] += n
            total += n
        if total != reach:
            continue
        out.append({"ts": ts, "b2b_reachable": reach, "buckets": buckets})
    out.sort(key=lambda p: p["ts"])
    return decimate(out)


def _run_self_test():
    POL = parse_policies(
        '[{"id":"m45","name":"45d","short":"45d","activation":973440,"end":979920,'
        '"enforcing_min_version":"29.4.2"},'
        '{"id":"m180","name":"180d","short":"180d","activation":979920,"end":1005840,'
        '"enforcing_min_version":"29.5.0"}]'
    )
    FIX = [
        '{"ts":1000,"b2b_reachable":1,"versions":{"/Satoshi:29.5.0/Knots:20270101/":1}}',
        '{"ts":2000,"b2b_reachable":2,"versions":{"/Satoshi:29.4.1/Knots:20260508/":1,'
        '"/OracleKnots:29.5.0/OracleKnots:20270101/":1}}',
        'garbage',                              # bad JSON -> skipped
        '{"ts":2500,"b2b_reachable":500}',      # torn: no versions -> skipped (no "pre" spike)
        '{"ts":2600,"b2b_reachable":9,"versions":{"/Satoshi:29.5.0/Knots:20270101/":2}}',  # sum!=reach -> skipped
        '{"ts":3000,"b2b_reachable":5,"versions":{"/Satoshi:29.4.2/Knots:20260508/":3,'
        '"/Satoshi:29.5.0/Knots:20270101/":2}}',
    ]
    pts = build_policy_history(FIX, POL)
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-28s got %r want %r" % ("ok  " if ok else "FAIL", label, got, want))

    check("point count", len(pts), 3)
    check("every bucket sums to reachable",
          all(sum(p["buckets"].values()) == p["b2b_reachable"] for p in pts), True)
    check("S1 top-tier only: m180", pts[0]["buckets"]["m180"], 1)
    check("S1 m45 zero", pts[0]["buckets"]["m45"], 0)
    check("S2 non-knots+old -> pre", pts[1]["buckets"]["pre"], 2)
    check("S3 split m45", pts[2]["buckets"]["m45"], 3)
    check("S3 split m180", pts[2]["buckets"]["m180"], 2)
    check("every policy id zero-filled",
          all(set(p["buckets"]) == {"pre", "m45", "m180"} for p in pts), True)
    # The fixture above spans seconds, so nothing in it is older than the cutoff: the
    # decimation must be invisible there, which is also the live situation today.
    check("short series is untouched", [p["ts"] for p in pts], [1000, 2000, 3000])

    # --- decimation ---------------------------------------------------------------
    # Hourly points over 120 days, ending on an exact UTC midnight boundary + 23h so the
    # last day is complete. Everything inside 90 days of the newest point survives; before
    # that only the last point of each UTC day does.
    NEWEST = 200 * _DAY + 23 * 3600          # a UTC day number with hours in it
    series = [{"ts": NEWEST - k * 3600, "b2b_reachable": 1, "buckets": {"pre": 1}}
              for k in range(120 * 24)]
    series.reverse()                          # oldest first, as build_policy_history emits
    dec = decimate(series)
    cutoff = NEWEST - DECIMATE_AFTER_DAYS * _DAY
    recent = [p for p in series if p["ts"] >= cutoff]
    old = [p for p in series if p["ts"] < cutoff]
    dec_recent = [p for p in dec if p["ts"] >= cutoff]
    dec_old = [p for p in dec if p["ts"] < cutoff]
    # Compared as counts and booleans, not as lists: these series are thousands of points
    # long and printing them would bury every other line of the self-test.
    check("decimate keeps every recent point untouched", dec_recent == recent, True)
    check("decimate thins the old region to one point per UTC day",
          len(dec_old), len({p["ts"] // _DAY for p in old}))
    check("decimate leaves no old UTC day twice",
          len({p["ts"] // _DAY for p in dec_old}), len(dec_old))
    check("decimate keeps the LAST point of each old day",
          all(p["ts"] == max(q["ts"] for q in old if q["ts"] // _DAY == p["ts"] // _DAY)
              for p in dec_old), True)
    check("decimate stays sorted oldest first",
          [p["ts"] for p in dec] == sorted(p["ts"] for p in dec), True)
    check("decimate drops nothing inside the window", len(recent), DECIMATE_AFTER_DAYS * 24 + 1)
    check("decimate keeps the newest point", dec[-1]["ts"], NEWEST)
    check("decimate keeps the oldest day", dec[0]["ts"] // _DAY, series[0]["ts"] // _DAY)
    check("decimate shrinks 120 hourly days", len(dec), len(dec_old) + len(dec_recent))
    check("decimate is a real saving", len(dec) < len(series), True)
    check("decimate is idempotent", decimate(dec) == dec, True)
    check("decimate of nothing", decimate([]), [])
    check("decimate of one point", decimate(series[:1]) == series[:1], True)
    # A series entirely inside the window is returned unchanged, points and objects alike.
    check("decimate of a 10-day series", decimate(recent[-240:]) == recent[-240:], True)

    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_run_self_test())
    if len(sys.argv) != 3:
        print("usage: gen_policy_history.py CENSUS_JSONL POLICIES_JSON | --self-test",
              file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[2], encoding="utf-8") as f:
        pol = parse_policies(f.read())
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(build_policy_history(f, pol), separators=(",", ":")))
