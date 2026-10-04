# Permission-aware RAG on Amazon EKS

This tutorial builds a retrieval-augmented generation platform on Amazon EKS where three people ask the same question and receive three different answers, because the authorization filter runs inside the vector search rather than after it. Every command and every output below was captured from a live run in `ap-south-1`. Account identifiers are replaced with `<account-id>`.

The infrastructure diagram for everything in this document is `docs/diagrams/H-infrastructure-deep-dive.excalidraw`. The decision record behind it is `docs/adr/001-vector-store-is-not-an-authorization-system.md`.

---

## 1. Problem statement

### Why enterprise GenAI pilots stall

Most enterprise GenAI pilots do not fail on model quality. The demo works. They fail weeks later in review with the security, risk and platform teams, on questions the demo never had to answer:

- **Identity:** who is asking, and how does the system know?
- **Governance:** who decided what each document is, and where is that recorded?
- **Security:** can the system be made to reveal something the user is not cleared for?
- **Observability:** can you show which documents shaped a given answer, and for whom?
- **Cost:** what does it cost to run, and to keep running?

This project addresses the first three directly and makes the last two visible.

### Retrieval becomes a security boundary

Put confidential documents into a retrieval system and retrieval stops being a quality concern. It becomes a security boundary.

An engineer asks a routine question. Semantic search returns the four most relevant chunks. Three are fine. The fourth comes from a restricted incident report. The model reads all four and writes one clear answer. It has no concept of the fourth chunk being confidential.

There is no second line of defence. You cannot instruct a model to keep a secret it has already been shown, and nothing sits between the model and the user to filter its output.

Traditional access control works because users request a specific resource. Open document 4471, the system checks the badge, access is granted or refused. AI search has no such moment. The user never names a document. The system decides what is relevant.

> In a normal system, access control decides what you are allowed to open. In an AI system, it has to decide what the search is allowed to look at.

The permission check therefore cannot happen at request time. It has to happen inside the search.

### Why post-filtering is wrong even when it leaks nothing

The implementation most teams write first searches everything, takes the top `k`, and then removes what the user may not see. Every individual authorization verdict it reaches is correct, and nothing restricted reaches the model. A code review finds nothing wrong. It is still wrong, in three ways:

1. **Degraded results.** The user asks for four results and receives however many survived. The answer gets worse because of documents belonging to other people. With a narrow enough corpus the user receives nothing and is told no information exists, while the system holds material they are cleared to read.
2. **Metadata leakage.** The number of discarded results is a fact about documents the user cannot see. A non-zero discard count on a question about an incident confirms the incident exists. Hiding the count does not fully help, because the drop in result count and answer quality reveals the same thing.
3. **Restricted data inside the application.** Restricted chunks are loaded into application memory for an unauthorized user before being dropped. One logging statement, one exception message or one defect in the filter loop exposes them. With pre-filtering they never leave the database.

The defect is not in a line of code. It is in the order of two steps.

---

## 2. Architecture and ADR-001 principles

### The governing principle

> The vector database is an optimised search index. It is not the source of truth for identity, authorization, or document governance.

Every design decision in this repository follows from that sentence.

### Consequences

- **Documents store what they are, never who can read them.** Each chunk carries classification, owning team, customer and region. There is no user list, no group list and no resolved access control list anywhere in the payload.
- **Owning teams and customers are stable identifiers, never display names.** When a team is renamed, document metadata does not change.
- **User attributes come from the token and are never stored.** Clearance, teams and customers are read from a signed JWT at query time and discarded afterwards. This side cannot drift, because the identity provider re-evaluates claims on every token issue.
- **Only the document side can go stale.** That is a much smaller problem than keeping a second copy of the company's entitlements in sync.

### Access rule

```
public        anyone
internal      clearance >= internal
confidential  clearance >= confidential AND (owning team OR entitled customer)
restricted    clearance >= restricted AND owning team
```

Clearance is ordered `public < internal < confidential < restricted`. Internal is company-wide. Need-to-know applies at confidential and above. The rule is implemented once, in `src/common/authorization.py`, as `build_filter()`, which returns a Qdrant filter that is passed into the search.

### Corpus

Six synthetic documents, classified by `manifest.yaml`:

| Document | Classification | Owning team | Customer |
|---|---|---|---|
| AWS Well-Architected Summary | public | team-architecture | |
| SRE Runbook, Production API Latency | public | team-sre | |
| Platform Runbook, Kubernetes Node Replacement | internal | team-platform | |
| Internal Security Architecture Standard | confidential | team-security | |
| Customer Architecture Review, Project Apollo | confidential | team-architecture | cust-apollo |
| Security Incident Postmortem | restricted | team-security | |

A document present in the bucket but absent from the manifest is skipped, not ingested with a default. Failing closed is deliberate: an unclassified document defaulting to public is how leaks happen.

### Users

