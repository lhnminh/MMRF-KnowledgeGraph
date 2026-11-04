# Complete DisMech treatment evidence

The core loader reads the complete evidence list from each staged DisMech
treatment record. Evidence is attached to the disease-to-treatment relationship;
the same treatment may be recorded for several diseases with different evidence.
Existing treatment and drug nodes are reused, so reloading adds no duplicate paths.

Relationship properties:

- `evidence_json`: every original evidence item, including `reference`,
  `reference_title`, `evidence_source`, `supports`, snippets, curator explanations
  and any other supplied fields. Missing evidence becomes `[]`.
- `evidence_count`: the number of evidence items, including items without an ID.
- `publications`: all distinct reference identifiers separated by semicolons.
- `source` and `source_version`: DisMech and the original pinned Git revision.
- `evidence_pmid`, `evidence_snippet`, `dismech_explanation`: the first item,
  retained for compatibility with existing named agent tools.

This also preserves full evidence for phenotype, genetic-factor and subtype
associations written by the same helper. Other core-loader mechanism/causal
relationships retain their existing first-citation behavior. The daily MM pipeline
already stamps full `evidence_json` on its relationships and remains compatible.
Rebuild its ingest package when deploying future pipeline code updates.

The staged records at revision
`b1ca0b232b815a21b4ba95a62828c9979e9a49e7` contain:

| Disease | Treatments | Evidence items | Source classifications |
| --- | ---: | ---: | --- |
| Multiple Myeloma | 6 | 7 | 6 `HUMAN_CLINICAL`, 1 `OTHER` |
| JMML | 3 | 3 | 3 `HUMAN_CLINICAL` |

`HUMAN_CLINICAL` is DisMech's evidence classification, not a guarantee that a
publication is a registered trial. For example, the additional Anti-CD38 citation
`PMID:41494138` is a guideline classified as `OTHER`. This change creates no trial
identifiers, trial phases, enrollment counts or structured outcomes.

## Deploy and reload

Build the existing Lambda package with `uv run python scripts/build_lambda.py`,
then deploy the ingest Lambda code through the existing Terraform workflow.
Review the plan before applying; unrelated infrastructure changes are unnecessary.
With the updated loader deployed and existing source files in S3:

```sh
aws lambda invoke \
  --profile mmrf-hackathon --region us-west-2 \
  --function-name mmrf-kg-ingest \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 310 \
  --payload '{"sources":["mm","jmml"]}' \
  /tmp/dismech-treatment-ingest.json
```

Inspect the invocation metadata for `FunctionError` and the saved response for
node/edge counts. Existing Open Targets edges are retained; there is no need to
reload the drug-target dataset for this update.

Verify every evidence item against the S3 raw record and its provenance checksum:

```sh
uv run python scripts/validate_treatment_evidence.py
```

The validator uses the existing read-only graph Lambda, checks the source version
and every original evidence field, and prints counts and reference IDs. It does
not print article quotations. Use `--sources mm` to validate MM alone.

## View in a Neptune notebook

Run this in a code cell:

```cypher
%%oc
MATCH (d:Disease)-[r:ASSOCIATION]->(t:Entity)
WHERE r.source = 'dismech'
  AND r.predicate = 'dismech:has_recorded_treatment'
RETURN d.name AS disease, t.label AS treatment,
       r.evidence_count AS evidence_items,
       r.publications AS references,
       r.evidence_json AS complete_evidence,
       r.source_version AS source_revision
ORDER BY disease, treatment
LIMIT 100
```

Neptune stores `evidence_json` as a string. Decode it with Python's `json.loads`
when individual citation titles, classifications or support statuses are needed.
For the interactive graph, return `d, r, t` instead of the scalar columns.
The existing graph API reads `publications` and shows links for all references;
existing named agent tools still present the first citation through their legacy
fields. Use the read-only query tool to retrieve the complete evidence JSON.

Raw DisMech files remain in the private sandbox S3 bucket. Keep source references
and curator commentary distinct; confirm DisMech redistribution terms before
sharing raw YAML or derived evidence exports (see [data terms](data-terms.md)).
