#!/usr/bin/env python3
"""Build the growing release-adoption table from census.jsonl.

One row per canonical release ever seen:
  {ua, first_ts, last_ts, peak_pct, peak_count, peak_ts, peak_basis, now_pct, now_count,
   now_basis}

Peak is the highest NODE COUNT a release ever reached, not the highest share: a share peak
moves when the denominator moves, so a release could "peak" at 90% of 70 nodes and never be
shown the day it actually ran on 880. peak_count therefore has no floor. peak_pct is that
sample's share, and it is null when the sample is below PEAK_FLOOR, so a handful of early
nodes still cannot read as 100%.

Each sample is counted on ONE of two bases, in this order:
  "storage"  -- versions_storage (per-version archival/pruned/other) summed per release,
                over every fork node polled inside the crawler's freshness window;
  "versions" -- the older versions map, over the seeder's rated-good set only.
The basis is recorded per row (peak_basis / now_basis) because the two populations differ:
the rated-good set excludes pruned nodes, so a release's count STEPS UP on the sample where
the seed first publishes versions_storage. That is a measurement change, not adoption, and
a row whose peak and now come from different bases says so rather than hiding it.

Run: `python3 gen_releases.py CENSUS_JSONL` writes the JSON array to stdout.
Test: `python3 gen_releases.py --self-test`.
"""
import json
import sys

try:
    from policy_model import canonicalize, merge_versions_storage, storage_total
except ImportError:  # running from another directory
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    from policy_model import canonicalize, merge_versions_storage, storage_total

PEAK_FLOOR = 50


def _as_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _storage_counts(rec):
    """(denom, {canonical_ua: count}) from versions_storage, or None if this sample does not
    carry a usable one. The per-release count is archival + pruned + other.

    When the sample also publishes the storage_* aggregates, the two must agree: that sum
    invariant is what makes the page's totals row equal its headline figure, so a sample
    that breaks it is dropped rather than published as a release count nobody can reconcile.
    """
    raw = rec.get("versions_storage")
    if not isinstance(raw, dict) or not raw:
        return None
    merged = merge_versions_storage(raw)
    counts = {ua: storage_total(b) for ua, b in merged.items()}
    counts = {ua: n for ua, n in counts.items() if n > 0}
    denom = sum(counts.values())
    if not counts or denom <= 0:
        return None
    for field, key in (("storage_archival", "archival"), ("storage_pruned", "pruned"),
                       ("storage_other", "other")):
        if field in rec and sum(b[key] for b in merged.values()) != _as_int(rec.get(field)):
            return None
    return denom, counts


def _version_counts(rec):
    """(denom, {canonical_ua: count}) from the older versions map, or None. A sample is
    rejected unless the counts sum exactly to b2b_reachable (the seed-census invariant), so
    a torn line never fabricates a release or a spike."""
    versions = rec.get("versions")
    reach = _as_int(rec.get("b2b_reachable"))
    if reach <= 0 or not isinstance(versions, dict) or not versions:
        return None
    merged, total = {}, 0
    for ua, count in versions.items():
        n = _as_int(count)
        if n <= 0:
            continue
        key = canonicalize(str(ua))
        merged[key] = merged.get(key, 0) + n
        total += n
    if total != reach or not merged:
        return None
    return reach, merged


def _parse_samples(lines):
    """Each VALID sample -> (ts, basis, denom, {canonical_ua: positive_count}).

    A sample that carries versions_storage is read on that basis ONLY: if its storage split
    does not validate the sample is skipped rather than quietly re-read on the narrower
    versions basis, which would put a step in every series for one bad line."""
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
        ts = _as_int(rec.get("ts"))
        if ts <= 0:
            continue
        if isinstance(rec.get("versions_storage"), dict) and rec["versions_storage"]:
            got, basis = _storage_counts(rec), "storage"
        else:
            got, basis = _version_counts(rec), "versions"
        if not got:
            continue
        denom, counts = got
        samples.append((ts, basis, denom, counts))
    samples.sort(key=lambda s: s[0])
    return samples


