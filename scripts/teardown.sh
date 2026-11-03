#!/usr/bin/env bash
# Tear down the Workstream 1 knowledge graph. Neptune bills while idle, so teardown is scripted.
# Does not touch the sandbox (Workstream 2).
#
# THIS DELETES NEPTUNE AND ITS DATA, WITH NO FINAL SNAPSHOT.
#
# Usage:  scripts/teardown.sh                 # shows the destroy plan and asks "yes"
#         scripts/teardown.sh -auto-approve   # no prompt
#
# Works in macOS/Linux shells and Git Bash on Windows. Needs terraform, the AWS CLI and
# credentials for the account (run `aws login` first). Run it from the machine or backend
# that holds the Terraform state; without the state Terraform cannot see what to destroy.
set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/../terraform" && pwd)"
NAME="${TF_VAR_name:-mmrf-kg}"
INGEST_LAMBDA="$NAME-ingest"
ZIP="$TF_DIR/lambda_loader.zip"

for cmd in terraform aws; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd not found on PATH" >&2; exit 1; }
done

echo "== AWS identity"
aws sts get-caller-identity --output table \
  || { echo "error: no AWS credentials; run 'aws login' first" >&2; exit 1; }

terraform -chdir="$TF_DIR" init -input=false >/dev/null
MAIN_STATE="$(terraform -chdir="$TF_DIR" state list 2>/dev/null || true)"

# Region comes from the deployed Neptune endpoint, not from variables, so every check looks
# where the stack actually is.
ENDPOINT="$(terraform -chdir="$TF_DIR" output -raw neptune_cluster_endpoint 2>/dev/null || true)"
REGION="$(printf '%s' "$ENDPOINT" | sed -nE 's/.*\.([a-z]{2}-[a-z]+-[0-9]+)\.neptune\.amazonaws\.com$/\1/p')"
REGION="${REGION:-${AWS_REGION:-us-west-2}}"
echo "== Stack region: $REGION"

if [ -z "$MAIN_STATE" ]; then
  echo "Terraform state is empty in $TF_DIR: nothing to destroy (or the state is elsewhere)."
  exit 0
fi

# Terraform evaluates filebase64sha256() even on destroy, so a checkout without the built
# Lambda zip fails. A temporary empty placeholder is enough for destroy.
CREATED_ZIP=0
if [ ! -f "$ZIP" ]; then
  : > "$ZIP"
  CREATED_ZIP=1
fi
cleanup() { if [ "$CREATED_ZIP" = 1 ]; then rm -f "$ZIP"; fi; }
trap cleanup EXIT

# Terraform orders the deletes from the dependency graph (Gateway target before Gateway,
# Neptune instance before cluster, Lambda before its subnets and security group). Lambda
# VPC network interfaces can take 20+ minutes to release, so the last step may be slow.
echo "== terraform destroy"
terraform -chdir="$TF_DIR" destroy "$@"

echo "== Checking nothing billable is left in $REGION"
LEFT=0
check() {  # check <label> <command...>: the command succeeding means the resource still exists
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  STILL EXISTS: $label"; LEFT=1; else echo "  gone: $label"; fi
}
check "Neptune cluster $NAME-cluster" \
  aws neptune describe-db-clusters --region "$REGION" --db-cluster-identifier "$NAME-cluster"
check "Lambda $NAME-kg-graph" \
  aws lambda get-function --region "$REGION" --function-name "$NAME-kg-graph"
check "Lambda $INGEST_LAMBDA" \
  aws lambda get-function --region "$REGION" --function-name "$INGEST_LAMBDA"

GATEWAYS="$(aws bedrock-agentcore-control list-gateways --region "$REGION" \
  --query "items[?name=='$NAME-gateway'].gatewayId" --output text 2>/dev/null || true)"
if [ -n "$GATEWAYS" ] && [ "$GATEWAYS" != "None" ]; then
  echo "  STILL EXISTS: AgentCore Gateway $NAME-gateway"; LEFT=1
else
  echo "  gone: AgentCore Gateway $NAME-gateway"
fi

if [ "$LEFT" = 1 ]; then
  echo "Some resources remain; check the console or rerun this script." >&2
  exit 1
fi
echo "Teardown complete."
