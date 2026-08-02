# Architecture and plan

This document explains what this platform is, why each piece of it exists, and
the order in which it gets built. If you are starting here, read this before
the code.

## What we are building

An internal engineering knowledge platform. One RAG system serving several
teams, over a document set where not every document is visible to every person.

Part 1 of this series built a RAG system on a laptop. Five public AWS
whitepapers, one user, FAISS on disk, everything inside a single Python
process. It worked, and it was honest about what it was. A proof of concept.

The gap between that and something an organisation can actually run is not a
Dockerfile. It is a different class of problem. Part 1 was an ML problem. This
is a distributed systems and access control problem.

## The problem that drives every design decision

Put confidential documents into a RAG system and retrieval stops being a
quality concern and becomes a security boundary.

If the wrong chunk reaches the language model, the answer contains information
the person asking was never cleared to see. There is no second line of defence.
The model will faithfully summarise whatever you hand it.

So the central question of this build is simple to state and not simple to
solve. How do you make sure that a search over a shared vector store only ever
considers the documents the person asking is permitted to see.

## The document model

Four classes of content, deliberately chosen because they exercise different
access patterns.

| Document class | Who can retrieve it |
|---|---|
| AWS Well-Architected whitepapers | Everyone |
| Platform runbooks and ADRs | Platform and SRE |
| Security incident postmortems | Security only |
| Customer architecture reviews | Named accounts only |

The whitepapers are open to all. The runbooks are shared across two teams,
which rules out any model where a document has a single owner. The postmortems
are restricted to one team. The customer reviews are not scoped by team at all,
they are scoped by which account an engineer works on.

The final demo is two people asking the same question and getting different
answers, with no indication to either of them that the other exists.

## Architecture

![Architecture](docs/architecture.svg)

There are two flows through this system and they are worth separating in your
head, because they run at different times, scale differently, and fail
differently.

### The query path

A request arrives at the load balancer, which terminates TLS. It passes through
Authentik, which establishes who the person is and which groups they belong to,
and issues a token carrying those groups.

The RAG API validates that token and reads the group list. Those groups are
turned into a search condition, and that condition is passed into Qdrant along
with the query vector.

This is the part that matters most. The filter is an argument to the search,
not a step that runs afterwards. The reason is covered below.

Qdrant returns four chunks, all of which the person is permitted to see. Those
chunks go into a prompt, the prompt goes through LiteLLM to Bedrock, and the
answer comes back.

### The ingestion path

Documents land in S3. The prefix they land under carries their access rule, so
a file placed under a security prefix is only ever visible to that group.

S3 raises an event, EventBridge routes it, SQS queues it. Ingestion workers
pick up the job, parse the document, split it into chunks, generate embeddings,
and write points into Qdrant with the group list and a version hash attached.

This is a separate application from the API, with its own deployment and its
own scaling behaviour. A bulk import of two thousand documents should not
affect query latency for people asking questions at the same time.

## Why each component

### Qdrant rather than FAISS

The usual reason given is scale. That is not the reason here.

Qdrant can apply a filter on document metadata inside the vector search itself.
FAISS cannot do this well. Everything about the access control design depends
on that capability, so the choice of vector store is not an implementation
detail, it is load bearing.

### Filtering before the search, not after

The obvious implementation is to retrieve the top four chunks and then remove
the ones the person is not allowed to see. It is wrong in two ways.

First, correctness. Someone with narrow access can have all four results
removed and be told no information exists, when in fact the system holds plenty
of relevant material they are cleared to read. Their experience of the product
silently degrades based on other people's permissions.

Second, disclosure. Result counts and response times become a signal. Ask a
question about an incident you are not cleared to know about, and the shape of
the response tells you whether documents matching it exist. You do not need to
read a document to learn something from it.

Filtering inside the search removes both problems. Ask for four results, get
four results, drawn only from the permitted set.

### Bedrock rather than the OpenAI API

Part 1 made an argument on camera. You cannot upload confidential company
documents to a public AI service, because of security policy, compliance, and
intellectual property.

If this build then sends security incident postmortems to a third party
endpoint, it contradicts its own argument. With Bedrock the data stays inside
the AWS account, every inference call is recorded in CloudTrail, and access to
each model is controlled through IAM.

### LiteLLM in front of Bedrock

An organisation does not commit to one model forever. Costs change, better
models ship, some workloads need a cheaper model than others.

LiteLLM gives one interface and one place to change the decision. Swapping the
model becomes a config change rather than a code change, and it is also where
per team spending limits are enforced.

### Authentik for identity

The application does not care which identity provider issued the token. It
cares that the token is valid and that it carries a list of groups.

