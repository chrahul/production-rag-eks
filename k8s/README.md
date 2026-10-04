# Kubernetes manifests

Everything that runs on the cluster. Apply in numeric order.

```
00-namespace.yaml      namespace rag, service account rag-api with the IRSA annotation
05-storageclass.yaml   gp3 storage for Qdrant
10-qdrant.yaml         Qdrant StatefulSet and headless Service
20-ingest-job.yaml     one-off ingestion Job, S3 and manifest driven
30-api.yaml            the RAG API Deployment and Service
```

## Values that are not in these files

Three values come from Terraform at apply time rather than being committed:
the IAM role ARN, the image URL and the bucket name. All three contain the
account ID, which never goes into a tracked file. The manifests carry
placeholders and a small `render` function fills them in.

The JWT signing secret is not in any file at all. It is generated at deploy
time and stored only as a Kubernetes Secret.

## Deploy, from a fresh cluster

Run from the repository root in Git Bash.

```bash
export AWS_PROFILE=production-rag AWS_REGION=ap-south-1

export RAG_API_ROLE_ARN=$(terraform -chdir=terraform output -raw rag_api_role_arn)
export RAG_API_IMAGE=$(terraform -chdir=terraform output -raw ecr_repository_url):v1
export DOCUMENTS_BUCKET=$(terraform -chdir=terraform output -raw documents_bucket)

render() {
  sed -e "s#\${RAG_API_ROLE_ARN}#$RAG_API_ROLE_ARN#g" \
      -e "s#\${RAG_API_IMAGE}#$RAG_API_IMAGE#g" \
      -e "s#\${DOCUMENTS_BUCKET}#$DOCUMENTS_BUCKET#g" "$1"
}
```

Point kubectl at the new cluster and confirm it before doing anything else.

```bash
aws eks update-kubeconfig --region ap-south-1 --name production-rag
kubectl config current-context
kubectl get nodes
```

Upload the corpus and the manifest. The bucket is destroyed with the cluster,
so this is needed after every apply.

```bash
aws s3 sync phase0-authorization-lab/documents/ s3://$DOCUMENTS_BUCKET/documents/ --exclude README.md
aws s3 cp manifest.yaml s3://$DOCUMENTS_BUCKET/manifest.yaml
```

Build and push the image.

```bash
aws ecr get-login-password | docker login --username AWS --password-stdin ${RAG_API_IMAGE%%/*}
docker build --platform linux/amd64 -t $RAG_API_IMAGE .
docker push $RAG_API_IMAGE
```

Qdrant.

```bash
render k8s/00-namespace.yaml | kubectl apply -f -
kubectl apply -f k8s/05-storageclass.yaml -f k8s/10-qdrant.yaml
kubectl -n rag rollout status statefulset/qdrant --timeout=5m
```

The signing secret, then ingestion.

```bash
kubectl -n rag create secret generic rag-jwt --from-literal=JWT_SECRET="$(openssl rand -hex 32)"

render k8s/20-ingest-job.yaml | kubectl apply -f -
kubectl -n rag wait --for=condition=complete job/rag-ingest --timeout=5m
kubectl -n rag logs job/rag-ingest
```

The API.

```bash
render k8s/30-api.yaml | kubectl apply -f -
kubectl -n rag rollout status deployment/rag-api --timeout=5m
```

## Use it

In one terminal:

```bash
kubectl -n rag port-forward svc/rag-api 8080:80
```

In another, mint tokens with the same secret the API verifies against. The
script stands in for an identity provider and runs on the laptop, not in the
cluster.

```bash
export JWT_SECRET=$(kubectl -n rag get secret rag-jwt -o jsonpath='{.data.JWT_SECRET}' | base64 -d)
eval "$(python -m scripts.mint_tokens)"

Q='{"question":"What is our approach to disaster recovery testing?"}'
for t in SAM ARJUN PRIYA; do
  tok=TOKEN_$t
  curl -s localhost:8080/ask -H "Authorization: Bearer ${!tok}" -H 'Content-Type: application/json' -d "$Q"
  echo
done
```

## Tear down

Delete the Qdrant volume claim first. Its EBS volume is created by the cluster
rather than by Terraform, so Terraform does not know to remove it.

```bash
kubectl delete namespace rag
kubectl get pv
terraform -chdir=terraform destroy
```

Wait until `kubectl get pv` shows nothing before running destroy.
