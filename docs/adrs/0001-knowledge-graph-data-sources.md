# AD-001: Knowledge Graph Data Sources for Workstream 1

| Field | Value |
|---|---|
| Decision ID | AD-001 |
| Status | Proposed |
| Date | 2026-11-02 |
| Architecture domain | Data Architecture |
| Scope | ORION Hackathon, Workstream 1 (Knowledge Graph) |
| Stakeholders | Translational researcher, scientific reviewer, customer platform team (per vision) |
| Related decisions | AD-002 (delivery sequence) |
| Source documents | `vision/Vision_ORION-revised.docx`, `vision/Tech_ORION-revised.docx` |

## 1. Context

The vision asks for an agent that answers plain-language myeloma questions from public
biomedical knowledge, shows the supporting subgraph, and states the dataset and version
used. Data must be synthetic, mocked, or public, and used under upstream license terms.
DisMech (Monarch Initiative) is the suggested starting point.

Three target journeys must be supported with mechanism links and cited evidence:
myeloma features, bone disease and calcium, and kidney problems versus anemia.

Evidence gathered on 2026-11-02 (DisMech repository and release v0.1.46; live Open Targets,
CIViC and Monarch endpoints):

- DisMech Multiple Myeloma (`kb/disorders/Multiple_Myeloma.yaml`, MONDO:0009693): 8 mechanism
  nodes, 7 causal edges, 9 HPO-bound phenotypes, 3 genetic factors, 6 treatments, 13 distinct
  PMIDs. It maps onto all three journeys. Anemia is a phenotype record with no edge from
  Bone Marrow Failure.
- Edge-level evidence is in the curated YAML (`downstream[].evidence`). The pathograph JSON
  carries evidence on nodes only.
- DisMech licensing is unsettled. GitHub metadata says BSD-3-Clause, but the LICENSE file is
  unedited template text. Site pages are marked CC-BY 4.0. The downloads page says the
  bulk-data license is not settled. `kb/` contains LOINC content under Regenstrief terms.
- DisMech has no query API found; it is static pages and release files.
- Open Targets: CC0 data, Apache-2.0 code. MONDO_0009693 has 5,341 associated targets and
  553 drug or clinical candidates (release 26.09).
- CIViC: CC0 content, MIT code. 49 evidence items for Multiple Myeloma (DOID:9538), including
  KRAS profiles.
- Monarch KG: 34 disease-phenotype and 3 correlated gene associations for MONDO:0009693. No
  KG-level license statement located; upstream licenses apply.
- Hetionet has no myeloma disease node. RTX-KG2 states its content is encumbered by upstream
  licenses (for example UMLS) that prevent redistribution.

## 2. Decision Drivers

- D1. Support the three vision journeys with mechanism paths and per-claim evidence.
- D2. Comply with upstream data licenses; avoid restricted sources (vision: DrugBank, OncoKB).
- D3. Show source dataset, version and provenance on every answer.
- D4. Fit a 2-day build; keep ingestion small.
- D5. Align with the customer's planned stack (Neptune preferred).
- D6. Keep observed facts separable from interpretation.

## 3. Options Considered

| Option | Meets D1 | Meets D2 | Notes |
|---|---|---|---|
| A. DisMech only | Yes | Uncertain | Small and thin on genes and drugs; license unconfirmed |
| B. DisMech core plus Open Targets and CIViC | Yes | Mostly (supplements CC0) | Weak joins below disease level; three schemas |
| C. Monarch KG as primary | No | Partly | Sparse for myeloma; no mechanism paths |
| D. Hetionet or PrimeKG | No | Partly | Hetionet lacks myeloma; PrimeKG superseded by OptimusKG |
| E. RTX-KG2 or ROBOKOP | Not assessed | No (RTX-KG2) | RTX-KG2 heavy to build and legally risky; ROBOKOP not assessed |

## 4. Decision

1. Core: DisMech Multiple Myeloma entry, pinned to release tag v0.1.46, converted from the
   curated YAML and loaded into Amazon Neptune.
2. Supplements: Open Targets and CIViC (both CC0), added as tools after the core journeys work.
3. Optional: a small Monarch KG subset for cross-references.
4. Excluded: RTX-KG2, DrugBank, OncoKB, DisGeNET (unverified; non-commercial terms from
   background knowledge), and anything derived from SemMedDB or UMLS.

Conditions on DisMech use:

- Use inside the sandbox only. Do not commit or republish DisMech data; commit a
  fetch-and-convert script pinned to the tag.
- Attribute DisMech and record release tag and commit on every node and edge.
- Remove LOINC content from any subset.
- Request written confirmation of bulk-data terms from the maintainers.

Cross-source rules:

- Join on explicit identifiers only: MONDO (disease) and HGNC (gene), with a mapping table
  for DOID, Ensembl and Entrez.
- Every edge carries `source_dataset`, `source_version` and `edge_origin = recorded | mapped`.
  Mapped edges are presented as associations, never as mechanism.

## 5. Rationale

- Option B is the only option that satisfies D1 and D2 while adding target and variant
  coverage. DisMech is the only source found with causal mechanism edges and quoted evidence
  for the three journeys (D1, D3).
- The supplements are CC0, so license risk is confined to one small DisMech subset. Removing
  DisMech would not invalidate the other sources (D2).
- Open Targets and CIViC cover questions DisMech cannot (candidate targets, variant evidence),
  and CIViC KRAS evidence links to the Workstream 2 KRAS scenario.
- Edge-level provenance and the `edge_origin` field support D3 and D6.
- Options C to E fail D1, fail D2, or are too heavy for D4.

## 6. Implications

Positive:

- The three journeys can be answered with cited evidence.
- Licensing exposure is small and isolated.

Negative and risks:

- DisMech is small and AI-curated; its own validation does not guarantee scientific correctness.
- Cross-source links are thin (two HGNC genes; treatments bind to generic NCIT classes), so
  cross-source reasoning is weak and must be labeled.
- Three schemas and an identifier mapping layer add build cost.
- Outstanding license confirmation could force replacement of the core dataset.

Impact on other architecture domains:

- Application: the agent needs source-specific query tools behind one tool contract (AD-002).
- Technology: Neptune load format is openCypher or Gremlin CSV, or RDF; a converter from
  DisMech YAML is required.
- Governance: source version, license and attribution are tracked as data attributes.

## 7. Compliance and Traceability

- Vision: sources and relationships shown per answer; source version identified; avoid
  restrictive licenses; preserve identifiers, relationships, provenance and references.
- Tech: Neptune preferred; convert exports to a supported format; confirm terms before import
  or redistribution.

## 8. Open Items

- Written confirmation of DisMech bulk-data terms.
- ROBOKOP not assessed. PrimeKG dataset license not verified at the primary source. Monarch
  KG-level license statement not located.
- Open Targets and CIViC load mode (live or Neptune subset), see AD-002.

## 9. Review

Revisit if the DisMech license answer is negative, or if a journey cannot be answered from
the loaded subset.
