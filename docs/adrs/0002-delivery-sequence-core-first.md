# AD-002: Delivery Sequence, DisMech Core First, API Supplements Second

| Field | Value |
|---|---|
| Decision ID | AD-002 |
| Status | Proposed |
| Date | 2026-11-02 |
| Architecture domain | Application and Technology Architecture |
| Scope | ORION Hackathon, Workstream 1 (Knowledge Graph) |
| Stakeholders | Hackathon team, customer platform team |
| Depends on | AD-001 |
| Source documents | `vision/Vision_ORION-revised.docx`, `vision/Tech_ORION-revised.docx` |

## 1. Context

The build is 2 days in an empty AWS sandbox. The vision lists "setup eats too much of day 1"
as a risk. Firm choices are Amazon Bedrock AgentCore for agents, Bedrock for models, and
Neptune preferred for the graph. The sandbox repository contains no Neptune, Bedrock or
AgentCore code, so all three are new.

DisMech has no query API and must be ingested. Open Targets and CIViC have public GraphQL
APIs. Per AD-001, DisMech is the core source and the two APIs are supplements.

## 2. Decision Drivers

- D1. Deliver the three vision journeys first; they depend on DisMech.
- D2. Retire the largest technical risk (Neptune, networking, AgentCore) early.
- D3. Keep a working demo available as early as possible and keep later work droppable.
- D4. Keep provenance consistent across sources.
- D5. Do not depend on the network during the demo.

## 3. Options Considered

| Option | Summary |
|---|---|
| A. Core first (selected) | Neptune and agent skeleton, DisMech ingest, three journeys, then API tools |
| B. API first | Open Targets and CIViC tools first, DisMech later |
| C. Load everything first | Load all three sources into Neptune before building the agent |
| D. No Neptune | Files or public APIs only |

## 4. Decision

Deliver in this order:

1. Stand up the Neptune cluster and the agent skeleton.
2. Ingest the DisMech Multiple Myeloma entry.
3. Get the three vision journeys working end to end.
4. Add the Open Targets and CIViC tools.

Design rules applied from step 1:

- All sources sit behind one tool contract. Each tool returns an envelope with
  `source_dataset`, `source_version`, `nodes`, `edges`, `evidence` and `edge_origin`.
- The UI renders nodes and edges from the envelope, not from source-specific shapes.
- Canonical IDs are MONDO (disease) and HGNC (gene); the mapping table is defined up front.
- Step 1 is time-boxed to **2.5 hours** from the start of applying the prepared Terraform (work
  breakdown K-1.1), decided 2026-11-02 (D-1). Preparing and testing the Terraform before day 1 is
  permitted, which allows the shorter box (the original value was 4 hours). The graph-store path
  is measured by three milestones: M1 at 60 minutes (Neptune available; a Lambda in the VPC runs
  a trivial openCypher query), M2 at 90 minutes (DisMech loaded; node and edge counts match),
  M3 at 150 minutes (a `kg.*` tool
  behind Gateway returns a valid envelope from Neptune, called with the chosen credential).
  Hosting the agent on AgentCore is a separate path: a failure there moves the demo to a lower
  topology rung (work breakdown section 12), not to the stand-in graph.
- Fallback triggers: M3 missed after 2.5 hours, or two consecutive milestones missed. Then use a
  stand-in graph behind the same tool contract (an in-memory graph loaded from the same converted
  files) as the demo's graph store. The stand-in is the default backend for every squad from the
  start, so no one waits, and it must speak openCypher so the guarded query tool can be tested
  off Neptune.
- Fast-fail rule: any blocker that needs someone outside the team (an SCP, a quota, a
  permission) is escalated immediately; downstream squads keep using the stand-in meanwhile.
- Review points: both leads review each milestone at its time; K-PLAT does not decide alone.
- At fallback, K-PLAT moves to Gateway authentication and the AWS deployment of the frontend
  (the other critical-path work) and the demo script states that the Neptune path was scripted
  but not live. Neptune work may continue in the background if it is within about an hour of working.
- Open question: whether other preparation (converter scripts, contracts, fixtures) is also permitted
  before day 1; only the Terraform is confirmed.
- Open Targets and CIViC responses are cached.

## 5. Rationale

- A satisfies D1: the journeys need DisMech mechanism edges, which the APIs do not provide.
  B would produce a first demo that does not answer the vision's examples.
- A satisfies D2: infrastructure unknowns surface while there is time to recover. B and C
  defer or enlarge that risk (C places identifier mapping before any working demo).
- A satisfies D3: after step 3 a stable knowledge-graph demo exists, and step 4 is additive for
  that demo. The combined demo is required (work breakdown D-6), and two of its journeys need the
  Open Targets and CIViC tools, so step 4 cannot be dropped overall. Its clients and caches start
  in parallel in day 1 because they do not depend on Neptune.
- The shared envelope satisfies D4 and makes supplements thin wrappers. Caching satisfies D5.
- D departs from the vision's preferred Neptune demonstration, so it is rejected.

## 6. Implications

Positive:

- The primary demo targets the vision's journeys and the customer's planned stack.
- Pipeline failures are infrastructure or conversion bugs on a small, known dataset.
- A working base remains for combining with Workstream 2.

Negative and risks:

- No visible demo until step 3; provisioning and access could consume a large share of day 1.
- Neptune is VPC-only. Agent runtime networking, IAM, and an S3 read role plus endpoint for
  bulk load are required. AgentCore Runtime documents a VPC mode (subnet and security group;
  see AD-005), but attachment must still be confirmed in step 1.
- An 8-node, 7-edge graph does not need Neptune; the justification is customer alignment, and
  the cluster costs money while idle.
- The early demo is narrow (one disease, one source); target and variant questions wait for step 4.
- Schema choices are made on one source and may need rework when the supplements arrive.
- If the DisMech license answer is negative, the core dataset must be replaced.
- Step 4 may not be reached if steps 1 to 3 overrun. Because the combined demo requires it, the
  mitigation is parallel work (clients and caches start in day 1) and the stand-in graph, not
  dropping the step.

Impact on work breakdown:

- Step 1 needs an explicit exit check: the agent runtime queries the graph store and returns
  an envelope.
- The tool contract and identifier mapping are first-class stories.

## 7. Compliance and Traceability

- Tech: AgentCore for agent hosting, Bedrock for models, Neptune preferred, Terraform
  preferred for infrastructure, Gateway (MCP) optional for tool exposure.
- Vision: plan and tool calls visible; observations separated from interpretation; limitation
  reported instead of invented results.

## 8. Review

Revisit if step 1 exceeds its time-box or the DisMech license answer is negative.