| User | Clearance | Teams | Customers |
|---|---|---|---|
| Sam | public | | |
| Arjun | confidential | team-platform, team-sre | cust-apollo |
| Priya | restricted | team-security | |

Priya holds the highest clearance and is not entitled to the Apollo account. The demonstration is built around that fact.

### Components

| Layer | Component | Role |
|---|---|---|
| Network | VPC `10.0.0.0/16`, two public subnets in two AZs, internet gateway, no NAT | Cost decision for a disposable cluster. Production uses private subnets with VPC endpoints for S3, ECR, STS and Bedrock. |
| Compute | EKS 1.35, managed node group of two `t3.medium` on Amazon Linux 2023 | Runs the API, the ingestion Job and Qdrant |
| Identity | IAM Roles for Service Accounts (IRSA) | Pods obtain temporary credentials. No access keys exist anywhere in the cluster. |
| Documents | S3 bucket, versioned, SSE-S3, public access blocked | Source of truth for document content and classification |
| Index | Qdrant `v1.12.4` StatefulSet on a 10 GiB encrypted gp3 EBS volume | Search index with payload indexes on classification, owning team, customer and region |
| Embeddings | Amazon Titan Text Embeddings v2, `ap-south-1` only | 1024-dimension vectors |
| Generation | Claude 3.5 Sonnet v2 through the `apac` inference profile | Receives only chunks the user is authorized to see. The profile routes only within APAC. |
| Registry | Amazon ECR | One image serves both the API and the ingestion Job |
| Tokens | HS256 JWT, signing secret in a Kubernetes Secret encrypted with KMS | Stands in for Entra, Okta, Cognito or Keycloak |

A regional inference profile is used rather than a global one. A global profile can route a request anywhere in the world, which would break the data boundary this system is designed to hold.

### Request path

1. The client presents a signed JWT.
2. The API verifies the signature, expiry, issuer and audience locally. Nothing is looked up.
3. The API builds a Qdrant filter from the token's claims.
4. The question is embedded with Titan.
5. Qdrant searches only the chunks that pass the filter.
6. The top four permitted chunks are sent to Claude.
7. Claude answers only from those chunks.

---

## 3. Implementation guide

### Prerequisites

| Tool | Version used |
|---|---|
| AWS CLI | 2.30.2 |
| Terraform | 1.14.5 |
| kubectl | 1.34.1 |
| Docker Desktop | 4.74.0, engine 29.4.3 |
| Python with PyJWT | 3.14, PyJWT 2.15 |

Bedrock access to `amazon.titan-embed-text-v2:0` and `anthropic.claude-3-5-sonnet-20241022-v2:0` in `ap-south-1` is required.

Run every command from the repository root in Git Bash unless stated otherwise. The shell must keep its environment variables between steps. If the terminal window is closed, repeat Steps 2, 25 and 26.

### Phase 1: Environment and identity

**Step 1. Confirm the AWS profile.**

```bash
aws configure list --profile production-rag
```

```
      Name                    Value             Type    Location
      ----                    -----             ----    --------
   profile           production-rag           manual    --profile
access_key     ******************** shared-credentials-file
secret_key     ******************** shared-credentials-file
    region               ap-south-1              env    ['AWS_REGION', 'AWS_DEFAULT_REGION']
```

**Step 2. Make the profile and region the default for this shell.**

```bash
export AWS_PROFILE=production-rag AWS_REGION=ap-south-1
```

A startup file that sets `AWS_PROFILE` to a different profile will silently override this in every new window. See Troubleshooting.

**Step 3. Confirm which account and identity the credentials resolve to.**

```bash
aws sts get-caller-identity --query Arn --output text | sed -E 's/[0-9]{8}([0-9]{4})/********\1/'
```

```
arn:aws:iam::<account-id>:user/rc_devops
```

### Phase 2: Infrastructure with Terraform

**Step 4. Initialise Terraform.**

```bash
terraform -chdir=terraform init
```

```
- Using previously-installed hashicorp/aws v5.100.0
...
Terraform has been successfully initialized!
```

**Step 5. Review the plan.**

```bash
terraform -chdir=terraform plan
```

```
Plan: 63 to add, 0 to change, 0 to destroy.
```

The 63 resources break down as: network 11, EKS cluster and node group 39, IRSA roles and policies 8, S3 4, ECR 1.

The cluster version is pinned to 1.35 in `terraform/variables.tf`. Versions in EKS extended support cost 0.60 USD per hour for the control plane instead of 0.10. Amazon Linux 2 node images are not published for 1.33 and later, so the node group sets `ami_type = "AL2023_x86_64_STANDARD"` explicitly.

**Step 6. Apply.**

```bash
terraform -chdir=terraform apply
```

```
Apply complete! Resources: 63 added, 0 changed, 0 destroyed.
```

Allow 15 to 20 minutes. The outputs printed after apply contain the account ID.

