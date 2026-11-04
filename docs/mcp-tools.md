# Knowledge graph MCP tools

For agents that use the multiple myeloma knowledge graph. There are eight read-only tools behind one AgentCore Gateway. The envelope they return is defined in [AD-004](adrs/0004-integration-seam-tool-contract-and-response-types.md).

## Connect

| | |
| --- | --- |
| **URL** | `https://mmrf-kg-gateway-hjyd9sqogd.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp` (also `terraform output gateway_url`) |
| **Protocol** | MCP over HTTPS. After `initialize`, send the `MCP-Protocol-Version` header with the version it returned, or the Gateway answers HTTP 400. MCP client libraries normally do this for you. |
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
| `kg_get_gene_context` | See everything recorded about one human gene, per source: Monarch gene-disease associations, DisMech genetic factors and the subtypes the gene defines. Says "none recorded" for each loaded source with nothing. | `gene` (required): an HGNC id such as `HGNC:6407` or an approved symbol such as `KRAS`. `limit`: 1-200, default 50. |
| `kg_get_gene_disease_association` | Get the link between one gene and one disease from each source: the Monarch association (relationship, knowledge source, disease id), the DisMech genetic factor and the subtype the gene defines. With a `variant`, says whether anything exists at that specificity. | `gene` and `disease_id` (both required). `variant`: optional, such as `KRAS G12D`. |
| `kg_get_treatment_evidence` | Get a disease's recorded treatments with their agents, the mechanism each acts on (with that link's evidence and the mechanism's place in the disease record), and the cited evidence text. Lists treatments with no mechanism link and agents with no recorded target. | `disease_id` (required). `treatment`, `mechanism`: optional name filters, such as `trametinib` or `RAS/MAPK`. |
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

Every tool declares an `outputSchema` matching this shape, so a client that negotiates MCP protocol version `2025-06-18` or later should also get the envelope back as `structuredContent`, avoiding a second JSON parse of `content[0].text`. As of writing, the Gateway itself still negotiates protocol version `2025-03-26` (which predates `outputSchema`/`structuredContent`), so `structuredContent` isn't populated yet — only `content[0].text`. This should start working with no changes on our side once AgentCore Gateway updates its MCP protocol support.

### Rules for your agent

- **Don't rely on `isError`.** Rejected input and failed queries still report `isError: false`. Read `summary` and `limitations`.
- **Always pass on `limitations`.** They say what the answer can't support.
- **Keep the two kinds of statement apart.** `observations` are what a source record states. `interpretation` is explanation, and is not an established fact.
- **Say "none recorded", never "none exists".** An empty result only describes this graph.
- **Show your sources.** Each edge's `evidence_ids` point to `evidence`, and each edge has a `source_dataset` and `source_version`. See [how citations work](citations.md) for how a `refs` array gets built.
- **Don't give treatment recommendations.** Graph content is published knowledge. It isn't clinical advice and doesn't validate a synthetic Virtual Lab result.

## What the graph contains

111 nodes and 138 relationships for multiple myeloma (MM) and juvenile myelomonocytic leukemia (JMML, `MONDO:0011908`):

| Source | Version | Provides |
| --- | --- | --- |
| Monarch KG | `2026-10-07` | MM: 43 associations (phenotypes, related diseases and others). JMML: 5, including the gene-level KRAS association (`HGNC:6407`, Orphanet) |
| DisMech | `v0.1.46` (commit `b1ca0b232b815a21b4ba95a62828c9979e9a49e7`) | MM: 8 mechanisms, 7 causal edges, 9 phenotypes, 3 genetic factors, and 6 treatments with 11 named drugs. JMML: 5 mechanisms, 5 subtypes with their driver genes, 7 genetic factors, 3 treatments each linked to a mechanism |

