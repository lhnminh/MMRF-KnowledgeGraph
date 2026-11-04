# AD-004: Integration Seam, Tool Contract, Response Types and In-Chat Visualization

| Field | Value |
|---|---|
| Decision ID | AD-004 |
| Status | Proposed |
| Date | 2026-11-02 |
| Architecture domain | Application and Data Architecture |
| Scope | ORION Hackathon, Workstream 1 and Workstream 2 |
| Stakeholders | Hackathon team, scientific reviewer, customer platform team |
| Depends on | AD-001, AD-002, AD-003 |
| Related decisions | AD-005 (shared infrastructure) |
| Source documents | `vision/Vision_ORION-revised.docx`, `vision/Tech_ORION-revised.docx`, `src/mmrf-data-commons`, `src/mmrf-virtual-lab-sandbox` |

## 1. Context

AD-002 introduced a tool envelope for knowledge sources (`source_dataset`, `source_version`,
`nodes`, `edges`, `evidence`, `edge_origin`). That shape is graph-specific. Workstream 2 tools
return something different: cohort state, analysis results and native Virtual Lab
visualizations (Kaplan-Meier, Oncomatrix, differential expression), which must be shown in the
conversation and stay consistent with the platform's cohort and filter state.

The vision requires, for both workstreams:

- Each answer shows its source and supporting relationships (Workstream 1).
- The user sees the plan, selected tools, settings and results, and the platform's existing
  interactive visualizations appear in the conversation (Workstream 2).
- Observation is kept separate from interpretation.
- When a tool result is insufficient, the agent says so instead of guessing.

Facts relevant to the seam:

- AgentCore Gateway exposes Lambda functions, OpenAPI endpoints, Smithy models and MCP servers
  as MCP tools, and the Technology document names Lambda as a simple tool backend.
- The Virtual Lab frontend is Next.js 16, React 19 and Mantine 9 (`mmrf-data-commons`).
- Sandbox genes use Ensembl IDs that match Open Targets for five of the six panel genes (for example
  KRAS ENSG00000133703). **DIS3 is the exception:** the sandbox labels DIS3 `ENSG00000150907`, which
  Open Targets resolves to FOXO1 (checked 2026-11-02; DIS3 is `ENSG00000083520`). Tools therefore map
  sandbox genes by symbol and send Open Targets the canonical ID (`kg/ingest/id_mapping.json`).

Findings from the code investigation of in-chat visualization (read from source; not executed,
because the stack was not running and dependencies were not installed). ProteinPaint client
inspected: 2.216.0, the version locked in `package-lock.json`.

- The tools (Correlation, OncoMatrix, DE and others) are 8-line app files over
  `features/proteinpaint/*Wrapper.tsx`. Each wrapper mounts the tool into a div with
  `bindProteinPaint`.
- The wrappers accept almost no parameters. They read the current cohort from the Gen3 store
  and pass it as `filter0`; the DE wrapper also passes all saved cohorts as `cohorts0`.
- `_app.tsx` renders the whole app client-side inside `MantineProvider` and `Gen3Provider`, so a
  component mounted beside `<Component>` persists across pages and shares the store and modals.
- Cohorts live in browser state and are created by dispatching
  `createNewCohort({ name, filters, setAsCurrent })` (as `SaveCohortModal` does). Case-ID filters
  use `{ case_centric: { mode: 'and', root: { case_id: { operator: 'in', operands: [...] } } } }`.
- The ProteinPaint client accepts configuration the wrappers do not pass:

| Journey | Client support | Wrapper today |
|---|---|---|
| Survival by CGS risk | `state.plots`; survival chart keys `term`, `term2`; sandbox terms `overall_survival` and `cgs_risk_category` | Passes nothing; opens a blank input screen |
| Top 3 mutated genes | `arg.genes`, `arg.state` | `genes` line is commented out |
| KRAS differential expression | `state.plots[0].groups[]` (name, color, filter) and `autoSubmit`, documented in the client for embedders | Passes nothing |

- Defects and constraints in the wrappers for chat use: they set a white background on
  `rootElem.parentNode.parentNode.parentNode`; they always use the global current cohort; the
  charts need roughly full-page width.
- The sandbox's own UI test drives DE by clicking Add group, Cohort, rename, Submit, Wilcoxon, Run.
  Whether `autoSubmit` also selects the method and runs the analysis is not confirmed.