**Step 7. Point kubectl at the new cluster and confirm the nodes.**

```bash
aws eks update-kubeconfig --region ap-south-1 --name production-rag
kubectl get nodes
```

```
NAME                                         STATUS   ROLES    AGE   VERSION
ip-10-0-21-100.ap-south-1.compute.internal   Ready    <none>   18m   v1.35.8-eks-f4fc4f1
ip-10-0-3-28.ap-south-1.compute.internal     Ready    <none>   18m   v1.35.8-eks-f4fc4f1
```

One node sits in each subnet. `update-kubeconfig` writes the active `AWS_PROFILE` into `~/.kube/config`, so kubectl continues to use the correct profile even in a shell where `AWS_PROFILE` is wrong.

### Phase 3: Corpus and container image

**Step 8. Upload the six documents.**

```bash
aws s3 sync phase0-authorization-lab/documents/ s3://$(terraform -chdir=terraform output -raw documents_bucket)/documents/ --exclude README.md
```

```
upload: phase0-authorization-lab\documents\01_public_aws_well_architected_summary.md to s3://production-rag-documents-<account-id>/documents/01_public_aws_well_architected_summary.md
...
upload: phase0-authorization-lab\documents\02_platform_runbook.md to s3://production-rag-documents-<account-id>/documents/02_platform_runbook.md
```

Six `upload:` lines. The bucket is destroyed with the cluster, so this step is needed after every apply.

**Step 9. Upload the manifest.**

```bash
aws s3 cp manifest.yaml s3://$(terraform -chdir=terraform output -raw documents_bucket)/manifest.yaml
```

```
upload: .\manifest.yaml to s3://production-rag-documents-<account-id>/manifest.yaml
```

**Step 10. Build the image.**

```bash
docker build --platform linux/amd64 -t rag-api:v3 .
```

```
[+] Building 2.6s (12/12) FINISHED
 => CACHED [5/7] RUN pip install --no-cache-dir -r requirements.txt
 => [6/7] COPY src/ ./src/
 => => naming to docker.io/library/rag-api:v3
```

Image `v3` contains two fixes found during the first cluster run: a 30-second clock-skew leeway in JWT verification, and line-ending normalisation in ingestion. Both are described in Troubleshooting.

**Step 11. Log Docker in to ECR.**

```bash
aws ecr get-login-password | docker login --username AWS --password-stdin $(terraform -chdir=terraform output -raw ecr_repository_url | cut -d/ -f1)
```

```
Login Succeeded
```

**Step 12. Tag the image with its ECR name.**

```bash
docker tag rag-api:v3 $(terraform -chdir=terraform output -raw ecr_repository_url):v3
```

**Step 13. Push.**

```bash
docker push $(terraform -chdir=terraform output -raw ecr_repository_url):v3
```

```
2a53836ad068: Pushed
v3: digest: sha256:5a2dcf7e4ca55067ac0a3e43bd446261d3790e687c7ffa8feb6dbbf6dafb9e07 size: 856
```

### Phase 4: Namespace, identity binding and Qdrant

The manifests in `k8s/` carry placeholders for values that contain the account ID. `sed` substitutes them from Terraform output at apply time, so the account ID never appears in a tracked file.

**Step 14. Create the namespace and the service account.**

```bash
sed "s#\${RAG_API_ROLE_ARN}#$(terraform -chdir=terraform output -raw rag_api_role_arn)#" k8s/00-namespace.yaml | kubectl apply -f -
kubectl get sa -n rag
```

```
NAME      AGE
default   23s
rag-api   23s
```

The IAM role trusts only `rag:rag-api`. A pod running as `rag:default` receives no AWS access.

**Step 15. Verify the IRSA annotation.**

```bash
kubectl -n rag get sa rag-api -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}' | sed -E 's/[0-9]{8}([0-9]{4})/********\1/'; echo
```

```
arn:aws:iam::<account-id>:role/production-rag-rag-api
```

**Step 16. Create the gp3 storage class and Qdrant.**

```bash
kubectl apply -f k8s/05-storageclass.yaml -f k8s/10-qdrant.yaml
kubectl get all -n rag
```

```
NAME           READY   STATUS    RESTARTS   AGE
pod/qdrant-0   1/1     Running   0          76s

NAME             TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)             AGE
service/qdrant   ClusterIP   None         <none>        6333/TCP,6334/TCP   77s

NAME                      READY   AGE
statefulset.apps/qdrant   1/1     77s
```

`CLUSTER-IP None` is a headless Service. The DNS name `qdrant` resolves directly to the pod.

**Step 17. Verify the volume claim.**

```bash
kubectl -n rag get pvc
```

```
NAME               STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS
storage-qdrant-0   Bound    pvc-891b8baa-783b-49f7-b120-a9f31b7bf0d9   10Gi       RWO            gp3
```

