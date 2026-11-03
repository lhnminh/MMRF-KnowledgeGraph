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

# AD-005: Neptune port open only to the tool backend's security group, not to whole CIDRs.
# The description is left as-is on purpose: changing it forces Terraform to replace the
# security group, which fails while Neptune is attached to it.
resource "aws_security_group" "neptune" {
  name        = "${var.name}-neptune-sg"
  description = "Allow openCypher/Bolt (8182) from inside the private VPC and the peered default VPC"
  vpc_id      = aws_vpc.kg.id

  ingress {
    description     = "kg-graph Lambda"
    from_port       = 8182
    to_port         = 8182
    protocol        = "tcp"
    security_groups = [aws_security_group.loader_lambda.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.kg.cidr_block]
  }
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                  = "${var.name}-cluster"
  engine                              = "neptune"
  engine_version                      = "1.4.5.0"
  neptune_subnet_group_name           = aws_neptune_subnet_group.this.name
  vpc_security_group_ids              = [aws_security_group.neptune.id]
  skip_final_snapshot                 = true
  apply_immediately                   = true
  iam_database_authentication_enabled = false
  deletion_protection                 = false

  serverless_v2_scaling_configuration {
    min_capacity = 1
    max_capacity = 8
  }
}

resource "aws_neptune_cluster_instance" "this" {
  cluster_identifier = aws_neptune_cluster.this.id
  instance_class     = "db.serverless"
  engine             = "neptune"
  apply_immediately  = true
}
