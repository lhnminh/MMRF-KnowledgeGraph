data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_route_table" "default_main" {
  vpc_id = data.aws_vpc.default.id
  filter {
    name   = "association.main"
    values = ["true"]
  }
}

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

resource "aws_security_group" "neptune" {
  name        = "${var.name}-neptune-sg"
  description = "Allow openCypher/Bolt (8182) from inside the private VPC and the peered default VPC"
  vpc_id      = aws_vpc.kg.id

  ingress {
    from_port   = 8182
    to_port     = 8182
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.kg.cidr_block, data.aws_vpc.default.cidr_block]
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

# Peering so the SageMaker notebook (default VPC, has internet) can still reach Neptune.
# The private VPC itself gets no route back out to the internet.
resource "aws_vpc_peering_connection" "notebook_to_kg" {
  vpc_id      = data.aws_vpc.default.id
  peer_vpc_id = aws_vpc.kg.id
  auto_accept = true

  tags = { Name = "${var.name}-notebook-to-kg-peering" }
}

resource "aws_route" "default_to_kg" {
  route_table_id            = data.aws_route_table.default_main.id
  destination_cidr_block    = aws_vpc.kg.cidr_block
  vpc_peering_connection_id = aws_vpc_peering_connection.notebook_to_kg.id
}

resource "aws_route" "kg_to_default" {
  route_table_id            = aws_route_table.kg_private.id
  destination_cidr_block    = data.aws_vpc.default.cidr_block
  vpc_peering_connection_id = aws_vpc_peering_connection.notebook_to_kg.id
}

resource "aws_iam_role" "notebook" {
  name = "${var.name}-notebook-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "notebook_s3" {
  role       = aws_iam_role.notebook.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
}

resource "aws_security_group" "notebook" {
  name        = "${var.name}-notebook-sg"
  description = "Neptune workbench notebook outbound access"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_sagemaker_notebook_instance" "workbench" {
  name                   = "${var.name}-workbench"
  role_arn               = aws_iam_role.notebook.arn
  instance_type          = "ml.t3.medium"
  subnet_id              = data.aws_subnets.default.ids[0]
  security_groups        = [aws_security_group.notebook.id]
  direct_internet_access = "Enabled"

  lifecycle_config_name = aws_sagemaker_notebook_instance_lifecycle_configuration.workbench.name
}

resource "aws_sagemaker_notebook_instance_lifecycle_configuration" "workbench" {
  name = "${var.name}-workbench-lifecycle"

  on_start = base64encode(<<-EOF
    #!/bin/bash
    set -e
    sudo -u ec2-user -i <<'INNER'
    source /home/ec2-user/anaconda3/bin/activate python3
    pip install -q --index-url https://pypi.org/simple neo4j pyyaml requests
    conda deactivate
    INNER
  EOF
  )
}
