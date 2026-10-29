#!/usr/bin/env python3
"""
Emit stable-nodes.json: fork nodes this crawler has found consistently reachable.

Intended for people running a FIXED-LIST seed. A curated list is Sybil-resistant
because a human chooses it, but nobody currently gives that human evidence about
which nodes are actually reliable. This supplies the evidence; it is deliberately
NOT a list to serve unexamined.

On the uptime windows: the seeder keeps exponentially-weighted reliability figures
over 2h/8h/1d/7d/30d. They start at zero and fill as the crawler runs, so a 30-day
figure is meaningless until the crawler has been up for roughly that long. The
qualifying rule below therefore keys off the longest window the crawler has
been running for at least three times over (so the figure has converged), and
tightens on its own over time.
"""
import json, os, sys, time

DUMP = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/dnsseed/dnsseed.dump"
START = int(sys.argv[2]) if len(sys.argv) > 2 else 0   # crawler start, unix seconds

B2B_NIBBLES = set("13579bdfBDF")          # bit 28 set -> low bit of the first hex digit
# A window needs runtime well beyond its own length before its figure is worth
# trusting: these are exponentially-weighted averages that start at zero, so at
# exactly one window length they are still filling and read far too low. Requiring
# 3x the window length before using it avoids rejecting perfectly good nodes purely
# because the crawler is young.
CONVERGENCE_FACTOR = 3
WINDOWS = [                                # (label, column index, window length in seconds)
    ("30d", 7, 30 * 86400),
    ("7d",  6, 7 * 86400),
    ("1d",  5, 86400),
    ("8h",  4, 8 * 3600),
    ("2h",  3, 0),
]
WINDOWS = [(lbl, col, length * CONVERGENCE_FACTOR) for lbl, col, length in WINDOWS]
THRESHOLD = 90.0                           # percent reliability required in that window

def pick_window(start, now):
    """(label, column index, crawler age in seconds) for a crawler that started at `start`.

    START is 0 when CRAWLER_START is unset in /etc/default/seed-publish AND the fallback
    (the unit's ActiveEnterTimestamp) could not be read either. The age is then 0, so the
    rule falls through to the 2h window -- the WEAKEST bar on the list, where a node that
    happened to answer for the last two hours qualifies as stable. That is the opposite of
    what an unknown start time should mean, and it used to happen in total silence, with the
    page reporting "≥90% over 2h" as though 2h had been chosen on the evidence.

    The behaviour is deliberately left as it is: the alternative, refusing to emit a file,
    would make seed-publish keep the previous stable-nodes.json for ever once it happened,
    and the honest bar for a crawler of unknown age is the shortest window rather than none.
    It now says so on stderr, which is where the operator reads it.
    """
    age = max(0, int(now) - start) if start else 0
    if not start:
        print("warning: the crawler start time is 0 or unset, so no uptime window has any "
              "proven runtime behind it. The qualifying rule falls back to the %s window, "
              "the weakest on the list: a node reachable for only that long will be "
              "reported as stable. Set CRAWLER_START (e.g. 2026-09-27T21:28:00Z) in "
              "/etc/default/seed-publish." % WINDOWS[-1][0], file=sys.stderr)
    label, col, _ = next(w for w in WINDOWS if age >= w[2])
    return label, col, age


