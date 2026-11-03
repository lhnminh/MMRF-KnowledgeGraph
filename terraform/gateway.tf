resource "aws_iam_role" "kg_gateway" {
  name = "${var.name}-gateway-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "kg_gateway_invoke_lambda" {
  name = "${var.name}-gateway-invoke-kg-graph"
  role = aws_iam_role.kg_gateway.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.loader.arn
    }]
  })
}

resource "aws_bedrockagentcore_gateway" "kg" {
  name            = "${var.name}-gateway"
  description     = "Exposes kg-graph as an MCP tool for the orchestrator agent"
  role_arn        = aws_iam_role.kg_gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"

  protocol_configuration {
    mcp {
      instructions = "Query the multiple myeloma knowledge graph (Monarch associations and DisMech mechanism paths)."
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "kg_graph" {
  gateway_identifier = aws_bedrockagentcore_gateway.kg.gateway_id
  name               = "kg-graph"
  description        = "Query disease overview, associations, or mechanism paths from Neptune"

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.loader.arn

        tool_schema {
          inline_payload {
            name        = "kg_graph_query"
            description = "Query the myeloma knowledge graph for a disease's overview, Monarch associations, or DisMech mechanism path."

            input_schema {
              type = "object"

              property {
                name        = "disease_id"
                type        = "string"
                description = "MONDO identifier of the disease, e.g. MONDO:0009693"
                required    = true
              }

              property {
                name        = "query_type"
                type        = "string"
                description = "One of: overview, associations, mechanism"
              }
            }
          }
        }
      }
    }
  }

  credential_provider_configuration {
    gateway_iam_role {}
  }
}
