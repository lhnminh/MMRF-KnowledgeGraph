# MMRF ORION — WS1: Knowledge Graph

Hackathon prep for Workstream 1 (2026-11-03/04): ingest a permitted public
myeloma knowledge graph and let an agent answer plain-language questions with
a shown subgraph + provenance, distinguishing graph-encoded relationships from
added explanation.

## Data sources & licensing

- **Monarch KG / API** — primary source. CC0 per
  https://monarch-app.monarchinitiative.org/Licensing/. Pulled live via the
  public API (`https://api.monarchinitiative.org`), entry point
  `MONDO:0009693` (plasma cell myeloma / multiple myeloma).
- **DisMech** — supplementary mechanism paths ("pathograph") for multiple
  myeloma only, from
  `kb/disorders/Multiple_Myeloma.yaml` in
  https://github.com/monarch-initiative/dismech. DisMech's **bulk-data
  license is explicitly unsettled** (see their downloads page) — do not bulk
  import. We use only the single Multiple Myeloma disorder page, whose
  rendered site content is CC-BY 4.0, and cite it as such with the repo
  commit/tag it was pulled from.

Both sources carry MONDO/PMID identifiers so provenance can be shown
per-edge in the agent's answers.

## Local dev loop (pre-AWS-sandbox)

Neptune supports openCypher, so local dev runs against Neo4j Community in
Docker — closest free equivalent for Cypher semantics.

```sh
docker compose up -d          # starts neo4j on :7474 (browser) / :7687 (bolt)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 ingestion/fetch_monarch.py      # -> data/raw/monarch_mm.json
python3 ingestion/fetch_dismech.py      # -> data/raw/dismech_mm.yaml
docker compose --profile tools run --rm loader   # loads both into local Neo4j
```

Browse the graph at http://localhost:7474 (user `neo4j`, password `password`).

> Note: on this machine, Zscaler blocks host-side loopback connections to
> Docker-forwarded ports, so `load_neo4j.py` can't run from the host venv
> directly against `localhost:7687`. The `loader` service runs it inside the
> Docker network instead (`bolt://neo4j:7687`), which works. The fetch
> scripts run fine from the host venv since they only make outbound HTTPS
> calls.

## Migrating to Neptune

Neptune's openCypher endpoint accepts the same Cypher used against local
Neo4j, modulo a few syntax gaps (no `CREATE CONSTRAINT`, auth via Sigv4). Swap
the bolt driver in `ingestion/load_neo4j.py` for the Neptune openCypher HTTPS
endpoint once the AWS sandbox is live; `terraform/` holds the Neptune
provisioning for that.

## Status

Prep only — no AWS sandbox access until the hackathon account is issued
2026-11-03. `terraform/` is a placeholder until then.
