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
    m = re.match(r"^(/Satoshi:[0-9]+(?:\.[0-9]+)*)(?:\([^)]*\))?(/Knots:[^/]+/)$", ua)
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
    # current_policy by height
    check("h=973440 -> m45", current_policy(POL, 973440)["id"], "m45")
    check("h=979919 -> m45", current_policy(POL, 979919)["id"], "m45")
    check("h=979920 -> m180", current_policy(POL, 979920)["id"], "m180")
    check("h=2000000 -> m180", current_policy(POL, 2000000)["id"], "m180")
    check("h=900000 -> None", current_policy(POL, 900000), None)
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
