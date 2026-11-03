resource "aws_s3_bucket" "graph" {
  bucket_prefix = "${var.name}-graph-"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "graph" {
  bucket                  = aws_s3_bucket.graph.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "graph" {
  bucket = aws_s3_bucket.graph.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.graph.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.graph.arn, "${aws_s3_bucket.graph.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_iam_role" "loader" {
  name_prefix = "${var.name}-loader-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "rds.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.sandbox_account_id } }
  }] })
}
resource "aws_iam_role_policy" "loader" {
  role = aws_iam_role.loader.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.graph.arn, Condition = { StringLike = { "s3:prefix" = ["graph/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.graph.arn}/graph/*" }
  ] })
}
resource "aws_neptune_subnet_group" "graph" {
  name       = var.name
  subnet_ids = aws_subnet.graph[*].id
}
resource "aws_neptune_cluster" "graph" {
  cluster_identifier                  = var.name
  neptune_subnet_group_name           = aws_neptune_subnet_group.graph.name
  vpc_security_group_ids              = [aws_security_group.neptune.id]
  storage_encrypted                   = true
  iam_database_authentication_enabled = true
  iam_roles                           = [aws_iam_role.loader.arn]
  backup_retention_period             = 1
  deletion_protection                 = false
  skip_final_snapshot                 = true
  depends_on                          = [aws_iam_role_policy.loader, aws_vpc_endpoint.s3, aws_vpc_security_group_egress_rule.neptune_s3]
}
resource "aws_neptune_cluster_instance" "graph" {
  identifier          = "${var.name}-1"
  cluster_identifier  = aws_neptune_cluster.graph.id
  instance_class      = var.neptune_instance_class
  publicly_accessible = false
}