`Bound` also proves the EBS CSI driver's own IRSA role works, because it had to call AWS to create the volume.

### Phase 5: Signing secret and ingestion

**Step 18. Create the JWT signing secret.**

```bash
kubectl -n rag create secret generic rag-jwt --from-literal=JWT_SECRET="$(openssl rand -hex 32)"
kubectl -n rag get secret rag-jwt
```

```
NAME      TYPE     DATA   AGE
rag-jwt   Opaque   1      4m4s
```

The value is generated and passed directly to the cluster. It is never printed or written to a file. The API Deployment references it with a non-optional `secretKeyRef`, so the pod refuses to start without it rather than falling back to the development default in `src/api/tokens.py`.

**Step 19. Run ingestion.**

```bash
sed -e "s#\${RAG_API_IMAGE}#$(terraform -chdir=terraform output -raw ecr_repository_url):v3#" \
    -e "s#\${DOCUMENTS_BUCKET}#$(terraform -chdir=terraform output -raw documents_bucket)#" \
    k8s/20-ingest-job.yaml | kubectl apply -f -
```

```
job.batch/rag-ingest created
```

**Step 20. Read the ingestion log.**

```bash
kubectl -n rag logs -f job/rag-ingest
```

```
====================================================================
Ingestion
  bucket     s3://production-rag-documents-<account-id>
  manifest   manifest.yaml
  qdrant     http://qdrant:6333
====================================================================

manifest lists 6 documents

collection 'enterprise_docs' created with payload indexes

    3 chunks   public / team-architecture          documents/01_public_aws_well_architected_summary.md
    3 chunks   internal / team-platform            documents/02_platform_runbook.md
    3 chunks   restricted / team-security          documents/03_security_incident_postmortem.md
    3 chunks   confidential / cust-apollo          documents/04_customer_architecture_review.md
    2 chunks   public / team-sre                   documents/05_sre_incident_runbook.md
    3 chunks   confidential / team-security        documents/06_security_architecture_standard.md

17 chunks indexed
```

This is the first test of IRSA for application code. The pod holds no AWS keys, yet it read S3 and made 17 Titan calls. The count of 17 also confirms the line-ending fix: the files in S3 have Windows line endings.

### Phase 6: API deployment and tokens

**Step 21. Deploy the API.**

```bash
sed "s#\${RAG_API_IMAGE}#$(terraform -chdir=terraform output -raw ecr_repository_url):v3#" k8s/30-api.yaml | kubectl apply -f -
```

```
deployment.apps/rag-api created
service/rag-api created
```

**Step 22. Wait for readiness.**

```bash
kubectl -n rag rollout status deployment/rag-api
```

```
deployment "rag-api" successfully rolled out
```

The readiness probe calls `/readyz`, which succeeds only if Qdrant is reachable.

**Step 23. Open a tunnel in a second terminal and leave it running.**

```bash
kubectl -n rag port-forward svc/rag-api 8080:80
```

```
Forwarding from 127.0.0.1:8080 -> 8000
Forwarding from [::1]:8080 -> 8000
```

Nothing is exposed to the internet. The tunnel runs through the authenticated EKS API server.

**Step 24. Confirm the tunnel.**

```bash
curl -s localhost:8080/healthz; echo
```

```
{"status":"ok"}
```

**Step 25. Load the signing secret into the shell.**

```bash
export JWT_SECRET=$(kubectl -n rag get secret rag-jwt -o jsonpath='{.data.JWT_SECRET}' | base64 -d)
```

**Step 26. Mint tokens for the three users.**

```bash
eval "$(python -m scripts.mint_tokens)"
```

This sets `TOKEN_SAM`, `TOKEN_ARJUN` and `TOKEN_PRIYA`, each valid for one hour. `scripts/mint_tokens.py` stands in for the identity provider and runs on the operator's machine, not in the cluster.

### Phase 7: Authorization demonstrations

**Step 27. Identity comes from the token.**

```bash
curl -s localhost:8080/whoami -H "Authorization: Bearer $TOKEN_ARJUN"; echo
```

```json
{"subject":"arjun","name":"Arjun Mehta","clearance":"confidential","teams":["team-platform","team-sre"],"customers":["cust-apollo"],"source":"the token you presented, nothing was looked up"}
```

**Step 28. The risk: unfiltered search.**

```bash
kubectl -n rag exec deploy/rag-api -- python -c "from src.common.retrieval import client, COLLECTION; from src.common.embeddings import embed; [print(f'{p.score:.3f}  {p.payload[\"classification\"]:<13} {p.payload[\"doc_title\"]}') for p in client().query_points(COLLECTION, query=embed('What is our approach to disaster recovery testing?'), limit=4, with_payload=True).points]"
```

```
0.421  public        AWS Well-Architected Summary, Reliability and Operations
0.369  confidential  Customer Architecture Review, Project Apollo
0.327  confidential  Customer Architecture Review, Project Apollo
0.305  public        AWS Well-Architected Summary, Reliability and Operations
```

