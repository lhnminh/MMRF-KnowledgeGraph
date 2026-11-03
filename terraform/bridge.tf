# IRSA keeps AWS credentials in the server-side bridge. No credentials enter the browser.
resource "aws_iam_openid_connect_provider" "eks" {
  url            = data.aws_eks_cluster.sandbox.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
}
locals {
  oidc_host = replace(data.aws_eks_cluster.sandbox.identity[0].oidc[0].issuer, "https://", "")
}
resource "aws_iam_role" "bridge" {
  name_prefix = "${var.name}-bridge-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = aws_iam_openid_connect_provider.eks.arn },
    Condition = { StringEquals = {
      "${local.oidc_host}:aud" = "sts.amazonaws.com",
      "${local.oidc_host}:sub" = "system:serviceaccount:mmrf-synthetic:orion-bridge"
    } }
  }] })
}
resource "aws_iam_role_policy" "bridge" {
  count = var.runtime_image_uri == "" ? 0 : 1
  role  = aws_iam_role.bridge.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"],
    Resource = [aws_bedrockagentcore_agent_runtime.orion[0].agent_runtime_arn, "${aws_bedrockagentcore_agent_runtime.orion[0].agent_runtime_arn}/runtime-endpoint/*"]
  }] })
}
output "bridge_role_arn" { value = aws_iam_role.bridge.arn }
