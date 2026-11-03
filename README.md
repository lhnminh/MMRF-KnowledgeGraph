# ORION — Multiple Myeloma research prototype

One workflow: ask about TP53 → resolve graph entities → retrieve connections and publication evidence → query the same synthetic cohort through Guppy and ProteinPaint → render the existing Virtual Lab bar chart → ask a contextual follow-up.

The AWS implementation uses **Bedrock Converse**, **AgentCore Runtime**, and **Neptune openCypher**. A clearly labeled **scripted rehearsal** with a local JSON graph is also provided. Rehearsal does not call a foundation model and does not satisfy the AWS demonstration acceptance gate.

The frontend is an overlay on the sandbox-pinned Virtual Lab revision `27cf09aca01a496b9d52f1b6a14e6859bf530fbb`, not a replacement UI. [sources.json](sources.json) also pins the sandbox. No real patient data, customer credentials, or customer infrastructure is needed.

## Start the local demonstration

Requirements: Python 3.12+, uv, Git, Docker Compose 2.17+, and the upstream sandbox's 12 GiB Docker memory / 30 GiB free disk. The sandbox frontend requires Node 24.18.1+ and npm 11.16.0+ when building outside Docker.

```sh
uv sync --group dev --python 3.12
uv run python scripts/transform.py --curated-only
uv run python scripts/validate.py
uv run python scripts/bootstrap.py
export ORION_ROOT="$PWD"
cd vendor/sandbox
python3 synthetic/generate.py
docker compose -f compose.yaml -f "$ORION_ROOT/deploy/compose.orion.yaml" build
docker compose -f compose.yaml -f "$ORION_ROOT/deploy/compose.orion.yaml" up -d --wait --wait-timeout 300
```

Open **http://localhost:3080/orion**. Select “Female synthetic cases” and click the TP53 example. Inspect the graph path, evidence records, tool statuses and interactive chart. Save the returned selection using the existing Virtual Lab modal. Click “Show related genes with similar phenotypes.”

The packaged graph contains a handful of original public-fact paraphrases, publication URLs, gene/protein identifiers, disease phenotypes and pathway connections. The actual sandbox supplies the cohort measurements. If it is unavailable, ORION reports the tool error and shows no invented chart or cohort result.

`bootstrap.py` verifies the frontend pin and refuses conflicting ORION files. Once the overlay is applied, use Compose directly; upstream `scripts/start.sh` correctly rejects modified source checkouts. Keep durable frontend changes in `frontend/overlay/`, then submit them to the frontend repository when desired.

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

Raw inputs and transformed outputs are ignored by Git. The Docker build includes **only** curated public facts, not raw DisMech/Monarch files. DisMech YAML data terms remain unconfirmed; a rendered page's license does not establish the YAML's redistribution terms. See [data terms and attribution](docs/data-terms.md).

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
```

These fetchers save source snapshots. The current `scripts/transform.py` and
CSV loaders do not yet import Open Targets or CIViC, so downloading these
files does not add their records to Neo4j or Neptune.

Every gene in the Open Targets and CIViC outputs carries `hgnc_symbol`, so
the two sources can be joined on gene symbol. Both outputs also record
`graph_disease_id: MONDO:0009693`; future transformation into the graph
should use `graph_disease_id` rather than each source's native disease ID
(`MONDO_0009693` / `DOID:9538`). Each fetch saves the exact API reply next to
the processed JSON (`*.raw*.json`) with its sha256, fetch timestamp (UTC),
source version and license, so a snapshot can be verified later even though
the APIs are live. Preserve PMIDs and clinical-trial links where provided
so provenance can be shown per relationship in the agent's answers.

## AWS deployment and acceptance

To deploy **only Neptune** and load the existing ETL export without EKS, follow [the Neptune-only setup](docs/neptune.md). It provisions a private database and a VPC-connected bulk-loader Lambda using `terraform/neptune-only`.

Follow [deployment instructions](docs/deployment.md) to deploy the upstream synthetic sandbox to EKS, provision private Neptune and AgentCore using Terraform, upload CSVs to same-Region S3, invoke the bulk loader from a VPC-connected Lambda, and install the server-side invocation bridge.

The AWS Definition of Done is **not established by unit tests**. Run the live smoke test with `--require-aws`, then perform the browser checklist in [demo and verification](docs/demo.md). No AWS resources have been deployed by this implementation session.

```sh
uv run pytest
node --test frontend/tests/*.test.mjs
uv run ruff check src scripts tests terraform/utility.py
terraform -chdir=terraform fmt -check
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Tests cover the compound question through the actual Bedrock tool-use protocol with a mocked model/API, contextual follow-ups, streaming HTTP contract, bounded/parameterized queries, provenance-preserving exports, empty selections, and normalized API failures. They do not establish scientific validity or live AWS connectivity.

## Scope and teardown

Graph tools accept structured arguments, cap paths at three hops, bound results and apply a server query timeout. The model cannot submit query text or arbitrary endpoints. The API adapter supports current synthetic case selections and gender filters; unsupported filters fail explicitly. Cohort storage remains in existing frontend state. History is sent as up to six complete prior turns; it is not a durable research record.

No clinical diagnosis or treatment recommendations. Shared disease associations are not evidence of identical gene-level phenotypes. The subset intentionally reports that limitation for the example follow-up.

For local teardown, run the same two-file Compose command with `down`; add `--volumes` to delete generated indexes. For AWS, remove the ORION bridge, empty only the ORION graph prefix and ECR repository, destroy the ORION Terraform stack, then use the upstream sandbox teardown procedure. Full commands are in [deployment instructions](docs/deployment.md).
