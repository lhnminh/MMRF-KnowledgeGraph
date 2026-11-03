# Ingestion resources for an EXISTING Neptune cluster. This root owns no database.
data "aws_vpc" "existing" { id = var.vpc_id }
data "aws_subnet" "existing" {
  for_each = var.subnet_ids
  id       = each.value
}
data "aws_security_group" "neptune" { id = var.neptune_security_group_id }
data "aws_route_tables" "existing" { vpc_id = var.vpc_id }

resource "aws_s3_bucket" "graph" {
  bucket_prefix = "${var.name}-graph-"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "graph" {
  bucket                  = aws_s3_bucket.graph.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "graph" {
  bucket = aws_s3_bucket.graph.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.graph.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.graph.arn, "${aws_s3_bucket.graph.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_iam_role" "loader" {
  name_prefix = "${var.name}-loader-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "rds.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.sandbox_account_id } }
  }] })
}
resource "aws_iam_role_policy" "loader" {
  role = aws_iam_role.loader.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.graph.arn,
    Condition = { StringLike = { "s3:prefix" = ["graph/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.graph.arn}/graph/*" }
  ] })
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.existing.ids
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Principal = "*", Action = ["s3:ListBucket", "s3:GetObject"],
    Resource = [aws_s3_bucket.graph.arn, "${aws_s3_bucket.graph.arn}/graph/*"]
  }] })
}
resource "aws_security_group" "utility" {
  name_prefix = "${var.name}-utility-"
  description = "Neptune ingestion utility"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_egress_rule" "utility_neptune" {
  security_group_id            = aws_security_group.utility.id
  referenced_security_group_id = var.neptune_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}
resource "aws_vpc_security_group_ingress_rule" "utility_neptune" {
  security_group_id            = var.neptune_security_group_id
  referenced_security_group_id = aws_security_group.utility.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}
resource "aws_vpc_security_group_egress_rule" "neptune_s3" {
  security_group_id = var.neptune_security_group_id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_iam_role" "utility" {
  name_prefix = "${var.name}-utility-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "utility_vpc" {
  role       = aws_iam_role.utility.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
resource "aws_iam_role_policy" "utility" {
  role = aws_iam_role.utility.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["neptune-db:StartLoaderJob", "neptune-db:GetLoaderJobStatus", "neptune-db:ReadDataViaQuery"],
    Resource = "arn:aws:neptune-db:${var.region}:${var.sandbox_account_id}:${var.cluster_resource_id}/*" },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.loader.arn }
  ] })
}
data "archive_file" "utility" {
  type        = "zip"
  source_file = "${path.module}/../utility.py"
  output_path = "${path.module}/utility.zip"
}
resource "aws_cloudwatch_log_group" "utility" {
  name              = "/aws/lambda/${var.name}-graph-utility"
  retention_in_days = 7
}
resource "aws_lambda_function" "utility" {
  function_name    = "${var.name}-graph-utility"
  role             = aws_iam_role.utility.arn
  filename         = data.archive_file.utility.output_path
  source_code_hash = data.archive_file.utility.output_base64sha256
  handler          = "utility.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 256
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.utility.id]
  }
  environment {
    variables = {
      GRAPH_BUCKET     = aws_s3_bucket.graph.id
      LOADER_ROLE_ARN  = aws_iam_role.loader.arn
      NEPTUNE_ENDPOINT = var.neptune_endpoint
    }
  }
  lifecycle {
    precondition {
      condition     = alltrue([for s in data.aws_subnet.existing : s.vpc_id == var.vpc_id]) && data.aws_security_group.neptune.vpc_id == var.vpc_id
      error_message = "Neptune security group and utility subnets must belong to the selected VPC."
    }
  }
  depends_on = [aws_iam_role_policy_attachment.utility_vpc, aws_iam_role_policy.utility, aws_cloudwatch_log_group.utility,
    aws_vpc_security_group_ingress_rule.utility_neptune, aws_vpc_security_group_egress_rule.utility_neptune,
  aws_vpc_security_group_egress_rule.neptune_s3]
}
