data "aws_eks_cluster" "sandbox" { name = var.eks_cluster_name }
data "aws_eks_node_group" "sandbox" {
  cluster_name    = var.eks_cluster_name
  node_group_name = var.eks_node_group_name
}
data "aws_vpc" "sandbox" { id = data.aws_eks_cluster.sandbox.vpc_config[0].vpc_id }

# Reuse the sandbox VPC; private graph subnets have no Internet default route.
resource "aws_subnet" "graph" {
  count                = 2
  vpc_id               = data.aws_vpc.sandbox.id
  cidr_block           = cidrsubnet(data.aws_vpc.sandbox.cidr_block, 8, var.subnet_numbers[count.index])
  availability_zone_id = var.availability_zone_ids[count.index]
  tags                 = { Name = "${var.name}-graph-${count.index}" }
}
resource "aws_route_table" "graph" { vpc_id = data.aws_vpc.sandbox.id }
resource "aws_route_table_association" "graph" {
  count          = 2
  subnet_id      = aws_subnet.graph[count.index].id
  route_table_id = aws_route_table.graph.id
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.sandbox.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.graph.id]
}
resource "aws_security_group" "runtime" {
  name_prefix = "${var.name}-runtime-"
  vpc_id      = data.aws_vpc.sandbox.id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = [data.aws_vpc.sandbox.cidr_block]
  }
  egress {
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
  }
}
resource "aws_security_group" "endpoints" {
  name_prefix = "${var.name}-endpoints-"
  vpc_id      = data.aws_vpc.sandbox.id
  ingress {
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    security_groups = [aws_security_group.runtime.id]
  }
  # Interface endpoint private DNS applies to the entire VPC, including existing EKS image pulls.
  ingress {
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    security_groups = [data.aws_eks_cluster.sandbox.vpc_config[0].cluster_security_group_id]
  }
}
resource "aws_vpc_endpoint" "services" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs", "bedrock-runtime"])
  vpc_id              = data.aws_vpc.sandbox.id
  service_name        = "com.amazonaws.${var.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.graph[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
}
resource "aws_security_group" "neptune" {
  name_prefix = "${var.name}-neptune-"
  vpc_id      = data.aws_vpc.sandbox.id
  ingress {
    protocol        = "tcp"
    from_port       = 8182
    to_port         = 8182
    security_groups = [aws_security_group.runtime.id]
  }
  egress {
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
  }
}

resource "aws_security_group" "tools" {
  name_prefix = "${var.name}-tools-"
  vpc_id      = data.aws_vpc.sandbox.id
  ingress {
    protocol        = "tcp"
    from_port       = 80
    to_port         = 80
    security_groups = [aws_security_group.runtime.id]
  }
  egress {
    protocol        = "tcp"
    from_port       = 30081
    to_port         = 30081
    security_groups = [data.aws_eks_cluster.sandbox.vpc_config[0].cluster_security_group_id]
  }
}
resource "aws_vpc_security_group_ingress_rule" "tools_nodes" {
  security_group_id            = data.aws_eks_cluster.sandbox.vpc_config[0].cluster_security_group_id
  referenced_security_group_id = aws_security_group.tools.id
  ip_protocol                  = "tcp"
  from_port                    = 30081
  to_port                      = 30081
}
resource "aws_lb" "tools" {
  name_prefix        = "orion-"
  internal           = true
  load_balancer_type = "application"
  subnets            = aws_subnet.graph[*].id
  security_groups    = [aws_security_group.tools.id]
}
resource "aws_lb_target_group" "tools" {
  name_prefix = "orion-"
  port        = 30081
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = data.aws_vpc.sandbox.id
  health_check { path = "/_status" }
}
resource "aws_lb_listener" "tools" {
  load_balancer_arn = aws_lb.tools.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.tools.arn
  }
}
resource "aws_autoscaling_attachment" "tools" {
  autoscaling_group_name = data.aws_eks_node_group.sandbox.resources[0].autoscaling_groups[0].name
  lb_target_group_arn    = aws_lb_target_group.tools.arn
}
