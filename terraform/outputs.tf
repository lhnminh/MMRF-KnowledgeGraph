output "neptune_cluster_endpoint" {
  value = aws_neptune_cluster.this.endpoint
}

output "neptune_bolt_uri" {
  value = "bolt://${aws_neptune_cluster.this.endpoint}:8182"
}

output "ingest_bucket" {
  value = aws_s3_bucket.ingest.id
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.kg_alerts.arn
}

output "gateway_url" {
  value = aws_bedrockagentcore_gateway.kg.gateway_url
}
