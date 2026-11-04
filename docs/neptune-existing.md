# Load an existing Neptune cluster

Use `terraform/neptune-ingest` for a cluster that already exists. This Terraform root creates only ingestion support: a private S3 staging bucket, a read/list loader role, an S3 gateway endpoint, security-group rules and a VPC-connected utility Lambda. It does not own the Neptune cluster or instance, VPC or subnets. Use its own state and teardown before destroying the parent cluster/network.

The hackathon cluster discovered in this session is `mmrf-kg-cluster` in **us-west-2**, account `548574235556`. Its browser-authenticated AWS profile is `mmrf-hackathon`. No resources should be deployed using a personal sandbox profile.

## Verified deployment and load

The ingestion stack was applied and the public export loaded on 2026-11-03.

- Utility: `mmrf-kg-ingest-graph-utility`.
- Staging bucket: `mmrf-kg-ingest-graph-625cdaede0eaa28bd93d4b6b7f`.
- Loader job: `3b7e2b6e-081c-4880-bf1b-5808e36a5217`, status `LOAD_COMPLETED`.
- Export: 1,738 nodes and 2,894 relationships; 36 existing nodes reused.
- Result: 1,743 `Entity` nodes, including five existing entities outside the export. Other legacy nodes and relationship types were preserved.
- Relationship sources: Open Targets 2,608; CIViC 239; Monarch 35; curated 12.
- CIViC: 26 accepted and 23 submitted evidence items.
- Validation: TP53 publication/source links present, zero missing relationship provenance, zero duplicate logical IDs among Entity nodes. Standalone legacy Disease nodes outside the Entity projection are retained.

To reload this deployment, export the profile/Region below and run the load commands with these bucket/function names, or read them from the ingestion Terraform outputs.

## Configure once

Copy `terraform/neptune-ingest/terraform.tfvars.example` to the ignored `terraform.tfvars` in that directory. Fill in the existing cluster resource ID, HTTPS endpoint, VPC, private subnets and Neptune security group. Obtain the resource ID from `DbClusterResourceId` in `aws neptune describe-db-clusters`. The account ID is an explicit provider restriction.

This configuration assumes the selected VPC does not already have an S3 gateway endpoint. If one exists, reuse/import it rather than creating another endpoint for the same route tables.

```sh
export AWS_PROFILE=mmrf-hackathon
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION=us-west-2
aws sts get-caller-identity --no-cli-pager
terraform -chdir=terraform/neptune-ingest init
terraform -chdir=terraform/neptune-ingest plan -out=ingest.tfplan
terraform -chdir=terraform/neptune-ingest apply ingest.tfplan
```

Use `.tools/terraform` in place of `terraform` if using the repository-local binary. The plan should add ingestion resources and security-group rules, with no database replacement/deletion.

Associate the newly created loader role with the **existing** cluster once:

```sh
LOADER_ROLE=$(terraform -chdir=terraform/neptune-ingest output -raw loader_role_arn)
aws neptune add-role-to-db-cluster \
  --db-cluster-identifier mmrf-kg-cluster \
  --role-arn "$LOADER_ROLE" \
  --region "$AWS_REGION"
aws neptune describe-db-clusters \
  --db-cluster-identifier mmrf-kg-cluster \
  --query 'DBClusters[0].AssociatedRoles' \
  --region "$AWS_REGION"
```

Wait for the association to show `ACTIVE`. If that exact role is already associated, skip `add-role-to-db-cluster`.

This association is a separate AWS CLI operation because the parent Terraform state owns the cluster. Preserve the association in the parent's `iam_roles` configuration if it manages that field. The parent configuration also needs to preserve the added Neptune ingress rule from the utility security group and TCP 443 egress to the S3 prefix list: an apply of exclusive inline security-group rules could otherwise remove these rules. Coordinate the parent and ingestion states before future infrastructure updates.

## Load and verify

For browser login profiles, the Python SDK needs its optional CRT dependency. Install the version required by the installed botocore without changing its version:

```sh
uv pip install --python .venv/bin/python "botocore[crt]==$(.venv/bin/python -c 'import botocore; print(botocore.__version__)')"
```

Then generate and bulk load the same export used by Neo4j:

```sh
.venv/bin/python scripts/transform.py
.venv/bin/python scripts/validate.py
GRAPH_BUCKET=$(terraform -chdir=terraform/neptune-ingest output -raw graph_bucket)
GRAPH_UTILITY=$(terraform -chdir=terraform/neptune-ingest output -raw graph_utility_function)
.venv/bin/python scripts/bulk_load.py \
  --bucket "$GRAPH_BUCKET" \
  --function "$GRAPH_UTILITY" \
  --region "$AWS_REGION"
.venv/bin/python scripts/validate.py --function "$GRAPH_UTILITY"
```

The script first reads a bounded identity map from Neptune and checks for ambiguous logical node IDs. If a legacy standalone Disease and one Entity share an ID, it reuses the Entity and preserves the standalone Disease. Multiple Entity nodes with the same ID stop the import. If an earlier import used generated native IDs, it remaps the staging CSV's native IDs and relationship endpoints to reuse those nodes. Original source IDs stay in the `id` property; the local Neo4j export is unchanged. Existing single-value properties on matched nodes can be updated by the loader.

It uploads **only** the two staging CSVs to a content-hashed `graph/` prefix, starts the loader through the Lambda inside the VPC, polls for `LOAD_COMPLETED` and checks provenance, duplicate IDs and the TP53 association. The current public export contains 1,738 nodes and 2,894 relationships, including Monarch, Open Targets, CIViC and curated records. Existing records outside this export are retained, so total database counts may be higher. `--curated-only` produces a smaller export. DisMech remains opt-in pending data-term confirmation.

The cluster endpoint is private. The S3 upload and Lambda invocation run from your laptop; the database calls run inside the VPC. Local Neo4j and AWS Neptune remain separate databases. The ingestion utility exposes only fixed load/status/validation actions; it does not change the existing agent-facing `kg-graph` Lambda or its query schema.

For loader status/errors, see [status commands](neptune.md) or the `/aws/lambda/mmrf-kg-ingest-graph-utility` CloudWatch log group. Preserve an unfinished load ID rather than starting multiple overlapping jobs.

## Teardown ingestion support

Destroy this stack before deleting the parent cluster or VPC. Remove only this stack's role association and S3 graph prefix, review the destroy plan, then apply it:

```sh
LOADER_ROLE=$(terraform -chdir=terraform/neptune-ingest output -raw loader_role_arn)
GRAPH_BUCKET=$(terraform -chdir=terraform/neptune-ingest output -raw graph_bucket)
aws neptune remove-role-from-db-cluster \
  --db-cluster-identifier mmrf-kg-cluster --role-arn "$LOADER_ROLE" --region "$AWS_REGION"
aws s3 rm "s3://$GRAPH_BUCKET/graph/" --recursive
terraform -chdir=terraform/neptune-ingest plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/neptune-ingest apply destroy.tfplan
```

This removes ingestion support, leaving the loaded graph in the existing database. Delete the cluster through its owning Terraform stack when finished with the demonstration.

AWS references: [bulk loading](https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load.html), [S3/IAM prerequisites](https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load-tutorial-IAM.html), and [openCypher CSV format](https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load-tutorial-format-opencypher.html).
