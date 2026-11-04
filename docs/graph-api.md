# Neptune graph API and explorer

Sandbox deployment: [open the graph explorer](https://c7k3jma2cf.execute-api.us-west-2.amazonaws.com/).
The JSON endpoint is `/api/graph` on the same host. This URL changes if the API is recreated.

The graph explorer is a small, token-protected sandbox application. It reads live
Neptune data through `mmrf-kg-kg-graph`, the existing read-only Lambda. Its own Lambda
has permission to invoke only that function. It does not connect to Neptune or
change the graph, its networking, or the AgentCore Gateway.

The public `/` route serves an empty application shell. `/api/graph` requires an
`Authorization: Bearer <token>` header. The page keeps the token in memory, clears
the input after connecting, and never puts it in a URL, local storage, or a log.
This shared sandbox token is not a production user-management system.

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

The stack creates one HTTP API, one Lambda, a scoped IAM role/policy, short-lived
logs, and a generated token stored as an SSM SecureString. Lambda stores only the
token's SHA-256 hash. API requests have best-effort throttling of 5 requests/second
with a burst of 20. No CORS access is enabled; the page uses the same origin.

Terraform state contains the generated token. State, plans, and deployment zips
are ignored by Git; keep the local state private and available for teardown.
Commit source files and `.terraform.lock.hcl`, not state or plans.

## Open the graph

Open the `graph_url` output. On macOS, copy the token to your clipboard without
printing it in a terminal or chat:

```sh
aws ssm get-parameter --name /mmrf-kg-graph-api/access-token \
  --with-decryption --query Parameter.Value --output text \
  --profile mmrf-hackathon --region us-west-2 | pbcopy
```

Paste into **Graph access token** and select **Connect**. The initial clinical
view focuses on bone, kidney, calcium, and anemia features plus mechanisms. Choose
**All recorded relationships** for the full returned disease subset, or a view
for phenotypes, mechanisms, genetic factors, or recorded treatments/drugs.

Combine source, entity type, relationship, and node-text filters. Type and text
filters retain a matching node's relationship endpoints as context. Selecting a
node lists its visible connections; selecting an arrow shows its original
identifier, predicate, source version, and references. PubMed citations link to
the publication. The keyboard-accessible node list offers the same inspection.

**Refresh** queries Neptune again. **Fit** resets pan/zoom. **Edge labels** shows
predicates. **Download JSON** saves the currently returned graph and its metadata.
Disconnect clears the token and graph; reloading requires entering the token again.

## HTTP contract

```http
GET /api/graph?view=phenotypes&source=monarch&q=anemia
Authorization: Bearer <token>
```

| Parameter | Meaning |
| --- | --- |
| `disease_id` | Only `MONDO:0009693` is supported; this is the default. |
| `view` | `all` (API default), `clinical`, `phenotypes`, `mechanisms`, `genetics`, `treatments`. |
| `source` | Exact dataset name, such as `monarch` or `dismech`. |
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

The adapter sends a fixed parameterized query to `kg_run_readonly_query`. Callers
cannot supply queries, tools, traversal depths, or arbitrary disease IDs. It reads
outgoing disease relationships and a second hop limited to `LEADS_TO` / `HAS_AGENT`.
This is a bounded disease subset, not every record in Neptune. It currently covers
Monarch and DisMech; Open Targets and CIViC appear only after a compatible ingestion
and adapter update. Source/relationship filters are applied in the Lambda to the
bounded live result, not to an unrestricted graph traversal.

The existing tool caps results at 100 rows. The API reports upstream truncation and
the requested output limit; it does not silently promise complete large graphs.
Citation metadata is returned without article snippets, DisMech descriptions, or
curator commentary. Keep this a restricted prototype; confirm source data terms
before broader distribution. See [data terms](data-terms.md).

HTTP errors are `400` for invalid filters, `401` for missing/invalid tokens, and
`502` for upstream failures. Unknown routes are unavailable. An empty graph is a
successful `200` with an explicit limitation. Failures clear stale data in the page.

## Test and demonstrate

```sh
uv run python -m pytest tests/test_graph_api.py -q
uv run ruff check graph_api tests/test_graph_api.py
node --check graph_api/static/app.js
```

For an authenticated live smoke test without exposing the token:

```sh
uv run python scripts/test_graph_api.py \
  --url "$(terraform -chdir=terraform/graph-api output -raw graph_url)"
```

Demonstrate the clinical view, select an arrow and open a citation, then filter to
Monarch. Reset and choose genetic factors; search for `TP53`. Finally choose
recorded treatments to inspect the recorded drug relationships. These are source
records, not diagnoses, recommendations, or synthetic cohort observations.

## Rotate the token

```sh
terraform -chdir=terraform/graph-api plan \
  -replace=random_password.access_token -out=graph-api.tfplan
terraform -chdir=terraform/graph-api apply graph-api.tfplan
```

Retrieve the replacement from SSM. Existing tokens stop working after the Lambda
update; reconnect with the new one.

## Teardown

```sh
terraform -chdir=terraform/graph-api plan -destroy -out=graph-api.tfplan
terraform -chdir=terraform/graph-api apply graph-api.tfplan
```

This removes the explorer, API, token, role, and its logs. Neptune and the existing
knowledge tools remain deployed. Do not use the parent stack's teardown for this.

Implementation references: [AWS HTTP API Lambda integrations](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html),
[HTTP API throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html),
and [Cytoscape.js](https://js.cytoscape.org/). Cytoscape.js 3.33.4 is bundled locally
with its MIT license; its npm tarball integrity was verified at installation.