Authentik is used because it makes the mapping from group membership to
retrieval permission visible and explainable. Cognito would work in exactly the
same way. The application code would not change.

### IRSA for AWS access

No static credentials anywhere in the cluster. Pods reach Bedrock, S3, SQS and
Secrets Manager through IAM roles bound to their Kubernetes service account.

This is the difference between Kubernetes that happens to run on AWS and a
system that is genuinely built for it.

### Terraform for everything

The cluster is described in code and can be destroyed and rebuilt on demand.
This matters practically as well as architecturally. An idle EKS cluster costs
money every hour it exists, and this one only exists while it is being worked
on.

## Two environments, one codebase

This is the part people get wrong, so it is worth being explicit.

There are two places this system runs and they exist for different reasons.

Locally, under docker compose, is the development loop. Change a line, save,
see the result immediately. Attach a debugger. Inspect the Qdrant dashboard
directly. Delete everything and start again in thirty seconds. Costs nothing.

On EKS is the deployment target. Real load balancer, real identity provider,
real IAM, real scaling. This is where the system becomes what Part 1 promised.

| Component | Local | EKS | Why |
|---|---|---|---|
| Qdrant | yes | yes | Same image. StatefulSet with EBS on the cluster. |
| Postgres and Redis | yes | yes | Same. |
| RAG API | Python process | container | Runs as a process locally so reloads are instant. |
| Ingestion worker | Python process | container | Same. |
| Bedrock | real calls | real calls | Works from a laptop with AWS credentials. No mock. |
| Authentik | no | yes | Test tokens locally. See below. |
| ALB, IRSA, Karpenter | no | yes | These have no local equivalent, which is the point. |

The rule that makes this work is that application code never knows which
environment it is in. It reads a Qdrant URL from config. Locally that is
localhost. On the cluster it is a service name. Same code, same image.

### Why Authentik is not in the local stack

Running a full identity provider would slow the development loop for very
little gain, because what the application actually consumes is a token with a
groups claim.

Locally, those tokens are generated by a small script. A token claiming
membership of the security group is indistinguishable, to the API, from one
Authentik issued. The entire authorisation path is developed and tested against
them.

Authentik arrives in Phase 4 and issues real tokens. If the boundary was drawn
correctly, no application code changes. That swap working first time is the
proof that the design was right.

## Build order

Each phase ends with something that can be demonstrated. Nothing is built ahead
of the phase that needs it.

| Phase | What gets built | What proves it works |
|---|---|---|
| 0 | Access control model, pre-filtered retrieval, local Qdrant | Two fake tokens, same question, different chunks |
| 1 | Terraform for VPC, EKS, Karpenter, ALB controller, ECR | A hello world pod reachable over HTTPS |
| 2 | Qdrant, Postgres, Redis on the cluster | Qdrant survives a pod delete with data intact |
| 3 | API containerised, Bedrock through LiteLLM, IRSA | Part 1 questions answered from the cluster |
| 4 | Authentik, OIDC, real token validation | Test tokens replaced, nothing else changes |
| 5 | Access control enforced end to end | The two user demo, on real infrastructure |
| 6 | Event driven ingestion, document versioning | Updating a document replaces its chunks |
| 7 | Blue green reindex | Chunk size changed with no downtime |
| 8 | Observability | Retrieval quality regression caught on a dashboard |
| 9 | Evaluation harness and cost controls | Scored answers, spend capped per team |

Phases 0 to 5 are the second video. Phases 6 to 9 are the third.

### Why this order

Phase 0 solves the hardest design problem in the entire series before any
infrastructure exists to complicate it. It costs nothing and runs on a laptop
with a debugger attached.

If everything were built at once and the wrong chunk came back, the cause could
be the filter logic, the payload index, the IAM role, service discovery, or the
token claims. Five candidates and no way to isolate them.

Building in sequence means that when something breaks in Phase 3, the access
control logic is already known to work, because it was proved in Phase 0.

## Running costs

An idle cluster is not free.

| Item | Approximate monthly cost |
|---|---|
| EKS control plane | 73 USD |
| Two t3.large worker nodes on spot | 25 USD |
| NAT gateway | 32 USD plus data transfer |
| ALB | 16 USD |
| EBS gp3 for Qdrant | 5 USD |

The NAT gateway is the largest avoidable cost. VPC endpoints for S3, ECR and
Bedrock remove most of the need for one.

Development happens locally. The cluster is created for integration testing and
recording, then destroyed. Set a budget alert before the first apply.

## Where to go next

Part 1, the laptop version, is here:
https://github.com/chrahul/aws-wellarchitected-rag

Start with Phase 0. It requires nothing but Docker and Python.