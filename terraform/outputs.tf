output "neptune_cluster_endpoint" {
  value = aws_neptune_cluster.this.endpoint
}

output "neptune_bolt_uri" {
  value = "bolt://${aws_neptune_cluster.this.endpoint}:8182"
}
