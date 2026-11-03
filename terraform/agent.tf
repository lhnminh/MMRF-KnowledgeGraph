resource "aws_ecr_repository" "agent" {
  name                 = "${var.name}/agent"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
}
locals {
  graph_arn = "arn:aws:neptune-db:${var.region}:${var.sandbox_account_id}:${aws_neptune_cluster.graph.cluster_resource_id}/*"
  log_arn   = "arn:aws:logs:${var.region}:${var.sandbox_account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
}
resource "aws_iam_role" "agent" {
  name_prefix        = "${var.name}-agent-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "bedrock-agentcore.amazonaws.com" }, Condition = { StringEquals = { "aws:SourceAccount" = var.sandbox_account_id }, ArnLike = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.region}:${var.sandbox_account_id}:*" } } }] })
}
resource "aws_iam_role_policy" "agent" {
  role = aws_iam_role.agent.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["neptune-db:ReadDataViaQuery"], Resource = local.graph_arn },
    { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = var.bedrock_model_arns },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = aws_ecr_repository.agent.arn },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutResourcePolicy"], Resource = [local.log_arn, "${local.log_arn}:log-stream:*"] },
    { Effect = "Allow", Action = ["logs:DescribeLogGroups"], Resource = "arn:aws:logs:${var.region}:${var.sandbox_account_id}:log-group:*" }
  ] })
}
resource "aws_bedrockagentcore_agent_runtime" "orion" {
  count              = var.runtime_image_uri == "" ? 0 : 1
  agent_runtime_name = replace(var.name, "-", "_")
  role_arn           = aws_iam_role.agent.arn
  agent_runtime_artifact {
    container_configuration { container_uri = var.runtime_image_uri }
  }
  network_configuration {
    network_mode = "VPC"
    network_mode_config {
      security_groups = [aws_security_group.runtime.id]
      subnets         = aws_subnet.graph[*].id
    }
  }
  protocol_configuration { server_protocol = "HTTP" }
  lifecycle_configuration {
    idle_runtime_session_timeout = 60
    max_lifetime                 = 600
  }
  environment_variables = {
    AWS_REGION          = var.region
    ORION_AGENT_MODE    = "bedrock"
    ORION_GRAPH_BACKEND = "neptune"
    NEPTUNE_ENDPOINT    = "https://${aws_neptune_cluster.graph.endpoint}:8182"
    BEDROCK_MODEL_ID    = var.bedrock_model_id
    VIRTUAL_LAB_URL     = "http://${aws_lb.tools.dns_name}"
  }
  depends_on = [aws_iam_role_policy.agent, aws_neptune_cluster_instance.graph, aws_vpc_endpoint.services, aws_lb_listener.tools]
}
resource "aws_iam_role" "utility" {
  name_prefix        = "${var.name}-utility-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }] })
}
resource "aws_iam_role_policy_attachment" "utility_vpc" {
  role       = aws_iam_role.utility.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
resource "aws_iam_role_policy" "utility" {
  role = aws_iam_role.utility.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["neptune-db:StartLoaderJob", "neptune-db:GetLoaderJobStatus", "neptune-db:ReadDataViaQuery"], Resource = local.graph_arn },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.loader.arn }
  ] })
}
data "archive_file" "utility" {
  type        = "zip"
  source_file = "${path.module}/utility.py"
  output_path = "${path.module}/utility.zip"
}
resource "aws_lambda_function" "utility" {
  function_name    = "${var.name}-graph-utility"
  role             = aws_iam_role.utility.arn
  filename         = data.archive_file.utility.output_path
  source_code_hash = data.archive_file.utility.output_base64sha256
  handler          = "utility.handler"
  runtime          = "python3.12"
  timeout          = 120
  vpc_config {
    subnet_ids         = aws_subnet.graph[*].id
    security_group_ids = [aws_security_group.runtime.id]
  }
  environment {
    variables = {
      GRAPH_BUCKET     = aws_s3_bucket.graph.id
      LOADER_ROLE_ARN  = aws_iam_role.loader.arn
      NEPTUNE_ENDPOINT = "https://${aws_neptune_cluster.graph.endpoint}:8182"
    }
  }
  depends_on = [aws_iam_role_policy_attachment.utility_vpc, aws_iam_role_policy.utility]
}
