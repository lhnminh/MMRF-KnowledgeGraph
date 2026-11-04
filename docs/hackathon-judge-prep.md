# Judge Prep — Workstream 1 Knowledge Graph

Internal prep doc for the 2026-11-05 hackathon presentation. Not part of the technical
documentation set — do not link to this from `README.md` or other docs. Everything below is
grounded in the actual code and ADRs in this repo; file references let you pull up the real
artifact live if a judge wants to see it.

---

## 0. One-page cheat sheet

**The pitch:** A myeloma knowledge graph (Neptune, openCypher) exposed to AI agents as 8 named,
typed tools over MCP — not a raw query endpoint. Every tool answer separates *what the graph
says* from *what the agent is inferring*, and cites its source down to the PMID. Built in
~2 days as Workstream 1 of ORION; composes with a separate orchestrator (Workstream 2) through
one typed JSON contract (AD-004), so two teams built independently without touching each other's
code.

**Numbers to have ready:**
- Deployed Neptune graph: **1,738 nodes / 2,894 relationships** (Open Targets 2,608 edges,
  CIViC 239, Monarch 35, curated 12) — loaded 2026-11-03, see `docs/neptune-existing.md`.
- The 8 agent-facing tools currently scope their answers to **Monarch + DisMech only**
  (164 nodes / 289 relationships in that slice, per `docs/mcp-tools.md`) — Open Targets/CIViC
  are in the graph but not yet wired into tool responses. **This is a real, honest gap — see
  Q10 below for how to answer it.**
- 8 MCP tools, all read-only; 1 write path (`kg-ingest` Lambda), privilege-separated at the IAM
  level from the read path.
- 2-day build. Zero NAT gateway cost (private VPC, S3 gateway endpoint only).
- All data sources are public/CC0 or pending-license-confirmation — **no real patient data**
  anywhere in the system (AD-006).

**If you only remember four things:**
1. **Typed envelope (AD-004)** is the single most defensible "originality" answer — it's the
   seam that let two independently-built agents compose.
2. **Ontology-ID-only joins** (MONDO/HGNC, never name-matching) is the single most defensible
   "healthcare-safety" answer.
3. **technology-choices.md section 1** is your answer to "why Neptune" — including the honest
   admission that at today's scale a simpler store would also work, and exactly what would
   change that answer.
4. **well-architected-review.md** is your answer to "what would you fix with more time" — it's
   already written down, dated, and prioritized. Use it; don't improvise a gaps list live.

---

## 1. Technical Capability & Healthcare Innovation

### What it does
A graph of Multiple Myeloma biology — diseases, phenotypes, genes, mechanisms, drugs,
mechanism-of-action chains — queryable by an AI agent through 8 named tools
(`docs/mcp-tools.md`), each returning a typed envelope with `observations` (graph facts),
`interpretation` (agent reasoning, explicitly tagged `source_curator_commentary` when it's a
human curator's explanation rather than a structural fact), `limitations` (self-reported gaps,
e.g. missing citations), and `evidence` (PMIDs/snippets an agent can show verbatim).

### Why this matters for "healthcare accessibility for millions who rely on charitable
healthcare services"
- The target failure mode in clinical AI is a plausible-sounding, uncited claim. This system
  makes that structurally hard: `Envelope.build` in `terraform/lambda_build/handler.py` appends
  a `limitations` entry naming the specific edge whenever a relationship has zero evidence ids,
  rather than silently returning an uncited claim (`docs/citations.md` section 4). An agent
  built on top of this can't accidentally present an unsourced fact as sourced.
- Every data source is public and free to use at this scale (DisMech license pending
  confirmation but not redistributed; Open Targets and CIViC are CC0; see `docs/data-terms.md`).
  There's no paid clinical-data license or EHR integration required to stand this up — relevant
  for a charitable/resource-constrained deployment context.
