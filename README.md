# production-rag-eks# production-rag-eks

Production grade RAG platform on AWS EKS with document level access control.

This is Part 2 of a series. Part 1 built a working RAG system on a laptop:
https://github.com/chrahul/aws-wellarchitected-rag

This repo answers the question that came right after. What has to change before
that system can serve a real organisation?

Status: in development. See the roadmap below.

## The problem

Part 1 worked. One user, public AWS documentation, FAISS on disk, everything in
one process. That was enough to prove the concept.

None of it survives contact with a real organisation.

Documents are not all public. Security postmortems, customer architecture
reviews, internal runbooks. Retrieval has to respect who is asking.

A retrieval bug becomes a security incident. If the wrong chunk reaches the LLM,
the answer contains data the user was never cleared to see.

Documents change. Update a runbook and naive ingestion leaves the old chunks in
place. Retrieval returns both versions and the model blends them into an answer
that is confidently wrong.

Nobody is watching. At five thousand queries a day you cannot eyeball answers.
Retrieval quality degrades silently and you find out from a complaint.

Tokens cost money forever. Embedding is a one time cost. Context is not.

## What this builds

An internal engineering knowledge platform. One RAG system, several teams, mixed
document sensitivity.

| Document class | Who can retrieve it |
|---|---|
| AWS Well-Architected whitepapers | Everyone |
| Platform runbooks and ADRs | Platform and SRE |
| Security incident postmortems | Security only |
| Customer architecture reviews | Named accounts only |

The same question asked by two people in different groups returns different
answers. The system never reveals that the restricted content exists.

## Architecture

```
User
  -> ALB with ACM TLS
     -> Authentik, OIDC with group claims in the JWT
        -> RAG API, FastAPI with HPA
           -> Permission filter, applied BEFORE similarity search
           -> Qdrant, StatefulSet on gp3 EBS with payload indexes
           -> Postgres and Redis, document registry and cache
           -> LiteLLM -> Bedrock, Claude and Titan embeddings

S3 -> EventBridge -> SQS -> Ingestion workers -> Qdrant
```

Two applications, deployed and scaled independently. The API serves queries. The
ingestion worker consumes documents. Same separation as Part 1 had between
ingest.py and chatbot.py, grown up.

## Key design decisions

### Bedrock instead of the OpenAI API

Part 1 argued that you cannot upload confidential documents to a public AI
service. Shipping security postmortems to a third party endpoint here would
contradict that argument.

With Bedrock the data stays inside the AWS account, every inference call is
audited in CloudTrail, and model access is scoped by IAM. LiteLLM sits in front
so models can be swapped without touching application code.

### Qdrant instead of FAISS

This is not mainly about scale. Qdrant applies metadata filters inside the
vector search. FAISS does not do this well.

### Pre-filter instead of post-filter

The obvious implementation retrieves the top four chunks and then removes the
ones the user lacks permission for. This fails in two ways.

A user with narrow access can have all four results filtered away. They get told
no information exists when plenty does.

Result counts and latency become a side channel. An attacker can infer whether
restricted documents matching a query exist, even without seeing them.

The filter has to be part of the search, not applied after it.

### IRSA for every AWS call

No static credentials in pods. Bedrock, S3, SQS and Secrets Manager access all
flow through IAM roles bound to Kubernetes service accounts.

## Repo layout

```
production-rag-eks/
  src/
    api/          FastAPI, auth and retrieval and generation
    ingestion/    SQS consumer, parse and chunk and embed
    common/       ACL model, Qdrant client, LLM client
  terraform/
    00-network/   VPC, subnets, endpoints
    10-eks/       cluster, node groups, Karpenter
    20-platform/  ALB controller, External Secrets, cert-manager
    30-data/      S3, SQS, Secrets Manager, IAM roles
  charts/         Helm charts per component
  evals/          golden question set and scoring harness
  docs/
  docker-compose.yml
```

Directories appear when the phase that needs them lands. No empty scaffolding.

## Roadmap

Each phase ends with something you can demonstrate.

| Phase | Scope | Done |
|---|---|---|
| 0 | ACL model and pre-filtered retrieval, local Qdrant | no |
| 1 | Terraform: VPC, EKS, Karpenter, ALB controller, ECR | no |
| 2 | Qdrant StatefulSet, Postgres, Redis on cluster | no |
| 3 | RAG API containerised, Bedrock via LiteLLM, IRSA | no |
| 4 | Authentik OIDC, JWT validation, group claims | no |
| 5 | ACL enforcement end to end, the Part 2 demo | no |
| 6 | Event driven ingestion, document versioning | no |
| 7 | Blue green reindex with zero downtime | no |
| 8 | Observability with OTel, Prometheus, Langfuse | no |
| 9 | Evaluation harness and cost controls | no |

Phases 0 to 5 become video Part 2. Phases 6 to 9 become Part 3.

## Running costs

An idle EKS cluster is not free. Rough monthly figures for this stack.

| Item | Approximate cost |
|---|---|
| EKS control plane | 73 USD |
| Worker nodes, two t3.large on spot | 25 USD |
| NAT gateway | 32 USD plus data |
| ALB | 16 USD |
| EBS gp3 for Qdrant | 5 USD |

Everything is behind terraform apply and terraform destroy with remote state, so
the cluster only exists while it is being worked on.

VPC endpoints for S3, ECR and Bedrock replace per AZ NAT gateways. That is the
single largest avoidable cost in this stack.

Local development runs against docker-compose. EKS is for integration testing
and recording, not for iteration.

## Prerequisites

An AWS account with Bedrock model access enabled for Claude and Titan embeddings.

Terraform 1.6 or newer, kubectl, helm, awscli v2.

Docker and Python 3.11 or newer.

A budget alert. Set it before the first terraform apply.

## Author

Rahul Chaubey, Cloud and AI Transformation Leader.
Ex-AWS, Ex-Microsoft, Ex-Oracle.

Part 1 repo: https://github.com/chrahul/aws-wellarchitected-rag

## Licence

MIT