def _run_self_test():
    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += not ok
        print("%s  %-46s got %r want %r" % ("ok  " if ok else "FAIL", label, got, want))

    DAY = 86400
    # The window widens as the crawler's runtime passes 3x each window's own length.
    check("age 0 -> 2h", pick_window(0, 0)[0], "2h")
    check("unset START -> 2h, age 0", pick_window(0, 10 ** 9)[:3], ("2h", 3, 0))
    check("1h old -> 2h", pick_window(10 ** 9, 10 ** 9 + 3600)[0], "2h")
    check("23h old -> 2h", pick_window(10 ** 9, 10 ** 9 + 23 * 3600)[0], "2h")
    check("24h old -> 8h (3x8h)", pick_window(10 ** 9, 10 ** 9 + 24 * 3600)[0], "8h")
    check("3d old -> 1d", pick_window(10 ** 9, 10 ** 9 + 3 * DAY)[0], "1d")
    check("20d old -> 1d", pick_window(10 ** 9, 10 ** 9 + 20 * DAY)[0], "1d")
    check("21d old -> 7d (3x7d)", pick_window(10 ** 9, 10 ** 9 + 21 * DAY)[0], "7d")
    check("89d old -> 7d", pick_window(10 ** 9, 10 ** 9 + 89 * DAY)[0], "7d")
    check("90d old -> 30d (3x30d)", pick_window(10 ** 9, 10 ** 9 + 90 * DAY)[0], "30d")
    # A start time in the future clamps the age at 0 rather than going negative, so it
    # picks the shortest window instead of raising StopIteration.
    check("future START -> 2h", pick_window(10 ** 9 + 999, 10 ** 9)[0], "2h")
    check("future START age clamped", pick_window(10 ** 9 + 999, 10 ** 9)[2], 0)
    # The column index must address the right element of the five uptime percentages.
    check("2h reads pct[0]", pick_window(0, 0)[1] - 3, 0)
    check("30d reads pct[4]", pick_window(10 ** 9, 10 ** 9 + 90 * DAY)[1] - 3, 4)
    # Every window label the page may have to render a column for.
    check("window labels", [w[0] for w in WINDOWS], ["30d", "7d", "1d", "8h", "2h"])
    print("\n%s" % ("ALL PASS" if not failed else "%d FAILED" % failed))
    return 1 if failed else 0


if "--self-test" in sys.argv:
    sys.exit(_run_self_test())

window_label, window_col, age = pick_window(START, time.time())

nodes = []
try:
    with open(DUMP) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.split()
            if len(f) < 11:
                continue
            svcs = f[9]
            if not svcs or svcs[0] not in B2B_NIBBLES:
                continue
            if f[1] != "1":                 # not currently reachable
                continue
            try:
                pct = [float(f[i].rstrip("%")) for i in range(3, 8)]
                height = int(f[8])
            except ValueError:
                continue
            # Deliberately NOT recording the per-node software version here.
            # The addresses are already public (the seed hands them out), and the
            # version mix is published in aggregate on the census page. But pairing
            # a specific address to a specific version on a ~70 node network is an
            # inventory of who is running what, and becomes a target list the moment
            # some version has a known bug. The aggregate answers the useful
            # question; the mapping only adds risk.
            nodes.append({
                "address": f[0],
                "uptime": {"2h": pct[0], "8h": pct[1], "1d": pct[2], "7d": pct[3], "30d": pct[4]},
                "height": height,
                "services": svcs,
                "qualifies": pct[window_col - 3] >= THRESHOLD,
            })
except FileNotFoundError:
    print(json.dumps({"error": "dump not found"}), file=sys.stderr)
    sys.exit(1)

nodes.sort(key=lambda n: (-n["uptime"][window_label], n["address"]))
qualifying = [n for n in nodes if n["qualifies"]]

print(json.dumps({
    "generated": int(time.time()),
    "crawler_started": START,
    "crawler_age_days": round(age / 86400, 2),
    "criterion": {
        "window": window_label,
        "threshold_pct": THRESHOLD,
        "explanation": (
            f"Reachable fork nodes with at least {THRESHOLD:.0f}% reliability over the "
            f"{window_label} window. That is the longest window the crawler has been "
            f"running for at least {CONVERGENCE_FACTOR} times over, so the figure has had time "
            f"to settle; it lengthens, and the list gets stronger, the longer the crawler runs."
        ),
    },
    "qualifying_count": len(qualifying),
    "observed_count": len(nodes),
    "nodes": qualifying,
    "all_reachable": nodes,
}, indent=1))
