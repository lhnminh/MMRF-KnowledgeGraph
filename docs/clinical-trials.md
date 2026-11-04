# Registered clinical trials

The trial subset starts from `HUMAN_CLINICAL` treatment citations in the pinned
MM and JMML DisMech YAML records. PubMed's structured `DataBankList` links the
publication to ClinicalTrials.gov NCT identifiers. The fetcher retrieves those
exact registry records using the public v2 API. There is no name matching, model
extraction of identifiers, or import of all trials for a disease.

The pinned citations resolve to eight studies: six for MM and two for JMML.
The teclistamab publication links two studies, which are both retained. Two
clinical citations have no structured PubMed registry accession and remain
explicitly unmatched. The guideline classified as `OTHER` is excluded.

Sources:

- [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/about-api),
  [API metadata](https://clinicaltrials.gov/api/v2/studies/metadata) and
  [terms](https://clinicaltrials.gov/about-site/terms-conditions).
- [NCBI E-utilities reference](https://www.ncbi.nlm.nih.gov/books/NBK25499/).

## Graph model and provenance

```text
Disease ──HAS_CLINICAL_TRIAL──> ClinicalTrial
Treatment ──HAS_CLINICAL_TRIAL──> ClinicalTrial
```

`ClinicalTrial.id` is the original NCT identifier. Nodes retain the registry
title, phase, study type, overall status, enrollment count and count type,
last-update date, `hasResults`, conditions and interventions. These are registry
metadata; posted study results are archived in S3 but are not converted into
efficacy claims or individual patient records.

Relationships have `source=clinicaltrials_gov`, the registry's `dataTimestamp` as
`source_version`, retrieval time, registry URL, exact supporting PMIDs, the
DisMech revision and `mapping_basis=pubmed_structured_registry_accession`.
The relationships are ORION cross-source projections, explicitly marked
`edge_origin=derived_from_source_cross_reference`; they are not upstream
ontology assertions or evidence of clinical benefit. A treatment-to-trial edge
also records `disease_id` so a shared treatment name does not merge disease
contexts. A publication's registry cross-reference does not identify a specific
treatment arm; no treatment-arm matching is inferred.

The full registry records and original PubMed XML are archived under an immutable
UUID prefix in private S3. Nodes and relationships retain the snapshot S3 URI
and checksum; relationships also retain the PubMed XML URI and checksum. Raw
downloads remain ignored by Git. No new AWS resources are required.

## Fetch, upload and ingest

Use the existing AWS sandbox profile and same-region bucket:

```sh
uv run python ingestion/fetch_clinical_trials.py \
  --sources mm jmml \
  --bucket mmrf-kg-ingest-116383a80f025a64d381928e54 \
  --upload
```

The script verifies the staged DisMech source checksums, requests at most 30
distinct publication IDs and 20 registry records, and checks that the registry
data timestamp stays constant during retrieval. Missing PubMed records,
invalid NCT identifiers, mismatched registry responses and oversized snapshots
fail before upload. Original source responses and their manifest go to:

```text
raw/clinical_trials/snapshots/<uuid>/pubmed.xml
raw/clinical_trials/snapshots/<uuid>/snapshot.json
raw/clinical_trials/snapshots/<uuid>/provenance.json
raw/clinical_trials.json
raw/clinical_trials.json.provenance.json
```

After deploying the updated existing ingest Lambda package:

```sh
aws lambda invoke \
  --profile mmrf-hackathon --region us-west-2 \
  --function-name mmrf-kg-ingest \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 310 \
  --payload '{"sources":["mm","jmml"],"clinical_trials":true}' \
  /tmp/clinical-trials-ingest.json

uv run python scripts/validate_clinical_trials.py
```

Inspect the invocation metadata for `FunctionError` before trusting the saved
result. The loader verifies the snapshot hash and every joined citation against
the selected DisMech records before any graph writes. The existing private VPC
Lambda performs bounded, parameterized upserts for this small addition. Repeated
loads reuse the same nodes and relationships. Refreshes upsert; records removed
upstream are not automatically deleted.

Build the core package with `uv run python scripts/build_lambda.py`. Deploy only
the reviewed ingest Lambda code change through the existing deployment workflow;
the parent Terraform stack may contain unrelated infrastructure changes. The
read-only tool Lambda needs no code update. Deploy the `terraform/graph-api`
package for the new explorer view, keeping its existing settings.

This source is refreshed manually with these commands. The existing daily
EventBridge schedule still refreshes the original MM Monarch/DisMech snapshot;
it does not fetch registry records. No Step Functions workflow is introduced.

## View in the graph explorer

Open the existing graph explorer, refresh, and choose **Clinical trials** from
**View**. The **ClinicalTrials.gov** source filter also opens that view. Select
a trial node for phase, status, enrollment and a registry link; select an arrow
for the publication IDs and cross-source mapping provenance. The web explorer
currently displays the MM subset; JMML is available through Neptune and the
read-only query tool.

API request:

```text
GET /api/graph?view=clinical_trials&source=clinicaltrials_gov
```

In a Neptune notebook code cell:

```cypher
%%oc
MATCH (d:Disease)-[r:HAS_CLINICAL_TRIAL]->(t:ClinicalTrial)
RETURN d.name AS disease, t.id AS trial, t.name AS title,
       t.study_phase AS phase, t.study_status AS status,
       t.enrollment_count AS enrollment, t.enrollment_type AS enrollment_type,
       t.source_url AS registry_url, r.publications AS references,
       r.mapping_basis AS mapping_basis, r.source_version AS source_version
ORDER BY disease, trial
LIMIT 20
```

To render the graph, use `RETURN d, r, t` instead of scalar columns. For treatment
paths, match `(d:Disease)-[:ASSOCIATION]->(a:Entity)-[r:HAS_CLINICAL_TRIAL]->(t:ClinicalTrial)`
and require `r.disease_id = d.id`.

## Validation and rollback

`uv run pytest` covers missing cross-references, prose-only NCT mentions,
guideline exclusion, multiple studies per publication, source-version drift,
wrong registry identities, corrupted snapshots, unsupported joins and duplicate
links. The graph API tests check the trial view, source links, registry metadata
and exclusion of raw source text. Run `node --test tests/test_graph_viewer.mjs`
for source-selection behavior.

The live validator compares both disease/trial and treatment/trial paths and
the registry metadata against the staged snapshot, and checks the immutable
registry/PubMed archive hashes. Upserts preserve other data sources. To roll
back code, redeploy the saved previous Lambda packages. Trial records remain in
Neptune until explicitly removed; code rollback does not delete graph data.
