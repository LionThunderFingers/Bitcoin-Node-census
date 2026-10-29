# Bitcoin Node Census

An hourly count of the reachable post-fork (BLAKE2b, service bit 28) Bitcoin nodes, taken by crawling the network from one independent DNS seed, `seed.thelionpool.org`. Every hour the seed host writes a new sample, turns it into the JSON and text files in this repository and pushes them here. The page at https://lionthunderfingers.github.io/Bitcoin-Node-census/ reads the same files. The seeder that does the crawling is https://github.com/LionThunderFingers/LionSeed.

## Using the data

All files are in the root of `main` and can be fetched from either URL:

| File | GitHub Pages | Raw |
|---|---|---|
| `data.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/data.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/data.json |
| `history.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/history.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/history.json |
| `releases.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/releases.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/releases.json |
| `policy-history.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/policy-history.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/policy-history.json |
| `stable-nodes.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/stable-nodes.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/stable-nodes.json |
| `seeds.txt` | https://lionthunderfingers.github.io/Bitcoin-Node-census/seeds.txt | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/seeds.txt |
| `policies.json` | https://lionthunderfingers.github.io/Bitcoin-Node-census/policies.json | https://raw.githubusercontent.com/LionThunderFingers/Bitcoin-Node-census/main/policies.json |

**Update cadence.** A sample is taken once an hour and published a few minutes later (`tools/systemd/*.timer`). A commit is made only when the content changed, which in practice is every hour. Use `ts` inside the file for the time of the measurement; the commit time is only when it was pushed.

**Fetch example.**

```sh
curl -s https://lionthunderfingers.github.io/Bitcoin-Node-census/data.json \
  | jq '{ts, rated_good: .b2b_reachable, polled_recently: .b2b_polled_recently, known: .b2b, max_height}'
```

**Past snapshots.** Every publish is a commit whose message is `census YYYY-MM-DD HH:MM UTC`, so any earlier state of any file can be read back from git history:

```sh
git clone https://github.com/LionThunderFingers/Bitcoin-Node-census.git && cd Bitcoin-Node-census
git log --format='%H %s' -- data.json          # one line per published snapshot
git show <sha>:data.json > data-<sha>.json     # that snapshot's file
```

The first published `data.json` is from 2026-09-19; `seeds.txt` starts on 2026-09-27. `history.json` only carries the most recent 2000 samples (`tools/seed-publish`, history block), so for anything older than that use git history. `releases.json` and `policy-history.json` are rebuilt from the full sample series every hour.

**Compatibility.** Changes are additive only. Existing field names and their meanings are never changed or removed; new data arrives under new names. Ignore fields you do not recognise. Older snapshots in git history may lack fields that were added later (for example `networks`, `versions_storage`, `policy`, `next_policy`); treat a missing field as "not measured in that sample", not as zero.

## How it is measured

- One crawler (LionSeed) connects to each address it learns of and completes a Bitcoin handshake, recording the services, block height, protocol version and user agent the node reports about itself.
- Only nodes that accept incoming connections can be seen. Nodes behind NAT that never listen are invisible, so every count is a lower bound.
- A node is post-fork if it advertises service bit 28 (`0x10000000`, `NODE_BLAKE2B`). That bit is taken only from the node's own handshake, never from addresses gossiped by other peers.
- Clearnet (IPv4, IPv6), Tor and I2P are crawled. Tor and I2P since 27 Sep 2026.
- The crawler bootstraps from post-fork seeds, so the post-fork share of the whole crawl is not an unbiased sample of the Bitcoin network.
- All timestamps are unix seconds, UTC.
- Before 2026-09-27 21:28 UTC the samples came from a different seeder (an older dnsseed); the series has a visible step there.

## The three populations

Fields mix three different sets of post-fork nodes. Read the population column in the tables below before comparing two numbers.

| Name used here | Meaning | Size field |
|---|---|---|
| **rated good** | The seeder's own quality gate: the node is post-fork, its last successful handshake is under an hour old, it is on port 8333 (any port for I2P), it advertises `NODE_NETWORK`, reports protocol >= 70001 and a height at or above the fork, and has a sustained reliability record. Because `NODE_NETWORK` is required, **pruned nodes (which advertise only `NODE_NETWORK_LIMITED`) are never rated good.** Defined in LionSeed `src/node.rs`, `is_good()`. | `b2b_reachable` |
| **polled recently** | Every post-fork node whose last successful handshake is less than 4980 seconds (83 minutes) old when the sample is taken. Archival and pruned nodes alike. (`tools/seed-census`, the `age < 4980` test.) | `b2b_polled_recently` |
| **known** (`b2b`) | Every post-fork node the seeder has ever completed a handshake with and still holds. The seeder never drops a node that has answered, however long it has been offline, so this grows almost monotonically. It can dip by one or two when a node's later handshake no longer advertises bit 28. | `b2b` |

Note the naming: in the JSON, `b2b_reachable` means **rated good**, not "polled recently". The live page's headline "post-fork nodes reachable" is the polled-recently figure (`storage_archival + storage_pruned + storage_other`).

## Field reference

### data.json

The latest sample. One JSON object.

| Field | Type | Population | Meaning |
|---|---|---|---|
| `ts` | int | - | When the sample was taken (unix seconds). |
| `dump_ts` | int | - | Modification time of the seeder dump the sample was read from. |
| `total` | int | all chains | Rows in the seeder dump: every address the seeder has tried at least once, post-fork or not, answered or not. |
| `reachable` | int | rated good, all chains | Rows marked good in the dump. LionSeed only rates post-fork nodes good, so this currently always equals `b2b_reachable`. |
| `b2b` | int | known | Post-fork nodes the seeder holds. |
| `b2b_reachable` | int | rated good | Post-fork nodes rated good. |
| `b2b_polled_recently` | int | polled recently | Post-fork nodes with a successful handshake in the last 4980 s. |
| `b2b_poll_age_median` | int | known | Median seconds since last successful handshake, over all known post-fork nodes. |
| `b2b_poll_age_p90` | int | known | 90th percentile of the same. |
| `b2b_poll_age_max` | int | known | Maximum of the same. |
| `storage_archival` | int | polled recently | Nodes advertising `NODE_NETWORK` (serve the full chain). |
| `storage_pruned` | int | polled recently | Nodes advertising `NODE_NETWORK_LIMITED` without `NODE_NETWORK`. |
| `storage_other` | int | polled recently | Neither bit. `storage_archival + storage_pruned + storage_other == b2b_polled_recently`. |
| `versions_storage` | object | polled recently | `{user agent: {archival, pruned, other}}`. Summed over all user agents it equals the three `storage_*` fields. User agents are canonicalised as in `versions`. |
| `versions` | object | rated good | `{user agent: node count}`. Sums to `b2b_reachable`. A `-uacomment` is stripped (counts merged) only when the whole string is `/Satoshi:X(comment)/Knots:Y/`; anything with extra segments is kept as reported. Rated good only, so pruned-only user agents do not appear here; use `versions_storage` for the wider set. |
| `max_height` | int | rated good | Highest block height reported by a rated-good node at its last handshake. Peer-reported, not verified. |
| `heights` | object | rated good | `{tip, near, day, behind}`: nodes at `max_height`, 1-6 blocks behind, 7-144 behind, more than 144 behind. Heights are as of each node's last handshake, so some lag is crawl timing. Sums to `b2b_reachable`. |
| `heights_by_version` | object | rated good | `{user agent: {tip, near, day, behind}}`, same buckets. User agents are as reported (not canonicalised). Summed over user agents it equals `heights`. |
| `heights_by_enforcement` | object | rated good | `{enforcing: {tip, near, day, behind}, not_enforcing: {...}}`: `heights_by_version` split by whether the user agent enforces the policy in `policy`. |
| `networks` | object | rated good | `{ipv4, ipv6, tor, i2p}`, by address type. Sums to `b2b_reachable`. |
| `policy` | object | - | `{id, name, short}` of the policy epoch in force at `max_height` (or the first epoch, before any has activated). From `policies.json`. |
| `activation_height` | int | - | `activation` of that epoch. |
| `activation_end_height` | int | - | `end` of that epoch. |
| `enforcing_versions` | array of string | - | Every user agent appearing in `versions`, `versions_storage` or `heights_by_version` that enforces the current policy, sorted. Contains both canonical and as-reported forms, so it can be matched by exact string against any of those three maps. |
| `next_policy` | object or null | - | The next epoch that has not activated yet: `{id, name, short, activation, end, enforcing_versions}`, where `enforcing_versions` is scored against that epoch's own minimum version. `null` once the last epoch in `policies.json` has activated. |

**Which software counts as enforcing.** Only a user agent that starts with an official Knots release string, `/Satoshi:X.Y.Z(optional comment)/Knots:YYYYMMDD(optional rcN)/`, at or above the epoch's `enforcing_min_version`. Anything after that prefix is ignored. Renamed forks, plain `/Satoshi:X/`, dev builds and malformed strings are not enforcing. See `tools/policy_model.py`, `classify_enforcing()`, and the test table in `tools/seed-publish --self-test`.

### history.json

An array, oldest first, one object per sample, last 2000 samples.

| Field | Type | Meaning |
|---|---|---|
| `ts` | int | Sample time. |
| `b2b` | int | Known post-fork nodes. |
| `b2b_reachable` | int | Rated-good post-fork nodes. |
| `max_height` | int | As in `data.json`. |
| `enforcing` | int | Rated-good nodes whose user agent enforces the policy currently in force. The whole series is rescored with today's policy every hour, so this is not what was enforcing at the time in any historical sense. For the per-epoch view use `policy-history.json`. |

### releases.json

An array, one row per release (canonical user agent) ever seen, sorted by `now_pct` then `peak_pct`, both descending. Two families of fields that use different populations:

- `peak_*` and `now_*` are the original fields and are kept unchanged for existing consumers. They are **shares of the rated-good set**, and only samples with at least 50 rated-good nodes count (`PEAK_FLOOR` in `tools/gen_releases.py`).
- `nodes_*` are **node counts**. Each sample is counted on the `"storage"` basis (`versions_storage`, the polled-recently set) when the sample has it, otherwise on the `"versions"` basis (`versions`, rated good). The basis is recorded per row because the storage basis includes pruned nodes, so counts step up at the sample where `versions_storage` first appears. That step is a measurement change, not adoption.

| Field | Type | Meaning |
|---|---|---|
| `ua` | string | Canonical user agent. |
| `first_ts` | int | First sample in which this release had at least one node (either basis). |
| `last_ts` | int | Last such sample. |
| `peak_pct` | float or null | Highest share (0-100) of rated-good nodes, over samples with at least 50 rated-good nodes. `null` if the release never appeared in such a sample. |
| `peak_count` | int or null | Rated-good node count in that peak-share sample. |
| `peak_ts` | int or null | Time of that sample. |
| `now_pct` | float | Share (0-100) of rated-good nodes in the latest sample with at least 50 rated-good nodes. |
| `now_count` | int | Rated-good node count in that sample. |
| `nodes_peak` | int | Highest node count ever reached (no floor). If reached more than once, the first time. Can be `null` in the edge case where a release appears only in samples rejected for the `nodes_*` count (`tools/gen_releases.py:164`); none do today. Same for `nodes_peak_ts` and `nodes_peak_basis`. |
| `nodes_peak_ts` | int | Time of that sample. |
| `nodes_peak_pct` | float or null | Share (0-100) in that sample, on its basis; `null` if that sample's population was under 50. |
| `nodes_peak_basis` | string | `"storage"` or `"versions"`: the population `nodes_peak` was counted on. |
| `nodes_now` | int | Node count in the latest sample whose population is at least 50. |
| `nodes_now_pct` | float | Share (0-100) in that sample. On the storage basis the denominator is the sum of `versions_storage` (equal to `b2b_polled_recently`). |
| `nodes_now_basis` | string | `"storage"` or `"versions"`. |

A sample is dropped (not counted on either basis) if its per-version counts do not add up to its totals; see `_storage_counts()` and `_version_counts()` in `tools/gen_releases.py`.

### policy-history.json

An array, oldest first, one object per sample, built from the full sample series.

| Field | Type | Meaning |
|---|---|---|
| `ts` | int | Sample time. |
| `b2b_reachable` | int | Rated-good post-fork nodes. |
| `buckets` | object | `{"pre": n, "<policy id>": n, ...}`. Each rated-good node is counted once, under the newest policy its software enforces, else `"pre"`. Every policy id in `policies.json` is present, zero-filled. Buckets sum to `b2b_reachable`. |

Samples whose `versions` counts do not sum to `b2b_reachable` are skipped (`tools/gen_policy_history.py`), so this file can have fewer points than `history.json` over the same period.

### stable-nodes.json

Rated-good post-fork nodes with their uptime figures, meant as evidence for someone curating a fixed seed list, not as a list to serve unexamined. Software versions are deliberately not paired with addresses (`tools/seed-stable.py`).

| Field | Type | Meaning |
|---|---|---|
| `generated` | int | When the file was written. |
| `crawler_started` | int | When the current crawler started collecting (set on the seed host). |
| `crawler_age_days` | float | Days since `crawler_started`. |
| `criterion` | object | `{window, threshold_pct, explanation}`. A node qualifies if its uptime over `window` is at least `threshold_pct` (90). `window` is the longest of 30d/7d/1d/8h/2h for which the crawler has run at least three times that length (the figures start at zero and need time to converge), so it lengthens on its own. |
| `qualifying_count` | int | Length of `nodes`. |
| `observed_count` | int | Length of `all_reachable`. |
| `nodes` | array | The qualifying entries. |
| `all_reachable` | array | Every rated-good post-fork node, qualifying or not, sorted by uptime in `window` descending. |

Each entry in `nodes` / `all_reachable`:

| Field | Type | Meaning |
|---|---|---|
| `address` | string | `host:port`; IPv6 in brackets, `.onion` or `.b32.i2p` for Tor and I2P. |
| `uptime` | object | `{"2h", "8h", "1d", "7d", "30d"}`, percent, as in the dump. |
| `height` | int | Height reported at the last handshake. |
| `services` | string | Service bits, 8 hex digits. |
| `qualifies` | bool | Meets `criterion`. |

### seeds.txt

The post-fork rows of the seeder's dump, in the `dnsseed.dump` text format that Knots' `contrib/seeds/makeseeds.py` reads. Line 1 is a `#` header; then one row per **known** post-fork node (good or not), whitespace-separated, good rows first, then by the seeder's internal 30-day reliability. That ordering uses the figure before the scaling described under `%(30d)` below, so the printed column can look out of order.

| Column | Meaning |
|---|---|
| address | `host:port`. IPv6 in brackets; Tor `.onion`; I2P `.b32.i2p` with port 0. |
| good | `1` if rated good at dump time, else `0`. |
| lastSuccess | Unix time of the last successful handshake. |
| %(2h) %(8h) %(1d) %(7d) %(30d) | Exponentially weighted success rate over each window, as a percentage. LionSeed scales each figure by how much of the window has passed since it first saw the node, so time before discovery counts as downtime (LionSeed `src/dump.rs`). |
| blocks | Block height reported at the last handshake. |
| svcs | Service bits from the last handshake, 8 hex digits. First digit odd = bit 28 (post-fork). |
| version | Protocol version. |
| user agent | The rest of the line, in double quotes (quotes and control characters removed from the string itself). |

### policies.json

The consensus-policy epochs, in increasing activation order. Hand-edited data, not generated.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique key, used in `policy-history.json` buckets. `"pre"` is reserved. |
| `name` | string | Display name. |
| `short` | string | Short display name. |
| `activation` | int | Block height at which the rule takes effect. |
| `end` | int | Block height at which the epoch ends (exclusive: an epoch is in force for `activation <= height < end`). |
| `enforcing_min_version` | string | Oldest Knots release (`X.Y.Z`) that enforces the rule. |

Validation rules (strictly increasing activation and minimum version, no overlap) are in `tools/policy_model.py`, `parse_policies()`.

## Privacy

The JSON files carry only aggregates, except `stable-nodes.json`, which lists addresses with uptime but no software version. `seeds.txt` lists the addresses of post-fork nodes that accept incoming connections: addresses any crawler can find, with the details each node reports about itself in its handshake. It goes further than a DNS seed's answers in three ways: it includes per-node uptime figures, it includes nodes that are not currently rated good, and git history keeps every hourly version. Addresses of non-post-fork nodes are not published.

## License and credit

The data and everything else in this repository are licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/); full text in [LICENSE](LICENSE). You may copy, redistribute, adapt and build on it, commercially or not, as long as you give credit. Please credit "Bitcoin Node Census" and link this repository.

## Pipeline

`policies.json` is the editable list of consensus-policy epochs (activation height, window end, oldest enforcing release). It drives the activation countdown, the enforcement split and the policy-adoption chart from one place. Edit it to record a new rule; see `tools/` for the generators and self-tests (`seed-publish --self-test`).

The pipeline that produces these files lives in `tools/`: `seed-census` (hourly sample from the seeder's dump), `seed-publish` (builds and pushes the JSON), `seed-stable.py`, the generators and `systemd/` units. These are installed on the seed host by hand and are never run out of this clone, so a push here cannot change what executes there. `seed-census` reads the `dnsseed.dump` format written by the seeder. Details and install steps: [tools/README.md](tools/README.md).