- `docs/mcp-tools.md` "Rules for your agent" explicitly forbids treatment recommendations; the
  footer of both `README.md` and the graph explorer UI (`graph_api/static/index.html`) repeats
  "No clinical diagnosis or treatment recommendations." This is a deliberate scope boundary, not
  an oversight — the system answers "what does the evidence say," not "what should this patient
  do."

### Data sources and curation (AD-001)
- **Core:** DisMech (pinned commit `b1ca0b232b815a21b4ba95a62828c9979e9a49e7`, release v0.1.46) —
  curated mechanism chains with PMID evidence.
- **Supplements:** Open Targets (CC0, top-50-tied gene-disease associations + all myeloma
  drugs/clinical candidates) and CIViC (CC0, all MM evidence items, accepted + submitted,
  status-tagged so the two are distinguishable).
- **Optional:** Monarch KG associations.
- **Explicitly excluded:** RTX-KG2, DrugBank, OncoKB, DisGeNET, SemMedDB/UMLS-derived sources —
  AD-001 names these and the specific reason each didn't fit (license, scope, or provenance
  granularity), which is a good answer if a judge asks "did you consider X."
- **Cross-source join rule:** only on explicit ontology IDs (MONDO for disease, HGNC for gene) —
  never fuzzy name-matching. Every edge carries `source_dataset`, `source_version`,
  `edge_origin` (recorded vs. mapped). This is the actual mechanism, not just a policy
  statement — see `docs/technology-choices.md` section 10.

### Automated, auditable refresh pipeline (not just a one-time load)
`docs/source-pipeline.md`: EventBridge Scheduler triggers a daily (02:00 UTC) fetch → S3
(manifest-committed-last, so a partial download can never look ingested) → direct Lambda invoke
→ Neptune, with transactional write + read-back count validation before the run is marked
successful. Deterministic run IDs make retries idempotent. This demonstrates the system isn't
a static demo snapshot — it has a real, auditable data-refresh lifecycle with provenance down to
the S3 object's SHA-256.

---

## 2. User Interface & Design

### What's actually built
`graph_api/` is an independent stack (`terraform/graph-api`) — a token-gated HTTP API
(5 rps / burst 20) in front of a Cytoscape.js graph explorer (`graph_api/static/`).
- **Interactive graph:** color-coded by entity type (Disease/Phenotype/Mechanism/Gene/Drug),
  click a node or edge to see identifiers, source version, and citations in a side panel
  (`app.js` `selectNode`/`selectEdge`).
- **Filters:** view, source, entity type, predicate, free-text — all client-driven, debounced
  text search (`app.js` line 130).
- **Accessibility:** a keyboard-navigable `<details>/<summary>` node list exists as a fallback
  to the canvas-rendered graph (`index.html`), so the UI isn't canvas-only.
