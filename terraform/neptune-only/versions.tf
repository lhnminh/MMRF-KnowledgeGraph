terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.71" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
}

provider "aws" {
  profile             = var.aws_profile
  region              = var.region
  allowed_account_ids = [var.sandbox_account_id]
  default_tags {
    tags = { Project = "orion-mmrf", Environment = "sandbox", Component = "neptune-only", Data = "public-or-synthetic" }
  }
}
