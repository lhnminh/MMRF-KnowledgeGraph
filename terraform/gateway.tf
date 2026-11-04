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

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
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

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
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

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
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
                description = "Edge id copied from graph.edges[].id of a previous result, e.g. monarch:HGNC:6407|biolink:gene_associated_with_condition|MONDO:0011908"
              }

              property {
                name        = "reference"
                type        = "string"
                description = "Citation or knowledge source, e.g. PMID:34201396 or infores:orphanet"
              }
            }

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
              }
            }
          }

          inline_payload {
            name        = "kg_get_gene_context"
            description = "Everything the graph records about one human gene, per source: Monarch gene-disease associations, DisMech genetic factors, the disease subtypes the gene defines, and the drugs that act on it (Open Targets mechanism of action). States none recorded for Monarch and DisMech when a source has nothing, and that CIViC is not loaded."

            input_schema {
              type = "object"

              property {
                name        = "gene"
                type        = "string"
                description = "Human gene as an HGNC id (e.g. HGNC:6407) or approved symbol (e.g. KRAS)"
                required    = true
              }

              property {
                name        = "limit"
                type        = "integer"
                description = "Maximum edges to return, 1-200 (default 50)"
              }
            }

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
              }
            }
          }

          inline_payload {
            name        = "kg_get_gene_disease_association"
            description = "The recorded link between one gene and one disease, from each source: the Monarch gene-disease association (relationship, knowledge source, disease id), the DisMech genetic factor and the disease subtype the gene defines. If a variant is named and only a gene-level record exists, states the change in specificity. Use the disease id returned here to continue into the DisMech disease record."

            input_schema {
              type = "object"

              property {
                name        = "gene"
                type        = "string"
                description = "Human gene as an HGNC id (e.g. HGNC:6407) or approved symbol (e.g. KRAS)"
                required    = true
              }

              property {
                name        = "disease_id"
                type        = "string"
                description = "MONDO identifier of the disease, e.g. MONDO:0011908"
                required    = true
              }

              property {
                name        = "variant"
                type        = "string"
                description = "Optional variant the user asked about, e.g. KRAS G12D. Used only to report whether the graph has anything at that specificity."
              }
            }

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
              }
            }
          }

          inline_payload {
            name        = "kg_get_treatment_evidence"
            description = "DisMech recorded treatments for a disease with their agents, the mechanism each treatment acts on (with that edge's own evidence, and the mechanism's place in the disease record) and the cited evidence text. Also returns each agent's molecular targets from Open Targets (a separate source from DisMech, capped at 10 per agent), and reports treatments with no mechanism link and agents with no recorded target. Recorded knowledge, not a recommendation."

            input_schema {
              type = "object"

              property {
                name        = "disease_id"
                type        = "string"
                description = "MONDO identifier of the disease, e.g. MONDO:0011908"
                required    = true
              }

              property {
                name        = "treatment"
                type        = "string"
                description = "Optional: only treatments or agents whose name contains this text, e.g. trametinib"
              }

              property {
                name        = "mechanism"
                type        = "string"
                description = "Optional: only treatments acting on a mechanism whose name contains this text, e.g. RAS/MAPK"
              }
            }

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran, present when one did."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
              }
            }
          }

          inline_payload {
            name        = "kg_run_readonly_query"
            description = "Run a single read-only openCypher query when no named tool fits. Writes, CALL and unbounded variable-length paths are rejected; results are capped at 100 rows. Graph: (:Disease {id,name})-[:ASSOCIATION {predicate,source,source_version,edge_origin,primary_knowledge_source,publications}]->(:Entity or :Disease {id,label,category}); (:Disease)-[:HAS_MECHANISM]->(:Mechanism {id,name,disease_id,description}); (:Mechanism)-[:LEADS_TO {evidence_pmid,evidence_snippet,dismech_explanation}]->(:Mechanism); (:Mechanism)-[:LEADS_TO_PHENOTYPE]->(:Entity) for a downstream phenotype; a Monarch gene-disease association has the gene as subject: (:Entity {category:'biolink:Gene'})-[:ASSOCIATION]->(:Disease); (:Entity)-[:HAS_AGENT]->(:Entity); (:Entity {drug})-[:TARGETS_GENE {source:'opentargets',action_type,mechanism_of_action,references}]->(:Entity {gene}); (:Entity {subtype})-[:HAS_DRIVER_GENE]->(:Entity {gene}); (:Entity {treatment})-[:TARGETS_MECHANISM {treatment_effect,evidence_pmid,evidence_snippet}]->(:Mechanism)."

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

            output_schema {
              type        = "object"
              description = "AD-004 envelope (contract_version 0.2, result_type \"table\"): observations are recorded facts, interpretation is added explanation, limitations state what the answer can't support."

              property {
                name     = "contract_version"
                type     = "string"
                required = true
              }

              property {
                name     = "result_type"
                type     = "string"
                required = true
              }

              property {
                name     = "summary"
                type     = "string"
                required = true
              }

              property {
                name        = "observations"
                type        = "array"
                description = "What a source record states, each with a refs array of evidence ids."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "interpretation"
                type        = "array"
                description = "Curator explanation, not an established fact. Each item carries basis and refs."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name     = "graph"
                type     = "object"
                required = true

                property {
                  name       = "nodes"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "edges"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name        = "paths"
                  type        = "array"
                  description = "Ordered chains of graph.edges: node_ids[i] and node_ids[i + 1] are joined by edge_ids[i]."
                  items_json  = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "evidence"
                type        = "array"
                description = "Flat lookup table of citations; observations/interpretation refs point into this by id."
                required    = true

                items {
                  type = "object"
                }
              }

              property {
                name        = "query"
                type        = "object"
                description = "The openCypher query that ran."

                property {
                  name = "language"
                  type = "string"
                }

                property {
                  name = "text"
                  type = "string"
                }

                property {
                  name = "parameters"
                  type = "object"
                }
              }

              property {
                name        = "limitations"
                type        = "array"
                description = "What the answer can't support. Always pass these on."
                required    = true

                items {
                  type = "string"
                }
              }

              property {
                name     = "trace"
                type     = "object"
                required = true

                property {
                  name = "request_id"
                  type = "string"
                }

                property {
                  name       = "plan"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }

                property {
                  name       = "tool_calls"
                  type       = "array"
                  items_json = jsonencode({ type = "object" })
                }
              }

              property {
                name        = "table"
                type        = "object"
                description = "Query result rows, present for result_type \"table\". columns is the list of RETURN aliases; each row is a list of cell values aligned to columns, in that order."

                property {
                  name       = "columns"
                  type       = "array"
                  items_json = jsonencode({ type = "string" })
                }

                property {
                  name       = "rows"
                  type       = "array"
                  items_json = jsonencode({ type = "array" })
                }
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
data "aws_iam_role" "orchestrator_agent_runtime" {
  name = "mmrf-orchestrator-agent-runtime"
}

resource "aws_iam_role_policy" "orchestrator_agent_invoke_kg_gateway" {
  name = "invoke-kg-gateway"
  role = data.aws_iam_role.orchestrator_agent_runtime.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock-agentcore:InvokeGateway"
      Resource = aws_bedrockagentcore_gateway.kg.gateway_arn
    }]
  })
}
