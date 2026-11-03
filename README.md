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

- **Open Targets Platform** — CC0
  (https://platform-docs.opentargets.org/licence). Pulled live via the
  GraphQL API for `MONDO_0009693` only (same disease as our
  `MONDO:0009693`). We keep the top 50 associated targets by overall
  association score, plus any targets tied with #50 (53 as of release
  26.09), sorted by score then Ensembl ID so the list is reproducible. We
  also keep all drugs / clinical candidates for myeloma (with ChEMBL IDs,
  max clinical stage and mechanism-of-action targets). The Open Targets
  release (e.g. `26.09`) is recorded in the output.
- **CIViC** — CC0 (https://civic.readthedocs.io/en/latest/about/faq.html).
  Pulled live via the GraphQL API: all evidence items for Multiple Myeloma
  (`DOID:9538`) with gene (HGNC symbol + Entrez ID), variant, evidence
  type, level, significance, therapy and PMID. CIViC returns **accepted**
  (curator-reviewed) and **submitted** (not yet reviewed) items; rejected
  items are excluded. Each item keeps its `status` so the two can be told
  apart. The `DOID:9538` → `MONDO:0009693` mapping comes from CIViC's own
  cross-reference (`myDiseaseInfo.mondoId`), not from us.

Every gene in the Open Targets and CIViC outputs carries `hgnc_symbol`, so
the two sources can be joined on gene symbol. Both outputs also record
`graph_disease_id: MONDO:0009693`; **the loader should use
`graph_disease_id`** rather than each source's native disease ID
(`MONDO_0009693` / `DOID:9538`). Each fetch saves the exact API reply next to
the processed JSON (`*.raw*.json`) with its sha256, fetch timestamp (UTC),
source version and license, so a snapshot can be verified later even though
the APIs are live.

All sources carry MONDO/PMID identifiers so provenance can be shown
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
python3 ingestion/fetch_opentargets.py  # -> data/raw/opentargets_mm.json (+ .raw.json)
python3 ingestion/fetch_civic.py        # -> data/raw/civic_mm.json (+ .raw.N.json)
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
