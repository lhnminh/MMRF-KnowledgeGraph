output "graph_bucket" { value = aws_s3_bucket.graph.id }
output "graph_utility_function" { value = aws_lambda_function.utility.function_name }
output "loader_role_arn" { value = aws_iam_role.loader.arn }
output "cluster_identifier" { value = var.cluster_identifier }
output "region" { value = var.region }
