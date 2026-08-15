# Phase 0: the authorization lab

Two users. Same question. Different answers.

This runs on a laptop. Docker and Python, nothing else. No Kubernetes, no
cluster, and the only AWS call is for embeddings.

It exists to prove one thing before any infrastructure is built around it:
that a search over a shared vector store can be made to respect who is asking,
and that the obvious way of doing it is wrong.

## Why this is separate from the rest of the platform

The hardest problem in this project is not deployment. It is deciding where
authorization happens in a retrieval pipeline, and getting that decision wrong
is a security failure rather than a bug.

Solving it on a cluster means debugging the authorization model, IAM roles,
service discovery and TLS at the same time. Solving it here means debugging one
thing, with a debugger attached, for free.

The lab stays in the repository permanently. It is the local development loop
for anyone working on the retrieval logic, and it is the fastest way for someone
new to understand the design without an AWS account.

The decision it implements is recorded in
[ADR-001](../docs/adr/001-vector-store-is-not-an-authorization-system.md).

## What is in the corpus

Six synthetic documents with mixed sensitivity. They are fictional.

| Document | Classification | Owning team | Customer |
|---|---|---|---|
| AWS Well-Architected summary | public | team-architecture | |
| SRE incident runbook | public | team-sre | |
| Platform runbook | internal | team-platform | |
| Security architecture standard | confidential | team-security | |
| Customer architecture review | confidential | team-architecture | cust-apollo |
| Security incident postmortem | restricted | team-security | |

## The three users

| User | Clearance | Teams | Customers |
|---|---|---|---|
| Sam | public | | |
| Arjun | confidential | team-platform, team-sre | cust-apollo |
| Priya | restricted | team-security | |

Sam is a contractor. Arjun is a platform engineer on the Apollo account. Priya
is a security engineer.

Priya holds the highest clearance in the organisation and cannot read the Apollo
review. Arjun can. Clearance alone is not the model.

## The rule

```
public        anyone
internal      clearance >= internal
confidential  clearance >= confidential AND (owning team OR entitled customer)
restricted    clearance >= restricted AND owning team
```

Internal is company wide. Need to know applies at confidential and above.

## Running it

Requires Docker, Python 3.9 or newer, and AWS credentials with permission to
call Bedrock for Titan embeddings.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d
curl http://localhost:6333/healthz

export AWS_REGION=ap-south-1
python -m lab.ingest
python -m lab.demo
```

Ingestion runs once and takes about twenty seconds. It makes one Bedrock call
per chunk. The demo can then be run as often as you like.

## What the demo shows

Three acts.

The first prints who can read what.

The second asks all three users the same question and shows what each one gets
back. Every user asks for four results and receives four. The answers differ in
substance rather than in count.

The third asks the same question again through a deliberately naive
implementation, which retrieves first and filters afterwards.

That third act is the point of the lab.

## What the naive implementation gets wrong

It does not leak anything. Every authorization verdict it reaches is correct,
because it applies the same rule as the correct implementation.

It fails at the system level, in two ways.

A user asks for four results and receives however many survived filtering. Sam
receives two. His answer is worse because of documents belonging to other
people. With a narrower corpus he would receive nothing at all, and be told no
information exists while the system holds material he is cleared to read.

And the number discarded is observable. It is a fact about documents the user
cannot see. Ask about an incident you are not cleared to know about, and a non
zero discard count tells you that incident exists. You do not need to read a
document to learn something from it.

A code review of the naive version finds nothing wrong. The bug is not in a
line of code, it is in the order of two steps.

## The files

```
lab/
  authorization.py   the rules. document attributes, user context, filter builder
  users.py           three hardcoded users, replaced by real tokens later
  embeddings.py      Bedrock Titan client
  ingest.py          read, chunk, embed, write to Qdrant. runs once
  retrieval.py       the two search implementations
  demo.py            prints the three acts
```

`authorization.py` is the file worth reading. It contains no networking and no
model calls, only the rules. Note what `DocumentAttributes` does not have. There
is no user list and no group list. A resolved list of people goes stale the
moment someone changes teams. A classification is a property of the document.

`users.py` is deleted in a later phase and replaced by JWT parsing. If nothing
else changes when that happens, the boundary was drawn in the right place.

## What this lab does not do

There is no language model in the loop. The demo returns retrieved chunks, not
generated answers. That is deliberate. The claim being proved is about
retrieval, and generation would only obscure it.

Document attributes are declared in `ingest.py` rather than derived from an
object store prefix or a sidecar manifest. The ingestion pipeline is a later
phase.

Users are Python objects rather than signed tokens.

Everything runs in one process against one container. Nothing here is
production shaped, and it is not meant to be.

## Where this goes next

The same authorization model, unchanged, moves onto EKS. The application becomes
an HTTP service, the users become signed tokens, Qdrant becomes a StatefulSet on
persistent storage, and Claude turns the retrieved chunks into an answer.

The retrieval logic does not change. That is the test of whether Phase 0 was
built correctly.
