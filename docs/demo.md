# Demo and verification

## Target workflow

1. Open Virtual Lab `/orion`. Choose “Female synthetic cases.”
2. Ask: “How is TP53 connected to multiple myeloma, what evidence supports the relationship, and what does the synthetic Virtual Lab cohort show?”
3. Watch entity resolution, graph connection, supporting evidence and cohort tool statuses.
4. Inspect a TP53 → deletion/inactivation → Multiple Myeloma path. Expand source cards to see original predicates, review/confidence status, retrieval dates and publication links.
5. Inspect the existing interactive Virtual Lab bar chart. Hover to see raw synthetic counts. The default female selection contains **500 of the sandbox's 1,000 fabricated cases**; do not hard-code a gene mutation percentage.
6. Save the selection using the existing Virtual Lab cohort modal, then open an existing gene view.
7. Ask: “Show related genes with similar phenotypes.” The agent must preserve the prior gene context and inspect/compare related genes. It should explain that this subset demonstrates shared myeloma associations, not proven identical gene-specific phenotypes.

## Required live AWS acceptance

| Requirement | Evidence to capture |
| --- | --- |
| Bedrock selects structured tools | UI shows Bedrock mode and completed typed tool names; inspect runtime logs if needed |
| AgentCore hosts the agent | Browser → server bridge → IAM-signed `InvokeAgentRuntime`; live smoke uses `--require-aws` |
| Neptune is actually queried | Response reports `graph_backend=neptune`; utility validation returns loaded TP53 evidence |
| Virtual Lab API is called | Guppy selection and ProteinPaint mutation-count tools succeed with the same filter |
| Existing chart renders | Interactive bars, raw-count hover and accessible data list agree |
| Sources are traceable | Publication/source links open; predicates/IDs/status are visible |
| Cohort data stays distinct | Synthetic badges, fabricated-case wording, no diagnosis or treatment recommendations |
| Follow-up works | Related genes response uses the previous context and reports evidence limits |
| Empty/error behavior | Empty synthetic selection gives no divide-by-zero or chart; unavailable API gives a visible failed tool |

The scripted rehearsal validates the same contracts but explicitly says no foundation model was called. It is useful for local preparation and does not satisfy the AWS rows above.

## Automated checks

```sh
uv run pytest
node --test frontend/tests/*.test.mjs
uv run ruff check src scripts tests terraform/utility.py
uv run python scripts/transform.py --curated-only
uv run python scripts/validate.py
terraform -chdir=terraform fmt -check
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

After applying the frontend overlay to the pinned source and installing its locked dependencies:

```sh
cd vendor/sandbox/.sources/frontend
npm ci
npx tsc --noEmit
npm run build
```

Run the original sandbox smoke tests after rebuilding its services as well. From a running bridge:

```sh
uv run python scripts/smoke_orion.py --url http://localhost:8082/agentcore --require-aws
```

## Verification in the implementation workspace

Python workflow/contract tests use a mocked Bedrock model and a strict synthetic API fixture. No customer endpoint is configured. Local raw Monarch and opt-in DisMech transformations can be checked without AWS access. Streaming parser tests use byte-split UTF-8 events and truncated/malformed responses.

Completed checks: 25 Python tests, 6 Node proxy/streaming tests, Python lint/compilation, all five overlay TypeScript/TSX files' syntax, Terraform formatting, and CSV integrity for both the portable curated graph and the local public/DisMech subsets. TypeScript syntax verification is not a full upstream type check.

Full upstream frontend installation and Terraform provider initialization could not complete because the host ran out of disk space. Docker's engine availability check did not complete. No full browser run or AWS deployment is claimed. Re-run those checks on a machine meeting the upstream disk/memory requirements before the live demo.