- ProteinPaint is under an academic-use license; commercial use requires contacting St. Jude.

## 2. Decision Drivers

- D1. Workstreams combine through one narrow, versioned seam.
- D2. Every answer carries provenance and a visible observed-versus-interpreted split.
- D3. The UI renders from typed results, not source-specific shapes.
- D4. Knowledge queries are safe: read-only, bounded and reproducible.
- D5. The same contract works whether tools are called directly or through Gateway.
- D6. Existing interactive Virtual Lab visualizations appear in the conversation, and cohort and
  analysis context stay consistent with the rest of the platform.
- D7. Keep changes to the frontend fork small and isolated.

## 3. Options Considered

Contract and query style:

| Option | Summary |
|---|---|
| A. Gateway (MCP) with a typed result envelope (selected) | Tools behind Gateway; one envelope with a `result_type` union |
| B. Direct function calls, ad hoc shapes per tool | Fastest to start; no shared UI contract |
| C. Free-form text-to-query only | Closest to the "plain language to graph query" learning goal; unsafe and hard to reproduce |
| D. Deterministic named tools only | Safe and reproducible; does not exercise text-to-query |

In-chat visualization:

| Option | Summary |
|---|---|
| V1. Native wrapper components in the chat, driven by a view spec (selected) | Reuses the existing tools; needs small wrapper changes |
| V2. iframe of `/app/<tool>` | Isolation; cohort and state hand-off is awkward and the tool cannot be seeded |
| V3. Custom charts drawn from API JSON | Full control; loses the native interactive tools the vision asks for |
| V4. Screenshots of tools | Easy; not interactive |

## 4. Decision

1. Expose knowledge capability as tools behind an AgentCore Gateway (MCP). Implement with Lambda
   targets first. If Gateway blocks progress, call the same functions directly with the same schema.
2. Use one typed response envelope with a `result_type` of `graph`, `native_view`, `table` or
   `text`, plus an optional `ui_actions` list.
3. Offer both query styles in the knowledge toolset:
   - Primary: deterministic named tools.
   - Secondary: a guarded read-only openCypher tool for generated queries. Read-only is enforced
     by IAM permissions and query validation, not by the model.
4. Version the contract with a `contract_version` field.
5. In-chat visualization uses V1. The agent never handles pixels; it returns a `native_view`
   spec and the frontend renders the existing tool.
   - A chat panel is mounted in `_app.tsx` inside the providers, so it persists across pages and
     shares the store and modals.
   - A registry maps `native_view.tool` to a wrapper component (survival to `CorrelationWrapper`,
     matrix to `MatrixWrapper`, DE to `DEwrapper`).
6. Changes in the frontend fork are limited to the wrappers and the chat panel:
   - Wrappers accept optional `filter0` (pinned cohort), `state` (seeded plots or groups) and
     `genes` props, and fall back to the global current cohort when absent.
   - Remove or contain the three-levels-up background assignment.
7. The agent runs in AWS and cohorts live in the browser, so the agent does not create cohorts.
   It returns `ui_actions` (`create_cohort`, `set_current_cohort`), which the chat client executes
   by dispatching the same Gen3 actions the UI uses. Data lookups (for example Analysis API calls
   for case IDs) run server-side.
8. Fallback ladder, applied per visualization in this order: native embed with seeded state; a
   link to the full tool page with the cohort set; a custom chart or table from API JSON.
9. The browser reaches the agent through a Next.js API route in the fork that proxies to
   AgentCore with streaming, following the pattern of `pages/api/protein-paint/[...path].ts`.
   Authentication details are recorded in AD-005.

Envelope (illustrative):

