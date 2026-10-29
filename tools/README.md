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
- `seed-stable.py` lists consistently reachable nodes (`stable-nodes.json`).
- `seed-publish` turns the newest census line and the whole series into `data.json`, `history.json`,
  `policy-history.json`, `releases.json`, `seeds.txt` and `stable-nodes.json`, and pushes them with a
  repo-scoped deploy key.
- `policy_model.py`, `gen_releases.py`, `gen_policy_history.py` are the shared Python that
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
install -m 0755 tools/policy_model.py tools/gen_releases.py tools/gen_policy_history.py /usr/local/lib/census-tools/
install -m 0600 tools/seed-publish.default.example /etc/default/seed-publish   # then edit
cp -r tools/systemd/* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now seed-census.timer seed-publish.timer
```
