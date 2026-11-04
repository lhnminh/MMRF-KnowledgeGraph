# Well-Architected Review: Workstream 1 Knowledge Graph

Date: 2026-11-04. Scope: `terraform/` and `ingestion/load_neo4j.py` as deployed today (one
Neptune cluster, two Lambdas, AgentCore Gateway). Findings below are grouped by Well-Architected
pillar, worst first in each pillar. "Fixed" items were applied directly to Terraform; everything
else is open.

## Security

- **Open — Neptune IAM database authentication is disabled.** `iam_database_authentication_enabled
  = false` (`terraform/main.tf`). Both Lambdas still SigV4-sign their Bolt connections
  (`terraform/lambda_build/handler.py`), but Neptune ignores credentials while this is off, so the
  real connection check is the hardcoded default password (`handler.py`, `ingestion/load_neo4j.py`).
  AD-004 D4 assumes IAM enforces read-only access; it does not yet. The only working control against
  `kg-graph` writing is the regex guard in `validate_readonly_query`. Not applied in this pass:
  flipping this on is a behavior change to how both Lambdas authenticate, not an additive
  Terraform change, and needs to be tested against a live connection.
- **Fixed — S3 ingest bucket had no explicit encryption or public access block.** Added
  `aws_s3_bucket_server_side_encryption_configuration` (AES256) and
  `aws_s3_bucket_public_access_block` (all four flags on) in `terraform/ingest.tf`.
- **Fixed — Neptune security group egress was wider than needed.** Removed
  `aws_vpc_security_group_egress_rule.neptune_vpc`, which allowed all protocols/ports from Neptune
  to the whole VPC CIDR. Security groups are stateful, so Neptune does not need an egress rule to
  reply to the kg-graph/kg-ingest Bolt connections already permitted by the ingress rules in
  `main.tf` and `ingest.tf`. Removing a standalone rule resource does not touch the security
  group's identity, so this does not force replacement.
- **Fixed — no Neptune audit logging.** Neptune's audit log parameter
  (`neptune_enable_audit_log`) can't be set on the default (read-only) cluster parameter group.
  Added `aws_neptune_cluster_parameter_group.this` (family `neptune1.4`, the cluster's current
  family) with the parameter set to `1`, pointed the cluster at it via
  `neptune_cluster_parameter_group_name`, and added `enable_cloudwatch_logs_exports = ["audit"]`
  plus a 14-day-retention log group (`/aws/neptune/mmrf-kg-cluster/audit`). This is the one
  mechanism that can catch a direct Bolt connection or query that bypasses the Lambdas' own
  `_log_call` logging — relevant given the IAM-auth gap above. Caveat: `neptune_enable_audit_log`
  is a static parameter; it takes effect only after the instance's next reboot, not immediately on
  apply.
- **Open — Gateway has no WAF or request throttling.** Internet-reachable with `AWS_IAM` auth only
  (`terraform/gateway.tf`); flagged as open in AD-005 and still open.
- **Open — no AgentCore-level tool scoping.** The Gateway's IAM policy grants
  `bedrock-agentcore:InvokeGateway` on the whole gateway (`terraform/gateway.tf`), so any caller
  holding that role can invoke all five `kg_*` tools, including the open-ended
  `kg_run_readonly_query`, with only the Lambda's own regex guard behind it. An
  `aws_bedrockagentcore_policy_engine` + `aws_bedrockagentcore_policy` (Cedar), attached via the
  gateway's `policy_engine_configuration`, would add tool-level scoping as a second, independent
  layer. See AD-006 for why this becomes a required control, not optional hardening, if PHI ever
  enters scope.
