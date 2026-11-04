terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}
variable "aws_profile" {
  type    = string
  default = "mmrf-hackathon"
}
variable "account_id" {
  type    = string
  default = "548574235556"
}
variable "name" {
  type    = string
  default = "mmrf-kg-source-pipeline"
}
variable "existing_ingest_function" {
  type    = string
  default = "mmrf-kg-ingest"
}
variable "neptune_cluster_resource_id" {
  description = "Existing cluster DbClusterResourceId from aws neptune describe-db-clusters; used only to scope IAM"
  type        = string
  default     = "cluster-STYQ3QT7Q2KM4A4OA464BASUUM"
  validation {
    condition     = can(regex("^cluster-[A-Z0-9]+$", var.neptune_cluster_resource_id))
    error_message = "Provide Neptune's immutable DbClusterResourceId, not its name or endpoint."
  }
}
variable "dismech_revision" {
  description = "Reviewed immutable commit; nightly runs will not silently switch to main/latest"
  type        = string
  default     = "b1ca0b232b815a21b4ba95a62828c9979e9a49e7"
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.dismech_revision))
    error_message = "Use a full lowercase Git commit SHA."
  }
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "ORION", Component = "source-pipeline", Data = "public-biological-knowledge" }
  }
}

# Read-only lookups: this root never owns the existing bucket, Neptune, security groups or Lambdas.
data "aws_lambda_function" "existing_ingest" {
  function_name = var.existing_ingest_function
}
data "aws_s3_bucket" "ingest" {
  bucket = data.aws_lambda_function.existing_ingest.environment[0].variables["INGEST_BUCKET"]
}

locals {
  prefix              = "raw/source-pipeline/runs/"
  bucket              = data.aws_s3_bucket.ingest.id
  objects             = "${data.aws_s3_bucket.ingest.arn}/${local.prefix}*"
  ingest_function_arn = "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.name}-ingest"
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(["fetch", "ingest"])
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 3
}
resource "aws_iam_role" "lambda" {
  for_each = toset(["fetch", "ingest"])
  name     = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "fetch" {
  name = "snapshot-source-prefix-only"
  role = aws_iam_role.lambda["fetch"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = local.objects },
      { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = local.ingest_function_arn },
      {
        Effect    = "Allow", Action = "s3:ListBucket", Resource = data.aws_s3_bucket.ingest.arn,
        Condition = { StringLike = { "s3:prefix" = "${local.prefix}*" } }
      },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda["fetch"].arn}:*" }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "ingest_vpc" {
  role       = aws_iam_role.lambda["ingest"].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
resource "aws_iam_role_policy" "ingest" {
  name = "read-snapshots-and-upsert-one-neptune-cluster"
  role = aws_iam_role.lambda["ingest"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "s3:GetObject", Resource = local.objects },
      {
        Effect   = "Allow", Action = ["neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery"],
        Resource = "arn:aws:neptune-db:${var.region}:${var.account_id}:${var.neptune_cluster_resource_id}/*"
      }
    ]
  })
}

resource "aws_lambda_function" "pipeline" {
  for_each         = toset(["fetch", "ingest"])
  function_name    = "${var.name}-${each.key}"
  role             = aws_iam_role.lambda[each.key].arn
  handler          = "source_pipeline.${each.key}.handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  filename         = "${path.module}/${each.key}.zip"
  source_code_hash = filebase64sha256("${path.module}/${each.key}.zip")
  timeout          = each.key == "fetch" ? 900 : 300
  memory_size      = 256
  # Serialize the write + read-back validation so two scheduled runs cannot stamp over each other.
  reserved_concurrent_executions = each.key == "ingest" ? 1 : -1
  dynamic "vpc_config" {
    for_each = each.key == "ingest" ? [1] : []
    content {
      subnet_ids         = data.aws_lambda_function.existing_ingest.vpc_config[0].subnet_ids
      security_group_ids = data.aws_lambda_function.existing_ingest.vpc_config[0].security_group_ids
    }
  }
  environment {
    variables = merge({ INGEST_BUCKET = local.bucket }, each.key == "fetch" ? {
      DISMECH_REVISION     = var.dismech_revision
      INGEST_FUNCTION_NAME = "${var.name}-ingest"
      } : {
      NEPTUNE_URI      = data.aws_lambda_function.existing_ingest.environment[0].variables["NEPTUNE_URI"]
      NEPTUNE_IAM_AUTH = "true"
    })
  }
  depends_on = [aws_iam_role_policy.fetch, aws_iam_role_policy.ingest, aws_iam_role_policy_attachment.ingest_vpc]
}

output "source_bucket" {
  value = local.bucket
}
output "runs_prefix" {
  value = "s3://${local.bucket}/${local.prefix}"
}
output "fetch_function" {
  value = aws_lambda_function.pipeline["fetch"].function_name
}
output "ingest_function" {
  value = aws_lambda_function.pipeline["ingest"].function_name
}
