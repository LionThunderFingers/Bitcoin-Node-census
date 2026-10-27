# Bitcoin Node Census

Hourly census of the reachable nodes on the Bitcoin network, measured by crawling it from an independent DNS seed (`seed.thelionpool.org`).

Live page: https://lionthunderfingers.github.io/Bitcoin-Node-census/

`data.json`, `history.json`, `releases.json` and `policy-history.json` are regenerated hourly by the seed host and pushed here; the page reads them. Figures count only nodes that accept incoming connections, so they are a lower bound on the real network.

`policies.json` is the editable list of consensus-policy epochs (activation height, window end, oldest enforcing release). It drives the activation countdown, the enforcement split and the policy-adoption chart from one place. Edit it to record a new rule; see `tools/` for the generators and self-tests (`seed-publish --self-test`).

Seeder and deploy tooling: https://github.com/LionThunderFingers/LionSeed
