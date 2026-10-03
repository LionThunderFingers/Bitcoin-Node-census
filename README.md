# Bitcoin Node Census

Hourly census of the **reachable** Bitcoin network, measured by crawling it from an independent DNS seed (`seed.thelionpool.org`).

Live page: https://lionthunderfingers.github.io/Bitcoin-Node-census/

`data.json` is regenerated hourly by the seed host and pushed here; the page reads it. Figures count only nodes that accept incoming connections, so they are a lower bound on the real network.

Seeder and deploy tooling: https://github.com/LionThunderFingers/BTC-blake2b-seeder
