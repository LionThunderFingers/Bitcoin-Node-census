# Census pipeline

Hourly census of the reachable post-fork network, published to
https://lionthunderfingers.github.io/Bitcoin-Node-census/ from the seed host. The scripts and
systemd units here are what runs there. They are installed by hand and are never executed from a
clone of this repo, so a push here cannot change what runs on the host.

- `seed-census` reads the seeder's `dnsseed.dump` and appends one JSON line to `census.jsonl`:
  version mix, height spread, `networks` (tor/i2p/ipv4/ipv6), `storage_archival` /
  `storage_pruned` / `storage_other` and `versions_storage` (that same split per user agent).
  The storage split covers nodes that completed a handshake in the last 83 minutes, so it sums to
  `b2b_polled_recently`, and `versions_storage` summed over all user agents equals it bucket by
  bucket. It deliberately does not use the seeder's "good" flag, which excludes pruned nodes.
  `seed-census --self-test` checks those invariants against a fixture dump.
- `cesspool-fetch` (separate, unprivileged) downloads cesspool.lol's per-block grade files
  (`/api/v1/tip.json`, `/api/v1/blocks/<n>.json`) into `/var/lib/census-cesspool/`. It runs as
  system user `census-fetch`, which cannot read the deploy key, `/etc/default/seed-publish` or the
  site clone. Per block it keeps only `height`, `hash`, `time`, `sewage_share`, `weight`, each
  validated; no text from cesspool is cached except `grader_version`, pattern-checked. Hourly = 2 requests; a full re-read happens when
  `grader_version` changes and every 24 h, at 2.5 s spacing, all downloads finish before any file is written. On any failure it
  exits 1 and leaves the cache untouched, and `seed-publish` just keeps the previous output.
  `cesspool-fetch --self-test` runs its checks (no network).
- `seed-stable.py` lists consistently reachable nodes (`stable-nodes.json`).
- `seed-publish` turns the newest census line and the whole series into `data.json`, `history.json`,
  `policy-history.json`, `releases.json`, `spam-share.json`, `seeds.txt` and `stable-nodes.json`, and
  pushes them with a repo-scoped deploy key. `spam-share.json` comes from `gen_spam_share.py`, which
  reads the cesspool cache (`CESSPOOL_CACHE`, default `/var/lib/census-cesspool`, read-only),
  `census.jsonl` and the `releases.json` generated just before it. It never fails the publish: a generator failure keeps the previous file; a
  missing or unreadable cache (no readable `state.json`) keeps the previous file, or on the very
  first publish writes one with `days: []`; a stale but present cache regenerates from the stale
  data (the panel shows its latest block).
- `policy_model.py`, `gen_releases.py`, `gen_policy_history.py`, `gen_spam_share.py` are the shared Python that
  `seed-publish` imports (`seed-publish --self-test` runs their checks, and also
  `seed-census --self-test` — it resolves `seed-census` on `PATH`, or `SEED_CENSUS=/path/to/it`).
- `data.json` additions: `versions_storage` (canonicalized), `policy` (`{id,name,short}` of the
  epoch in force) and `next_policy` (the same plus `activation`, `end` and its own
  `enforcing_versions`, or `null` once the last epoch has activated). The page titles its
  activation section from them; nothing existing changed meaning.
- `../policies.json` is data, not code: the ordered policy epochs. A malformed file aborts the whole
  publish, so validate it first with `python3 policy_model.py --self-test` and `parse_policies`.

The systemd drop-ins point both services at the seeder dump (`/var/lib/lionseed/lionseed.dump`).
Files must keep LF line endings (`.gitattributes` enforces it); CRLF breaks them on Linux.

## Install

```
install -m 0755 tools/seed-census         /usr/local/bin/seed-census
install -m 0755 tools/seed-stable.py      /usr/local/bin/seed-stable
install -m 0755 tools/seed-publish        /usr/local/bin/seed-publish
install -d -m 0755 /usr/local/lib/census-tools
install -m 0755 tools/policy_model.py tools/gen_releases.py tools/gen_policy_history.py tools/gen_spam_share.py /usr/local/lib/census-tools/
install -m 0600 tools/seed-publish.default.example /etc/default/seed-publish   # then edit
cp -r tools/systemd/* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now seed-census.timer seed-publish.timer
```

The cesspool fetcher is installed separately:

```
useradd --system --no-create-home --shell /usr/sbin/nologin census-fetch
install -m 0755 tools/cesspool-fetch.py /usr/local/bin/cesspool-fetch
cp tools/systemd/cesspool-fetch.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl start cesspool-fetch   # once, then check `journalctl -u cesspool-fetch` and /var/lib/census-cesspool
systemctl enable --now cesspool-fetch.timer
```

Run the fetcher once and check its output before installing the new `seed-publish`; otherwise the first publish commits `days: []` to the public file.
