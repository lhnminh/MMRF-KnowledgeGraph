# Deploy Neptune without EKS

If Neptune is already deployed, follow [existing-cluster ingestion](neptune-existing.md) instead. The hackathon cluster `mmrf-kg-cluster` is in **us-west-2**.

Use `terraform/neptune-only` when you want to move the local graph to AWS before deploying the full ORION application. Neptune is an AWS managed database; it does not run inside the local Docker container. This stack is independent of the EKS stack in `terraform/`. Do not apply both to manage the same resources.

The stack creates one encrypted, IAM-authenticated `db.t4g.medium` Neptune instance, a private VPC with two subnets, a same-Region S3 bucket and gateway endpoint, a loader role, and a VPC-connected utility Lambda. There is no public database endpoint or NAT gateway. Neptune and the supporting AWS services incur charges while provisioned. This is a disposable sandbox configuration: deletion protection is disabled and teardown does not create a final database snapshot.

## Select the sandbox and deploy

Run commands from the repository root. Use an explicitly selected sandbox profile; never paste AWS credentials into Terraform or this repository. The following examples use `us-east-1`. Other Regions require two corresponding `availability_zone_ids` in the variable file.

```sh
export AWS_PROFILE=YOUR_SANDBOX_PROFILE
export AWS_REGION=us-east-1
aws sts get-caller-identity --no-cli-pager
cp terraform/neptune-only/terraform.tfvars.example terraform/neptune-only/terraform.tfvars
```

Edit the ignored `terraform.tfvars`: set `aws_profile` to the selected profile and `sandbox_account_id` to the account ID returned by STS. Terraform restricts deployment to that account. Authenticate using your organization's normal AWS CLI process if STS fails. The deploying identity needs permission to create VPC, Neptune, S3, IAM, Lambda and CloudWatch resources, pass the execution/loader roles, upload the CSVs and invoke the utility Lambda.

Terraform 1.9 or newer is required. A locally downloaded, checksum-verified Terraform binary, when available, can be invoked as `.tools/terraform` instead of `terraform`.

```sh
terraform -chdir=terraform/neptune-only init
terraform -chdir=terraform/neptune-only validate
terraform -chdir=terraform/neptune-only plan -out=neptune.tfplan
terraform -chdir=terraform/neptune-only apply neptune.tfplan
terraform -chdir=terraform/neptune-only output
```

Review the plan before applying. Preserve the ignored Terraform state locally to manage and destroy the resources. Provisioning the database can take several minutes. A local validation or plan does not establish that AWS has provisioned the database.

## Upload and bulk load the ETL output

Use the existing export, or regenerate it with `scripts/transform.py`. DisMech remains opt-in pending confirmation of its data terms. Both local validation and the bulk-loader script use the repository's Python environment.

```sh
.venv/bin/python scripts/validate.py
GRAPH_BUCKET=$(terraform -chdir=terraform/neptune-only output -raw graph_bucket)
GRAPH_UTILITY=$(terraform -chdir=terraform/neptune-only output -raw graph_utility_function)
.venv/bin/python scripts/bulk_load.py \
  --bucket "$GRAPH_BUCKET" \
  --function "$GRAPH_UTILITY" \
  --region "$AWS_REGION"
```

The script validates the CSVs, uploads only `nodes.csv` and `relationships.csv` under a content-hashed `graph/` prefix, and invokes the utility Lambda. The Lambda starts the Neptune openCypher bulk loader from inside the VPC. The script polls until completion and checks node/relationship counts, the TP53–Multiple Myeloma connection and relationship provenance. The original default export contains 43 nodes and 47 relationships; regenerated subsets can have different counts.

To rerun the read-only remote checks:

```sh
.venv/bin/python scripts/validate.py --function "$GRAPH_UTILITY"
```

Inspect failed Lambda invocations in the `/aws/lambda/orion-neptune-graph-utility` CloudWatch log group. Loader failures are reported by the script with the load ID and error details. A timeout does not cancel a loader job; inspect its status before starting another load:

```sh
aws lambda invoke \
  --function-name "$GRAPH_UTILITY" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"status","load_id":"REPLACE_WITH_LOAD_ID"}' \
  /tmp/orion-neptune-load-status.json
cat /tmp/orion-neptune-load-status.json
```

## Access and visualization

The `neptune_endpoint` output is private and requires signed requests from an authorized client inside the VPC. Opening the endpoint in your laptop's browser will not show a graph. Local Neo4j Browser remains useful for exploring the same CSV export; it is a separate database and does not automatically reflect changes made in Neptune.

The utility Lambda exposes only load, status and fixed validation actions. For the ORION agent, place its runtime in the output VPC/subnets, allow its security group with `additional_client_security_group_ids`, and grant the runtime `neptune-db:ReadDataViaQuery` on the cluster resource ARN. Deploying this stack alone does not deploy Bedrock, AgentCore or the Virtual Lab interface. See [the full application deployment](deployment.md) for that separate workflow.

## Teardown

This stack has its own state. Keep the same sandbox profile and Region selected. Empty only this stack's `graph/` prefix, review the destroy plan, then destroy the resources:

```sh
GRAPH_BUCKET=$(terraform -chdir=terraform/neptune-only output -raw graph_bucket)
aws s3 rm "s3://$GRAPH_BUCKET/graph/" --recursive
terraform -chdir=terraform/neptune-only plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/neptune-only apply destroy.tfplan
```

The bucket deliberately has `force_destroy = false`; unrelated objects must be reviewed if they prevent deletion. Database deletion permanently removes the loaded sandbox graph without a final snapshot. The local ETL export and Neo4j database remain available for another demonstration.

AWS references: [Neptune bulk loading](https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load-data.html), [loader IAM role](https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load-tutorial-IAM.html), and [IAM database authentication](https://docs.aws.amazon.com/neptune/latest/userguide/iam-auth.html).