Without a filter, two of the four most relevant chunks are confidential. These would reach the model for every user.

**Step 29. What a stored chunk contains.**

```bash
kubectl -n rag exec deploy/rag-api -- python -c "from src.common.retrieval import client, COLLECTION; from qdrant_client import models; import json; p=client().scroll(COLLECTION, scroll_filter=models.Filter(must=[models.FieldCondition(key='customer', match=models.MatchValue(value='cust-apollo'))]), limit=1, with_payload=True)[0][0].payload; p['text']=p['text'][:70]+' ...'; print(json.dumps(p, indent=2))"
```

```json
{
  "classification": "confidential",
  "owning_team": "team-architecture",
  "customer": "cust-apollo",
  "region": "india",
  "doc_id": "doc-apollo-architecture-review",
  "doc_title": "Customer Architecture Review, Project Apollo",
  "source_key": "documents/04_customer_architecture_review.md",
  "text": "plication subnets, managed Kubernetes for selected workloads, centrali ...",
  "chunk_index": 1
}
```

No field names a person. The index cannot drift into a second entitlement system because it holds no entitlement data.

**Step 30. What each user's filter permits.**

```bash
kubectl -n rag exec -i deploy/rag-api -- python - <<'EOF'
from src.common.authorization import UserContext, build_filter
from src.common.retrieval import client, COLLECTION
users = [UserContext("Sam", "public"),
         UserContext("Arjun", "confidential", ["team-platform", "team-sre"], ["cust-apollo"]),
         UserContext("Priya", "restricted", ["team-security"])]
for u in users:
    points = client().scroll(COLLECTION, scroll_filter=build_filter(u), limit=50, with_payload=True)[0]
    print(f"\n{u.username} ({u.clearance}): {len(points)} of 17 chunks eligible")
    for cls, title in sorted({(p.payload["classification"], p.payload["doc_title"]) for p in points}):
        print(f"    {cls:<13} {title}")
EOF
```

```
Sam (public): 5 of 17 chunks eligible
    public        AWS Well-Architected Summary, Reliability and Operations
    public        SRE Runbook, Production API Latency

Arjun (confidential): 11 of 17 chunks eligible
    confidential  Customer Architecture Review, Project Apollo
    internal      Platform Runbook, Kubernetes Node Replacement
    public        AWS Well-Architected Summary, Reliability and Operations
    public        SRE Runbook, Production API Latency

Priya (restricted): 14 of 17 chunks eligible
    confidential  Internal Security Architecture Standard, Data Handling
    internal      Platform Runbook, Kubernetes Node Replacement
    public        AWS Well-Architected Summary, Reliability and Operations
    public        SRE Runbook, Production API Latency
    restricted    Security Incident Postmortem, Temporary Credential Exposure
```

Each confidential document is visible to exactly one of these users, and a different one in each case. Clearance decides how high a user can reach. Team and customer decide which documents at that level belong to them.

**Steps 31 to 33. The same question from three users.**

```bash
Q='{"question":"What is our approach to disaster recovery testing?"}'
curl -s localhost:8080/ask -H "Authorization: Bearer $TOKEN_SAM"   -H "Content-Type: application/json" -d "$Q" | python -m json.tool
curl -s localhost:8080/ask -H "Authorization: Bearer $TOKEN_ARJUN" -H "Content-Type: application/json" -d "$Q" | python -m json.tool
curl -s localhost:8080/ask -H "Authorization: Bearer $TOKEN_PRIYA" -H "Content-Type: application/json" -d "$Q" | python -m json.tool
```

Full responses are reproduced in Section 4.

**Step 34. The naive implementation, for comparison.**

```bash
curl -s localhost:8080/compare -H "Authorization: Bearer $TOKEN_SAM" -H "Content-Type: application/json" -d "$Q" \
  | python -c "import json,sys; d=json.load(sys.stdin); [print(f\"{k:<13} searched {d[k]['searched']:>2}  returned {d[k]['returned']}  discarded {d[k]['discarded']}   \" + ', '.join(s['doc_title'].split(',')[0] for s in d[k]['sources'])) for k in ('prefiltered','postfiltered')]"
```

```
prefiltered   searched  5  returned 4  discarded 0   AWS Well-Architected Summary, AWS Well-Architected Summary, AWS Well-Architected Summary, SRE Runbook
postfiltered  searched 17  returned 2  discarded 2   AWS Well-Architected Summary, AWS Well-Architected Summary
```

The post-filtered path leaked no text, returned half the requested results, and disclosed through `discarded 2` that two relevant documents exist which Sam cannot read.

**Step 35. A change on the identity side takes effect immediately.**

Simulate Priya being granted the Apollo account by the identity provider. Nothing on the cluster changes.

