# Technology choices, rationale, and alternatives

This walks through every major technology in Workstream 1 and why it was picked, citing the ADR
that decided it. Most choices trace to one of three forces, not free engineering preference:
**(a)** an explicit customer/vision mandate (Neptune, AgentCore, Bedrock, Lambda-as-backend),
**(b)** the 2-day build time-box forcing the cheapest safe path (no NAT, S3 gateway endpoint,
serverless over provisioned), or **(c)** a licensing/safety constraint (CC0-only supplements,
read/write IAM split, a guarded query tool). The ADRs are candid about which of their own choices
are oversized or a compromise for the sandbox scope rather than best practice in general; that
candor is preserved below rather than smoothed over.

## 1. Amazon Neptune (the graph database)

**Why:** AD-001 decision driver D5 — "Align with the customer's planned stack (Neptune
preferred)." This is a stakeholder mandate from the hackathon brief, not something derived from
the data's shape. On its own merits it still fits reasonably: the data is causal graph structure
(disease → mechanism → phenotype/gene/treatment chains with typed, versioned edges), so openCypher
traversal suits `kg_get_mechanism_paths` and the variable-hop `kg_run_readonly_query` better than
relational joins would.

**Alternatives considered (AD-001 §3):**
- **C. Monarch KG as primary** — rejected: too sparse for myeloma, no mechanism paths (fails D1).
- **D. Hetionet / PrimeKG** — rejected: Hetionet has no myeloma disease node at all; PrimeKG is
  superseded by OptimusKG.
- **E. RTX-KG2 / ROBOKOP** — rejected: RTX-KG2 is heavy to build and carries UMLS-derived license
  risk; ROBOKOP was not assessed.
- **AD-002 option D, no Neptune at all** — rejected because it "departs from the vision's
  preferred Neptune demonstration."

**Known cost, named directly in AD-002:** "An 8-node, 7-edge graph does not need Neptune; the
justification is customer alignment, and the cluster costs money while idle." At the current
scale (~50 nodes) this remains oversized for the data volume — a named tradeoff, not an
oversight.

### Independent technical assessment: is Neptune actually the right tool, scale aside from the mandate?

Setting the customer mandate aside and judging the workload on its own terms, the honest case
against Neptune here is stronger than the ADRs state outright:

- **Scale doesn't need a graph database.** The graph is currently ~50 nodes (DisMech core), and
  even fully loaded with Open Targets (5,341 associations) and CIViC (49 evidence items, per
  AD-001) it stays in the low thousands — static, public, loaded in one batch, not written
  continuously by many users. Neptune's value is scale (billions of edges) and low-latency
  concurrent traversal at volume; neither applies. `kg_run_readonly_query` caps traversal at 5
  hops and 100 rows, which an in-memory graph or a recursive SQL CTE handles trivially.
- **Neptune's openCypher is a crippled subset, and the code works around it.** From this repo:
  `ingestion/load_neo4j.py`'s docstring notes Neptune negotiates Bolt protocol 3, allowing only
  one open result per transaction, which forces careful driver-buffering discipline. Neptune also
  has no `CALL` and no list-valued property predicates (`terraform/lambda_build/handler.py`), so
  multi-valued fields like `publications` are hand-encoded as `;`-delimited strings instead of a
  plain list property — a workaround for a database limitation, not a chosen design.
- **It drags the network design with it.** Neptune is VPC-only. That single fact cascades into a
  dedicated private VPC, two subnets across two AZs, a Neptune subnet group, security groups, and
  — because the VPC then has no internet route — an S3 gateway endpoint just so `kg-ingest` can
  read files. Most of `terraform/main.tf` and `ingest.tf` exists to satisfy Neptune's own
  networking requirement, not the application logic.
- **It isn't cheaper or safer at this stage either.** `docs/well-architected-review.md` lists, as
  still open: IAM database authentication disabled (the only real write guard today is a regex in
  the Lambda handler), a single instance with no reader and `skip_final_snapshot = true` (an AZ
  fault is unrecoverable data loss), and a serverless floor of 1 NCU running 24/7 with nothing to
  catch an idle cluster.

**What a technology-only choice would have picked**, given data that is small, static, public,
read-mostly, with bounded-depth traversal and provenance-heavy edges:

1. **Neo4j** — this repo already uses it for local preview (`docker compose up -d neo4j`) and
   already has working ingestion code for it (`ingestion/load_neo4j.py` runs against both). Full
   Cypher instead of a hobbled subset, and a managed option (Aura) or small container avoids the
   VPC-only constraint entirely.
