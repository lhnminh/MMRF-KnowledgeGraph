# Data terms and provenance

Terms were checked on 2026-11-03. This is an implementation record, not a grant of rights to upstream data.

| Source | Prototype use | Terms / limits |
| --- | --- | --- |
| ORION curated public facts | Tiny graph shipped in the image | Original factual paraphrases and identifiers with source links. No article bodies, snippets or patient-level research measurements are packaged. Underlying publications retain their terms. |
| Monarch API | Optional bounded local extraction | Monarch's [licensing guidance](https://monarch-app.monarchinitiative.org/Licensing/) recommends CC0 or CC-BY and says individual repository licenses take precedence. It is not a blanket CC0 grant for every integrated source. Preserve `primary_knowledge_source`, aggregators and publications; check each source before redistributing an export. |
| Open Targets Platform | Optional local myeloma target/drug snapshot | The [Platform license](https://platform-docs.opentargets.org/licence) marks Platform data as CC0 1.0 and documents its contributing sources. Keep release, original identifiers, association scores, candidate stages and clinical-report URLs. The processed snapshot and exact API response stay ignored by Git and outside the image. |
| CIViC | Optional local myeloma evidence snapshot | The [CIViC FAQ](https://civic.readthedocs.io/en/latest/about/faq.html#how-is-civic-licensed) identifies its data as CC0. Preserve accepted/submitted status, evidence direction and significance, molecular-profile context, therapies and publication references. The source API is live; the latest monthly-release label is metadata, not a guarantee that the live results equal that release. |
| DisMech Multiple Myeloma YAML | Optional local transformation only | [Downloads and license statement](https://dismech.monarchinitiative.org/downloads/index.html) says site pages are CC-BY 4.0 but a bulk-data-specific license/citation is not settled. Do not infer that a single YAML record inherits the rendered site's terms. No DisMech YAML or derived graph is included in the image or committed outputs. Confirm terms before sharing derived exports. |
| ClinicalTrials.gov / PubMed registry cross-references | Small registered-trial subset linked from DisMech treatment PMIDs | Preserve NCT IDs, PMID cross-references, registry version, retrieval time and record URLs. Registry values are source-reported metadata, not ORION-validated outcomes. Consult [ClinicalTrials.gov terms](https://clinicaltrials.gov/about-site/terms-conditions). PubMed source XML may contain copyrighted abstracts; retain raw responses privately and publish only identifier/registry metadata through the graph API. See [clinical trial ingestion](clinical-trials.md). |
| Synthetic Virtual Lab | Cohort summary and mutation visualization | All measurements and case identifiers come from the sandbox's generator. [Sandbox source and notices](https://github.com/theMMRF/mmrf-virtual-lab-sandbox/tree/2e47ab88baf57c2b8af1c388b6b48fca93ea5ac5) document the synthetic fixtures and component terms. No real patient records are used. |
| Virtual Lab / ProteinPaint | Existing components and APIs | Retain original component notices. The sandbox is Apache-2.0; dependencies retain their terms, including ProteinPaint's academic-use license. Confirm permitted use for your deployment. |

Curated evidence sources:

- [TP53 copy-number study, PMID:30373884](https://pubmed.ncbi.nlm.nih.gov/30373884/).
- [Myeloma genetic heterogeneity, PMC4241387](https://pmc.ncbi.nlm.nih.gov/articles/PMC4241387/).
- [RAS/RAF landscape, PMC11451330](https://pmc.ncbi.nlm.nih.gov/articles/PMC11451330/).
- [IMWG disease features, PMID:25439696](https://pubmed.ncbi.nlm.nih.gov/25439696/).
- [UniProt p53 gene/product mapping, P04637](https://www.uniprot.org/uniprotkb/P04637/entry).

`orion:*` predicates/identifiers describe the prototype's explicit projection choices. They are not claimed to be ontology-issued relationships. Public associations are labeled as source assertions that have not been independently reviewed. Missing confidence is `not_reported`, never a fabricated score. The manually curated records use their documented verification date; original raw downloads without a recorded retrieval time remain `unknown`.

`opentargets:*` and `civic:*` predicates are explicit ORION projections of their JSON fields, not upstream ontology predicates. Complete source records remain attached. An Open Targets association score is not a calibrated probability or a confidence estimate. CIViC evidence keeps its original `ACCEPTED` or `SUBMITTED` status; this is upstream curation status, not independent verification by ORION. Resistance, evidence that does not support an association, and combination contexts must be read from the evidence item rather than inferred from graph connectivity.

The optional extractor writes a retrieval manifest beside its raw JSON, including exact API parameters and truncation information. Transformation preserves the original record as JSON on each relationship, even when the query projection uses a simpler physical `RELATIONSHIP` type. All reference entries are retained, including snippets and curator explanations in opt-in local DisMech exports; these are separate from the source predicate and must not be presented as independently verified facts.

ORION uses and displays data and algorithms from the Monarch Initiative. See its [citation guide](https://beta.monarchinitiative.org/kg/citation) and [2024 platform paper](https://doi.org/10.1093/nar/gkad1082). Pin a specific release/commit and record permission before redistributing DisMech-derived data.