```bash
export TOKEN_PRIYA_APOLLO=$(python -c "from src.api.tokens import issue; print(issue('priya', 'Priya Nair', 'restricted', ['team-security'], ['cust-apollo']))")
curl -s localhost:8080/ask -H "Authorization: Bearer $TOKEN_PRIYA_APOLLO" -H "Content-Type: application/json" -d "$Q" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('eligible', d['eligible_chunks'], 'of', d['total_chunks']); [print(f\"  {s['score']:.3f}  {s['classification']:<13} {s['doc_title']}\") for s in d['sources']]"
```

```
eligible 17 of 17
  0.421  public        AWS Well-Architected Summary, Reliability and Operations
  0.369  confidential  Customer Architecture Review, Project Apollo
  0.327  confidential  Customer Architecture Review, Project Apollo
  0.305  public        AWS Well-Architected Summary, Reliability and Operations
```

No sync job ran and no data was re-indexed. Access changed on the next request.

**Step 36. A change on the document side requires re-ingestion.**

Reclassify the Apollo review from confidential to internal in S3 only. The local `manifest.yaml` is untouched so the change can be reverted.

```bash
sed '/04_customer_architecture_review/,/customer:/ s/classification: confidential/classification: internal/' manifest.yaml \
  | aws s3 cp - s3://$(terraform -chdir=terraform output -raw documents_bucket)/manifest.yaml
aws s3 cp s3://$(terraform -chdir=terraform output -raw documents_bucket)/manifest.yaml - | grep -A6 04_customer
```

```
  - key: documents/04_customer_architecture_review.md
    title: Customer Architecture Review, Project Apollo
    doc_id: doc-apollo-architecture-review
    classification: internal
    owning_team: team-architecture
    customer: cust-apollo
    region: india
```

**Step 37. Observe the stale index.** Priya's original token, before re-ingestion:

```
eligible 14 of 17
  0.421  public        AWS Well-Architected Summary, Reliability and Operations
  0.305  public        AWS Well-Architected Summary, Reliability and Operations
  0.291  public        AWS Well-Architected Summary, Reliability and Operations
  0.209  internal      Platform Runbook, Kubernetes Node Replacement
```

The source of truth says internal. The index still says confidential.

**Step 38. Re-ingest.** A Kubernetes Job runs once, so delete it and create it again:

```bash
kubectl -n rag delete job rag-ingest
sed -e "s#\${RAG_API_IMAGE}#$(terraform -chdir=terraform output -raw ecr_repository_url):v3#" \
    -e "s#\${DOCUMENTS_BUCKET}#$(terraform -chdir=terraform output -raw documents_bucket)#" \
    k8s/20-ingest-job.yaml | kubectl apply -f -
kubectl -n rag logs -f job/rag-ingest
```

```
    3 chunks   internal / cust-apollo              documents/04_customer_architecture_review.md
...
17 chunks indexed
```

**Step 39. Priya's original token, after re-ingestion:**

```
eligible 17 of 17
  0.421  public        AWS Well-Architected Summary, Reliability and Operations
  0.369  internal      Customer Architecture Review, Project Apollo
  0.327  internal      Customer Architecture Review, Project Apollo
  0.305  public        AWS Well-Architected Summary, Reliability and Operations
```

**Step 40. Restore the original classification.**

```bash
aws s3 cp manifest.yaml s3://$(terraform -chdir=terraform output -raw documents_bucket)/manifest.yaml
kubectl -n rag delete job rag-ingest
# re-run Step 19 and Step 20
```

```
    3 chunks   confidential / cust-apollo          documents/04_customer_architecture_review.md
...
17 chunks indexed
```

S3 versioning retains all three versions of the manifest: original, reclassified and restored.

### Phase 8: Platform verification

**Step 41. No AWS keys in the pod.**

```bash
kubectl -n rag exec deploy/rag-api -- env | grep '^AWS_' | sed -E 's/[0-9]{8}([0-9]{4})/********\1/'
```

```
AWS_REGION=ap-south-1
AWS_STS_REGIONAL_ENDPOINTS=regional
AWS_ROLE_ARN=arn:aws:iam::<account-id>:role/production-rag-rag-api
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
```

There is no `AWS_ACCESS_KEY_ID` and no `AWS_SECRET_ACCESS_KEY`.

**Step 42. The identity AWS sees.**

```bash
kubectl -n rag exec deploy/rag-api -- python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])" | sed -E 's/[0-9]{8}([0-9]{4})/********\1/'
```

```
arn:aws:sts::<account-id>:assumed-role/production-rag-rag-api/botocore-session-1791092141
```

A temporary session for the role, not a user.

**Step 43. Delete the Qdrant pod.**

```bash
kubectl -n rag delete pod qdrant-0
kubectl -n rag get pods -l app=qdrant -w
```

