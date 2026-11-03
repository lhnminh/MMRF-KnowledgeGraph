variable "region" {
  description = "AWS region for the hackathon sandbox (allowed: us-east-1, us-west-2)"
  type        = string
  default     = "us-west-2"
}

variable "name" {
  description = "Name prefix for Neptune resources"
  type        = string
  default     = "mmrf-kg"
}

variable "neptune_instance_class" {
  description = "Neptune instance class"
  type        = string
  default     = "db.t3.medium"
}
