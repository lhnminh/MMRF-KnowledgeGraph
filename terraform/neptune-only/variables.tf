variable "aws_profile" {
  type        = string
  description = "Explicit AWS CLI profile for the disposable sandbox account."
}
variable "sandbox_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.sandbox_account_id))
    error_message = "Provide the explicit twelve-digit sandbox account ID."
  }
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "name" {
  type    = string
  default = "orion-neptune"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,39}$", var.name))
    error_message = "Use a 3–40 character lowercase resource name starting with a letter."
  }
}
variable "vpc_cidr" {
  type    = string
  default = "10.92.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && try(tonumber(split("/", var.vpc_cidr)[1]) == 16, false)
    error_message = "Use an unused IPv4 /16 for the isolated sandbox VPC."
  }
}
variable "availability_zone_ids" {
  type        = list(string)
  default     = ["use1-az1", "use1-az2"]
  description = "Two AZ IDs in the selected Region. Defaults also support a future AgentCore runtime."
  validation {
    condition     = length(var.availability_zone_ids) == 2 && length(distinct(var.availability_zone_ids)) == 2
    error_message = "Provide two distinct AZ IDs."
  }
}
variable "neptune_instance_class" {
  type    = string
  default = "db.t4g.medium"
}
variable "additional_client_security_group_ids" {
  type        = set(string)
  default     = []
  description = "Optional existing client security groups in this VPC, for example an AgentCore runtime."
}
