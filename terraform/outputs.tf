output "cluster_name" {
  value = module.eks.cluster_name
}

output "region" {
  value = var.region
}

output "kubeconfig_command" {
  description = "Run this after apply to point kubectl at the cluster"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_repository_url" {
  description = "Where to push the rag-api image"
  value       = aws_ecr_repository.rag_api.repository_url
}

output "rag_api_role_arn" {
  description = "Annotate the rag-api service account with this so pods can reach Bedrock"
  value       = module.rag_api_irsa.iam_role_arn
}

output "ecr_login_command" {
  value = "aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${split("/", aws_ecr_repository.rag_api.repository_url)[0]}"
}

output "documents_bucket" {
  description = "Upload the corpus and manifest.yaml here"
  value       = aws_s3_bucket.documents.id
}

output "upload_command" {
  value = "aws s3 sync ./phase0-authorization-lab/documents/ s3://${aws_s3_bucket.documents.id}/documents/ --region ${var.region}"
}
