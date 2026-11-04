# Knowledge graph MCP tools

For agents that use the multiple myeloma knowledge graph. There are five read-only tools behind one AgentCore Gateway. The envelope they return is defined in [AD-004](adrs/0004-integration-seam-tool-contract-and-response-types.md).

## Connect

| | |
| --- | --- |
| **URL** | `https://mmrf-kg-gateway-hjyd9sqogd.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp` (also `terraform output gateway_url`) |
| **Protocol** | MCP over HTTPS |
| **Auth** | AWS SigV4, signing service `bedrock-agentcore`, region `us-west-2` |
| **IAM permission** | `bedrock-agentcore:InvokeGateway` on `arn:aws:bedrock-agentcore:us-west-2:548574235556:gateway/mmrf-kg-gateway-hjyd9sqogd` |

Your agent needs no VPC access. Keep the URL in configuration, because recreating the Gateway changes it. Calls have only been tested from within account `548574235556`.

To check your access, run this with your own credentials. It lists the tools and makes one call:

```sh
uv run python scripts/test_gateway.py --gateway-url <URL>
```

## Tools

Over MCP, every name has the target prefix: `kg-graph___kg_search_entities`, and so on.

| Tool | Use it to | Arguments |
| --- | --- | --- |
| `kg_search_entities` | Turn a plain-language term into a graph id. Start here. | `text` (required): a name fragment or exact id. `limit`: 1-200, default 20. |
| `kg_get_phenotypes` | List the phenotypes recorded for a disease (Monarch and DisMech). A phenotype in both has one edge per source. | `disease_id` (required), such as `MONDO:0009693`. `limit`: 1-200, default 50. |
| `kg_get_mechanism_paths` | Get DisMech mechanisms and the causal edges between them, with evidence. | `disease_id` (required). |
| `kg_get_evidence` | Look up the evidence for one edge, or every edge citing a reference. | Exactly one of `edge_id` (from `graph.edges[].id`) or `reference` (such as `PMID:34201396` or `infores:orphanet`). |
| `kg_run_readonly_query` | Ask something no named tool covers. Use this last. | `query` (required): one openCypher read. `parameters`: values for `$name` placeholders. |

### Limits on `kg_run_readonly_query`

- One statement of at most 4,000 characters, and it must `RETURN`.
- No `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, `FOREACH`, `CALL`, `LOAD CSV` or `USING PERIODIC`.
- Variable-length paths need an upper bound of at most 5 hops, such as `[:LEADS_TO*1..3]`.
- Results stop at 100 rows.
- Rows carry provenance only if your query returns those properties.

## Read the response

Each result's `content[0].text` is a JSON string holding this envelope:

```json
{
  "contract_version": "0.2",
  "result_type": "graph",
  "summary": "DisMech records 8 mechanisms and 7 causal edges for Multiple Myeloma (MONDO:0009693).",
  "observations": [{ "text": "DisMech records that A leads to B.", "refs": ["<evidence id>"] }],
  "interpretation": [{ "text": "<curator commentary>", "basis": "source_curator_commentary", "refs": ["<evidence id>"] }],
  "graph": {
    "nodes": [{ "id": "dismech:mechanism:A", "label": "A", "type": "Mechanism", "source_dataset": "dismech", "source_version": "<commit>" }],
    "edges": [{ "id": "dismech:A|leads_to|B", "source": "dismech:mechanism:A", "target": "dismech:mechanism:B", "predicate": "leads_to",
                "edge_origin": "recorded", "source_dataset": "dismech", "source_version": "<commit>", "evidence_ids": ["<evidence id>"] }]
  },
  "evidence": [{ "id": "<evidence id>", "reference": "PMID:34201396", "snippet": "<quoted text>", "source_dataset": "dismech", "source_version": "<commit>" }],
  "query": { "language": "openCypher", "text": "<the query that ran>", "parameters": {} },
  "limitations": ["Only the first evidence item of each DisMech causal edge is loaded into the graph."],
  "trace": { "request_id": "<id>", "plan": [], "tool_calls": [{ "tool": "kg_get_mechanism_paths", "inputs": {} }] }
}
```

`kg_run_readonly_query` returns `result_type: "table"` with an added `table: { "columns": [], "rows": [] }`. Rejected input and failures return `result_type: "text"`.

### Rules for your agent

- **Don't rely on `isError`.** Rejected input and failed queries still report `isError: false`. Read `summary` and `limitations`.
- **Always pass on `limitations`.** They say what the answer can't support.
- **Keep the two kinds of statement apart.** `observations` are what a source record states. `interpretation` is explanation, and is not an established fact.
- **Say "none recorded", never "none exists".** An empty result only describes this graph.
- **Show your sources.** Each edge's `evidence_ids` point to `evidence`, and each edge has a `source_dataset` and `source_version`. See [how citations work](citations.md) for how a `refs` array gets built.
- **Don't give treatment recommendations.** Graph content is published knowledge. It isn't clinical advice and doesn't validate a synthetic Virtual Lab result.

## What the graph contains

54 nodes and 67 relationships for multiple myeloma:

| Source | Version | Provides |
| --- | --- | --- |
| Monarch KG | `2026-10-07` | 43 associations: phenotypes, related diseases and others |
| DisMech | `v0.1.46` (commit `b1ca0b232b815a21b4ba95a62828c9979e9a49e7`) | 8 mechanisms, 7 causal edges and 9 phenotypes |

```text
(:Disease {id, name})-[:ASSOCIATION {predicate, source, source_version, edge_origin, primary_knowledge_source, publications, frequency, description, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Entity or :Disease {id, label, category})
(:Disease)-[:HAS_MECHANISM {source, source_version, edge_origin, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Mechanism {name, description, source, source_version})
(:Mechanism)-[:LEADS_TO {source, source_version, edge_origin, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Mechanism)
```

On `ASSOCIATION`, Monarch edges use `primary_knowledge_source` and `publications`, and DisMech edges use the other citation properties. Filter on `source` to separate them. `publications` is a `;`-separated string. `dismech_explanation` is curator commentary, so report it as interpretation.

**Not available yet:**

- Open Targets and CIViC data, so there are no gene, drug or variant answers.
- `kg_get_gene_context`, which AD-004 plans.
- DisMech's genetic and treatment records. Only its mechanisms and phenotypes are loaded.
- Links between phenotypes and mechanisms. Anemia, for example, is a phenotype with no edge from Bone Marrow Failure.

## Example: what leads to hypercalcemia?

1. `kg_search_entities` with `{"text": "myeloma"}` finds `MONDO:0009693`.
2. `kg_get_mechanism_paths` with `{"disease_id": "MONDO:0009693"}` returns the mechanisms, causal edges and evidence.
3. For a narrower question, use `kg_run_readonly_query`:

   ```json
   {
     "query": "MATCH (a:Mechanism)-[:LEADS_TO*1..2]->(b:Mechanism {name: $target}) RETURN a.name AS cause, b.name AS effect",
     "parameters": { "target": "Hypercalcemia" }
   }
   ```

   This returns Osteolytic Bone Disease and Clonal Plasma Cell Proliferation.
