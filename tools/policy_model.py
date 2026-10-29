#!/usr/bin/env python3
"""Policy model for the census: parse policies.json, bucket a node's user agent by the
newest policy its software enforces, and pick the current policy for a given height.

Standalone and dependency-free so it can be unit-tested with `python3 policy_model.py
--self-test` and imported by the seed-publish generators. The Knots-UA classifier helpers
are copied verbatim from seed-publish's COMMON_PY; at integration they come from the shared
block, not a second copy.
"""
import json
import re
import sys

# --- copied verbatim from seed-publish COMMON_PY ---------------------------------
_OFFICIAL_KNOTS_UA = re.compile(
    r"/Satoshi:(?P<ver>[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
    r"(?:\([A-Za-z0-9 .,;_?@-]*\))?"
    r"/Knots:[0-9]{8}(?:rc[0-9]+)?/"
)


def _version_tuple(text):
    parts = [int(p) for p in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def parse_min_version(text):
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", text or ""):
        raise SystemExit("enforcing_min_version must look like 29.4.2, got %r" % (text,))
    return _version_tuple(text)


def classify_enforcing(ua, min_version):
    if not isinstance(ua, str):
        return False
    m = _OFFICIAL_KNOTS_UA.match(ua)
    return bool(m) and _version_tuple(m.group("ver")) >= min_version


def canonicalize(ua):
    """Drop the -uacomment from the LEADING /Satoshi:X(comment)/ segment and keep every
    following segment exactly as reported, so two nodes on the same build differ only by
    their operator's vanity string collapse onto one key.

    The comment is stripped regardless of what follows. It used to be stripped only when a
    /Knots:.../ segment was the LAST thing in the string (the pattern was anchored with $),
    so /Satoshi:29.4.3(Cypherpunk Is Alive)/Knots:20260508/Plumb:8/ stayed separate from
    /Satoshi:29.4.3/Knots:20260508/Plumb:8/ -- one release split across several rows, and the
    page's "releases no longer seen" list filled up with vanity strings.

    Only the leading segment's comment goes. A comment in a LATER segment (a wrapper's own
    parenthesis, e.g. /BLAKE2B(Firing the miners)/) is part of how that wrapper identifies
    itself, not an operator's -uacomment, and is left alone.

    A string that does not start /Satoshi:<version>(...)/ is returned unchanged, so a
    renamed fork, a string with no comment, and anything malformed all stay as reported.
    """
    m = re.match(r"^(/Satoshi:[0-9]+(?:\.[0-9]+)*)\([^)]*\)(/.*)$", ua)
    return m.group(1) + m.group(2) if m else ua


# --- policy model ---------------------------------------------------------------
def parse_policies(text):
    """Parse policies.json text into a validated list of epoch dicts, in the strictly
    increasing activation order they must be given in. Each dict is
    {id, name, short, activation:int, end:int, min_version:tuple}. Raises SystemExit on any
    malformation, so a bad file makes the caller fail closed rather than publish nonsense.
    """
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise SystemExit("policies.json is not valid JSON: %s" % e)
    if not isinstance(raw, list) or not raw:
        raise SystemExit("policies.json must be a non-empty list of policy epochs")
    out = []
    seen_ids = set()
    prev_activation = prev_end = None
    prev_min = None
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit("policy %d is not an object" % i)
        for key in ("id", "name", "short", "activation", "end", "enforcing_min_version"):
            if key not in item:
                raise SystemExit("policy %d is missing %r" % (i, key))
        pid = item["id"]
        if not isinstance(pid, str) or not pid:
            raise SystemExit("policy %d has a non-string or empty id" % i)
        if pid == "pre":
            raise SystemExit("policy id 'pre' is reserved for the catch-all bucket")
        if pid in seen_ids:
            raise SystemExit("duplicate policy id %r" % pid)
        seen_ids.add(pid)
        # bool is an int subclass, so reject it explicitly; floats/strings are rejected too, so
        # a value like 29.40 (a JSON number, not a string) cannot silently become "29.4".
        for k in ("activation", "end"):
            if not isinstance(item[k], int) or isinstance(item[k], bool):
                raise SystemExit("policy %r has a non-integer %s" % (pid, k))
        activation, end = item["activation"], item["end"]
        if not isinstance(item["enforcing_min_version"], str):
            raise SystemExit("policy %r enforcing_min_version must be a string" % pid)
        min_version = parse_min_version(item["enforcing_min_version"])
        if end <= activation:
            raise SystemExit("policy %r has end <= activation" % pid)
        if prev_activation is not None and activation <= prev_activation:
            raise SystemExit("policies must be in strictly increasing activation order "
                             "(policy %r breaks it)" % pid)
        if prev_end is not None and activation < prev_end:
            raise SystemExit("policy %r overlaps the previous epoch (its activation is before "
                             "the previous end)" % pid)
        if prev_min is not None and min_version <= prev_min:
            raise SystemExit("policy %r enforcing_min_version must be strictly greater than the "
                             "previous epoch's (newer software, newer rule)" % pid)
        prev_activation, prev_end, prev_min = activation, end, min_version
        out.append({
            "id": pid, "name": str(item["name"]), "short": str(item["short"]),
            "activation": activation, "end": end, "min_version": min_version,
        })
    return out


def policy_bucket(ua, policies):
    """The id of the highest-activation policy whose enforcing_min_version this node's
    software satisfies, else 'pre'. Exclusive: a node is counted in exactly one bucket.
    """
    for p in sorted(policies, key=lambda x: x["activation"], reverse=True):
        if classify_enforcing(ua, p["min_version"]):
            return p["id"]
    return "pre"


def current_policy(policies, max_height):
    """The epoch whose [activation, end) contains max_height; else the latest already
    activated (max_height >= activation); else None (before the first activation).
    """
    ordered = sorted(policies, key=lambda x: x["activation"])
    for p in ordered:
        if p["activation"] <= max_height < p["end"]:
            return p
    activated = [p for p in ordered if p["activation"] <= max_height]
    return activated[-1] if activated else None


def next_policy(policies, max_height):
    """The first epoch that has NOT activated yet at max_height, i.e. the lowest activation
    strictly greater than it; None once the last epoch has activated.

    Strictly greater, not >=: at exactly the activation height the rule is already in force
    (current_policy returns it), so a countdown to it would read "0 blocks remaining" for a
    rule that is live. The page needs the two to be mutually exclusive.
    """
    for p in sorted(policies, key=lambda x: x["activation"]):
        if p["activation"] > max_height:
            return p
    return None


# --- per-version storage split --------------------------------------------------
_STORAGE_KEYS = ("archival", "pruned", "other")


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def merge_versions_storage(raw):
    """versions_storage from a census line, with its keys canonicalized the same way
    d["versions"] is, counts summed on collision.

    Returns {} for anything that is not a dict, and skips entries that are not dicts, so a
    torn line degrades to "this sample has no split" rather than to fabricated zeros. Only
    the three known sub-keys are kept: an unknown one would silently leave the archival +
    pruned + other == total invariant the page relies on.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for ua, buckets in raw.items():
        if not isinstance(buckets, dict):
            continue
        key = canonicalize(str(ua))
        slot = out.setdefault(key, {k: 0 for k in _STORAGE_KEYS})
        for k in _STORAGE_KEYS:
            slot[k] += _as_int(buckets.get(k))
    return out


def storage_total(buckets):
    """archival + pruned + other of one versions_storage entry."""
    if not isinstance(buckets, dict):
        return 0
    return sum(_as_int(buckets.get(k)) for k in _STORAGE_KEYS)


# --- what data.json publishes about the policies ---------------------------------
def policy_summary(policy):
    """The naming half of a policy epoch, for the page's section titles. Deliberately
    without min_version: the page must never re-derive enforcement from a version number
    in JS, only read the generated enforcing list.
    """
    if not policy:
        return None
    return {"id": policy["id"], "name": policy["name"], "short": policy["short"]}


def enforcing_versions(uas, min_version):
    """Every user agent in uas that classifies as enforcing at min_version, sorted for a
    stable diff between runs."""
    return sorted(ua for ua in uas if classify_enforcing(ua, min_version))


def next_policy_block(policies, max_height, uas):
    """data.json's next_policy: the epoch that has not activated yet at max_height, with
    the readiness list scored against ITS minimum version over the same user agents the
    sample publishes. None once the last epoch has activated.

    enforcing_versions is generated here rather than left to the page because the page
    must not parse version numbers out of user-agent strings -- that is the one piece of
    consensus-sensitive classification, and it has exactly one home.
    """
    p = next_policy(policies, max_height)
    if not p:
        return None
    out = policy_summary(p)
    out.update({
        "activation": p["activation"],
        "end": p["end"],
        "enforcing_versions": enforcing_versions(uas, p["min_version"]),
    })
    return out


# --- self-test ------------------------------------------------------------------
def _run_self_test():
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-6s want %-6s  %s" % ("ok  " if ok else "FAIL", got, want, label))

    def expect_raises(label, fn):
        nonlocal failed
        try:
            fn()
            failed += 1
            print("FAIL  (no raise)  %s" % label)
        except SystemExit:
            print("ok    (raised)    %s" % label)

    POL = parse_policies(
        '[{"id":"m45","name":"45d","short":"45d","activation":973440,"end":979920,'
        '"enforcing_min_version":"29.4.2"},'
        '{"id":"m180","name":"180d","short":"180d","activation":979920,"end":1005840,'
        '"enforcing_min_version":"29.5.0"}]'
    )
    # bucketing (highest tier satisfied, exclusive)
    check("29.4.2 -> m45", policy_bucket("/Satoshi:29.4.2/Knots:20260508/", POL), "m45")
    check("29.4.3 -> m45", policy_bucket("/Satoshi:29.4.3/Knots:20260508/", POL), "m45")
    check("29.5.0 -> m180", policy_bucket("/Satoshi:29.5.0/Knots:20270101/", POL), "m180")
    check("30.0 -> m180", policy_bucket("/Satoshi:30.0/Knots:20270101/", POL), "m180")
    check("29.4.1 -> pre", policy_bucket("/Satoshi:29.4.1/Knots:20260508/", POL), "pre")
    check("OracleKnots -> pre",
          policy_bucket("/OracleKnots:29.5.0/OracleKnots:20270101/", POL), "pre")
    check("comment+trailer -> m45",
          policy_bucket("/Satoshi:29.4.2(x)/Knots:20260508/Plumb:6/", POL), "m45")
    # canonicalize: the leading -uacomment goes whatever follows it, every following
    # segment survives, and anything else is returned untouched.
    check("canon: comment, Knots last",
          canonicalize("/Satoshi:29.4.2(vanity)/Knots:20260508/"),
          "/Satoshi:29.4.2/Knots:20260508/")
    # THE regression: a trailing segment used to block the strip, splitting one release
    # across a row per vanity string.
    check("canon: comment, trailing segment",
          canonicalize("/Satoshi:29.4.3(Cypherpunk Is Alive)/Knots:20260508/Plumb:8/"),
          "/Satoshi:29.4.3/Knots:20260508/Plumb:8/")
    check("canon: two vanity strings agree",
          canonicalize("/Satoshi:29.4.3(Cypherpunk Is Alive)/Knots:20260508/Plumb:8/") ==
          canonicalize("/Satoshi:29.4.3(PyBLOCK-BIP110)/Knots:20260508/Plumb:8/") ==
          "/Satoshi:29.4.3/Knots:20260508/Plumb:8/", True)
    check("canon: several trailing segments",
          canonicalize("/Satoshi:29.4.2(x)/Knots:20260508/PATUM:1.0.1(https://b.xyz:8443)/Crew/"),
          "/Satoshi:29.4.2/Knots:20260508/PATUM:1.0.1(https://b.xyz:8443)/Crew/")
    # A parenthesis in a LATER segment belongs to that wrapper, not to -uacomment: kept.
    check("canon: later segment keeps its own parens",
          canonicalize("/Satoshi:29.4.2(BitcoinIsMoney)/Knots:20260508/BLAKE2B(Firing the miners)/"),
          "/Satoshi:29.4.2/Knots:20260508/BLAKE2B(Firing the miners)/")
    check("canon: empty comment",
          canonicalize("/Satoshi:29.4.2()/Knots:20260508/"), "/Satoshi:29.4.2/Knots:20260508/")
    check("canon: no Knots segment at all",
          canonicalize("/Satoshi:29.4.2(x)/"), "/Satoshi:29.4.2/")
    check("canon: no comment is untouched",
          canonicalize("/Satoshi:29.4.2/Knots:20260508/Plumb:8/"),
          "/Satoshi:29.4.2/Knots:20260508/Plumb:8/")
    check("canon: renamed fork untouched",
          canonicalize("/OracleKnots:29.4.2(Forever Money)/OracleKnots:20260508/"),
          "/OracleKnots:29.4.2(Forever Money)/OracleKnots:20260508/")
    check("canon: not anchored at the start -> untouched",
          canonicalize("/Fake:1.0/Satoshi:29.4.2(x)/Knots:20260508/"),
          "/Fake:1.0/Satoshi:29.4.2(x)/Knots:20260508/")
    check("canon: comment not followed by a segment -> untouched",
          canonicalize("/Satoshi:29.4.2(x)"), "/Satoshi:29.4.2(x)")
    check("canon: empty string", canonicalize(""), "")
    check("canon: garbage", canonicalize("garbage"), "garbage")
    # Canonicalizing an already-canonical key is a no-op, so republishing a file that was
    # written by an older build of this module converges instead of drifting.
    check("canon: idempotent",
          canonicalize(canonicalize("/Satoshi:29.4.3(v)/Knots:20260508/Plumb:8/")),
          "/Satoshi:29.4.3/Knots:20260508/Plumb:8/")
    # Stripping the comment must never change the enforcement verdict: the classifier
    # already ignores a well-formed comment, so both forms score the same.
    for _ua in ("/Satoshi:29.4.3(Cypherpunk Is Alive)/Knots:20260508/Plumb:8/",
                "/Satoshi:29.4.1(MiniBolt node)/Knots:20260508/",
                "/OracleKnots:29.4.2(Forever Money)/OracleKnots:20260508/"):
        check("canon keeps the verdict for %s" % _ua,
              classify_enforcing(canonicalize(_ua), _version_tuple("29.4.2")),
              classify_enforcing(_ua, _version_tuple("29.4.2")))
    # current_policy by height
    check("h=973440 -> m45", current_policy(POL, 973440)["id"], "m45")
    check("h=979919 -> m45", current_policy(POL, 979919)["id"], "m45")
    check("h=979920 -> m180", current_policy(POL, 979920)["id"], "m180")
    check("h=2000000 -> m180", current_policy(POL, 2000000)["id"], "m180")
    check("h=900000 -> None", current_policy(POL, 900000), None)
    # next_policy: the countdown target. Mutually exclusive with current_policy at every
    # height, and absent once the last epoch has activated.
    check("next h=900000 -> m45", next_policy(POL, 900000)["id"], "m45")
    check("next h=973439 -> m45", next_policy(POL, 973439)["id"], "m45")
    check("next h=973440 -> m180", next_policy(POL, 973440)["id"], "m180")
    check("next h=979919 -> m180", next_policy(POL, 979919)["id"], "m180")
    check("next h=979920 -> None", next_policy(POL, 979920), None)
    check("next h=2000000 -> None", next_policy(POL, 2000000), None)
    check("next != current at h=973440",
          next_policy(POL, 973440)["id"] != current_policy(POL, 973440)["id"], True)
    # versions_storage: canonical merge, and the per-entry total the page sums
    VS = merge_versions_storage({
        "/Satoshi:29.4.2/Knots:20260508/": {"archival": 5, "pruned": 2, "other": 0},
        "/Satoshi:29.4.2(vanity)/Knots:20260508/": {"archival": 1, "pruned": 3},
        "/OracleKnots:29.4.3/OracleKnots:20260508/": {"other": 4},
        "/Satoshi:29.4.1/Knots:20260508/": "not a dict",
    })
    check("vs canonical merge", VS["/Satoshi:29.4.2/Knots:20260508/"],
          {"archival": 6, "pruned": 5, "other": 0})
    check("vs missing sub-key -> 0", VS["/OracleKnots:29.4.3/OracleKnots:20260508/"],
          {"archival": 0, "pruned": 0, "other": 4})
    check("vs non-dict entry dropped", "/Satoshi:29.4.1/Knots:20260508/" in VS, False)
    check("vs merge preserves the sum", sum(storage_total(v) for v in VS.values()), 15)
    check("vs row count after merge", len(VS), 2)
    check("vs not a dict -> {}", merge_versions_storage(None), {})
    # The same merge through versions_storage, for the trailing-segment case that used to
    # keep one release on a row per vanity string.
    VS2 = merge_versions_storage({
        "/Satoshi:29.4.3(Cypherpunk Is Alive)/Knots:20260508/Plumb:8/": {"archival": 2},
        "/Satoshi:29.4.3(PyBLOCK-BIP110)/Knots:20260508/Plumb:8/": {"pruned": 1},
        "/Satoshi:29.4.3/Knots:20260508/Plumb:8/": {"archival": 3, "other": 1},
    })
    check("vs trailing-segment merge row count", len(VS2), 1)
    check("vs trailing-segment merge counts", VS2["/Satoshi:29.4.3/Knots:20260508/Plumb:8/"],
          {"archival": 5, "pruned": 1, "other": 1})
    check("storage_total of junk", storage_total("x"), 0)
    # next_policy_block: what data.json publishes for the countdown. The user-agent set is
    # the union of versions and versions_storage keys, as seed-publish assembles it.
    UAS = ["/Satoshi:29.4.1/Knots:20260508/", "/Satoshi:29.4.2/Knots:20260508/",
           "/Satoshi:29.5.0/Knots:20270101/", "/OracleKnots:29.5.0/OracleKnots:20270101/"]
    NB = next_policy_block(POL, 973440, UAS)
    check("next block id", NB["id"], "m180")
    check("next block activation", NB["activation"], 979920)
    check("next block end", NB["end"], 1005840)
    # scored at m180's own minimum (29.5.0), so 29.4.2 is NOT ready for it, and the
    # renamed fork never is
    check("next block enforcing", NB["enforcing_versions"], ["/Satoshi:29.5.0/Knots:20270101/"])
    check("next block has no min_version", "min_version" in NB, False)
    check("next block before any activation", next_policy_block(POL, 900000, UAS)["id"], "m45")
    check("next block after the last", next_policy_block(POL, 979920, UAS), None)
    check("current summary", policy_summary(current_policy(POL, 979919)),
          {"id": "m45", "name": "45d", "short": "45d"})
    check("summary of None", policy_summary(None), None)
    check("enforcing list sorted + filtered", enforcing_versions(UAS, _version_tuple("29.4.2")),
          ["/Satoshi:29.4.2/Knots:20260508/", "/Satoshi:29.5.0/Knots:20270101/"])
    # validation failures
    expect_raises("empty list", lambda: parse_policies("[]"))
    expect_raises("not a list", lambda: parse_policies('{"id":"x"}'))
    expect_raises("out of order", lambda: parse_policies(
        '[{"id":"b","name":"b","short":"b","activation":979920,"end":1005840,"enforcing_min_version":"29.5.0"},'
        '{"id":"a","name":"a","short":"a","activation":973440,"end":979920,"enforcing_min_version":"29.4.2"}]'))
    expect_raises("bad version", lambda: parse_policies(
        '[{"id":"a","name":"a","short":"a","activation":973440,"end":979920,"enforcing_min_version":"29.x"}]'))
    expect_raises("end<=activation", lambda: parse_policies(
        '[{"id":"a","name":"a","short":"a","activation":979920,"end":979920,"enforcing_min_version":"29.4.2"}]'))
    _base = '{"id":"a","name":"a","short":"a","activation":973440,"end":979920,"enforcing_min_version":"29.4.2"}'
    expect_raises("duplicate id", lambda: parse_policies('[%s,%s]' % (_base,
        '{"id":"a","name":"b","short":"b","activation":979920,"end":985000,"enforcing_min_version":"29.5.0"}')))
    expect_raises("id 'pre' reserved", lambda: parse_policies(
        '[{"id":"pre","name":"a","short":"a","activation":973440,"end":979920,"enforcing_min_version":"29.4.2"}]'))
    expect_raises("overlapping epochs", lambda: parse_policies('[%s,%s]' % (_base,
        '{"id":"b","name":"b","short":"b","activation":979000,"end":985000,"enforcing_min_version":"29.5.0"}')))
    expect_raises("non-monotonic min version", lambda: parse_policies('[%s,%s]' % (_base,
        '{"id":"b","name":"b","short":"b","activation":979920,"end":985000,"enforcing_min_version":"29.4.1"}')))
    expect_raises("numeric (unquoted) version", lambda: parse_policies(
        '[{"id":"a","name":"a","short":"a","activation":973440,"end":979920,"enforcing_min_version":29.40}]'))
    expect_raises("float activation", lambda: parse_policies(
        '[{"id":"a","name":"a","short":"a","activation":973440.5,"end":979920,"enforcing_min_version":"29.4.2"}]'))

    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_run_self_test())
    print("usage: policy_model.py --self-test", file=sys.stderr)
    sys.exit(2)
