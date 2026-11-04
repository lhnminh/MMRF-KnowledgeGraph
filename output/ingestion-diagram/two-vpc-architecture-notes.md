# ORION architecture revision

Files: `slide-2-whats-new-two-vpc.png`, matching `.svg`, and `orion-two-vpc-agentcore-ingestion.drawio`.

The diagram follows the flat AWS Solutions convention, with two workload VPC boundaries and numbered flows. The legacy `kg-ingest` component is intentionally omitted from this view; this change does not delete any deployed function. No AWS infrastructure was changed.

## Verified deployment details — 4 November 2026, us-west-2

- VPC 1 hosts the Virtual Lab application / AgentCore client; VPC 2 hosts Neptune and the private graph-query and scheduled-ingestion Lambdas.
- The active AgentCore Runtime uses `PUBLIC` networking. Runtime and Gateway are therefore shown as AWS-managed services outside the two workload VPCs.
- Runtime execution role `mmrf-orchestrator-agent-runtime` permits `bedrock-agentcore:InvokeGateway`.
- Gateway execution role `mmrf-kg-gateway-role` permits `lambda:InvokeFunction` on the graph-query Lambda `mmrf-kg-kg-graph`.
- IAM authorizes these service calls. It does not provide a routed network connection between VPCs. Gateway invokes the VPC-attached Lambda through the Lambda service API. The Lambda then reaches Neptune privately over port 8182 under its VPC security groups.
- No active VPC peering was found. Neptune IAM database authentication is currently disabled, so the diagram does not claim IAM authenticates database queries.
- EventBridge runs the fetch Lambda daily at 02:00 UTC. Fetch archives bounded MM Monarch and DisMech snapshots and the manifest in S3, then directly invokes `source-pipeline-ingest` in the Neptune VPC. Ingestion reads through the S3 gateway endpoint, verifies snapshot hashes, performs parameterized incremental writes, and validates the graph.
- ClinicalTrials.gov / PubMed enrichment, Open Targets, and additional MM/JMML artifacts are shown as manually staged sources. They are outside the daily MM-only refresh shown here.
- Initial bulk loading is not depicted in this daily-refresh view. There are no Step Functions.

## AWS references

- [Agentic AI factory on Amazon Bedrock AgentCore](https://docs.aws.amazon.com/solutions/building-an-agentic-ai-factory-on-amazon-bedrock-agentcore/)
- [Gateway VPC access — Lambda invocation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
- [AgentCore Runtime VPC configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)
- [AWS Data Transfer Hub architecture](https://docs.aws.amazon.com/solutions/latest/data-transfer-hub/architecture-overview.html)

## Repository replacement

The isolated sandbox fork checkout has these three replacement files:

- `docs/diagrams/slides/slide-2-whats-new.png`
- `docs/diagrams/mmrf-chat-architecture-whats-new.png`
- `docs/diagrams/mmrf-chat-architecture-whats-new.drawio`

`update-ingestion-diagram-two-vpc.patch` contains the complete binary diff against that checkout's `origin/main`. It can be applied in a matching checkout with `git apply /path/to/update-ingestion-diagram-two-vpc.patch`.

The GitHub fork has not been updated: the earlier push was rejected because the authenticated GitHub account lacks write access. The PNG and SVG were rendered locally with browser networking disabled and zero network requests.
