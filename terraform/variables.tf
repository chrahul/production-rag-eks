variable "region" {
  description = "AWS region. Must be one where Bedrock has Titan embeddings and a Claude inference profile."
  type        = string
  default     = "ap-south-1"
}

variable "cluster_name" {
  type    = string
  default = "production-rag"
}

variable "cluster_version" {
  type    = string
  default = "1.31"
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "node_count" {
  type    = number
  default = 2
}
