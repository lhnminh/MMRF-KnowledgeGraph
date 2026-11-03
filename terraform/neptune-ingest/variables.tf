variable "aws_profile" { type = string }
variable "sandbox_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.sandbox_account_id))
    error_message = "Provide the verified sandbox account ID."
  }
}
variable "region" { type = string }
variable "cluster_identifier" { type = string }
variable "cluster_resource_id" { type = string }
variable "neptune_endpoint" {
  type = string
  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+\\.neptune\\.amazonaws\\.com:8182$", var.neptune_endpoint))
    error_message = "Provide the existing Neptune HTTPS endpoint on port 8182."
  }
}
variable "vpc_id" { type = string }
variable "subnet_ids" { type = set(string) }
variable "neptune_security_group_id" { type = string }
variable "name" {
  type    = string
  default = "mmrf-kg-ingest"
}
