# Deployment and teardown

This is a disposable hackathon stack. AWS deployment has not been performed in the implementation workspace. Finish the live acceptance gate before presenting it as an AgentCore/Neptune demonstration.

## 1. Deploy the pinned synthetic Virtual Lab

Authenticate to the **empty sandbox account**, using its own AWS profile. Do not reuse a customer profile. Install Docker, Git, Python/uv, AWS CLI v2, kubectl, Helm and Terraform 1.9+.

```sh
export AWS_PROFILE=YOUR_SANDBOX_PROFILE
export AWS_REGION=us-east-1
uv run python scripts/bootstrap.py --without-overlay
export ORION_ROOT="$PWD"
cd vendor/sandbox
python3 scripts/aws_sandbox.py doctor
python3 scripts/aws_sandbox.py init --profile "$AWS_PROFILE" --account-id YOUR_SANDBOX_ACCOUNT_ID
python3 scripts/aws_sandbox.py deploy
cd "$ORION_ROOT"
```

This bootstrap fetches pristine sandbox-pinned sources for the first upstream AWS deployment. Apply the overlay and rebuild just the frontend in step 5. If you already applied ORION locally, use a separate pristine sandbox checkout for the first upstream deployment; do not discard your edited frontend files.

The upstream [AWS deployment guide](https://github.com/theMMRF/mmrf-virtual-lab-sandbox/blob/2e47ab88baf57c2b8af1c388b6b48fca93ea5ac5/docs/aws-deployment.md) documents its operator role, endpoint CIDR restrictions, image publication and Helm deployment. Keep its password-protected browser entry.

## 2. Provision graph resources

```sh
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit sandbox_account_id, region, eks_cluster_name and permitted Bedrock model ARNs.
terraform -chdir=terraform init
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
terraform -chdir=terraform plan -out=orion.tfplan
terraform -chdir=terraform apply orion.tfplan
```

The AWS provider restricts deployment to the explicit account ID. The stack reuses the upstream EKS VPC, creates two private /24 subnets (indices 100 and 101 by default), encrypted Neptune with IAM database authentication, a same-Region S3 bucket, its read/list loader role and cluster association, an S3 gateway endpoint, interface endpoints, a VPC-connected utility Lambda, an ECR repository, an internal tools ALB, and bridge/runtime IAM roles. The runtime is created after its image URI is supplied.

Check that the subnet indices are unused. Choose **AgentCore-supported AZ IDs**, not arbitrary account-specific AZ names; defaults are `use1-az1` and `use1-az2`. Change `availability_zone_ids` for another Region. Confirm model access in the sandbox account. The default is Nova Pro through Bedrock Converse. For an inference profile, set both its ID and the exact required profile/destination model resource ARNs.

Terraform owns the internal ALB's registration with the sandbox node group's ASG. A dedicated NodePort points to the existing identity gateway. Runtime → ALB and ALB → node access use security-group references. No Kubernetes ClusterIP is assumed reachable directly from AgentCore. This avoids adding an AWS load-balancer controller.

If the EKS OIDC provider already exists, import that provider into `aws_iam_openid_connect_provider.eks` before applying; do not create a duplicate. The pinned upstream sandbox does not create one. The deploying principal needs service-linked-role creation permission for AgentCore VPC networking. AgentCore-managed ENIs can persist after runtime deletion; see teardown below.

## 3. Bulk-load Neptune

```sh
uv run python scripts/transform.py --curated-only
uv run python scripts/validate.py
export ORION_GRAPH_BUCKET="$(terraform -chdir=terraform output -raw graph_bucket)"
export ORION_UTILITY="$(terraform -chdir=terraform output -raw graph_utility_function)"
uv run python scripts/bulk_load.py --bucket "$ORION_GRAPH_BUCKET" --function "$ORION_UTILITY" --region "$AWS_REGION"
uv run python scripts/validate.py --function "$ORION_UTILITY"
```

The script checks the bucket Region and CSV integrity, uploads only the two CSVs into `graph/<content-hash>/`, and invokes the Lambda. **The Lambda**, inside the VPC, calls Neptune's bulk loader using `format=opencypher`, then checks job status. Failed loads fail the command. Initial loads never use per-row Cypher mutations.

Remote validation checks node/edge counts, the TP53–myeloma assertion and missing provenance. Content hashes make the source prefix repeatable. Existing node properties are not silently overwritten; for this tiny disposable graph use a fresh dataset/cluster when changing its schema. The operator needs S3 upload/list/location and Lambda invoke permissions; the runtime has graph read permission only.

## 4. Publish and host the agent

```sh
export ORION_REPOSITORY="$(terraform -chdir=terraform output -raw agent_repository)"
export ORION_REGISTRY="${ORION_REPOSITORY%%/*}"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ORION_REGISTRY"
# ARM64 is required by AgentCore; the existing EKS worker is x86_64.
docker buildx build --platform linux/arm64,linux/amd64 -t "$ORION_REPOSITORY:demo-1" --push .
# Add runtime_image_uri = "<repository>:demo-1" to terraform/terraform.tfvars.
terraform -chdir=terraform plan -out=orion.tfplan
terraform -chdir=terraform apply orion.tfplan
uv run python scripts/render_deployment.py --image "$ORION_REPOSITORY:demo-1" --region "$AWS_REGION"
kubectl apply -f deploy/orion.rendered.yaml
kubectl -n mmrf-synthetic rollout status deployment/orion-bridge
```

The image implements [AgentCore's HTTP contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-configure-invocation.html): `GET /ping`, `POST /invocations`, port 8080. Bedrock chooses tools through Converse; Python executes validated typed functions. The bridge uses IRSA to invoke the exact runtime. AWS credentials stay server-side. Stateless bounded conversation history travels with each invocation; no cross-user process memory is used.

The manifests assume the upstream namespace and Helm release are `mmrf-synthetic`, and node-group name `synthetic`. If renamed, update the bridge trust policy, release label and manifest namespace together. The graph stack's model calls and image pulls use VPC endpoints; no NAT gateway is added.

## 5. Rebuild the existing frontend and connect the bridge

The overlay must be present before building the sandbox frontend. Build using its original Dockerfile and sanitizer, preserving the pinned dependencies and existing components.

```sh
# Apply once to the pristine pinned checkout; reruns permit identical ORION files.
uv run python scripts/apply_frontend.py vendor/sandbox/.sources/frontend
cd vendor/sandbox
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build frontend
# Publish the rebuilt image to the sandbox frontend ECR repository with a new immutable tag.
# Use the image repository returned by the upstream Terraform output image_repositories.
# Update the upstream Helm values' images.frontend to that tag, then helm upgrade.
cd "$ORION_ROOT"
kubectl -n mmrf-synthetic set env deployment/frontend ORION_SERVER_URL=http://orion-bridge:8080/agentcore
kubectl -n mmrf-synthetic rollout status deployment/frontend
```

For a concrete frontend push/update, after setting `ORION_FRONTEND_REPOSITORY` to the upstream frontend ECR URI:

```sh
docker tag mmrf-sandbox/frontend:local "$ORION_FRONTEND_REPOSITORY:orion-demo-1"
docker push "$ORION_FRONTEND_REPOSITORY:orion-demo-1"
kubectl -n mmrf-synthetic set image deployment/frontend "frontend=$ORION_FRONTEND_REPOSITORY:orion-demo-1"
```

Record the same image URI and `ORION_SERVER_URL` environment in the durable Helm workload configuration before the next upstream deploy; imperative changes are otherwise overwritten by Helm. The upstream sanitizer replaces historical fixture values; do not ship an unsanitized frontend image.

## 6. Live verification

```sh
kubectl -n mmrf-synthetic port-forward service/orion-bridge 8082:8080
# In another shell, from this repository:
uv run python scripts/smoke_orion.py --url http://localhost:8082/agentcore --require-aws
```

Then open the sandbox HTTPS URL at `/orion` and complete [the browser checklist](demo.md). This is the required live gate, including model selection, Neptune queries, the actual developer API, chart rendering, provenance and the follow-up.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `agent_unavailable` | Runtime logs, Bedrock model entitlement/ARNs, Region and allowed model policy |
| `tool_unavailable` for graph | Neptune writer readiness, IAM read action, SG port 8182 and private DNS |
| Runtime VPC creation fails | Supported AZ IDs, deployer's service-linked-role permission, interface endpoint availability |
| Guppy/ProteinPaint error | Internal ALB health, NodePort 30081 selector, node SG, network policy and identity gateway |
| Bridge cannot invoke runtime | Service-account IRSA annotation, OIDC trust, STS reachability and runtime ARN |
| Unsupported cohort filter | Use explicit `SYNTH-CASE-*` selections or gender; other fields are intentionally outside this demo |
| No streamed tool statuses | Check proxy buffering and gateway/CloudFront idle timeouts; no long silent reasoning steps are expected for the small workflow |
| Frontend build or Terraform init fails on disk | Upstream sandbox needs 30 GiB free; free workspace capacity before building |

## Teardown

First remove the frontend's bridge URL, then the ORION workloads. Commands below delete only the ORION bucket prefix and ORION image repository content after you have verified their values.

```sh
kubectl -n mmrf-synthetic set env deployment/frontend ORION_SERVER_URL-
kubectl delete -f deploy/orion.rendered.yaml
aws s3 rm "s3://$ORION_GRAPH_BUCKET/graph/" --recursive
# Delete the demo tags/manifests from ORION_REPOSITORY (list before deleting).
aws ecr list-images --repository-name orion-mmrf/agent --region "$AWS_REGION"
aws ecr batch-delete-image --repository-name orion-mmrf/agent --image-ids imageTag=demo-1 --region "$AWS_REGION"
# Multi-architecture images may leave untagged manifests: list and remove those by digest too.
terraform -chdir=terraform destroy
```

S3 `force_destroy` and ECR `force_delete` are deliberately false; destroy stops if you have not emptied those resources. AgentCore can retain shared VPC ENIs for up to eight hours after deleting a runtime. If subnet/SG deletion reports a dependency, wait for the service cleanup and rerun destroy; do not force-delete managed ENIs. Remove remaining ORION Lambda/runtime log groups after reviewing their names if they were created by the services.

Finally use `python3 scripts/aws_sandbox.py destroy` in `vendor/sandbox` to tear down the upstream stack, following its guide. Keep any Terraform state required until all resources have actually been deleted. Remove ignored local raw/transformed data only when no longer needed.