```text
(:Disease {id, name})-[:ASSOCIATION {predicate, source, source_version, edge_origin, primary_knowledge_source, publications, record_name, frequency, association_type, ontology_term, therapeutic_modality, description, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Entity or :Disease {id, label, category})
(:Entity {category: 'biolink:Treatment'})-[:HAS_AGENT {source, source_version, edge_origin}]->(:Entity {category: 'biolink:Drug'})
(:Entity {category: 'biolink:Gene'})-[:ASSOCIATION {predicate, source: 'monarch', ...}]->(:Disease)   // Monarch gene-disease: the gene is the subject
(:Entity {subtype})-[:HAS_DRIVER_GENE {source, source_version, edge_origin, association_type}]->(:Entity {gene})
(:Entity {treatment})-[:TARGETS_MECHANISM {source, source_version, edge_origin, treatment_effect, description, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Mechanism)
(:Disease)-[:HAS_MECHANISM {source, source_version, edge_origin, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Mechanism {name, description, source, source_version})
(:Mechanism)-[:LEADS_TO {source, source_version, edge_origin, evidence_pmid, evidence_snippet, dismech_explanation}]->(:Mechanism)
```

On `ASSOCIATION`, Monarch edges use `primary_knowledge_source` and `publications`, and DisMech edges use the other citation properties. Filter on `source` to separate them. `publications` is a `;`-separated string. `dismech_explanation` and DisMech's `description` on a `Mechanism` or `ASSOCIATION` are curator commentary, so report them as interpretation.

**Not available yet:**

- Open Targets and CIViC data, so there are no target or variant answers. Genes and drugs are limited to what Monarch and DisMech record for the two loaded diseases.
- Variant-level records. Associations are at gene level, so `kg_get_gene_disease_association` reports a requested variant as a change in specificity.
- A molecular target for a drug. DisMech names targets such as MEK1/2 only in descriptions, which are text rather than edges, so the tools report "no molecular target recorded".
- Trial results as structured data. Population and response appear only in the quoted evidence snippet.
- Only the loaded diseases are covered. Anything else is "none recorded" until it is loaded (`ingestion/fetch_*.py`, then the `sources` argument of `kg-ingest`).
- JMML mechanism nodes that are really phenotypes. Seven `Mechanism` nodes (such as Anemia and Splenomegaly) exist only because a JMML mechanism lists them as a downstream step, and they have no source or description.
- DisMech's cell types, biological processes and locations for each mechanism.
- Links between phenotypes and mechanisms. Anemia, for example, is a phenotype with no edge from Bone Marrow Failure. `kg_get_mechanism_paths` lists every step its mechanism descriptions state without an edge in `limitations`, beginning `Not a recorded edge:`.

## Example: from a gene to a drug in JMML

1. `kg_get_gene_disease_association` with `{"gene": "KRAS", "disease_id": "MONDO:0011908", "variant": "KRAS G12D"}` returns the Monarch association between `HGNC:6407` and JMML (relationship `gene_associated_with_condition`, source `infores:orphanet`) and DisMech's KRAS-mutated subtype. `limitations` says the record is at gene level, not specific to the variant.
2. `kg_get_mechanism_paths` with `{"disease_id": "MONDO:0011908"}` returns the DisMech JMML mechanisms, including RAS/MAPK Pathway Hyperactivation.
3. `kg_get_treatment_evidence` with `{"disease_id": "MONDO:0011908", "treatment": "trametinib"}` returns trametinib, the mechanism it inhibits, and the phase II trial text (PMID:38867349) in `evidence`. `limitations` says no molecular target is recorded and the trial result is text only.

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

## Example: which treatments and drugs are recorded?

There is no named tool for this yet, so use `kg_run_readonly_query`. Change the predicate to `dismech:has_genetic_factor` and drop the `OPTIONAL MATCH` for genetic factors. Report these as recorded knowledge, not recommendations.

```json
{
  "query": "MATCH (:Disease {id: $id})-[r:ASSOCIATION {predicate: 'dismech:has_recorded_treatment'}]->(t) OPTIONAL MATCH (t)-[:HAS_AGENT]->(a) RETURN t.label AS treatment, r.evidence_pmid AS pmid, r.source_version AS version, collect(a.label) AS agents ORDER BY treatment",
  "parameters": { "id": "MONDO:0009693" }
}
```

To see one record's citation and curator commentary, call `kg_get_evidence` with `edge_id`, such as `dismech:MONDO:0009693|dismech:has_recorded_treatment|dismech:treatment:CAR-T Cell Therapy`.
