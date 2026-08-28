# ─────────────────────────────────────────────────────────────────────────────
# Network
#
# Public subnets, no NAT gateway. Nodes get public IPs and reach the internet
# through the internet gateway directly.
#
# This is a deliberate cost decision for a demo cluster, not a production
# pattern. A NAT gateway is roughly 32 USD a month plus data transfer, and this
# cluster only exists while it is being worked on. In production the nodes
# would sit in private subnets.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"

  azs            = local.azs
  public_subnets = ["10.0.0.0/20", "10.0.16.0/20"]

  enable_nat_gateway   = false
  enable_dns_hostnames = true
  enable_dns_support   = true

  map_public_ip_on_launch = true

  # The load balancer controller finds subnets by these tags.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Cluster
# ─────────────────────────────────────────────────────────────────────────────

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  cluster_endpoint_public_access = true

  # Whoever runs terraform apply gets cluster admin. Without this you create a
  # cluster you cannot talk to.
  enable_cluster_creator_admin_permissions = true

  # OIDC provider. This is what makes IRSA work, which is how pods reach
  # Bedrock without any static credentials.
  enable_irsa = true

  cluster_addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni    = {}
    aws-ebs-csi-driver = {
      service_account_role_arn = module.ebs_csi_irsa.iam_role_arn
    }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = [var.instance_type]
      min_size       = var.node_count
      max_size       = var.node_count + 1
      desired_size   = var.node_count

      # Nodes are in public subnets, so they need public IPs to reach the
      # internet without a NAT gateway.
      subnet_ids = module.vpc.public_subnets
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM roles for service accounts
#
# Two of them. The EBS CSI driver needs one to create volumes. The application
# needs one to call Bedrock.
#
# This is the piece that makes it EKS rather than Kubernetes that happens to
# run on AWS. No credentials exist anywhere in the cluster.
# ─────────────────────────────────────────────────────────────────────────────

module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.47"

  role_name             = "${var.cluster_name}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

module "rag_api_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.47"

  role_name = "${var.cluster_name}-rag-api"

  role_policy_arns = {
    bedrock   = aws_iam_policy.bedrock_invoke.arn
    documents = aws_iam_policy.documents_read.arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["rag:rag-api"]
    }
  }
}

# Bedrock permissions.
#
# An inference profile is a separate resource from the model behind it, and
# invoking through a profile requires permission on both. The profile routes
# within a geography, so the underlying model must be allowed in every region
# it can route to. That is why the model resource is wildcarded by region.
resource "aws_iam_policy" "bedrock_invoke" {
  name        = "${var.cluster_name}-bedrock-invoke"
  description = "Invoke Titan embeddings and Claude via an APAC inference profile"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeEmbeddings"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
        ]
      },
      {
        Sid    = "InvokeClaudeViaProfile"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0",
          "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
        ]
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

# ─────────────────────────────────────────────────────────────────────────────
# Document store
#
# Documents are data, not code. They live in S3 and are read at ingestion time,
# so changing a document does not mean rebuilding a container image.
#
# Each document's attributes come from a manifest file in the same bucket. The
# manifest says what a document is: classification, owning team, customer.
# It never says who may read it. That distinction is the whole of ADR-001.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "documents" {
  bucket        = "${var.cluster_name}-documents-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id

  # Versioning matters here. A document's content and its classification can
  # both change, and being able to see what a document looked like when it was
  # ingested is the difference between an audit you can answer and one you
  # cannot.
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_iam_policy" "documents_read" {
  name        = "${var.cluster_name}-documents-read"
  description = "Read documents and the manifest from the corpus bucket"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.documents.arn]
      },
      {
        Sid      = "ReadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.documents.arn}/*"]
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Container registry
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "rag_api" {
  name                 = "${var.cluster_name}/rag-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}
