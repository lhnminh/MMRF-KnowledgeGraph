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
      instructions = "Query the multiple myeloma knowledge graph (DisMech mechanism paths and Monarch associations). Every tool returns the AD-004 envelope: report observations as recorded facts, interpretation as added explanation, and always state limitations."
    }
  }
}

# One Lambda backs every tool; it routes on the tool name Gateway passes in the context
# ("kg-graph___<tool>"). Each tool returns the AD-004 envelope (contract_version 0.2).
resource "aws_bedrockagentcore_gateway_target" "kg_graph" {
  gateway_identifier = aws_bedrockagentcore_gateway.kg.gateway_id
  name               = "kg-graph"
  description        = "Named, read-only tools over the Neptune myeloma knowledge graph"

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.loader.arn

        tool_schema {
          inline_payload {
            name        = "kg_search_entities"
            description = "Find diseases, phenotypes and mechanisms in the knowledge graph by name or exact id. Use first to resolve a plain-language term to a graph id."

            input_schema {
              type = "object"

              property {
                name        = "text"
                type        = "string"
                description = "Name fragment (case-insensitive), or an exact id such as MONDO:0009693 or HP:0000938"
                required    = true
              }

              property {
                name        = "limit"
                type        = "integer"
                description = "Maximum entities to return, 1-200 (default 20)"
              }
            }
          }

          inline_payload {
            name        = "kg_get_phenotypes"
            description = "Phenotypes recorded for a disease (Monarch has_phenotype associations), with their knowledge sources and publications."

            input_schema {
              type = "object"

              property {
                name        = "disease_id"
                type        = "string"
                description = "MONDO identifier of the disease, e.g. MONDO:0009693"
                required    = true
              }

              property {
                name        = "limit"
                type        = "integer"
                description = "Maximum associations to return, 1-200 (default 50)"
              }
            }
          }

          inline_payload {
            name        = "kg_get_mechanism_paths"
            description = "DisMech pathophysiology mechanisms for a disease and the causal leads_to edges between them, with the cited evidence for each edge."

            input_schema {
              type = "object"

              property {
                name        = "disease_id"
                type        = "string"
                description = "MONDO identifier of the disease, e.g. MONDO:0009693"
                required    = true
              }
            }
          }

          inline_payload {
            name        = "kg_get_evidence"
            description = "Evidence for one edge (by the edge id returned in graph.edges[].id), or every edge that cites a reference such as PMID:34201396. Pass exactly one of edge_id or reference."

            input_schema {
              type = "object"

              property {
                name        = "edge_id"
                type        = "string"
                description = "Edge id from a previous result, e.g. dismech:Clonal Plasma Cell Proliferation|leads_to|Bone Marrow Failure"
              }

              property {
                name        = "reference"
                type        = "string"
                description = "Citation or knowledge source, e.g. PMID:34201396 or infores:orphanet"
              }
            }
          }

          inline_payload {
            name        = "kg_run_readonly_query"
            description = "Run a single read-only openCypher query when no named tool fits. Writes, CALL and unbounded variable-length paths are rejected; results are capped at 100 rows. Graph: (:Disease {id,name})-[:ASSOCIATION {predicate,source,source_version,edge_origin,primary_knowledge_source,publications}]->(:Entity or :Disease {id,label,category}); (:Disease)-[:HAS_MECHANISM]->(:Mechanism {name,description}); (:Mechanism)-[:LEADS_TO {evidence_pmid,evidence_snippet,dismech_explanation}]->(:Mechanism)."

            input_schema {
              type = "object"

              property {
                name        = "query"
                type        = "string"
                description = "One openCypher read query; use $name placeholders for values"
                required    = true
              }

              property {
                name        = "parameters"
                type        = "object"
                description = "Values for the $name placeholders in the query"
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

# Gateway's own record of tool calls, delivered to CloudWatch. The kg-graph Lambda is not told
# who called, so this is where to look for the caller. Destroying the stack deletes these logs.
resource "aws_cloudwatch_log_group" "kg_gateway" {
  name              = "/aws/vendedlogs/bedrock-agentcore/gateway/${var.name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_delivery_source" "kg_gateway" {
  name         = "${var.name}-gateway-logs"
  log_type     = "APPLICATION_LOGS"
  resource_arn = aws_bedrockagentcore_gateway.kg.gateway_arn
}

resource "aws_cloudwatch_log_delivery_destination" "kg_gateway" {
  name = "${var.name}-gateway-logs"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.kg_gateway.arn
  }
}

resource "aws_cloudwatch_log_delivery" "kg_gateway" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.kg_gateway.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.kg_gateway.arn
}

# Lets Workstream 2's orchestrator agent call this Gateway. The role is theirs, created outside
# this stack; the data source just looks it up so our policy can attach to it.
data "aws_iam_role" "smoke_agent_runtime" {
  name = "mmrf-smoke-agent-runtime"
}

resource "aws_iam_role_policy" "smoke_agent_invoke_kg_gateway" {
  name = "invoke-kg-gateway"
  role = data.aws_iam_role.smoke_agent_runtime.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock-agentcore:InvokeGateway"
      Resource = aws_bedrockagentcore_gateway.kg.gateway_arn
    }]
  })
}
