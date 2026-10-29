#!/usr/bin/env python3
"""Build the growing release-adoption table from census.jsonl.

One row per canonical release ever seen:
  {ua, first_ts, last_ts, peak_pct, peak_count, peak_ts, now_pct, now_count,
   nodes_peak, nodes_peak_ts, nodes_peak_pct, nodes_peak_basis,
   nodes_now, nodes_now_pct, nodes_now_basis}

peak_* / now_* are the ORIGINAL fields and keep their original meaning, because other sites
read this file: peak = highest share over rated-good (versions) samples with at least
PEAK_FLOOR nodes; now = the latest such sample. Do not change them.

nodes_peak is the highest NODE COUNT a release ever reached, not the highest share: a share peak
moves when the denominator moves, so a release could "peak" at 90% of 70 nodes and never be
shown the day it actually ran on 880. nodes_peak therefore has no floor. nodes_peak_pct is that
sample's share, and it is null when the sample is below PEAK_FLOOR, so a handful of early
nodes still cannot read as 100%.

For nodes_*, each sample is counted on ONE of two bases, in this order:
  "storage"  -- versions_storage (per-version archival/pruned/other) summed per release,
                over every fork node polled inside the crawler's freshness window;
  "versions" -- the older versions map, over the seeder's rated-good set only.
The basis is recorded per row (nodes_peak_basis / nodes_now_basis) because the two populations differ:
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


def _legacy_parse(lines):
    """(ts, reach, counts) over the versions map only -- the original basis of releases.json."""
    samples = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or _as_int(rec.get("ts")) <= 0:
            continue
        got = _version_counts(rec)
        if got:
            samples.append((_as_int(rec["ts"]), got[0], got[1]))
    samples.sort(key=lambda s: s[0])
    return samples


def build_releases(lines):
    """peak_* / now_* keep their original meaning for external readers of releases.json
    (xbtstats): highest SHARE over rated-good samples at or above PEAK_FLOOR, and the
    rated-good share now. The page's node-count view is published alongside as nodes_*."""
    lines = list(lines)
    samples = _parse_samples(lines)
    legacy = _legacy_parse(lines)
    if not samples and not legacy:
        return []

    acc = {}  # ua -> aggregate

    def row(ua, ts):
        return acc.setdefault(ua, {
            "ua": ua, "first_ts": ts, "last_ts": ts,
            "peak_pct": None, "peak_count": None, "peak_ts": None,
            "now_pct": 0.0, "now_count": 0,
            "nodes_peak": None, "nodes_peak_ts": None, "nodes_peak_pct": None,
            "nodes_peak_basis": None, "nodes_now": 0, "nodes_now_pct": 0.0,
            "nodes_now_basis": None,
        })

    # Original fields, unchanged semantics.
    if legacy:
        _, l_reach, l_counts = next((s for s in reversed(legacy) if s[1] >= PEAK_FLOOR),
                                    legacy[-1])
        for ts, reach, counts in legacy:
            for ua, count in counts.items():
                a = row(ua, ts)
                a["first_ts"] = min(a["first_ts"], ts)
                a["last_ts"] = max(a["last_ts"], ts)
                if reach >= PEAK_FLOOR:
                    pct = count / reach * 100.0
                    if a["peak_pct"] is None or pct > a["peak_pct"]:
                        a["peak_pct"], a["peak_count"], a["peak_ts"] = pct, count, ts

    # Node-count view. "Now" comes from the latest sample whose population clears the floor,
    # so a tiny or torn final sample cannot drive every release's current share.
    if samples:
        _, now_basis, now_denom, now_counts = next(
            (s for s in reversed(samples) if s[2] >= PEAK_FLOOR), samples[-1])
        for ts, basis, denom, counts in samples:
            for ua, count in counts.items():
                a = row(ua, ts)
                a["first_ts"] = min(a["first_ts"], ts)
                a["last_ts"] = max(a["last_ts"], ts)
                # Strictly greater, so a count reached twice is dated to the FIRST time it
                # was reached. No floor on the count; the share stays null below the floor.
                if a["nodes_peak"] is None or count > a["nodes_peak"]:
                    a["nodes_peak"], a["nodes_peak_ts"], a["nodes_peak_basis"] = count, ts, basis
                    a["nodes_peak_pct"] = count / denom * 100.0 if denom >= PEAK_FLOOR else None

    for ua, a in acc.items():
        if legacy:
            c = l_counts.get(ua, 0)
            a["now_count"] = c
            a["now_pct"] = c / l_reach * 100.0 if l_reach > 0 else 0.0
        if samples:
            c = now_counts.get(ua, 0)
            a["nodes_now"], a["nodes_now_basis"] = c, now_basis
            a["nodes_now_pct"] = c / now_denom * 100.0 if now_denom > 0 else 0.0

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

    # --- original fields: exact pre-nodes_* semantics (external readers) ----------
    check("A.peak_pct (S2 90/100)", rows[A]["peak_pct"], 90.0)
    check("A.peak_count", rows[A]["peak_count"], 90)
    # now ignores the sub-floor S4 (reach 5) and uses S3: canonical variant merged
    check("A.now_count (floor skips S4)", rows[A]["now_count"], 12)
    check("A.first_ts", rows[A]["first_ts"], 2000)
    check("A.last_ts", rows[A]["last_ts"], 4000)
    check("B.now_count", rows[B]["now_count"], 108)
    check("B.now_pct", round(rows[B]["now_pct"], 1), 90.0)
    check("B.peak_pct", rows[B]["peak_pct"], 90.0)
    # C seen only in a sub-floor sample -> legacy peak is null across the board, now is zero
    check("C.peak_pct null", rows[C]["peak_pct"], None)
    check("C.peak_count null", rows[C]["peak_count"], None)
    check("C.now_count", rows[C]["now_count"], 0)
    check("release count (A,B,C)", len(rows), 3)

    # --- nodes_*: peak is the node count, not the share ---------------------------
    # Extra sample where the network is 10x larger: A's share is lower (50%) but its count
    # is higher (500). Legacy keeps the 90% share peak; nodes_peak moves to 500.
    big = {r["ua"]: r for r in build_releases(
        FIX + ['{"ts":2500,"b2b_reachable":1000,"versions":{"%s":500,"%s":500}}' % (A, B)])}
    check("legacy peak stays max share", (big[A]["peak_pct"], big[A]["peak_count"]), (90.0, 90))
    check("nodes_peak is max count", (big[A]["nodes_peak"], big[A]["nodes_peak_ts"]), (500, 2500))
    check("nodes_peak_pct share at that sample", big[A]["nodes_peak_pct"], 50.0)
    check("C nodes_peak (no floor)", rows[C]["nodes_peak"], 5)
    check("C nodes_peak_pct null below floor", rows[C]["nodes_peak_pct"], None)
    check("nodes_peak >= nodes_now (all rows)",
          all(r["nodes_peak"] >= r["nodes_now"] for r in big.values()), True)
    check("one basis throughout", {r["nodes_now_basis"] for r in rows.values()}, {"versions"})

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
        # BROKEN invariant (storage_pruned says 60, the split says 30): dropped from nodes_*,
        # and NOT re-read on the versions basis there
        '{"ts":7000,"b2b_reachable":100,"versions":{"%s":100},'
        '"storage_archival":100,"storage_pruned":60,"storage_other":0,'
        '"versions_storage":{"%s":{"archival":100,"pruned":30,"other":0}}}' % (A, A),
    ]
    srows = {r["ua"]: r for r in build_releases(VS)}
    check("storage basis recorded", srows[A]["nodes_now_basis"], "storage")
    # S6: A = (20+20) + (40+20) = 100 nodes; denominator 100 + 60 = 160
    check("A nodes_now (merged)", srows[A]["nodes_now"], 100)
    check("A nodes_peak", srows[A]["nodes_peak"], 100)
    check("A nodes_peak_ts (S6)", srows[A]["nodes_peak_ts"], 6000)
    check("A nodes_now_pct", round(srows[A]["nodes_now_pct"], 1), 62.5)
    check("B nodes_now", srows[B]["nodes_now"], 60)
    # legacy fields ignore versions_storage entirely: S7's versions map is valid, so it is
    # the legacy "now" (A 100/100), exactly as before versions_storage existed
    check("legacy now ignores storage", (srows[A]["now_count"], srows[B]["now_count"]), (100, 0))
    check("nodes_peak >= nodes_now (storage)",
          all(r["nodes_peak"] >= r["nodes_now"] for r in srows.values()), True)
    # A series that spans the deploy: a row whose peak and now come from different
    # populations reports both, and the page captions that step rather than presenting it as
    # adoption. A ran on 90 rated-good nodes at ts 2000 and on 90 recently-polled nodes at ts
    # 5000: equal counts, so the peak stays on the FIRST sample that reached it.
    mixed = {r["ua"]: r for r in build_releases([FIX[3], VS[0]])}
    check("mixed series reports both bases",
          (mixed[A]["nodes_peak_basis"], mixed[A]["nodes_now_basis"]), ("versions", "storage"))
    check("mixed A nodes_peak", mixed[A]["nodes_peak"], 90)
    check("mixed B switches basis",
          (mixed[B]["nodes_peak_basis"], mixed[B]["nodes_peak"]), ("storage", 50))

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
