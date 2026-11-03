# MMRF ORION: Workstream 1 Knowledge Graph

A myeloma knowledge graph in Amazon Neptune, exposed to agents as named tools through AgentCore Gateway. Each tool returns where its answer came from, the supporting subgraph, and any limitations. The design decisions are in [docs/adrs](docs/adrs).

No real patient data, customer credentials, or customer infrastructure is needed.

## Graph pipeline

To explore the ETL export in local Neo4j Browser:

```sh
uv run python scripts/transform.py
docker compose up -d neo4j
docker compose --profile tools run --build --rm loader
```

Open http://localhost:7474 and connect to `bolt://localhost:7687` with user `neo4j` and password `password`. Run `MATCH p=(n:Entity)-[:RELATIONSHIP]->(m:Entity) RETURN p LIMIT 100;` and select the graph result view. For TP53, run `MATCH p=(g:Gene {id:'HGNC:11998'})-[:RELATIONSHIP*1..2]-(d:Disease {id:'MONDO:0009693'}) RETURN p LIMIT 20;`. The local CSV loader preserves the Neptune schema and provenance, and repeated imports merge the same IDs. It adds/updates the exported records without clearing other graph data. This local preview does not load AWS Neptune.

The Compose loader connects inside the Docker network using `bolt://neo4j:7687`. Use it if host-side loopback access is blocked by Zscaler or another local proxy.

```sh
# Optional bounded public Monarch refresh (9 association calls, no full KG import):
uv run python scripts/extract.py
# Use the already downloaded Monarch file if present; preserve raw records and IDs:
uv run python scripts/transform.py
# Optional local-only DisMech YAML transformation after checking its data terms:
uv run python scripts/transform.py --include-dismech
uv run python scripts/validate.py
```

Outputs are `data/transformed/nodes.csv`, `relationships.csv`, and `graph.json`. Only the two CSV files go into the Neptune loader prefix. Original predicates, source records, all evidence entries, source identifiers, hashes, retrieval dates and review/confidence status are retained. Missing retrieval dates from older downloads are explicitly `unknown`; transformation time is recorded separately. DisMech-generated mechanism IDs are deterministic and namespaced because those source steps have no ontology IDs.

Raw inputs and transformed outputs are ignored by Git. DisMech YAML data terms remain unconfirmed; a rendered page's license does not establish the YAML's redistribution terms. See [data terms and attribution](docs/data-terms.md).

## Additional public source downloads

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

```sh
uv run python ingestion/fetch_opentargets.py  # -> data/raw/opentargets_mm.json (+ .raw.json)
uv run python ingestion/fetch_civic.py        # -> data/raw/civic_mm.json (+ .raw.N.json)
uv run python scripts/transform.py
uv run python scripts/validate.py
docker compose --profile tools run --build --rm loader
```

Transformation automatically includes these snapshots when present, verifies
the raw-response checksums, and exports them with the existing curated and
Monarch records. `--curated-only` excludes all optional raw snapshots. The
CSV loader merges the new IDs into the local Neo4j graph. See the additional
queries in [Neo4j examples](docs/neo4j-queries.cypher) to explore them.

Open Targets association scores are stored as `association_score`, separate
from confidence. Drugs are linked using `opentargets:drug_or_clinical_candidate`,
with the disease-specific `clinical_stage` and clinical-report URLs; this is
not a claim of approval or efficacy. Mechanism nodes link drugs to targets.

CIViC evidence nodes retain `ACCEPTED` versus `SUBMITTED` review status,
evidence type/level/direction, significance, publication links, the complete
molecular profile, its variants (including both fusion partners), and the
mentioned therapies. Rejected items are excluded. Evidence remains attached
to its molecular profile, including multi-variant and combination-therapy
context; it is not projected as a general gene–disease or treatment claim.

Every gene in the Open Targets and CIViC outputs carries `hgnc_symbol`, so
the two sources can be joined on gene symbol. Both outputs also record
`graph_disease_id: MONDO:0009693`; transformation uses this shared ID rather than each source's native disease ID
(`MONDO_0009693` / `DOID:9538`). Each fetch saves the exact API reply next to
the processed JSON (`*.raw*.json`) with its sha256, fetch timestamp (UTC),
source version and license, so a snapshot can be verified later even though
the APIs are live. Approved gene symbols join the two sources to existing
genes; native Ensembl, Entrez and HGNC identifiers remain in aliases and
original records. PMIDs and clinical-trial links are retained on the
relationships alongside source version, retrieval time, license and hashes.

## AWS

Everything runs in **us-west-2** from one Terraform stack in `terraform/`: a private VPC with no NAT or internet gateway, Neptune (`mmrf-kg-cluster`), AgentCore Gateway and its logs, and two Lambdas:

| Lambda | Job | Neptune access |
| --- | --- | --- |
| `mmrf-kg-kg-graph` | Serves the `kg_*` tools through Gateway | Read only |
| `mmrf-kg-ingest` | Reads the raw source files from S3 and writes them to Neptune | Read and write |

Nothing outside the VPC connects to Neptune directly.

### Deploy

Terraform deploys whatever zip it finds, so rebuild first. **Read the plan before applying, and stop if it says Neptune "must be replaced" or "forces replacement": that deletes the database and its data, with no snapshot.**

```sh
uv run python scripts/build_lambda.py
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform apply tfplan
```

### Load the core graph

`b1ca0b232b815a21b4ba95a62828c9979e9a49e7` is the commit for DisMech release `v0.1.46`, the pinned release in AD-001. The loader refuses files with no recorded source version, and it is safe to rerun because every write is a `MERGE`.

```sh
uv run python ingestion/fetch_monarch.py
uv run python ingestion/fetch_dismech.py --revision b1ca0b232b815a21b4ba95a62828c9979e9a49e7

BUCKET=$(terraform -chdir=terraform output -raw ingest_bucket)
FUNCTION=$(terraform -chdir=terraform output -raw ingest_function)
aws s3 cp data/raw/ "s3://$BUCKET/raw/" --recursive --exclude "*" \
  --include "monarch_mm.json" --include "dismech_mm.yaml" --include "dismech_mm.yaml.provenance.json"
aws lambda invoke --function-name "$FUNCTION" --cli-read-timeout 300 response.json
cat response.json
```

The response reports the graph's node and relationship counts and the versions loaded. To load the same files into local Neo4j instead, run `uv run python ingestion/load_neo4j.py` (or `docker compose up -d neo4j` first if it isn't running).

Open Targets and CIViC are not loaded into Neptune yet. The ETL above can export them, but they total about 12 MB, so when they are needed, revisit whether row-by-row writes are enough or the Neptune bulk loader is the better fit.

## Tests

```sh
uv run pytest
uv run ruff check src scripts tests ingestion/load_neo4j.py terraform/lambda_build/handler.py
```

Tests cover provenance-preserving exports and the ingest loader. They do not establish scientific validity or live AWS connectivity.

## Teardown

Neptune bills while idle. `scripts/teardown.sh` destroys the stack, including Neptune's data with no final snapshot. It needs the Terraform state.

No clinical diagnosis or treatment recommendations.