- **Honesty built into the chrome, not bolted on:** a visible "limitations" panel sourced
  directly from the same envelope's `limitations` array used by the agent tools, and a footer
  disclaimer ("No clinical diagnosis or treatment recommendations. No patient or synthetic
  cohort records are shown.").
- **Security posture visible in the UI itself:** token lives in a JS variable only, never
  `localStorage`/`sessionStorage` (`app.js` line 4, `let token = ''`); disconnecting clears it;
  every outbound link gets `rel="noopener noreferrer"`.

### Design philosophy to state out loud
The UI's job is to make provenance inspectable, not just display a pretty graph. The
"Visible connections" and "Recorded references" panels exist specifically so a user can audit
*why* an edge is in the graph, down to the PMID — same principle as the agent-facing envelope,
just rendered for a human instead of an LLM. That consistency (one provenance model, two
consumers: agent and browser) is a good design-coherence answer if asked.

---

## 3. Impact & Scalability

### Scale, honestly, component by component
Use `docs/well-architected-review.md` and `docs/architecture.md`'s components table as the
source of truth here — don't improvise. Per component:

| Component | Scales how | Known limit today |
|---|---|---|
| Neptune (`mmrf-kg-cluster`) | Serverless v2, compute auto-scales 1–8 NCUs with load | Single instance, no reader, no final snapshot — an AZ/instance fault is a full outage with no recovery path |
| `kg-graph` Lambda (read path) | Stateless, Lambda scales concurrency automatically | No reserved/provisioned concurrency; cold starts happen in-VPC; nothing upstream bounds concurrent calls except the Gateway |
| `kg-ingest` Lambda (write path) | Scales as a function, but each invocation does one full load | Row-by-row writes, no batching/backoff — fine at ~50-node scale, a known blocker at 15x volume (Open Targets + CIViC already total ~12MB unloaded) |
| AgentCore Gateway | Exposes 5(now 8)-tool MCP surface, `AWS_IAM` authorizer | No WAF, no request throttling, no per-tool scoping yet — any caller holding the invoke permission can call every tool |
| Graph API / explorer | Independent Lambda, scales with Lambda concurrency | Deliberately throttled 5 rps / burst 20 for the sandbox; every request still funnels through the one `kg-graph` Lambda |
| S3 ingest bucket | Scales inherently | None at this data size |

**The honest scaling story:** this is a hackathon-scoped deployment that is deliberately
right-sized for a ~50-to-few-thousand-node graph, not pre-scaled for production load. That's a
documented, reasoned tradeoff (AD-002, AD-005), not an oversight — and `technology-choices.md`
section 1 contains the team's own "is Neptune even the right tool at this scale" critique
(Neptune is probably oversized for today's data volume; a simpler store — Neo4j, Postgres with
recursive CTEs, or even in-memory NetworkX — would also work). The reason Neptune anyway:
it's the right call *if* scale grows by orders of magnitude or multiple teams need concurrent
writes — which is exactly the direction this grows if adopted beyond the hackathon. Say this
plainly if asked; it's a stronger answer than pretending the current scale justifies Neptune on
its own.

### What "millions of patients" scaling actually requires from here
Be ready to name the concrete next steps, because they're already written down:
1. Batch the ingest loader (currently one round-trip per node/edge) — the actual scaling
   blocker, not cluster size (well-architected-review.md, Performance Efficiency).
2. Add a Neptune reader + snapshot policy for HA.
3. Enable Neptune IAM database authentication (currently app-layer-only read/write
   enforcement via a regex guard — see Security section below) before any PHI-adjacent data
   enters scope.
4. AD-006's pre-agreed HIPAA readiness plan executes *only if* a future data source introduces
   real patient identifiers — it's scoped as a trigger condition, not built speculatively ahead
   of need. That's a deliberate, judge-defensible design choice: compliance machinery sized to
   actual data sensitivity, not over-built for a demo.

### Extensibility story
The MCP/Gateway seam (AD-004) means a new agent, or a new data source, extends the system
without touching the orchestrator or the other workstream. Concretely: Open Targets and CIViC
are already loaded into Neptune (1,738/2,894 total) but not yet exposed through the 8 tools —
extending tool coverage to them is additive work on the Lambda side only, nothing upstream or
downstream needs to change. That's a live, concrete example of extensibility, not a hypothetical
one.

---

## 4. Originality & Uniqueness

### The strongest answer: AD-004's typed envelope as an integration seam
`docs/adrs/0004-integration-seam-tool-contract-and-response-types.md` — every tool returns one
contract (`contract_version`, `result_type` ∈ {graph, native_view, table, text}, `observations`,
`interpretation`, `evidence`, `limitations`, `trace`). This is what let a graph-reasoning agent
(this repo) and a separate orchestrator/Virtual Lab agent (a different repo, built by a
different team) compose *without* coupling to each other's backend — they only share the
contract. Mention the ProteinPaint native-embed spike (AD-004) as evidence the contract was
validated against a real, independently-built visualization tool, not just designed on paper:
differential-expression and survival-curve views render correctly, and `createNewCohort` state
persists across reload, using the contract's `native_view` type.

### Second-strongest: self-reporting citation gaps as a first-class response field
Most RAG-style systems silently drop a claim that has no source, or silently cite nothing. This
system's `Envelope.build` explicitly appends a `limitations` entry *naming the specific edge*
whenever a claim has zero evidence ids (`docs/citations.md` section 4) — the gap in citation
coverage is visible in the response itself, not something you'd have to audit for separately.

### Third: ontology-ID-only joins as a governance mechanism, not just a style rule
Cross-source joins only happen on explicit MONDO/HGNC IDs. This is what prevents silent
false-positive merges across differently-curated sources (e.g., two different diseases that
happen to share a colloquial name). It's unglamorous but it's the actual thing standing between
"knowledge graph" and "confidently wrong knowledge graph."

### Fourth: compliance sized to actual risk, not demo theater
AD-006 treats "no real patient data is in scope" as a falsifiable, dated decision with a
pre-agreed readiness plan attached — rather than either ignoring HIPAA entirely or over-building
unused compliance machinery for a hackathon demo that doesn't touch PHI. Judges who've seen a lot
of hackathon health-tech projects will recognize this as unusually mature risk-scoping.

---

## 5. Anticipated Q&A

### System design

**Q: Walk me through a request end to end.**
A: An agent calls a named MCP tool (e.g. `kg_get_mechanism_paths`) through AgentCore Gateway,
SigV4-signed, `AWS_IAM`-authorized. The Gateway invokes the `kg-graph` Lambda, which runs a
fixed, parameterized openCypher query against Neptune over Bolt (port 8182), builds a typed
envelope (observations/interpretation/evidence/limitations), and returns it. The Lambda's IAM
role only has `neptune-db:ReadDataViaQuery` — it cannot write even if the code tried.
(`terraform/gateway.tf`, `terraform/loader_lambda.tf`, `terraform/lambda_build/handler.py`)

**Q: Why Lambda instead of a long-running service?**
A: Bursty, agent-triggered traffic with no steady-state load — Lambda's per-invocation billing
and automatic concurrency scaling fit that pattern better than a provisioned service, and it
keeps the whole stack serverless/VPC-private with no servers to patch. (`technology-choices.md`
section 6)

**Q: Why two Lambdas instead of one?**
A: Privilege separation. `kg-graph` has a read-only IAM policy; `kg-ingest` has read+write. A
compromised or buggy agent-facing path physically cannot write to the graph, independent of any
application-layer guard. (`terraform/loader_lambda.tf`, `terraform/ingest.tf`)

### Extensibility

**Q: How would you add a new data source?**
A: Write a loader function following the existing `load_monarch`/`load_dismech` pattern
(parameterized `MERGE` statements, so re-running is always idempotent), join new nodes to
existing ones only via explicit ontology IDs, tag every edge with `source_dataset`/
`source_version`/`edge_origin`. The tool layer and Gateway don't need to change unless the new
source needs a new tool shape. Open Targets and CIViC are the proof: already loaded into
Neptune, loader code already exists — exposing them through the 8 tools is additive work only.

**Q: How would you add a new consumer (another agent, another UI)?**
A: Anything that can call MCP tools over the Gateway with a valid `AWS_IAM` identity gets the
same typed envelope every other consumer gets. That's literally how Workstream 2's orchestrator
and this repo's graph explorer both already consume the same backend without coupling to each
other.

### Scalability (per component — see table in section 3)

**Q: What breaks first at 10x data volume?**
A: The `kg-ingest` Lambda's row-by-row write pattern — one round trip per node/edge with no
batching or backoff. At current scale (~50-node core, now 1,738/2,894 after the Open
Targets/CIViC bulk load) it's fine; at the next order of magnitude it risks Lambda timeout or
Neptune throttling with no retry to recover a partial load. This is the top-priority fix, ahead
of anything about cluster size. (well-architected-review.md, Reliability + Performance)

**Q: What breaks first at 10x concurrent agent traffic?**
A: Nothing bounds concurrent Gateway calls today — no WAF, no request throttling at the Gateway
layer, no reserved concurrency on `kg-graph`. The `kg_run_readonly_query` tool itself caps output
(100 rows, 5 hops) but not intermediate cardinality, so a 5-hop pattern over a much larger graph
could be expensive before that cap applies.

**Q: Is Neptune actually the right database at this scale?**
A: Answer honestly — see section 3. At today's size, probably not required; at 100x+ scale or
multi-team concurrent writes, yes. This is written down, not something improvised live.

### Security

**Q: What stops an agent from writing to the graph through the read-only tool?**
A: Two independent layers, but currently only one is fully active. Layer 1: IAM — `kg-graph`'s
role only grants `neptune-db:ReadDataViaQuery`, so even a successful write query would be denied
at the AWS layer. Layer 2 (app-layer, for the one tool that takes a caller-supplied query,
`kg_run_readonly_query`): a regex guard (`validate_readonly_query` in `handler.py`) blocks
CREATE/MERGE/DELETE/procedure calls before the query reaches Neptune. **Open gap, stated
plainly:** Neptune's own IAM database authentication (`iam_database_authentication_enabled`) is
currently `false` cluster-wide, so Neptune itself isn't yet enforcing per-caller IAM permissions
on the connection — both Lambdas SigV4-sign their connections, but Neptune isn't checking that
signature yet. This is the top open security item, flagged in `well-architected-review.md`, and
it's on the list specifically because flipping it on changes both Lambdas' connection behavior
and needs to be tested against a live connection — not deployed speculatively before it could be
verified.

**Q: What about PHI / HIPAA?**
A: No real patient data is in scope today — every source is public (Monarch, DisMech, Open
Targets, CIViC) or synthetic (AD-006). That's a dated, falsifiable decision, not an assumption,
and it comes with a pre-agreed 6-item readiness plan (BAA + AgentCore HIPAA-eligibility check,
closing the Neptune IAM-auth gap, de-identification at ingestion, extending audit logging to
Bedrock, Cedar-based per-tool policy scoping on the Gateway, Lambda failure/incident handling)
that executes only if a future source introduces real patient identifiers.

**Q: Any other open security items you'd fix next?**
A: In priority order: Neptune IAM database auth (above), Gateway-level WAF/throttling, per-tool
scoping on the Gateway (an AWS Bedrock AgentCore Cedar policy engine — not yet attached), and
X-Ray egress (tracing is enabled on both Lambdas but the VPC has no NAT/IGW/X-Ray endpoint, so
trace segments are silently dropped — Lambda's own CloudWatch logs are unaffected since those go
through the Lambda service plane, not VPC routing). All of these are in
`docs/well-architected-review.md`, dated and categorized by Well-Architected pillar — point a
judge there directly if they want to see the whole list rather than reciting it all live.

**Q: What already *is* hardened, so this doesn't sound like it's all gaps?**
A: Deletion protection on Neptune; encryption at rest (`storage_encrypted = true`) and in transit
(SigV4-signed Bolt connections); S3 SSE + full public-access block on the ingest bucket; Neptune
audit logging to CloudWatch (14-day retention); security-group egress trimmed to only what's
needed (stateful SGs mean no broad reply-path rule required); Checkov scanning every PR
(report-only by design, findings triaged not ignored); constant-time token comparison
(`hmac.compare_digest`) on the Graph API's bearer check; the Graph API never forwards
caller-supplied input into a query — it always runs one fixed, hardcoded openCypher statement,
so a leaked token can't be used to run arbitrary queries, only to re-run the same fixed read.

---

## 6. If you get a question you don't have a crisp answer for

Default to: "That's an open item we tracked and prioritized rather than solved in the 2-day
window — it's in our Well-Architected review, here's exactly what it would take." This reads as
engineering maturity, not evasion, *if* you can actually point to the line in
`well-architected-review.md` or the relevant ADR. Don't improvise a number or a mechanism live —
every gap in this system is already written down with the reason it's open and what closing it
would require.
