# How citations work

How a claim in a tool response (`docs/mcp-tools.md`) gets from a source record to a `refs` array an agent can show. Envelope shape is defined in [AD-004](adrs/0004-integration-seam-tool-contract-and-response-types.md).

## 1. Source record → Neptune property

Citations are stored as plain properties on the relationship they support, not as separate nodes:

| Relationship | Properties | Set by |
| --- | --- | --- |
| `(:Disease)-[:ASSOCIATION {source: 'monarch'}]->(:Entity\|:Disease)` | `publications`, `primary_knowledge_source` | `load_monarch` in `ingestion/load_neo4j.py` |
| `(:Disease)-[:ASSOCIATION {source: 'dismech'}]->(:Entity)` | `evidence_pmid`, `evidence_snippet`, `dismech_explanation` | `load_dismech`, from the phenotype's own `evidence[0]` |
| `(:Disease)-[:HAS_MECHANISM]->(:Mechanism)` | `evidence_pmid`, `evidence_snippet`, `dismech_explanation` | `load_dismech`, from the step's own `evidence[0]` |
| `(:Mechanism)-[:LEADS_TO]->(:Mechanism)` | `evidence_pmid`, `evidence_snippet`, `dismech_explanation` | `load_dismech`, from that `downstream[].evidence[0]` |

All DisMech relationships pull from the same shape in the raw YAML (`evidence: [{reference, snippet, explanation}]`) — a phenotype's own evidence for `ASSOCIATION`, a mechanism's own evidence for `HAS_MECHANISM`, a causal edge's evidence for `LEADS_TO`. Only the first evidence item is loaded for any of them. `kg_get_mechanism_paths` reports this as a limitation for causal edges; today every DisMech phenotype and causal edge has only one item, so only Immune Dysregulation's second citation is left out.

## 2. Neptune property → evidence id

`terraform/lambda_build/handler.py` queries every relationship type through one of three fixed column sets (`ASSOCIATION_COLUMNS`, `HAS_MECHANISM_COLUMNS`, `LEADS_TO_COLUMNS`), each aliasing its own citation properties into the same row shape: `pmid`, `snippet`, `explanation`, `publications`, `primary_knowledge_source`. That lets one function, `add_edge_row`, build citations the same way regardless of which Cypher pattern produced the row.

`add_edge_row` turns each row's citation fields into one or more evidence ids, deduplicated by id in `Envelope.add_evidence`:

- Rows with a `pmid` (all DisMech rows): one evidence id, e.g. `dismech:Clonal Plasma Cell Proliferation|leads_to|Bone Marrow Failure#PMID:34201396`.
- Other `association` rows (Monarch): one evidence id per entry in `publications` (or `primary_knowledge_source` if `publications` is empty), e.g. `monarch:MONDO:0009693|biolink:has_phenotype|HP:0004341#infores:orphanet`.

Each id is registered once in `env.evidence` as `{id, reference, snippet, source_dataset, source_version}`.

## 3. Evidence id → claim

Every entry in `observations[]` and `interpretation[]` carries a `refs: [...]` array of evidence ids — this is the actual citation on the claim. `interpretation` entries also carry `basis: "source_curator_commentary"`, since DisMech's `explanation` field is curator-added reasoning, not a graph-encoded fact, and must not be shown as equivalent to an `observations` claim. DisMech's mechanism and phenotype descriptions go here too, because they often explain a cause ("anemia due to ...") that no edge records.

The final envelope exposes `evidence` as a flat array (the lookup table) and `refs` arrays that point into it by id, so an agent can render "claim — sourced from PMID:x, snippet: '...'" without a second graph query.

## 4. Self-reporting missing citations

If a relationship produces zero evidence ids, `Envelope.build` appends a `limitations` entry naming that edge (`"No evidence reference is recorded for: <edge id>."`) rather than silently returning an uncited claim. This is how a gap in citation coverage surfaces on its own — check `limitations` first when auditing a tool response, before reading every `observations` entry by hand.

## Worked example

`kg_get_mechanism_paths` for `MONDO:0009693` returns, among others:

```json
{
  "observations": [
    { "text": "DisMech lists Bone Marrow Failure as a pathophysiology mechanism of Multiple Myeloma.",
      "refs": ["dismech:Multiple Myeloma|has_mechanism|Bone Marrow Failure#PMID:34201396"] },
    { "text": "DisMech records that Clonal Plasma Cell Proliferation leads to Bone Marrow Failure.",
      "refs": ["dismech:Clonal Plasma Cell Proliferation|leads_to|Bone Marrow Failure#PMID:34201396"] }
  ],
  "interpretation": [
    { "text": "Anemia from bone marrow failure is a defining feature (CRAB criteria) of myeloma, as confirmed by this comprehensive review.",
      "basis": "source_curator_commentary",
      "refs": ["dismech:Clonal Plasma Cell Proliferation|leads_to|Bone Marrow Failure#PMID:34201396"] }
  ],
  "evidence": [
    { "id": "dismech:Clonal Plasma Cell Proliferation|leads_to|Bone Marrow Failure#PMID:34201396",
      "reference": "PMID:34201396",
      "snippet": "Patients with MM usually present with hypercalcemia, anemia, renal damage, increased risk for infections, and pathological fracture secondary to osteolytic bone destruction",
      "source_dataset": "dismech", "source_version": "b1ca0b232b815a21b4ba95a62828c9979e9a49e7" }
  ]
}
```
