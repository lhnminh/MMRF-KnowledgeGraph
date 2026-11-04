terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
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
  default = "mmrf-kg-graph-api"
}

variable "graph_function_name" {
  type    = string
  default = "mmrf-kg-kg-graph"
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "ORION", Component = "graph-api", Data = "public-biological-knowledge" }
  }
}

# Independent state: this stack reads the existing tool Lambda but never manages Neptune.
data "aws_lambda_function" "graph" {
  function_name = var.graph_function_name
}

resource "aws_kms_key" "graph_api" {
  description         = "${var.name} key for Lambda env vars and log groups"
  enable_key_rotation = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableIAMUserPermissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogsEncryption"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${var.account_id}:log-group:*"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "graph_api" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.graph_api.key_id
}

resource "aws_iam_role" "api" {
  name = var.name
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.graph_api.arn
}

resource "aws_iam_role_policy" "api" {
  name = "invoke-one-graph-function-and-write-logs"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = data.aws_lambda_function.graph.arn },
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
      { Effect = "Allow", Action = "kms:Decrypt", Resource = aws_kms_key.graph_api.arn }
    ]
  })
}

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../../graph_api"
  output_path = "${path.module}/graph-api.zip"
  excludes    = ["__pycache__", "__pycache__/**"]
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:Sandbox viewer Lambda only calls the read-only graph Lambda via AWS APIs; VPC placement would need NAT setup with no security benefit here
  #checkov:skip=CKV_AWS_116:No DLQ; synchronous API Gateway invocation has no retry/async failure path for a DLQ to catch
  #checkov:skip=CKV_AWS_272:Code-signing not configured; sandbox deployment, no code-signing pipeline exists yet
  function_name                  = var.name
  role                           = aws_iam_role.api.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "handler.handler"
  filename                       = data.archive_file.api.output_path
  source_code_hash               = data.archive_file.api.output_base64sha256
  timeout                        = 28
  memory_size                    = 256
  kms_key_arn                    = aws_kms_key.graph_api.arn
  reserved_concurrent_executions = 5

  tracing_config {
    mode = "Active"
  }
  environment {
    variables = {
      GRAPH_FUNCTION_NAME = data.aws_lambda_function.graph.function_name
    }
  }
  depends_on = [aws_iam_role_policy.api]
}

resource "aws_apigatewayv2_api" "graph" {
  name          = var.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.graph.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "api" {
  for_each  = toset(["GET /", "GET /assets/{asset}", "GET /api/graph"])
  api_id    = aws_apigatewayv2_api.graph.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_lambda_permission" "gateway" {
  statement_id  = "AllowGraphHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.graph.execution_arn}/*/GET/*"
}

resource "aws_cloudwatch_log_group" "http" {
  name              = "/aws/apigateway/${var.name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.graph_api.arn
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.graph.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 5
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.http.arn
    format = jsonencode({
      requestId = "$context.requestId", route = "$context.routeKey",
      status    = "$context.status", responseLength = "$context.responseLength"
    })
  }
}

output "graph_url" {
  value = "${aws_apigatewayv2_api.graph.api_endpoint}/"
}

output "graph_api_url" {
  value = "${aws_apigatewayv2_api.graph.api_endpoint}/api/graph"
}
