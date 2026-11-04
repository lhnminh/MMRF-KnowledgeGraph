# kg-ingest: a Lambda in the private VPC that reads the raw source files from S3 and writes them
# to Neptune. Upload to the bucket, invoke the function; see the README. No path from a laptop to
# Neptune is needed. Only this role can write to the graph; kg-graph stays read-only.

resource "aws_s3_bucket" "ingest" {
  bucket_prefix = "${var.name}-ingest-"
  # Holds only public source files that the fetch scripts can regenerate.
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "ingest" {
  bucket                  = aws_s3_bucket.ingest.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "ingest" {
  bucket = aws_s3_bucket.ingest.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Source files are re-fetched from upstream, not archived; old versions just cost storage.
resource "aws_s3_bucket_lifecycle_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# The VPC has no internet route, so this is how the Lambda reaches S3. Gateway endpoints are free.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.kg.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.kg_private.id]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:ListBucket", "s3:GetObject"]
      Resource = [
        aws_s3_bucket.ingest.arn,
        "${aws_s3_bucket.ingest.arn}/raw/*",
      ]
    }]
  })
}

resource "aws_security_group" "ingest_lambda" {
  name        = "${var.name}-ingest-lambda-sg"
  description = "kg-ingest Lambda: outbound to Neptune and S3 only"
  vpc_id      = aws_vpc.kg.id
}

resource "aws_vpc_security_group_egress_rule" "ingest_neptune" {
  security_group_id            = aws_security_group.ingest_lambda.id
  referenced_security_group_id = aws_security_group.neptune.id
  description                  = "Neptune openCypher"
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}

resource "aws_vpc_security_group_egress_rule" "ingest_s3" {
  security_group_id = aws_security_group.ingest_lambda.id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  description       = "S3 gateway endpoint"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "neptune_ingest" {
  security_group_id            = aws_security_group.neptune.id
  referenced_security_group_id = aws_security_group.ingest_lambda.id
  description                  = "kg-ingest Lambda"
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}

resource "aws_iam_role" "ingest_lambda" {
  name = "${var.name}-ingest-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ingest_lambda_vpc" {
  role       = aws_iam_role.ingest_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "ingest_lambda" {
  name = "${var.name}-ingest"
  role = aws_iam_role.ingest_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.ingest.arn}/raw/*"
      },
      {
        # MERGE needs read as well as write. No delete: reloading never removes data.
        Effect   = "Allow"
        Action   = ["neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery"]
        Resource = "arn:aws:neptune-db:${var.region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.this.cluster_resource_id}/*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "ingest_lambda" {
  name              = "/aws/lambda/${var.name}-ingest"
  retention_in_days = 14
}

resource "aws_lambda_function" "ingest" {
  function_name                  = "${var.name}-ingest"
  role                           = aws_iam_role.ingest_lambda.arn
  handler                        = "load_neo4j.lambda_handler"
  runtime                        = "python3.12"
  timeout                        = 300
  memory_size                    = 256
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  # The same package as kg-graph; scripts/build_lambda.py builds it.
  filename         = "${path.module}/lambda_loader.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_loader.zip")

  vpc_config {
    subnet_ids         = aws_subnet.kg_private[*].id
    security_group_ids = [aws_security_group.ingest_lambda.id]
  }

  environment {
    variables = {
      INGEST_BUCKET    = aws_s3_bucket.ingest.id
      NEPTUNE_URI      = "bolt://${aws_neptune_cluster.this.endpoint}:8182"
      NEPTUNE_IAM_AUTH = "true"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.ingest_lambda_vpc,
    aws_iam_role_policy.ingest_lambda,
    aws_vpc_security_group_ingress_rule.neptune_ingest,
    aws_vpc_security_group_egress_rule.ingest_neptune,
    aws_vpc_security_group_egress_rule.ingest_s3,
    aws_cloudwatch_log_group.ingest_lambda,
  ]
}