- **Open — X-Ray tracing is enabled but can't egress.** Both `kg-graph` and `kg-ingest` set
  `tracing_config { mode = "Active" }` (`terraform/loader_lambda.tf`, `terraform/ingest.tf`), but
  the VPC has no NAT/IGW and no X-Ray interface endpoint. Lambda's own function logs still reach
  CloudWatch regardless (that path goes through the Lambda service plane, not this VPC's routing),
  but X-Ray segments, and anything the code might call directly against Secrets Manager, KMS, or
  STS from inside the VPC, have no egress path and fail or are silently dropped. No interface
  endpoints are deployed today.

## Reliability

- **Fixed — Neptune had no deletion protection.** Set `deletion_protection = true`
  (`terraform/main.tf`). `scripts/teardown.sh` now disables it via `aws neptune modify-db-cluster
  --no-deletion-protection` immediately before `terraform destroy`, so intentional teardown still
  works; an accidental destroy or a Terraform change that forces replacement no longer can.
- **Open — single Neptune instance, no snapshot.** `skip_final_snapshot = true`, one
  `aws_neptune_cluster_instance` despite two private subnets across two AZs. An instance or AZ fault
  is a full outage with no recovery path; a `terraform apply` that forces replacement still destroys
  all data.
- **Open — no DLQ, retry, or alarms on either Lambda.** No `on_failure` destination, no CloudWatch
  alarms on Lambda errors, Neptune CPU/storage, or Gateway errors.
- **Open — ingest loader has no batching or backoff.** `ingestion/load_neo4j.py` issues one
  round-trip per node/edge with a 300s / 256MB Lambda. Fine at the current ~50-node scale; at 15x
  data volume (Open Targets + CIViC) this risks Lambda timeout or Neptune throttling with no retry
  to recover from a partial load.

## Performance Efficiency / Scalability

- **Open — row-by-row writes don't batch.** Same root cause as the reliability item above; this is
  the actual blocker to loading the larger datasets, not cluster size.
- **Open — `kg_run_readonly_query` bounds final rows (100) and hop count (5) but not intermediate
  cardinality.** A 5-hop pattern over a 15x-larger graph can still be expensive before truncation
  applies.

## Cost Optimization

- **Fixed — Lambda log groups had no retention.** Both `kg-ingest` and `kg-graph` previously
  relied on auto-created `/aws/lambda/...` log groups that never expire. Added explicit
  `aws_cloudwatch_log_group` resources with `retention_in_days = 14` (matching the Gateway's
  existing log group) in `terraform/ingest.tf` and `terraform/loader_lambda.tf`.
- **Open — Neptune Serverless floor (`min_capacity = 1`) runs 24/7**, with no scheduled stop/start
  or budget alarm. README already flags "Neptune bills while idle"; nothing currently catches a
  cluster left running.
- **Open — design drift from AD-005.** AD-005 split `kg-graph` (Neptune-only) from a planned
  `kg-external` specifically so Open Targets/CIViC would not need to live in the paid Neptune
  cluster. The README and AD-002 now describe loading them into Neptune directly instead. Not
  necessarily wrong, but reverses a cost-motivated decision without an ADR update.

## Operational Excellence

- **Open — no Terraform remote state or locking.** `terraform/versions.tf` has no `backend` block;
  state is local only.
- **Open — observability is `print()`-based JSON log lines**, no metrics or dashboards.
- **Open — data loading is fully manual** (`aws s3 cp` then `aws lambda invoke` per the README),
  with no automated check that a load succeeded before the graph is queried.
- **Open — tribal-knowledge comments instead of enforced guards.** `terraform/main.tf` has comments
  warning not to change specific lines because Terraform will force-replace the Neptune cluster,
  rather than a `lifecycle { prevent_destroy = true }` block or CI plan-gating.

## Summary of changes made in this pass

| File | Change |
|---|---|
| `terraform/main.tf` | `deletion_protection = true` on the Neptune cluster; removed the unused Neptune-to-VPC egress rule; added a cluster parameter group with `neptune_enable_audit_log = 1` and `enable_cloudwatch_logs_exports = ["audit"]` |
| `scripts/teardown.sh` | Disable Neptune deletion protection before `terraform destroy` |
| `terraform/ingest.tf` | Added S3 SSE (AES256) and public access block on the ingest bucket; added a 14-day-retention log group for `kg-ingest` |
| `terraform/loader_lambda.tf` | Added a 14-day-retention log group for `kg-graph` |

All changes in this pass are additive or remove an unused rule — none force Neptune or either
Lambda to be replaced. `terraform validate` and `terraform fmt` pass. **Not independently verified
against a live plan**: this environment's AWS session lacks `ec2:Describe*`, `iam:GetRole`, and
`rds:Describe*` permissions, so `terraform plan` cannot complete end-to-end here. Run
`terraform -chdir=terraform plan` with a role that has read access before applying, and confirm the
plan shows no "forces replacement" on `aws_neptune_cluster.this`.

None of these changes affect the earlier recommendation that Open Targets and CIViC load into the
same Neptune cluster as Monarch and DisMech — that call stands on the ID-join reasoning in AD-001.
The highest-priority remaining gaps before scaling the data volume are: enabling Neptune IAM
database authentication, and batching/bounding the ingest loader. Several other open items (single
Neptune instance, no final snapshot, local Terraform state, manual load process) are documented,
deliberate tradeoffs for this 2-day hackathon sandbox scope (AD-002, AD-005), not oversights — worth
revisiting only if this moves toward a longer-lived deployment.

## Data sensitivity / HIPAA

No data loaded today is real patient data (all sources are public or synthetic — see AD-006), so
HIPAA does not currently apply. AD-006 records that boundary as a decision rather than an
assumption, and lays out a pre-agreed readiness plan — AWS BAA and service-eligibility check,
closing the Neptune IAM-auth gap, de-identification at ingestion, extending audit logging to
Bedrock, AgentCore tool scoping, and Lambda failure handling — to execute only if a future data
source introduces real patient identifiers. See `docs/adrs/0006-data-sensitivity-scope-and-hipaa-readiness.md`.
