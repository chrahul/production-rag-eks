# Cluster

Terraform for the EKS cluster this platform runs on.

The cluster is disposable. It is created when there is work to do and destroyed
afterwards. Nothing in it is a source of truth.

## What it creates

A VPC with two public subnets across two availability zones. No NAT gateway.

An EKS cluster with a managed node group of two t3.medium instances.

Add-ons for CoreDNS, kube-proxy, the VPC CNI, and the EBS CSI driver.

Two IAM roles bound to Kubernetes service accounts. One lets the EBS CSI driver
create volumes. One lets the application call Bedrock.

An ECR repository for the API image.

## Why public subnets and no NAT gateway

A NAT gateway costs around 32 USD a month plus data transfer, and this cluster
does not run continuously. Nodes sit in public subnets with public IPs and reach
the internet through the internet gateway.

In production the nodes would be in private subnets. Either behind a NAT
gateway, or with VPC endpoints for ECR, S3 and Bedrock so that traffic to AWS
services never leaves the VPC at all.

This is a cost decision for a demo cluster, and it is worth saying so rather
than presenting it as a pattern to copy.

## Why IRSA matters here

The application calls Bedrock. It does that through an IAM role attached to its
Kubernetes service account, which means there are no AWS credentials anywhere
in the cluster. No access keys in a secret, no keys in the image, nothing to
rotate and nothing to leak.

The Bedrock policy is scoped rather than wildcarded. Titan embeddings are
allowed in this region only. Claude is allowed through a specific APAC
inference profile.

The profile is deliberately regional rather than global. Global profiles route
requests anywhere in the world, which would undermine the argument that
confidential documents stay inside a boundary. The regional profile means an
older Claude model, which is an acceptable trade for a system whose purpose is
handling documents people are not all cleared to read.

## Use

```bash
terraform init
terraform apply
```

Roughly fifteen minutes.

```bash
aws eks update-kubeconfig --region ap-south-1 --name production-rag
kubectl get nodes
```

When finished:

```bash
terraform destroy
```

## State

State is local. `terraform.tfstate` is gitignored because it contains resource
identifiers and can contain sensitive values.

This works because one person runs it from one machine. A team would use an S3
backend with DynamoDB locking so that two people cannot apply at once.

If you lose the state file, Terraform no longer knows the cluster exists and
you will have to delete the resources by hand. Keep it.

## Cost

Roughly 5 USD a day while running. Most of that is the EKS control plane at
0.10 USD an hour, which is charged whether or not anything is deployed.

Destroy the cluster when you are not using it.
