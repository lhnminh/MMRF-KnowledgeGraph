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
    }
  }
}
