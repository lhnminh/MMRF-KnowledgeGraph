# AD-005: Shared Infrastructure, Network and Terraform Ownership

| Field | Value |
|---|---|
| Decision ID | AD-005 |
| Status | Proposed |
| Date | 2026-11-02 |
| Architecture domain | Technology Architecture |
| Scope | ORION Hackathon, Workstream 1 and Workstream 2 |
| Stakeholders | Hackathon team, customer platform team |
| Depends on | AD-002, AD-003, AD-004 |
| Source documents | `vision/Tech_ORION-revised.docx`, `src/mmrf-virtual-lab-sandbox/terraform/`, `docs/aws-deployment.md` |

## 1. Context

Both workstreams run in the same empty AWS sandbox account. Terraform is preferred.

What the sandbox provides (read from `terraform/` and `docs/aws-deployment.md`):

- A new VPC (default `10.86.0.0/16`, tagged `Name = <deployment name>`, default `mmrf-synthetic`,
  default region `us-east-1`).
- Two subnets in two Availability Zones, both public (internet gateway route, public IP on launch).
- An EKS cluster with one worker node, six ECR repositories.
- An optional private ALB behind a CloudFront distribution. The ALB security group accepts only
  the CloudFront origin-facing prefix list; the cluster accepts NodePort 30080 only from that
  security group.
- Terraform outputs are cluster name, region, ECR URLs, frontend access, HTTPS URL, origin CIDRs
  and target group ARN. No VPC ID or subnet IDs are exported.
- Locally, the Virtual Lab is served at `http://localhost:3080`.
- The sandbox README says application changes belong in their own repositories; the sandbox
  Terraform is the Workstream 2 foundation.

Documented platform constraints (AWS documentation, checked 2026-11-02):

- Neptune DB subnet groups need subnets in at least two Availability Zones, and Neptune is
  reached from inside a VPC.
- AgentCore Runtime can run in VPC mode: it needs at least one subnet and one security group,
  creates network interfaces in the subnet, and the security group controls what it can reach.
- AgentCore Gateway can reach self-hosted MCP servers in a VPC through private endpoints
  (VPC Lattice).
- Outbound access from private subnets to Bedrock and other AWS services needs a NAT gateway or
  VPC endpoints.

## 2. Decision Drivers

- D1. Workstreams must deploy and tear down independently.
- D2. Do not edit the sandbox Terraform or its state for Workstream 1.
- D3. Neptune must be private and reachable from the knowledge tool backend.
- D4. The Workstream 2 agent must reach the Virtual Lab in the sandbox deployment.
- D5. Keep cost and setup time low for a 2-day build.

## 3. Options Considered

| Option | Summary |
|---|---|
| A. Separate VPC for the knowledge graph, separate Terraform root (selected) | Own VPC, private subnets, Neptune, Lambda in VPC; no peering |
| B. Reuse the sandbox VPC via data lookup, new private subnets | Shared network; Terraform root reads sandbox resources by tag |
| C. Reuse the two existing public subnets for Neptune | No new subnets; not private |
| D. Edit the sandbox Terraform to export IDs and add Neptune | Single state; couples both workstreams |

## 4. Decision

1. Workstream 1 gets its own VPC and its own Terraform root. Placement proposed under
   `src/mmrf-breakingbarriers` (currently empty); the team confirms the location.
   - Two private subnets in two AZs, no internet gateway route.
   - Neptune cluster in a DB subnet group over those subnets; a security group allowing the
     Neptune port only from the tool backend's security group.
   - Knowledge tool backends exposed through Gateway (AD-004) as two Lambda functions (work
     breakdown D-3, decided 2026-11-02): `kg-graph`, attached to the VPC, talks to Neptune only;
     `kg-external`, not attached to any VPC, serves Open Targets and CIViC from a versioned S3
     snapshot, may call the live APIs when a flag is on (off in the demo), and invokes `kg-graph`
     for DisMech content.
   - The S3 gateway endpoint is the only VPC endpoint (free; needed by the Neptune bulk loader).
     No NAT gateway and no internet gateway in this VPC.
2. The sandbox Terraform is not modified for Workstream 1.
3. The Workstream 2 agent does not need a VPC attachment. It reaches the Virtual Lab through the
   sandbox's HTTPS entry point (CloudFront), and reaches knowledge tools through Gateway over
   HTTPS. No VPC peering is created.