```
pod "qdrant-0" deleted from rag namespace
NAME       READY   STATUS    RESTARTS   AGE
qdrant-0   1/1     Running   0          26s
```

**Step 44. Confirm the data survived.**

```bash
kubectl -n rag exec deploy/rag-api -- python -c "from src.common.retrieval import client, COLLECTION; print('points in', COLLECTION, ':', client().count(COLLECTION, exact=True).count)"
```

```
points in enterprise_docs : 17
```

The replacement pod reattached the same EBS volume. The API reconnected without a restart because it addresses Qdrant by Service name.

### Phase 9: Teardown

Delete the namespace first. The Qdrant EBS volume is created by the cluster, not by Terraform, so Terraform does not know to remove it.

**Step 45. Confirm the context, then delete the namespace.**

```bash
kubectl config current-context
kubectl delete namespace rag
```

```
namespace "rag" deleted
```

**Step 46. Wait until no persistent volumes remain.**

```bash
kubectl get pv
```

```
No resources found
```

**Step 47. Destroy the infrastructure.**

```bash
terraform -chdir=terraform destroy
```

```
Plan: 0 to add, 0 to change, 63 to destroy.
Destroy complete! Resources: 63 destroyed.
```

**Step 48. Verify nothing remains billing.**

```bash
aws eks list-clusters --query clusters --output text
aws ec2 describe-volumes --query 'Volumes[].VolumeId' --output text
aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text
```

All three return empty. The cluster's KMS key enters a 30-day pending-deletion window and is removed automatically.

---

## 4. Proof of execution

### Phase 0 lab and cluster agree

The local Phase 0 lab (`phase0-authorization-lab/`, run with `docker compose up -d`, `python -m lab.ingest` and `python -m lab.demo`) recorded six documents, seventeen chunks, Arjun receiving the Apollo review at scores 0.369 and 0.327, and Priya falling through to the platform runbook at 0.209. The cluster run reproduced every one of those values over HTTP:

| Checkpoint | Phase 0 lab | EKS cluster |
|---|---|---|
| Chunks indexed | 17 | 17 |
| Arjun, Apollo review | 0.369, 0.327 | 0.3688, 0.3274 |
| Priya, fourth source | Platform Runbook 0.209 | Platform Runbook 0.2095 |

The retrieval and authorization code did not change between the lab and the service. That was the stated test of whether Phase 0 was built correctly.

### Ingestion

```
manifest lists 6 documents

collection 'enterprise_docs' created with payload indexes

    3 chunks   public / team-architecture          documents/01_public_aws_well_architected_summary.md
    3 chunks   internal / team-platform            documents/02_platform_runbook.md
    3 chunks   restricted / team-security          documents/03_security_incident_postmortem.md
    3 chunks   confidential / cust-apollo          documents/04_customer_architecture_review.md
    2 chunks   public / team-sre                   documents/05_sre_incident_runbook.md
    3 chunks   confidential / team-security        documents/06_security_architecture_standard.md

17 chunks indexed
```

The manifest was validated before any document was read. Each chunk was written with document attributes only.

### Sam, public clearance

```json
{
    "answer": "From the provided context, I cannot give a complete answer about our specific approach to disaster recovery testing. While the documents mention that recovery procedures should be tested (\"tested recovery procedures reduce the time required to respond to operational events\" from AWS Well-Architected Summary), they don't detail how this testing should be done or what the specific approach is. ...",
    "sources": [
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.4206},
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.3053},
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.2908},
        {"doc_title": "SRE Runbook, Production API Latency", "classification": "public", "owning_team": "team-sre", "customer": null, "score": 0.1951}
    ],
    "asked_by": "Sam Patel",
    "clearance": "public",
    "eligible_chunks": 5,
    "total_chunks": 17,
    "latency_ms": 3722
}
```

The Apollo chunks that ranked second and third without a filter were never candidates. Sam received four results, not two. The model stated what was missing rather than inventing it.

### Arjun, confidential clearance, Apollo customer entitlement

```json
{
    "answer": "Based on the provided context, there are two key points about disaster recovery testing:\n\n1. From \"AWS Well-Architected Summary, Reliability and Operations\": Workloads should have clearly defined recovery objectives, monitored health signals, and automated recovery mechanisms where practical.\n\n2. From \"Customer Architecture Review, Project Apollo\": Disaster recovery designs should be validated through controlled exercises before production migration, rather than relying only on backups. ...",
    "sources": [
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.4206},
        {"doc_title": "Customer Architecture Review, Project Apollo", "classification": "confidential", "owning_team": "team-architecture", "customer": "cust-apollo", "score": 0.3688},
        {"doc_title": "Customer Architecture Review, Project Apollo", "classification": "confidential", "owning_team": "team-architecture", "customer": "cust-apollo", "score": 0.3274},
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.3053}
    ],
    "asked_by": "Arjun Mehta",
    "clearance": "confidential",
    "eligible_chunks": 11,
    "total_chunks": 17,
    "latency_ms": 3922
}
```

