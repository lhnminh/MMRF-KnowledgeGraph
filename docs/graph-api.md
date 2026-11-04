# Neptune graph API and explorer

Sandbox deployment: [open the graph explorer](https://c7k3jma2cf.execute-api.us-west-2.amazonaws.com/).
The JSON endpoint is `/api/graph` on the same host. This URL changes if the API is recreated.

The graph explorer is a public, read-only sandbox application. It reads live
Neptune data through `mmrf-kg-kg-graph`, the existing read-only Lambda. Its own Lambda
has permission to invoke only that function. It does not connect to Neptune or
change the graph, its networking, or the AgentCore Gateway.

The `/` route opens the viewer and automatically loads graph data from `/api/graph`.
Both routes are public and require no bearer token or AWS credentials. Anyone
with the URL can read the exposed public biological graph subset. The API accepts
only the documented filters; it has no write or arbitrary-query endpoint.

## Deploy

Use the independent root `terraform/graph-api`; do not apply the parent Neptune
stack to deploy this API. Defaults select profile `mmrf-hackathon`, account
`548574235556`, and region `us-west-2`.

```sh
terraform -chdir=terraform/graph-api init
terraform -chdir=terraform/graph-api validate
terraform -chdir=terraform/graph-api plan -out=graph-api.tfplan
terraform -chdir=terraform/graph-api apply graph-api.tfplan
terraform -chdir=terraform/graph-api output -raw graph_url
```

The stack creates one HTTP API, one Lambda, a scoped IAM role/policy, and logs.
API requests have best-effort throttling of 5 requests/second
with a burst of 20. No CORS access is enabled; the page uses the same origin.

State, plans, and deployment zips
are ignored by Git; keep the local state private and available for teardown.
Commit source files and `.terraform.lock.hcl`, not state or plans.

## Open the graph

Open the `graph_url` output; the graph loads automatically. To retrieve JSON:

```sh
curl 'https://c7k3jma2cf.execute-api.us-west-2.amazonaws.com/api/graph?source=opentargets'
```

The initial clinical view focuses on bone, kidney, calcium, and anemia features plus mechanisms. Choose
**All recorded relationships** for the full returned disease subset, or a view
for phenotypes, mechanisms, genetic factors, or recorded treatments/drugs.

The source selector includes **Open Targets** when drug-target records are loaded
for the displayed disease. Selecting it from a clinical/phenotype view switches
to **Recorded treatments & drugs**. Teal arrows represent the recorded drug-to-gene
targets, with Open Targets drug pages, source versions, action types, mechanisms
of action, and supporting reference identifiers available in the details panel.
These target records are a separate source from DisMech's treatment/trial evidence.

Combine source, entity type, relationship, and node-text filters. Type and text
filters retain a matching node's relationship endpoints as context. Selecting a
node lists its visible connections; selecting an arrow shows its original
identifier, predicate, source version, and references. PubMed citations link to
the publication. The keyboard-accessible node list offers the same inspection.

**Refresh** queries Neptune again. **Fit** resets pan/zoom. **Edge labels** shows
predicates. **Download JSON** saves the currently returned graph and its metadata.
Reloading the page fetches current data automatically.

## HTTP contract

```http
GET /api/graph?view=phenotypes&source=monarch&q=anemia
```

| Parameter | Meaning |
| --- | --- |
| `disease_id` | Only `MONDO:0009693` is supported; this is the default. |
| `view` | `all` (API default), `clinical`, `phenotypes`, `mechanisms`, `genetics`, `treatments`. |
| `source` | Exact dataset name, such as `monarch`, `dismech`, or `opentargets`. |
| `entity_type` | Exact returned type, such as `Gene` or `PhenotypicFeature`; matches either endpoint. |
| `predicate` | Exact recorded predicate, such as `biolink:has_phenotype`. |
| `q` | Case-insensitive substring of a node's name or identifier. |
| `node_id` | Exact node ID; returns its incident edges within this subset. |
| `limit` | 1–100 edges; default 100. Clipping is explicitly reported. |

The response has `graph.nodes`, `graph.edges`, `evidence`, `facets`, `limitations`,
`meta`, and the upstream tool `trace`. `meta.fetched_at` records response time;
it is not a source's retrieval date. Existing retrieval dates, confidence, review
status, and synthetic flags are preserved when present. Missing values are not
invented. Node provenance inferred from adjacent relationships is labelled with
`basis: "relationship"`. Evidence/source links are resolved from recorded citation
identifiers and source versions.

`original_id` is populated only if the relationship stores its own identifier.
Otherwise `tool_edge_id` follows the existing graph tool's deterministic
dataset/endpoint/predicate convention, and `identifier_origin` explicitly marks
it as derived. `id` is a stable visualization key, not a new biological identifier.

The adapter sends fixed parameterized queries to `kg_run_readonly_query`. Callers
cannot supply queries, tools, traversal depths, or arbitrary disease IDs. It reads
outgoing disease relationships and a second hop limited to `LEADS_TO` / `HAS_AGENT`.
An additional fixed path follows disease → recorded DisMech treatment → agent →
Open Targets `TARGETS_GENE` relationships, using at most two 100-row pages ordered
by the agent and gene IDs. This is a bounded Multiple Myeloma subset, not every
record in Neptune. It covers Monarch, DisMech, and loaded Open Targets drug targets;
CIViC needs a compatible ingestion and adapter update. Source/relationship filters
are applied to the combined bounded result before the requested display limit,
so genes on the second target page remain searchable. Facets include all fetched
sources even when a particular view or output limit omits some edges.

The existing tool caps each response at 100 rows. The adapter fetches at most 300
rows across the base query and target pages, then displays at most 100 filtered
relationships. It preserves request IDs/tool-call traces for every page and
reports unresolved upstream truncation and the requested output limit; it does
not silently promise complete large graphs.
Citation metadata is returned without article snippets, DisMech descriptions, or
curator commentary. Keep this a restricted prototype; confirm source data terms
before broader distribution. See [data terms](data-terms.md).

HTTP errors are `400` for invalid filters and
`502` for upstream failures. Unknown routes are unavailable. An empty graph is a
successful `200` with an explicit limitation. Failures clear stale data in the page.

## Test and demonstrate

```sh
uv run python -m pytest tests/test_graph_api.py -q
uv run ruff check graph_api tests/test_graph_api.py
node --check graph_api/static/app.js
node --test tests/test_graph_viewer.mjs
```

For a live smoke test without credentials:

```sh
uv run python scripts/test_graph_api.py \
  --url "$(terraform -chdir=terraform/graph-api output -raw graph_url)"
```

Demonstrate the clinical view, select an arrow and open a citation, then filter to
Monarch. Reset and choose genetic factors; search for `TP53`. Finally choose
recorded treatments and select Open Targets to inspect drug-to-gene targets and
their evidence links. These are source
records, not diagnoses, recommendations, or synthetic cohort observations.

## Upgrade from the token-protected viewer

Review the plan in this independent root. Removing the old token resources deletes
the SSM access-token parameter and Terraform's generated password, and removes
`ACCESS_TOKEN_SHA256` from the API Lambda environment. The updated viewer has no
token controls. Old local Terraform state backups may retain the retired secret;
keep state, backups, and plans private and out of Git.

## Teardown

```sh
terraform -chdir=terraform/graph-api plan -destroy -out=graph-api.tfplan
terraform -chdir=terraform/graph-api apply graph-api.tfplan
```

This removes the explorer, API, role, and its logs. Neptune and the existing
knowledge tools remain deployed. Do not use the parent stack's teardown for this.

Implementation references: [AWS HTTP API Lambda integrations](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html),
[HTTP API throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html),
and [Cytoscape.js](https://js.cytoscape.org/). Cytoscape.js 3.33.4 is bundled locally
with its MIT license; its npm tarball integrity was verified at installation.