def build_releases(lines):
    samples = _parse_samples(lines)
    if not samples:
        return []
    # "Now" comes from the latest sample whose population clears the floor, so a tiny or
    # torn final sample cannot drive every release's current share to a misleading value.
    now_ts, now_basis, now_denom, now_counts = next(
        (s for s in reversed(samples) if s[2] >= PEAK_FLOOR), samples[-1])

    acc = {}  # ua -> aggregate
    for ts, basis, denom, counts in samples:
        for ua, count in counts.items():
            a = acc.setdefault(ua, {
                "ua": ua, "first_ts": ts, "last_ts": ts,
                "peak_pct": None, "peak_count": None, "peak_ts": None, "peak_basis": None,
                "now_pct": 0.0, "now_count": 0, "now_basis": now_basis,
            })
            a["first_ts"] = min(a["first_ts"], ts)
            a["last_ts"] = max(a["last_ts"], ts)
            # Strictly greater, so a count reached twice is dated to the FIRST time it was
            # reached. peak_count has no floor; peak_pct is the share at that same sample
            # and stays null below the floor, where a share is not meaningful.
            if a["peak_count"] is None or count > a["peak_count"]:
                a["peak_count"], a["peak_ts"], a["peak_basis"] = count, ts, basis
                a["peak_pct"] = count / denom * 100.0 if denom >= PEAK_FLOOR else None

    for ua, a in acc.items():
        c = now_counts.get(ua, 0)
        a["now_count"] = c
        a["now_pct"] = c / now_denom * 100.0 if now_denom > 0 else 0.0

    rows = list(acc.values())
    rows.sort(key=lambda r: (r["now_count"], r["peak_count"] or 0), reverse=True)
    return rows