4. Local-only development: a cloud-hosted agent cannot reach `localhost:3080`. Until the sandbox
   is deployed to AWS, Workstream 2 agent code is run and tested locally against the local stack.
5. Each root has its own state and a documented teardown order; Workstream 1 teardown does not
   depend on the sandbox and the reverse.
6. Shared prerequisites verified once at the start and recorded: Bedrock model access, AgentCore
   availability in the region, and permissions in the sandbox role.
7. Because the combined demo is required (work breakdown D-6) and the orchestrator runs in AWS,
   the fork build is deployed to the AWS sandbox early, starting in P0 rather than waiting for
   the end of development. Local-only development remains a fallback rung, not the plan. The
   demo has three topology rungs (work breakdown section 12) so a failure at one layer does
   not remove the combined demo.

## 5. Rationale

- Separate VPCs let each workstream deploy and tear down alone (D1) and avoid editing the sandbox
  Terraform or its state (D2).
- The sandbox VPC has no private subnets and exports no IDs. Option B would need tag-based lookups
  and still add subnets inside a network another team owns; Option C places a database in
  public subnets; Option D couples state and review.
- The only cross-workstream traffic (orchestrator to knowledge tool) is HTTPS through Gateway, so
  peering and shared routing are unnecessary (D3, D4).
- Neptune stays private and reachable only from its backend (D3).
- A second VPC adds setup (D5); this is accepted for isolation. With no NAT and no internet
  gateway, its only standing cost is the Neptune cluster, since the S3 gateway endpoint is free.
  A NAT gateway would add about $0.045 per hour plus $0.045 per GB processed and an outbound path
  from a function that can reach the database; it was rejected.

## 6. Implications

Positive:

- No change to the sandbox repository.
- Clean teardown; Neptune and endpoints can be destroyed without affecting the Virtual Lab.
- Combination happens at one HTTPS seam.

Negative and risks:

- Duplicate network infrastructure and its cost.
- The VPC-attached function has no internet path. Open Targets and CIViC are reached only by
  `kg-external` (outside the VPC), snapshot first. This adds a second function and a cross-invoke
  hop for tools that combine DisMech and supplement data, and shared envelope code to package.
- The Gateway endpoint is reachable from the public internet with authentication; authentication
  design is open (AD-004).
- Whether the Workstream 2 agent can authenticate to the Virtual Lab's CloudFront entry point
  (password-protected) is not yet verified. Cloud-hosted agents may need a stored credential or
  a different path.
- Neptune bulk load from S3 needs an IAM role for Neptune and S3 connectivity from the VPC; for
  the very small DisMech dataset, parameterized openCypher writes from the backend are a
  fallback. The documented requirements should be re-checked before step 2.
- A Neptune cluster costs money while idle; teardown must be scripted.

Impact on other domains:

- Application: tool backends are Lambda in VPC (AD-004).
- Governance: credentials stay in the sandbox; no customer data or credentials (Tech document).

## 7. Compliance and Traceability

- Tech: Terraform preferred; managed services for new components; Neptune preferred; reuse the
  supplied deployment and focus on new capabilities; no real customer data or credentials.
- Sandbox: Terraform, Helm, compose and application source remain unmodified. The team's sandbox fork changes only `sources.json` and the source-fetch script's local-source override (work breakdown D-4, Option C).

## 8. Open Items

- Repository location: work breakdown D-7, Option A chosen (a new GitHub organization holding the team repository and both forks), provisionally public. Organization name chosen: `breaking-barriers-myeloma`. Owners and members are still to be provided; until the remote exists the repository is local git at `src/mmrf-breakingbarriers`. The Terraform roots live under it.
- NAT versus VPC endpoints: resolved (work breakdown D-3): neither; S3 gateway endpoint only, two Lambdas, snapshot-first. Open: terms of use for automated queries to Open Targets and CIViC.
- Agent authentication path to the Virtual Lab entry point. **Decided 2026-11-02 (lead):** the
  orchestrator's Virtual Lab tools call the in-cluster services (Analysis API, Guppy, ProteinPaint)
  directly from inside the sandbox VPC, not through CloudFront or the inner team login. The
  network path was mapped from the code (the in-cluster `identity` service mints a valid token for
  any caller that can reach it, so network reach alone would be the only control). **Path decided
  2026-11-02:** the orchestrator Runtime stays in PUBLIC network mode; a `vl-tools` Lambda inside the
  sandbox VPC is the only client of a new internal entry to `identity`, and the orchestrator reaches
  that Lambda through an AgentCore Gateway target (SigV4/IAM), the same pattern as the knowledge
  tools. The Lambda forwards only an allow-list of read-only routes. The sandbox-fork changes this
  needs (Helm NodePort Service and NetworkPolicy for `identity`, a security-group rule, launcher
  values) were approved by the lead as a further D-4 exception.