```json
{
  "contract_version": "0.2",
  "result_type": "graph | native_view | table | text",
  "summary": "string",
  "observations": [ { "text": "string", "refs": ["evidence_id"] } ],
  "interpretation": [ { "text": "string", "basis": "agent_inference" } ],
  "graph": {
    "nodes": [ { "id": "MONDO:0009693", "label": "string", "type": "Disease",
                 "source_dataset": "dismech", "source_version": "v0.1.46" } ],
    "edges": [ { "id": "string", "source": "id", "target": "id", "predicate": "causes",
                 "edge_origin": "recorded", "evidence_ids": ["string"] } ],
    "paths": [ { "node_ids": ["id"], "edge_ids": ["id"] } ]
  },
  "native_view": {
    "tool": "survival | matrix | de",
    "cohort_ref": { "name": "string", "filter": {} },
    "plots_state": [ { "chartType": "survival",
                       "term": { "id": "overall_survival" },
                       "term2": { "id": "cgs_risk_category" } } ],
    "genes": ["string"],
    "groups": [ { "name": "string", "color": "string", "filter": {} } ],
    "auto_submit": false,
    "settings_shown": { "method": "string", "ranking": "string" },
    "counts": { "group_name": 0 }
  },
  "ui_actions": [ { "action": "create_cohort | set_current_cohort",
                    "name": "string", "filters": {}, "set_as_current": true } ],
  "evidence": [ { "id": "string", "reference": "PMID:...", "snippet": "string",
                  "source_dataset": "string", "source_version": "string" } ],
  "query": { "language": "openCypher", "text": "string", "parameters": {} },
  "limitations": [ "string" ],
  "trace": { "plan": ["string"], "tool_calls": [ { "tool": "string", "inputs": {} } ] }
}
```

Initial knowledge tools (names provisional):

- `kg.search_entities`
- `kg.get_phenotypes`
- `kg.get_mechanism_paths`
- `kg.get_evidence`
- `kg.run_readonly_query` (guarded)
- Added after the first combined journey (proposed here, not in the original list):
  `kg.get_gene_disease_association` and `kg.get_treatment_evidence`. `kg.get_gene_context`
  (K-3.9, see Open Items) is implemented as well.
- Later: `opentargets.get_associations`, `opentargets.get_top_targets`,
  `opentargets.get_drug_candidates`, `civic.get_evidence`

Rules:

- `observations` contain only what a source record states. `interpretation` holds anything the
  agent added and must be labeled as such.
- `edge_origin` is `recorded` or `mapped`; mapped edges are presented as associations, never
  as mechanism (AD-001).
- A step described in a source record but absent as an explicit edge is reported in
  `limitations`.
- Empty or insufficient results return `limitations`, not invented content.
- Synthetic Virtual Lab results carry a flag that they have no clinical meaning; knowledge
  graph evidence is presented as published knowledge and never as validation of a synthetic result.
- Target and drug results are presented as recorded associations and development stages, never
  as treatment recommendations. A candidate's disease-specific stage and the drug's overall stage
  are reported separately, and an absent drug-target link is worded "none recorded", not "none exists".
- A `native_view` pins its cohort, so earlier messages do not change when the user switches the
  current cohort. Cohort sizes and ranking settings used are reported in `counts` and
  `settings_shown`.
- Seeded plot specs in the table above are inferred from client code and are not valid until the
  spikes in section 8 pass.

## 5. Rationale

- A single typed envelope lets the UI render graph and native views through one path (D3) and
  lets the Workstream 2 orchestrator call Workstream 1 without knowing about Neptune (D1, D5).
- Splitting `observations` from `interpretation` and carrying `limitations` and `trace`
  implements the vision's success criteria directly (D2).
- Deterministic tools give a dependable demo; the guarded query tool still lets the team show
  how well an agent turns a question into a graph query, and shows the query used (D2, D4).
- Gateway mirrors how the customer plans to expose tools, and Lambda is a listed backend.
- Free-form text-to-query alone (C) would let a model-generated query define the demo and is
  hard to make safe. Deterministic-only (D) leaves a stated learning objective untested.
- V1 meets D6 directly: it reuses the platform's own tools and cohort store. The client already
  supports seeded plots, genes and DE groups, so changes stay confined to the wrappers (D7).
- V2 cannot seed tool state and splits cohort state across frames. V3 and V4 do not deliver the
  interactive visualizations the vision names.
- Returning `ui_actions` keeps cohort state in the one place the platform already manages it,
  so the Cohort Manager and every other page stay consistent (D6).
- Pinning a cohort per card avoids stale or contradictory charts in a long conversation.

## 6. Implications

Positive:

- Workstream 2 can consume knowledge results as a tool without graph-specific code.
- The UI has one rendering contract; a graph view and native Virtual Lab views both fit.
- Provenance and the observed/interpreted split are structural, not left to prompt wording.
- The fallback ladder keeps a demo available if a native embed fails.