def _run_self_test():
    A = "/Satoshi:29.4.2/Knots:20260508/"
    B = "/Satoshi:29.5.0/Knots:20270101/"
    C = "/Satoshi:29.4.1/Knots:20260508/"
    FIX = [
        '{"ts":1000,"b2b_reachable":5,"versions":{"%s":5}}' % C,          # C: sub-floor only
        'garbage not json',                                               # skipped
        '{"ts":1500,"b2b_reachable":500}',                                # torn: no versions
        '{"ts":2000,"b2b_reachable":100,"versions":{"%s":90,"%s":10}}' % (A, B),
        '{"ts":3000,"b2b_reachable":120,"versions":{"/Satoshi:29.4.2(mempool)/Knots:20260508/":12,"%s":108}}' % B,
        '{"ts":4000,"b2b_reachable":5,"versions":{"%s":5}}' % A,          # sub-floor LATEST
    ]
    rows = {r["ua"]: r for r in build_releases(FIX)}
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-34s got %r want %r" % ("ok  " if ok else "FAIL", label, got, want))

    # Peak is the node count, so A peaks at S2 (90 nodes) rather than at the sample with
    # the highest share, and peak_pct is the share AT that sample.
    check("A.peak_count (S2, 90 nodes)", rows[A]["peak_count"], 90)
    check("A.peak_ts", rows[A]["peak_ts"], 2000)
    check("A.peak_pct (90/100)", rows[A]["peak_pct"], 90.0)
    check("A.peak_basis", rows[A]["peak_basis"], "versions")
    # now ignores the sub-floor S4 (denominator 5) and uses S3: canonical variant merged
    check("A.now_count (floor skips S4)", rows[A]["now_count"], 12)
    check("A.first_ts", rows[A]["first_ts"], 2000)
    check("A.last_ts", rows[A]["last_ts"], 4000)
    check("B.now_count", rows[B]["now_count"], 108)
    check("B.now_pct", round(rows[B]["now_pct"], 1), 90.0)
    check("B.peak_count", rows[B]["peak_count"], 108)
    check("B.peak_pct", round(rows[B]["peak_pct"], 1), 90.0)
    # C seen only in a sub-floor sample: the count is real and is kept, the share is not
    check("C.peak_count (no floor)", rows[C]["peak_count"], 5)
    check("C.peak_pct null below floor", rows[C]["peak_pct"], None)
    check("C.now_count", rows[C]["now_count"], 0)
    check("release count (A,B,C)", len(rows), 3)
    # INVARIANT, same basis throughout: no release can be running on more nodes now than
    # the most it ever ran on.
    check("peak_count >= now_count (all rows)",
          all(r["peak_count"] >= r["now_count"] for r in rows.values()), True)
    check("one basis throughout", {r["now_basis"] for r in rows.values()}, {"versions"})

    # --- versions_storage basis -------------------------------------------------
    # Same releases, now with the per-version split published. The storage population
    # includes pruned nodes, so the counts are LARGER than the rated-good versions map
    # would give for the same moment -- the deploy-time step the basis fields exist to show.
    VS = [
        '{"ts":5000,"b2b_reachable":100,"versions":{"%s":60,"%s":40},'
        '"storage_archival":100,"storage_pruned":40,"storage_other":0,'
        '"versions_storage":{"%s":{"archival":60,"pruned":30,"other":0},'
        '"%s":{"archival":40,"pruned":10,"other":0}}}' % (A, B, A, B),
        # canonical variant merges into A; invariant holds (90+50 == 140 archival/pruned)
        '{"ts":6000,"b2b_reachable":100,"versions":{"%s":60,"%s":40},'
        '"storage_archival":100,"storage_pruned":60,"storage_other":0,'
        '"versions_storage":{"/Satoshi:29.4.2(vanity)/Knots:20260508/":{"archival":20,"pruned":20,"other":0},'
        '"%s":{"archival":40,"pruned":20,"other":0},'
        '"%s":{"archival":40,"pruned":20,"other":0}}}' % (A, B, A, B),
        # BROKEN invariant (storage_pruned says 60, the split says 30): dropped entirely,
        # and NOT re-read on the versions basis
        '{"ts":7000,"b2b_reachable":100,"versions":{"%s":100},'
        '"storage_archival":100,"storage_pruned":60,"storage_other":0,'
        '"versions_storage":{"%s":{"archival":100,"pruned":30,"other":0}}}' % (A, A),
    ]
    srows = {r["ua"]: r for r in build_releases(VS)}
    check("storage basis recorded", srows[A]["now_basis"], "storage")
    # S6: A = (20+20) + (40+20) = 100 nodes; denominator 100 + 60 = 160
    check("A storage now_count (merged)", srows[A]["now_count"], 100)
    check("A storage peak_count", srows[A]["peak_count"], 100)
    check("A storage peak_ts (S6)", srows[A]["peak_ts"], 6000)
    check("A storage now_pct", round(srows[A]["now_pct"], 1), 62.5)
    check("B storage now_count", srows[B]["now_count"], 60)
    check("broken-invariant sample dropped", srows[A]["last_ts"], 6000)
    check("peak_count >= now_count (storage)",
          all(r["peak_count"] >= r["now_count"] for r in srows.values()), True)
    # A series that spans the deploy: samples without versions_storage are still read on
    # the versions basis, so the pre-deploy history is unaffected, and a row whose peak and
    # now come from different populations reports both -- the page captions that step rather
    # than presenting it as adoption. Here A ran on 90 rated-good nodes at ts 2000 and on 90
    # recently-polled nodes at ts 5000: equal counts, different populations, so the peak
    # stays on the FIRST sample that reached it.
    mixed = {r["ua"]: r for r in build_releases([FIX[3], VS[0]])}
    check("mixed series reports both bases",
          (mixed[A]["peak_basis"], mixed[A]["now_basis"]), ("versions", "storage"))
    check("mixed A peak_count", mixed[A]["peak_count"], 90)
    check("mixed B switches basis",
          (mixed[B]["peak_basis"], mixed[B]["peak_count"]), ("storage", 50))

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