- **Frontend audience and gate, decided 2026-11-02:** the deployed frontend fork is for the lead
  only, signed in through Midway. Mechanism: CloudFrontSigner (CFS), the Midway integration for
  CloudFront, in trusted-signer mode with the V2 API and external JS
  (`CloudFrontSignerConstructs` 6.x supports this combination; it needs no key pair, secret or
  custom-resource Lambda). CloudFront trusts the CFS account `076938169600` on every behavior
  except `/js/cfs-handler.js`; unsigned requests get `/error/403.html`, which sends the browser
  to CFS; CFS signs in with Midway and checks a Bindle that grants only the lead. The
  `403.html` and `cfs-handler.js` are CFS's own files, used unmodified (the HTML is
  hash-checked). Deploy order: gate first, verify an anonymous request gets the CFS redirect,
  then deploy the app. Consequences: signed cookies last 15 minutes and CFS does not
  re-authenticate `fetch` calls, so the chat panel must detect an expired session and reload
  through the gate; the orchestrator cannot come in through CloudFront and must reach the
  internal ALB inside the sandbox VPC. The sandbox's own gate (one shared username and password
  in a Kubernetes secret) is not relied on for this deployment. Bindle:
  `amzn1.bindle.resource.35vyrsvmdozxqvx74lpbnup2a` (kurianpv's PersonalSoftwareBindle, owner
  kurianpv; checked 2026-11-02). **Open:** where the gate's Terraform lives (the D-4 conflict).
- Browser-to-agent path: a Next.js API route proxying to AgentCore with streaming (AD-004);
  how it authenticates to AgentCore and where its credentials live. This gates demo rung R1
  (work breakdown D-9, target R1): the frontend runs in the EKS pod, which has no AWS credentials.
  Options in preference order, to be settled by spike O-1.8: a pod identity or IRSA role created
  from this Terraform root without editing the sandbox's Terraform; a Cognito JWT on the Runtime;
  an access key in a Kubernetes secret (not recommended). AgentCore Runtime's default inbound
  authentication is IAM SigV4, with JWT as the alternative.
  **Decided 2026-11-03 (lead), O-1.8 option A:** EKS Pod Identity. A role
  `<name>-frontend-agent` trusts `pods.eks.amazonaws.com` only for this cluster's
  `mmrf-synthetic/frontend` service account (session-tag conditions) and may only
  `bedrock-agentcore:InvokeAgentRuntime` on the one orchestrator Runtime (ARN fixed in Terraform and in
  the pod's env, never taken from the request). The pod reaches AgentCore through a private
  data-plane interface endpoint (`com.amazonaws.<region>.bedrock-agentcore`, private DNS, 443 from the
  node subnets, endpoint policy limited to that role and Runtime); a frontend-only NetworkPolicy opens
  just that and the Pod Identity agent, so the pod still has no internet egress. No key is stored.
  The route `/api/agent/ask` also requires a signature-checked Gen3 login and sits behind the CFS gate.
  **Deviations:** (1) the resources live in the sandbox fork's Terraform (`terraform/agent_access.tf`,
  Helm `agent-access.yaml`, launcher `agent-access`), not in this root: the endpoint's interfaces sit
  in the sandbox VPC and would block the sandbox's own destroy if another root owned them. This is a
  third D-4 exception, approved by the lead; the other lead's review is owed. (2) The route is
  request/response, not streaming; the panel shows a working state until the answer arrives.
- Region decided: `us-east-1` (work breakdown D-5). Bedrock model access, quotas and AgentCore usability in the account are confirmed in C-5.
- Step 1 time-box decided: 2.5 hours with milestones M1 to M3 (AD-002, work breakdown D-1). Preparing and testing the Terraform before day 1 is permitted (story K-1.0). Open: whether other pre-day-1 preparation is permitted.

## 9. Review

Revisit if org policies block network creation (the sandbox deployment notes this can happen even
with administrator access), or if peering or a shared VPC becomes necessary for the combined demo.
