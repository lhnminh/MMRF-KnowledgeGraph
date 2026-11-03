resource "aws_security_group" "loader_lambda" {
  name        = "${var.name}-loader-lambda-sg"
  description = "kg-graph Lambda outbound access to Neptune (private VPC only, no internet)"
  vpc_id      = aws_vpc.kg.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.kg.cidr_block]
  }
}

resource "aws_iam_role" "loader_lambda" {
  name = "${var.name}-loader-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "loader_lambda_vpc" {
  role       = aws_iam_role.loader_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_caller_identity" "current" {}

# Read-only at the database: once the cluster enforces IAM auth, Neptune refuses any query
# from this role that could write or delete (AD-004), whatever the handler's guard allows.
resource "aws_iam_role_policy" "kg_graph_neptune_read" {
  name = "${var.name}-kg-graph-neptune-read"
  role = aws_iam_role.loader_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "neptune-db:ReadDataViaQuery"
      Resource = "arn:aws:neptune-db:${var.region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.this.cluster_resource_id}/*"
    }]
  })
}

resource "aws_lambda_function" "loader" {
  function_name = "${var.name}-kg-graph"
  role          = aws_iam_role.loader_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 256

  filename         = "${path.module}/lambda_loader.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_loader.zip")

  vpc_config {
    subnet_ids         = aws_subnet.kg_private[*].id
    security_group_ids = [aws_security_group.loader_lambda.id]
  }

  environment {
    variables = {
      NEPTUNE_URI = "bolt://${aws_neptune_cluster.this.endpoint}:8182"
      # Sign connections now; set iam_database_authentication_enabled on the cluster later,
      # once this is confirmed working.
      NEPTUNE_IAM_AUTH = "true"
    }
  }
}