2. **PostgreSQL** — nodes/edges as two tables, bounded-hop traversal via a recursive CTE. The
   provenance fields (`source`, `source_version`, `edge_origin`, evidence refs) are relational in
   shape regardless; nothing in the tool list needs graph-specific algorithms (no PageRank, no
   community detection, no shortest-path-at-scale).
3. **An in-memory graph (NetworkX/igraph) rehydrated from a JSON/CSV snapshot in S3** on cold
   start — the simplest option at this volume. No database to operate, no VPC requirement, no
   standing cost; the Lambda effectively loads the whole dataset into memory for traversal queries
   anyway at this size.

Any of these removes the VPC/NAT/subnet-group machinery entirely, which is most of the Terraform
surface area in this repo today.

**Where Neptune does win:** it's managed, so nobody operates a database instance, and it is the
right call if this dataset is expected to grow by orders of magnitude (toward the RTX-KG2/UMLS
scale AD-001 explicitly rejected) or needs multi-team concurrent write load later. On today's
evidence neither is the plan — one disease, three journeys, two small supplement sources. So,
on technical grounds alone this is the wrong-sized tool for the project's current and near-term
scale. It remained the right call for this project because "align with customer's planned stack"
was an explicit, stated decision driver (D5) — a business/stakeholder reason, not a technical
one, and AD-002 says as much outright: "the justification is customer alignment."

## 2. Neptune Serverless v2, not provisioned

**Why:** `serverless_v2_scaling_configuration { min_capacity = 1, max_capacity = 8 }`
(`terraform/main.tf`) auto-scales compute with query load instead of sizing a fixed instance class
for unknown hackathon-demo traffic.

**Alternative:** a provisioned instance (e.g. `db.r5.large`) — not chosen because it would mean
guessing a fixed size for a short-lived demo workload with no real traffic history.

**Known cost:** the serverless floor (`min_capacity = 1`) still runs 24/7 with no scheduled
stop/start; the README already warns "Neptune bills while idle," and
`docs/well-architected-review.md` flags that nothing currently catches a cluster left running.

## 3. One private VPC, no NAT, no internet gateway

**Why (AD-005 §3-4):** Neptune must be private and reachable only from its own tool backend
(driver D3); the team also could not edit the shared sandbox account's Terraform or state
(driver D2).

**Alternatives considered (AD-005 §3):**
- **B. Reuse the sandbox VPC via tagged data lookups** — rejected: the sandbox VPC exports no
  subnet/VPC IDs, so this needs brittle tag-based lookups into a network another team owns.
- **C. Reuse the sandbox's two existing public subnets for Neptune** — rejected: puts a database
  in public subnets.
- **D. Edit the sandbox Terraform to export IDs and add Neptune** — rejected: couples state and
  review across teams.

A NAT gateway (to let the VPC reach the internet) was explicitly priced and rejected in AD-005:
"about $0.045/hour plus $0.045/GB processed... it was rejected." Hence no outbound path at all,
and S3 access via a free gateway endpoint instead (see next).

## 4. S3 + a gateway VPC endpoint (not an interface endpoint, not public fetch)

**Why:** a direct consequence of #3. With no NAT/IGW, the only free way to get raw source files
into the VPC for `kg-ingest` to read is S3 through a **gateway** endpoint — gateway endpoints are
free; **interface** endpoints bill per hour/GB like a NAT would. S3-as-drop-zone is not an
independently chosen storage technology; it's the cheapest way to satisfy the no-NAT constraint.

**Alternative:** an interface VPC endpoint, or simply opening a NAT path — both cost money the
free S3 gateway endpoint avoids.

## 5. Two Lambdas (`kg-graph` read-only, `kg-ingest` read+write), not one

**Why (AD-005 §4 item 1):** separation of privilege at the IAM level. `kg-graph`'s role only ever
gets `neptune-db:ReadDataViaQuery`; only `kg-ingest`'s role gets `WriteDataViaQuery`. Even if the
regex-based read-only guard in the handler had a bug, the write-side IAM policy is the backstop —
once IAM database authentication is actually turned on, which is still an open item per
`docs/well-architected-review.md`.

**Alternative:** one Lambda doing both jobs — rejected because it would need a single
over-privileged role instead of two narrowly scoped ones.

A planned third split in AD-005 — a `kg-external` Lambda outside the VPC for Open Targets/CIViC —
was decided but never built; `docs/well-architected-review.md` calls this out as design drift:
those sources ended up loading straight into Neptune instead, reversing a cost-motivated decision
without an ADR update.

## 6. AWS Lambda (not ECS/EKS/Fargate) as the compute backend

