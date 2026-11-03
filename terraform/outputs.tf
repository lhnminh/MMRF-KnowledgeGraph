output "neptune_cluster_endpoint" {
  value = aws_neptune_cluster.this.endpoint
}

output "neptune_bolt_uri" {
  value = "bolt://${aws_neptune_cluster.this.endpoint}:8182"
}

output "notebook_name" {
  value = aws_sagemaker_notebook_instance.workbench.name
}

output "notebook_console_url" {
  value = "https://${var.region}.console.aws.amazon.com/sagemaker/home?region=${var.region}#/notebook-instances/openNotebook/${aws_sagemaker_notebook_instance.workbench.name}?view=classic"
}
