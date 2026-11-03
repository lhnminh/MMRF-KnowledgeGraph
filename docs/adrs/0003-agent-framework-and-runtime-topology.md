# AD-003: Agent Framework and Runtime Topology

| Field | Value |
|---|---|
| Decision ID | AD-003 |
| Status | Proposed |
| Date | 2026-11-02 |
| Architecture domain | Application Architecture |
| Scope | ORION Hackathon, Workstream 1 and Workstream 2 |
| Stakeholders | Hackathon team, customer platform team |
| Depends on | AD-001, AD-002 |
| Related decisions | AD-004 (integration seam), AD-005 (shared infrastructure) |
| Source documents | `vision/Vision_ORION-revised.docx`, `vision/Tech_ORION-revised.docx` |

## 1. Context

Agent hosting on Amazon Bedrock AgentCore and models on Amazon Bedrock are firm choices. The
agent framework is a flexible choice: the customer proposal mentions Strands Agents, but
LangGraph or any framework that runs on AgentCore is acceptable. The vision lists "which
agent framework will each team use" as an open question.

The two workstreams have different agent jobs:

- Workstream 1: turn a plain-language question into graph queries and explain where the answer
  came from.
- Workstream 2: plan a task, select Virtual Lab tools, call them safely, and show native
  visualizations in the conversation.

Integration is optional (vision). If combined, a coordinating agent hands work to other agents
or tools ("how should a coordinating agent plan a task and hand off work").

Documented AgentCore capabilities (AWS documentation, checked 2026-11-02):

- Runtime hosts agents built with Strands, LangGraph, CrewAI or custom code.
- Runtime supports MCP servers and the Agent-to-Agent (A2A) protocol.
- Gateway exposes Lambda functions, OpenAPI endpoints, Smithy models and MCP servers as tools,
  and can proxy HTTP targets such as other Runtime agents.

## 2. Decision Drivers

- D1. Match the customer's planned platform and review-ability.
- D2. Let each workstream deliver independently.
- D3. Make later combination cheap.
- D4. Keep tool use safe and inspectable: plan, tool calls and results visible to the user.
- D5. Minimize learning cost in a 2-day build.

## 3. Options Considered

| Option | Summary |
|---|---|
| A. Strands Agents, one agent per workstream, combined via tool call | Each workstream owns an agent. Workstream 2's orchestrator calls knowledge capability as a tool through Gateway |
| B. Strands, single shared agent | One agent holds both toolsets |
| C. LangGraph per workstream | Graph-based orchestration, possibly different from the customer's proposal |
| D. Two agents coordinated over A2A | Peer discovery and task delegation between agents |
| E. Mixed frameworks, free choice per team | Fully independent |

## 4. Decision

1. Framework: Strands Agents for both workstreams, on AgentCore Runtime, using Bedrock models.
2. Topology: one agent per workstream.
   - Workstream 1 agent: knowledge capability (graph tools, later Open Targets and CIViC).
   - Workstream 2 agent: orchestrator for Virtual Lab tools.
3. Combination: the Workstream 2 orchestrator consumes Workstream 1 capability as tools
   through AgentCore Gateway (MCP), per AD-004. A2A is deferred as a stretch option and is not
   required for the combined demo.
4. Model and region (work breakdown D-5, decided 2026-11-02): region `us-east-1`; primary model a
   Claude Sonnet-class model through Bedrock. The bake-off on 2026-11-02 chose
   `us.anthropic.claude-sonnet-5-5` as primary and `us.anthropic.claude-haiku-4-5-20251001-v1:0`
   as fallback, both through US geographic inference profiles. The fallback was first proposed
   from another model family; the only Nova model that worked with tools gave weaker answers, so
   the lead chose a same-family fallback. It covers throttling and model faults, not an outage of
   the whole Anthropic family on Bedrock. Quotas are still to be confirmed.

The same framework is a convenience, not a requirement; the integration seam is protocol-based,
so a team may use another framework without changing the contract.

## 5. Rationale

- Strands matches the customer's proposal (D1) and has first-class templates in the AgentCore
  tooling.
- One agent per workstream keeps ownership and testing separate (D2).
- Agent-as-tool through Gateway needs only the tool contract to combine, with no agent discovery
  or task-delegation machinery (D3, D5). A2A adds capability the demo does not need.
- A single shared agent (B) couples the workstreams and enlarges the tool list the model must
  choose from, which lowers tool-selection accuracy.
- Mixed frameworks (E) are allowed by the vision but give the customer less comparable code.
- LangGraph (C) is a valid alternative; it is not selected because it does not match the
  customer proposal, not because it is unsuitable.
- Showing plan, tool calls and results (D4) is a UI and response-schema concern handled in AD-004.

## 6. Implications

Positive:

- Clear ownership; workstreams can ship alone.
- Combination uses a single, narrow seam.

Negative and risks:

- Orchestrator-to-tool calls add latency in a combined demo.
- A dedicated knowledge agent behind a tool hides its intermediate reasoning unless the tool
  response includes the query, retrieved subgraph and sources (handled in AD-004).
- Strands familiarity varies across the team; fallback is a custom Runtime agent with the same
  tool contract.
- Bedrock model access and AgentCore availability in the sandbox region are unverified.

Impact on other domains:

- Technology: two Runtime deployments, one Gateway, Bedrock access (AD-005).
- Data: none beyond AD-001.

## 7. Compliance and Traceability

- Tech: AgentCore for agent hosting; Bedrock for models; framework flexible; Gateway optional
  and mirrors the customer's tool-exposure plan.
- Vision: plan and tool calls visible; limitations reported instead of invented results.

## 8. Open Items

- Exact model ID, quotas, one-time Anthropic use-case form and SCP-allowed inference profile type in the sandbox account (work breakdown C-5).
- AgentCore availability in `us-east-1`: AWS documents Runtime, Gateway, Memory, Identity, built-in tools and Observability there; confirm in the sandbox account (C-5).
- Resolved 2026-11-02 (work breakdown D-6): the combined demo is a required deliverable. The
  orchestrator-calls-knowledge-tool-through-Gateway topology in this decision is therefore
  mandatory, not optional, and has defined degraded rungs (work breakdown section 12).

## 9. Review

Revisit if Strands blocks progress for more than the step-1 time-box in AD-002, or if the
combined demo needs richer agent-to-agent behavior than tool calls provide.