**Why:** AD-005's context notes AgentCore Gateway documents Lambda as a first-class, simple tool
backend, and the workload — small, bursty, invoke-on-demand query serving — fits Lambda's
scale-to-zero model without running or patching a container platform for a 2-day build.

**Alternative:** not seriously evaluated in the ADRs. Lambda was the path of least setup cost
given the "simple tool backend" framing, and nothing in the workload (sub-minute openCypher
queries) needs long-running compute.

## 7. AgentCore Gateway (MCP) as the tool-exposure layer

**Why (AD-004 §3-4):** decision driver D1 ("workstreams combine through one narrow, versioned
seam") and D5 (the same contract works whether a tool is called directly or through Gateway).
Gateway also mirrors how the customer plans to expose tools (AD-005 context).

**Alternatives considered (AD-004 §3):**
- **B. Direct function calls, ad hoc shapes per tool** — fastest to start; rejected because it
  builds no shared UI contract.
- **C. Free-form text-to-query only** — closest to the "plain language to graph query" learning
  goal; rejected as unsafe and hard to reproduce, since a model-generated query would define the
  demo.
- **D. Deterministic named tools only** — safer; rejected because it leaves the text-to-query
  learning objective untested.

**Decision:** both — deterministic named tools as primary, plus one guarded, read-only, bounded
openCypher tool (`kg_run_readonly_query`) as the secondary path, with read-only enforced by IAM
and query validation, "not by the model."

## 8. One typed response envelope (`contract_version`, observations/interpretation/limitations/trace)

**Why (AD-004 §2-4):** driver D2 — every answer must carry provenance and a visible
observed-vs-interpreted split, which the vision requires directly (no invented results, no
treatment recommendations). A single envelope also lets Workstream 2's orchestrator consume
Workstream 1 as a plain tool call without knowing Neptune exists (D1, D5), and lets the frontend
render both a graph view and native Virtual Lab views through one path (D3).

**Alternative:** source-specific shapes per tool — rejected because the UI would need per-source
rendering logic, and nothing would force the observed/interpreted split structurally; it would
depend on prompt wording instead.

## 9. DisMech as the core dataset; Open Targets + CIViC as supplements

**Why (AD-001 §4, AD-002 §4):** DisMech is the only source found with causal mechanism edges and
quoted evidence covering all three required vision journeys (bone/calcium, kidney/anemia, myeloma
features generally). Open Targets and CIViC are both CC0 — safe to add, but neither carries
mechanism paths.

**Alternatives (AD-001 §3):**
- **A. DisMech only** — rejected: thin on genes and drugs, and its license is still unconfirmed
  (GitHub metadata says BSD-3-Clause, the LICENSE file is unedited template text, site pages say
  CC-BY — unresolved).
- **B (selected). DisMech core + Open Targets/CIViC** — chosen as the only option meeting both D1
  (journey coverage) and D2 (license safety), confining legal risk to one small, isolated subset.

**Delivery sequencing (AD-002 §3)** rejected **B. API-first** (produces a demo that can't answer
the vision's example journeys) and **C. load everything first** (defers working-demo risk and
puts identifier-mapping work before anything runs). DisMech's own license uncertainty remains an
open, unresolved risk, not swept aside — see AD-001 §8.

## 10. Join only on explicit ontology IDs (MONDO, HGNC), never by name

**Why (AD-001 §4):** prevents false merges across sources with different naming conventions.
Every edge also carries `source_dataset`, `source_version`, and `edge_origin`
(`recorded` vs `mapped`), so an inferred cross-source link is never presented as if DisMech itself
recorded it.

**Alternative:** fuzzy or string-based entity matching — not used; it would silently blend
curated mechanism claims with inferred associations.

## 11. Strands Agents + AgentCore Runtime, one agent per workstream

**Why (AD-003):** matches the customer's own proposal and has first-class AgentCore tooling
support; one agent per workstream keeps independent ownership and testing.

**Alternatives:**
- **LangGraph** — valid, not selected only because it doesn't match the customer's proposal.
- **A single shared agent** — rejected: couples the workstreams and enlarges the tool list the
  model must choose from, which lowers tool-selection accuracy.
- **A2A peer coordination** — deferred as a stretch option; unnecessary machinery for an
  integration that is shaped as a tool call, not peer-to-peer delegation.

## See also

- [docs/architecture.md](architecture.md) — current infrastructure diagram and per-component
  scaling limits.
- [docs/well-architected-review.md](well-architected-review.md) — the open gaps referenced above.
- [docs/adrs](adrs) — the full decision records (AD-001 through AD-006) cited throughout this
  page.
