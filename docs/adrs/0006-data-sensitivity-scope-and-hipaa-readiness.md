# AD-006: Data Sensitivity Scope and HIPAA Readiness

| Field | Value |
|---|---|
| Decision ID | AD-006 |
| Status | Proposed |
| Date | 2026-11-04 |
| Architecture domain | Data Architecture, Governance |
| Scope | ORION Hackathon, Workstream 1 (Knowledge Graph) |
| Stakeholders | Hackathon team, customer platform team |
| Related decisions | AD-001 (data sources), AD-005 (shared infrastructure) |
| Source documents | `README.md`, `docs/data-terms.md`, `docs/well-architected-review.md` |

## 1. Context

Every data source loaded today is public or synthetic: Monarch, DisMech, Open Targets and CIViC
are public biomedical databases (AD-001); the Virtual Lab's cohort and case data is generator-built
synthetic data, not real patient records (`docs/data-terms.md`). The README states this directly:
"No real patient data, customer credentials, or customer infrastructure is needed."

The ADRs also name a "customer platform team" stakeholder throughout, and AD-004 already flags one
forward-looking gap: "ProteinPaint's academic-use license needs addressing for ORION production
use (not for the hackathon)." That pattern — a decision explicitly scoped to the hackathon, with a
known gap deferred to a later production build — applies to patient-data handling as well, and
unlike the ProteinPaint license it had no recorded decision. This ADR records one: HIPAA does not
apply to the system as built, and states what would need to change before it could.

## 2. Decision Drivers

- D1. Do not carry compliance burden the current scope does not need.
- D2. Do not let a future production build (with real patient data) start from a stack that was
  never evaluated against HIPAA.
- D3. Make the "no PHI today" boundary a recorded, falsifiable decision, not an assumption that
  erodes silently as data sources are added (AD-001's "other two datasets" question already shows
  that scope grows).

## 3. Decision

1. **No PHI or real patient data is in scope for this system today.** All four knowledge-graph
   sources (Monarch, DisMech, Open Targets, CIViC) are public; Virtual Lab data is synthetic
   (`docs/data-terms.md`). HIPAA's Security and Privacy Rules do not attach to the system as
   currently deployed.
2. **This boundary is a condition, not a fact about the domain.** It holds only as long as every
   data source loaded into Neptune, passed through the Lambdas, or sent to a Bedrock model remains
   public or synthetic. Any future change that introduces a real patient identifier, a real clinical
   record, or any of the 18 HIPAA identifiers alongside health information crosses this boundary
   and makes the plan in section 4 mandatory, not optional.
3. **The plan in section 4 is pre-agreed**, so that crossing the boundary is a scheduling and
   execution decision, not a fresh architecture review done under deadline pressure.

## 4. HIPAA Readiness Plan (triggered only if PHI enters scope)

If a future production build introduces real patient data, the following become required before
go-live, in rough dependency order:

1. **Execute an AWS BAA; confirm every service in the data path is HIPAA-eligible.** AWS requires a
   signed Business Associate Addendum at the account/org level before PHI touches any AWS service.
   Neptune, Lambda, and S3 are on AWS's published HIPAA-eligible services list as of this writing;
   **Bedrock AgentCore's HIPAA eligibility is not yet confirmed and must be checked against AWS's
   current list before relying on it to carry PHI.** If AgentCore is not eligible at that time, the
   Gateway/Runtime layer would need to move to an eligible alternative or be re-scoped so PHI never
   reaches it (for example, by de-identifying before the tool-call boundary, per item 3).
2. **Close the Neptune IAM-authentication gap.** `iam_database_authentication_enabled = false`
   (`terraform/main.tf`) means Neptune ignores the Lambdas' SigV4 signatures and falls back to a
   hardcoded default password (`terraform/lambda_build/handler.py`,
   `ingestion/load_neo4j.py`). This is a tracked gap today (`docs/well-architected-review.md`) and
   would become a hard blocker under HIPAA's unique-user-identification and access-control
   requirement (45 CFR §164.312(a)).