Negative and risks:

- The seeded specs are untested. In particular, whether `autoSubmit` runs the DE analysis
  including the method (Wilcoxon, ranked by adjusted p-value) is unconfirmed, and whether a
  seeded DE group can reference a saved cohort (the cohort term type fed by `cohorts0`) is
  unconfirmed. If not, the user completes the final step in the tool and the answer says so.
- Several ProteinPaint tools on one page at once is unverified. The fallback is one live card
  with earlier cards collapsed to a link to reopen.
- Chart width: a narrow drawer will not work; a wide panel or a link to the full tool page is needed.
- Cohorts exist only in the browser; whether they persist across reloads has not been checked.
- Wrapper changes diverge from the upstream fork and must be merged forward.
- ProteinPaint's academic-use license needs addressing for ORION production use (not for the hackathon).
- Large subgraphs can exceed tool response size or model context; results need limits and
  pagination.
- The guarded query tool needs a validation rule set (read-only, bounded depth and result size);
  its quality is a risk until tested.
- Contract changes break both workstreams, so changes need a version bump and a note.
- Gateway ingress authentication needs configuration; details not yet specified here.

Impact on other domains:

- Technology: Gateway, Lambda and IAM resources, and a streaming proxy route from the frontend to
  AgentCore (AD-005).
- Data: provenance fields become required properties on nodes and edges (AD-001).

## 7. Compliance and Traceability

- Vision: sources and relationships per answer; plan and tool calls visible; observation
  separate from interpretation; limitation reported instead of invented results; existing
  interactive visualizations shown in the conversation; cohort and analysis context consistent
  with the platform.
- Tech: Gateway (MCP) optional and mirrors the customer plan; Lambda as simple tool backend;
  frontend Next.js, React and TypeScript; prefer a team-owned fork of `mmrf-data-commons` starting
  from the revision pinned in `sources.json`.

## 8. Spikes (exit criteria for the visualization decision)

Each needs the sandbox stack running locally (12 GiB Docker memory; ProteinPaint needs amd64 or
emulation). Time-box each; on failure move to the next rung of the fallback ladder.

1. Mount `DEwrapper` in a chat-like container with a pinned `filter0` and seeded groups.
2. Mount two tools at once on one page.
3. Seed the survival plot with `overall_survival` and `cgs_risk_category`; confirm labeled
   curves, case counts and statistics appear.
4. Seed the matrix with a three-gene list from the `topMutatedGenes` endpoint.
5. Run DE with `autoSubmit`; confirm whether method and run are automatic; confirm group sizes
   match the RNA-seq-eligible counts shown in pre-analysis.
6. Dispatch `createNewCohort` from non-modal code and confirm the Cohort Manager shows it.
7. Check whether cohorts survive a page reload.

**Results, 2026-11-02** (local sandbox stack, frontend fork with the item 6 props, page `/bbm-spikes`): DE renders in a
480 px card with a pinned cohort and runs by itself; two tools on one page do not interfere; survival seeded with
`overall_survival` by `cgs_risk_category` draws labeled curves, counts and statistics; the matrix shows exactly the seeded
genes; `createNewCohort` from non-modal code works and the cohort survives a reload. The native embed with seeded state
(rung 1 of the fallback ladder) is confirmed. Also confirmed: the DE group sizes match the RNA-seq eligible counts, and a
contained card leaves the surrounding page untouched.

## 9. Open Items

- Results of the spikes above.
- Gateway ingress authentication choice. Now on the critical path: the combined demo is required,
  so this is due by the end of P1 (work breakdown I-2).
- New tool required by the combined journeys: `kg.get_gene_context`, returning per-source
  coverage for a gene with an explicit "no record" result per source (work breakdown K-3.9).
  Implemented as `kg_get_gene_context`; the per-source result is carried in `limitations`, not in a new
  envelope field, so `contract_version` stays 0.2.
- Response size limits and pagination.
- Final tool names and parameter schemas.
- Whether to upstream the wrapper changes or keep them in the fork.

## 10. Review

Revisit after the spikes, and before the first combined demo. If spikes 1 to 5 fail, drop to rung
two or three of the fallback ladder and update `native_view` accordingly.
