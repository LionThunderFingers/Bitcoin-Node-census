#!/usr/bin/env python3
"""Build the growing release-adoption table from census.jsonl.

One row per canonical release ever seen:
  {ua, first_ts, last_ts, peak_pct, peak_count, peak_ts, now_pct, now_count}
Peak is taken only over samples with b2b_reachable >= PEAK_FLOOR, so a tiny early sample
cannot read 100% off a handful of nodes; now_* come from the latest parsable sample.

Run: `python3 gen_releases.py CENSUS_JSONL` writes the JSON array to stdout.
Test: `python3 gen_releases.py --self-test`.
"""
import json
import sys

try:
    from policy_model import canonicalize
except ImportError:  # running from another directory
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    from policy_model import canonicalize

PEAK_FLOOR = 50


def _as_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_samples(lines):
    """Each VALID sample -> (ts, reach, {canonical_ua: positive_count}). A sample is dropped
    unless it is a dict with ts>0, b2b_reachable>0, a non-empty versions map, and version
    counts that sum exactly to b2b_reachable (the seed-census invariant). A torn line -- bad
    JSON, missing versions, a count that does not add up -- is skipped rather than reconciled,
    so a crawl glitch never fabricates a release or a spike."""
    samples = []
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
        if ts <= 0 or reach <= 0 or not isinstance(versions, dict) or not versions:
            continue
        merged, total = {}, 0
        for ua, count in versions.items():
            n = _as_int(count)
            if n <= 0:
                continue
            merged[canonicalize(str(ua))] = merged.get(canonicalize(str(ua)), 0) + n
            total += n
        if total != reach or not merged:
            continue
        samples.append((ts, reach, merged))
    samples.sort(key=lambda s: s[0])
    return samples


def build_releases(lines):
    samples = _parse_samples(lines)
    if not samples:
        return []
    # "Now" comes from the latest sample whose reachable count clears the floor, so a tiny or
    # torn final sample cannot drive every release's current share to a misleading value.
    now_sample = next((s for s in reversed(samples) if s[1] >= PEAK_FLOOR), samples[-1])
    _, now_reach, now_counts = now_sample

    acc = {}  # ua -> aggregate
    for ts, reach, counts in samples:
        for ua, count in counts.items():
            a = acc.setdefault(ua, {
                "ua": ua, "first_ts": ts, "last_ts": ts,
                "peak_pct": None, "peak_count": None, "peak_ts": None,
                "now_pct": 0.0, "now_count": 0,
            })
            a["first_ts"] = min(a["first_ts"], ts)
            a["last_ts"] = max(a["last_ts"], ts)
            if reach >= PEAK_FLOOR:
                pct = count / reach * 100.0
                if a["peak_pct"] is None or pct > a["peak_pct"]:
                    a["peak_pct"], a["peak_count"], a["peak_ts"] = pct, count, ts

    for ua, a in acc.items():
        c = now_counts.get(ua, 0)
        a["now_count"] = c
        a["now_pct"] = c / now_reach * 100.0 if now_reach > 0 else 0.0

    rows = list(acc.values())
    rows.sort(key=lambda r: (r["now_pct"], r["peak_pct"] or 0.0), reverse=True)
    return rows


def _run_self_test():
    A = "/Satoshi:29.4.2/Knots:20260508/"
    B = "/Satoshi:29.5.0/Knots:20270101/"
    C = "/Satoshi:29.4.1/Knots:20260508/"
    FIX = [
        '{"ts":1000,"b2b_reachable":5,"versions":{"%s":5}}' % C,          # C: sub-floor only
        'garbage not json',                                               # skipped
        '{"ts":1500,"b2b_reachable":500}',                                # torn: no versions -> skipped
        '{"ts":2000,"b2b_reachable":100,"versions":{"%s":90,"%s":10}}' % (A, B),
        '{"ts":3000,"b2b_reachable":120,"versions":{"/Satoshi:29.4.2(mempool)/Knots:20260508/":12,"%s":108}}' % B,
        '{"ts":4000,"b2b_reachable":5,"versions":{"%s":5}}' % A,          # sub-floor LATEST sample
    ]
    rows = {r["ua"]: r for r in build_releases(FIX)}
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-26s got %r want %r" % ("ok  " if ok else "FAIL", label, got, want))

    check("A.peak_pct (S2 90/100)", rows[A]["peak_pct"], 90.0)
    check("A.peak_count", rows[A]["peak_count"], 90)
    # now ignores the sub-floor S4 (reach 5) and uses S3 (reach 120): canonical variant merged
    check("A.now_count (floor skips S4)", rows[A]["now_count"], 12)
    check("A.first_ts", rows[A]["first_ts"], 2000)
    check("A.last_ts", rows[A]["last_ts"], 4000)
    check("B.now_count", rows[B]["now_count"], 108)
    check("B.now_pct", round(rows[B]["now_pct"], 1), 90.0)
    check("B.peak_pct", rows[B]["peak_pct"], 90.0)
    # C seen only in a sub-floor sample -> peak is null across the board, now is zero
    check("C.peak_pct null", rows[C]["peak_pct"], None)
    check("C.peak_count null", rows[C]["peak_count"], None)
    check("C.now_count", rows[C]["now_count"], 0)
    # torn/garbage lines became neither a release nor a spike
    check("release count (A,B,C)", len(rows), 3)
    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_run_self_test())
    if len(sys.argv) != 2:
        print("usage: gen_releases.py CENSUS_JSONL | --self-test", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(build_releases(f), separators=(",", ":")))
