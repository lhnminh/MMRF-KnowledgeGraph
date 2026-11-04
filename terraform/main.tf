# Private VPC for Neptune + kg-graph Lambda: no internet gateway, no NAT, no 0.0.0.0/0 route.
resource "aws_vpc" "kg" {
  cidr_block           = "10.90.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name}-private-vpc" }
}

resource "aws_subnet" "kg_private" {
  count             = 2
  vpc_id            = aws_vpc.kg.id
  cidr_block        = "10.90.${count.index}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.name}-private-subnet-${count.index}" }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_route_table" "kg_private" {
  vpc_id = aws_vpc.kg.id
  tags   = { Name = "${var.name}-private-rt" }
}

resource "aws_route_table_association" "kg_private" {
  count          = length(aws_subnet.kg_private)
  subnet_id      = aws_subnet.kg_private[count.index].id
  route_table_id = aws_route_table.kg_private.id
}

resource "aws_neptune_subnet_group" "this" {
  name       = "${var.name}-subnets"
  subnet_ids = aws_subnet.kg_private[*].id
}

# AD-005: Neptune port open only to named security groups, not to whole CIDRs.
# The description is left as-is on purpose: changing it forces Terraform to replace the
# security group, which fails while Neptune is attached to it.
#
# Rules are separate resources, never inline blocks: inline blocks make this resource own every
# rule on the group, and they conflict with the separate kg-ingest rule in ingest.tf.
resource "aws_security_group" "neptune" {
  name        = "${var.name}-neptune-sg"
  description = "Allow openCypher/Bolt (8182) from inside the private VPC and the peered default VPC"
  vpc_id      = aws_vpc.kg.id
}

resource "aws_vpc_security_group_ingress_rule" "neptune_kg_graph" {
  security_group_id            = aws_security_group.neptune.id
  referenced_security_group_id = aws_security_group.loader_lambda.id
  description                  = "kg-graph Lambda"
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}

# No egress rule on this group: security groups are stateful, so Neptune does not need one to
# reply to the kg-graph/kg-ingest Bolt connections above. A rule allowing all protocols/ports to
# the whole VPC CIDR was removed here on 2026-11-04 as unused attack surface.

# The default parameter group can't turn on audit logging (it's read-only); a custom group in
# the same family can. neptune_enable_audit_log is a static parameter: it takes effect after the
# instance in aws_neptune_cluster_instance.this next reboots, not on apply.
resource "aws_neptune_cluster_parameter_group" "this" {
  name   = "${var.name}-cluster-params"
  family = "neptune1.4"

  parameter {
    name  = "neptune_enable_audit_log"
    value = "1"
  }
}

resource "aws_cloudwatch_log_group" "neptune_audit" {
  name              = "/aws/neptune/${var.name}-cluster/audit"
  retention_in_days = 14
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                   = "${var.name}-cluster"
  engine                               = "neptune"
  engine_version                       = "1.4.5.0"
  neptune_subnet_group_name            = aws_neptune_subnet_group.this.name
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.this.name
  vpc_security_group_ids               = [aws_security_group.neptune.id]
  skip_final_snapshot                  = true
  apply_immediately                    = true
  iam_database_authentication_enabled  = false
  enable_cloudwatch_logs_exports       = ["audit"]
  # Protects against an accidental destroy or a Terraform change that forces replacement
  # (see the storage_encrypted comment below); scripts/teardown.sh must disable this first.
  deletion_protection = true
  # Must stay in line with the live cluster: changing it deletes and recreates the cluster and
  # loses all data. Read `terraform plan` for "forces replacement" before every apply.
  storage_encrypted = true

  serverless_v2_scaling_configuration {
    min_capacity = 1
    max_capacity = 8
  }

  depends_on = [aws_cloudwatch_log_group.neptune_audit]
}

resource "aws_neptune_cluster_instance" "this" {
  cluster_identifier = aws_neptune_cluster.this.id
  instance_class     = "db.serverless"
  engine             = "neptune"
  apply_immediately  = true
}