3. **De-identify or tokenize at the ingestion boundary.** Prefer keeping PHI out of the graph
   entirely: apply Safe Harbor de-identification (45 CFR §164.514) or replace patient identifiers
   with cohort-level or synthetic surrogate keys before data reaches Neptune or any Bedrock prompt,
   so the knowledge-graph and agent layers reason over de-identified data even in production.
4. **Extend audit logging to cover every place PHI could appear.** Neptune's audit log
   (`aws_neptune_cluster_parameter_group.this`, added in this pass) and the Gateway's existing
   CloudWatch log delivery (`terraform/gateway.tf`) cover the graph and tool-call layers. HIPAA's
   audit-controls requirement (45 CFR §164.312(b)) would also require covering Bedrock model
   invocations, since a prompt or response could contain PHI if de-identification in item 3 ever
   fails or is bypassed.
5. **Scope tool access with an AgentCore policy (Cedar).** `aws_bedrockagentcore_policy_engine` and
   `aws_bedrockagentcore_policy`, attached to the gateway via `policy_engine_configuration`, can
   restrict which caller may invoke which named tool, independent of the Lambda's own logic. Today
   every caller holding the orchestrator IAM role can invoke all five `kg_*` tools, including the
   open-ended `kg_run_readonly_query`, with only a regex guard behind it
   (`terraform/lambda_build/handler.py`). Under HIPAA's minimum-necessary standard (45 CFR
   §164.502(b)), this stops being optional hardening and becomes a required control: at minimum,
   restrict which roles may reach `kg_run_readonly_query` versus the four deterministic tools.
6. **Add Lambda failure handling and a documented incident-response path.** Neither Lambda has an
   `on_failure` destination or CloudWatch alarms today. HIPAA's contingency-plan and
   incident-response requirement (45 CFR §164.308) needs both a technical control (DLQ, alarms) and
   a written procedure.

## 5. Rationale

- Building HIPAA controls now, with no PHI in the system, would be speculative work the current
  scope does not need (D1) and would directly contradict the 2-day hackathon time-box (AD-002).
- Recording the plan now, rather than after a production decision is made, means the team is not
  doing compliance architecture under deadline pressure, and means the "no PHI" boundary is checked
  against every new data source instead of assumed (D2, D3).
- Several items in section 4 (Neptune IAM auth, audit logging, AgentCore tool scoping) are already
  tracked as general hardening in `docs/well-architected-review.md`; this ADR does not duplicate
  that work, it marks which of those items become mandatory, not optional, once PHI is in scope.

## 6. Implications

Positive:

- No compliance overhead added to the current hackathon build.
- A future production proposal has a concrete, pre-reviewed checklist instead of starting from
  zero.

Negative and risks:

- The "no PHI" boundary is a people-and-process control, not a technical one: nothing in the
  current stack detects or blocks PHI from being loaded. If a future data source is added without
  revisiting this ADR, the boundary could be crossed silently.
- AgentCore's HIPAA eligibility is unconfirmed and is a dependency for item 1; this could force a
  different Gateway/Runtime approach for a production build.
- Section 4 is a plan, not an implementation; none of its items are built, and some (BAA, legal
  review of de-identification approach) need the customer platform team and are outside this
  team's authority to execute alone.

Impact on other domains:

- Data: any future AD-001 revision that adds a data source must state explicitly whether the
  source could contain real patient data, so this ADR's boundary stays enforceable.
- Technology: items 2 and 5 above overlap with `terraform/main.tf` and `terraform/gateway.tf`
  changes already tracked in `docs/well-architected-review.md`.

## 7. Compliance and Traceability

- Vision/Tech documents: no real patient data, customer credentials, or customer infrastructure
  required for the hackathon (README).
- `docs/data-terms.md`: confirms all loaded sources are public or synthetic.

## 8. Open Items

- Confirm Bedrock AgentCore's current HIPAA-eligible-services status before any production
  go/no-go decision.
- Legal/compliance review of the proposed de-identification approach (item 3) once a production
  data source is named; Safe Harbor sufficiency depends on the specific fields involved.
- Whether a production build would run in a separate AWS account from this sandbox, to keep a BAA's
  scope minimal; not assessed here.

## 9. Review

Revisit the moment any future ADR proposes loading a data source that could contain real patient
identifiers, or if the customer platform team raises a production timeline. Until then, no action
in section 4 is required.
