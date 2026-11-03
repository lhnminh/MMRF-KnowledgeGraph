locals {
  graph_arn = "arn:aws:neptune-db:${var.region}:${var.sandbox_account_id}:${aws_neptune_cluster.graph.cluster_resource_id}/*"
}
resource "aws_iam_role" "utility" {
  name_prefix = "${var.name}-utility-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" }
  }] })
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
    subnet_ids         = aws_subnet.graph[*].id
    security_group_ids = [aws_security_group.utility.id]
  }
  environment {
    variables = {
      GRAPH_BUCKET     = aws_s3_bucket.graph.id
      LOADER_ROLE_ARN  = aws_iam_role.loader.arn
      NEPTUNE_ENDPOINT = "https://${aws_neptune_cluster.graph.endpoint}:8182"
    }
  }
  depends_on = [aws_iam_role_policy_attachment.utility_vpc, aws_iam_role_policy.utility, aws_cloudwatch_log_group.utility, aws_vpc_security_group_ingress_rule.utility_neptune, aws_vpc_security_group_egress_rule.utility_neptune, aws_neptune_cluster_instance.graph]
}