Arjun is not on the owning team. He receives the Apollo review through the customer branch of the confidential rule.

### Priya, restricted clearance, no Apollo entitlement

```json
{
    "answer": "From the provided context, I cannot give a complete answer about our specific approach to disaster recovery testing. ...",
    "sources": [
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.4206},
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.3053},
        {"doc_title": "AWS Well-Architected Summary, Reliability and Operations", "classification": "public", "owning_team": "team-architecture", "customer": null, "score": 0.2908},
        {"doc_title": "Platform Runbook, Kubernetes Node Replacement", "classification": "internal", "owning_team": "team-platform", "customer": null, "score": 0.2095}
    ],
    "asked_by": "Priya Nair",
    "clearance": "restricted",
    "eligible_chunks": 14,
    "total_chunks": 17,
    "latency_ms": 3952
}
```

The highest clearance in the organisation does not see the Apollo review, because Priya is neither on the owning team nor entitled to the customer. Her own confidential and restricted documents were eligible but not relevant to the question. Eligibility and relevance are separate decisions: the filter decides what the search may look at, and similarity decides what is best among those.

| User | Clearance | Eligible | Apollo review | Fourth source |
|---|---|---|---|---|
| Sam | public | 5 | no | SRE Runbook 0.195 |
| Arjun | confidential | 11 | yes, 0.369 and 0.327 | Apollo ranks second and third |
| Priya | restricted | 14 | no | Platform Runbook 0.209 |

---

## 5. Troubleshooting

These failures occurred during the build. Each is fixed in the code or documented in the procedure.

| Symptom | Cause | Resolution |
|---|---|---|
| Ingestion reports 6 chunks instead of 17 | Documents uploaded from Windows carry CRLF line endings. Chunking splits on `\n\n`, which never matches `\r\n\r\n`. | `read_document()` in `src/ingestion/ingest.py` normalises `\r\n` to `\n`. Fixed in image `v3`. |
| `token is not valid: The token is not yet valid (iat)` | The minting machine's clock was half a second ahead of the pod's. JWT verification had zero tolerance. | `verify()` in `src/api/tokens.py` allows 30 seconds of leeway. Fixed in image `v2`. |
| `curl: (7) Failed to connect to localhost port 8080` | `kubectl port-forward` closed after an idle period or sleep. | Restart Step 23. |
| `token is not valid: Not enough segments` | The token variable is empty because the shell was restarted. | Repeat Steps 25 and 26. |
| `{"detail":"token has expired"}` | Tokens are valid for one hour. | Repeat Step 26. |
| `InvalidAccessKeyId` from the AWS CLI while kubectl still works | A shell startup file sets `AWS_PROFILE` to another profile. kubectl uses the profile stored in `~/.kube/config`. | Repeat Step 2. Remove the stray setting from the startup file. |
| Upload from a pipe or a Windows path fails with `path does not exist` | Git Bash path conversion passes a POSIX path to the Windows `aws.exe`. | Use Windows paths for local files passed to `aws.exe`, or run from the repository root with relative paths. |
| Terraform plan files appear as untracked files | `*.tfplan` does not match a file named `tfplan`. | Write plan files outside the repository. |

---

## 6. Known limitations and open questions

| Area | Current state | Production direction |
|---|---|---|
| Token signing | HS256 shared secret. Any holder of the secret can mint tokens. | RS256 from the identity provider. The API holds only the public key from the JWKS endpoint. |
| User-side staleness | An issued token remains valid until it expires, up to one hour. | Shorter token lifetimes and identity provider revocation. |
| Document-side staleness | Reclassification is visible only after re-ingestion. A document tightened from internal to restricted remains visible to too many users during that window. | Trigger ingestion from manifest changes and define ordering against in-flight queries. |
| Service account scope | The API and the ingestion Job share one service account and one IAM role, so the API holds S3 read access it does not need. | Separate service accounts and roles per workload. |
| Bedrock policy | The underlying Claude model is permitted in any region (`arn:aws:bedrock:*`) so the inference profile can route. | List the six APAC regions the profile routes to. |
| Network | Public subnets, no NAT gateway. | Private subnets with VPC endpoints for S3, ECR, STS and Bedrock. |
| Audit | Pod sessions appear in CloudTrail as `botocore-session-<epoch>`. | Set `AWS_ROLE_SESSION_NAME` from the pod name so every call is traceable to a workload. |
| Terraform state | Local file, one operator. | S3 backend with locking. |
| Live authorization checks | Not implemented. Every decision is made from the token and the filter. | When added for high-consequence documents, fail closed per chunk rather than per query. A query returning three sources is degraded. A query returning none is an outage. |
| Document version linkage | Chunks record the S3 key, not the object version. | Store the S3 version ID on each chunk. |
