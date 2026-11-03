# Independent private network: no EKS cluster, public endpoint, IGW, or NAT is required.
resource "aws_vpc" "graph" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}
resource "aws_subnet" "graph" {
  count                   = 2
  vpc_id                  = aws_vpc.graph.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone_id    = var.availability_zone_ids[count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-private-${count.index}" }
}
resource "aws_route_table" "graph" { vpc_id = aws_vpc.graph.id }
resource "aws_route_table_association" "graph" {
  count          = 2
  subnet_id      = aws_subnet.graph[count.index].id
  route_table_id = aws_route_table.graph.id
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.graph.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.graph.id]
}
resource "aws_security_group" "utility" {
  name_prefix = "${var.name}-utility-"
  description = "VPC utility Lambda accesses only Neptune"
  vpc_id      = aws_vpc.graph.id
}
resource "aws_security_group" "neptune" {
  name_prefix = "${var.name}-db-"
  description = "Private IAM-authenticated Neptune"
  vpc_id      = aws_vpc.graph.id
}
resource "aws_vpc_security_group_ingress_rule" "utility_neptune" {
  security_group_id            = aws_security_group.neptune.id
  referenced_security_group_id = aws_security_group.utility.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}
resource "aws_vpc_security_group_egress_rule" "utility_neptune" {
  security_group_id            = aws_security_group.utility.id
  referenced_security_group_id = aws_security_group.neptune.id
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}
resource "aws_vpc_security_group_ingress_rule" "additional_client" {
  for_each                     = var.additional_client_security_group_ids
  security_group_id            = aws_security_group.neptune.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 8182
  to_port                      = 8182
}
resource "aws_vpc_security_group_egress_rule" "neptune_s3" {
  security_group_id = aws_security_group.neptune.id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
