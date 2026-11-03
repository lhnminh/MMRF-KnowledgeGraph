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

All sources carry disease identifiers (MONDO, or DOID mapped to MONDO), plus
PMIDs or clinical-trial links where the source provides them, so provenance
can be shown per-edge in the agent's answers.

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

`ingestion/load_neo4j.py` runs the same Cypher against local Neo4j and
Neptune (openCypher over Bolt, port 8182, encrypted; IAM DB auth is off in
`terraform/`, so no credentials are needed):

```sh
python3 ingestion/load_neo4j.py --dry-run            # counts only, no database needed
python3 ingestion/load_neo4j.py --neptune            # load (or set NEPTUNE_MODE=1)
python3 ingestion/load_neo4j.py --neptune --verify   # read-only counts vs. expected
```

Neptune can't store list-valued properties, so lists are stored as
`|`-joined strings (read back with `split(x, '|')`). On Neptune this includes
Monarch `ASSOCIATION.publications`, which stays a list on local Neo4j. There
is no `CREATE CONSTRAINT`; re-runs stay duplicate-free through `MERGE`.

### Loading Neptune from the SageMaker workbench notebook

Neptune sits in a private VPC, so load it from the workbench notebook
(`terraform output notebook_console_url`). The notebook has internet access
for the fetch scripts and reaches Neptune over the VPC peering. Its lifecycle
config already installs `neo4j`, `pyyaml` and `requests` into the `python3`
conda env.

1. On your machine, get the Neptune Bolt URI from Terraform:

   ```sh
   cd terraform && terraform output -raw neptune_bolt_uri
   # bolt://<cluster-endpoint>:8182
   ```

2. Open the notebook, then **New → Terminal**, and clone the repo into
   `~/SageMaker` (the only folder that survives a notebook restart):

   ```sh
   cd ~/SageMaker
   git clone https://github.com/lhnminh/MMRF-KnowledgeGraph.git
   cd MMRF-KnowledgeGraph
   git checkout load/opentargets-civic   # until this branch is merged to main
   source activate python3               # Python 3.10+ needed
   pip install -r requirements.txt       # no-op if the lifecycle config already ran
   ```

3. Fetch the four sources into `data/raw/` (gitignored; DisMech data must not
   be committed):

   ```sh
   python ingestion/fetch_monarch.py
   python ingestion/fetch_dismech.py
   python ingestion/fetch_opentargets.py
   python ingestion/fetch_civic.py
   ```

4. Point the loader at Neptune, check the expected counts, and load:

   ```sh
   export NEO4J_URI=bolt://<cluster-endpoint>:8182   # value from step 1
   python ingestion/load_neo4j.py --dry-run
   python ingestion/load_neo4j.py --neptune
   ```

5. Verify. This runs only read queries and prints each node label and
   relationship type next to the count expected from the files in
   `data/raw/`, marking each `ok` or `DIFFERENT`:

   ```sh
   python ingestion/load_neo4j.py --neptune --verify
   ```

   Re-running the load is safe (everything is `MERGE`d), so the counts should
   stay the same. If a count is higher than expected, the graph holds data
   from an earlier load that used different source files.

## Status

Prep only — no AWS sandbox access until the hackathon account is issued
2026-11-03. `terraform/` is a placeholder until then.
