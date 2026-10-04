# I built the answer to "how do you stop your vector database becoming a second IAM system?" Here is the proof.

In my last article I wrote that I thought permission-aware RAG was the hard problem, and I was wrong. The hard problem was where permissions live. Xin Xing asked the question that changed my design: how do you stop your vector database from becoming a second, gradually wrong IAM system?

I landed on a principle and promised to build it before committing to it in writing. I have now built it on Amazon EKS. This article walks through the proof: the commands I ran, the output they produced, and what each one shows about the problem.

Everything below ran on a real cluster in ap-south-1. The code, the Kubernetes manifests and a full step-by-step tutorial are here: https://github.com/chrahul/production-rag-eks

Previous article: https://rahulch-unix.medium.com/i-thought-permission-aware-rag-was-the-hard-problem-i-was-wrong-5a009854a4fa

---

## The problem in one paragraph

Put confidential documents into a retrieval system and retrieval stops being a quality concern. It becomes a security boundary. A user asks a routine question. Semantic search picks the four most relevant chunks. One of them comes from a document that user is not cleared to read. The model reads all four and writes one fluent answer. You cannot instruct a model to keep a secret it has already been shown, and there is nothing between the model and the user to filter.

Traditional access control works because users ask for a specific thing. Open document 4471, the system checks your badge. AI search has no such moment. The user never names a document. The system decides what is relevant.

> In a normal system, access control decides what you are allowed to open. In an AI system, it has to decide what the search is allowed to look at. Get that wrong and there is nothing downstream to catch it.

## The principle

> The vector database is an optimised search index. It is not the source of truth for identity, authorization, or document governance.

Three consequences follow, and the rest of this article tests each one:

1. Documents store what they are, never who can read them. Classification, owning team, customer, region. No user lists, no group lists.
2. User attributes come from the signed token at query time and are never stored. Clearance, teams, customers.
3. The authorization filter is built from the token and applied inside the vector search, not after it.

The rule itself:

```
public        anyone
internal      clearance >= internal
confidential  clearance >= confidential AND (owning team OR entitled customer)
restricted    clearance >= restricted AND owning team
```

Internal is company-wide. Need-to-know applies at confidential and above.

## The test setup

Six synthetic documents and three fictional people.

The documents that matter most:

- **Customer Architecture Review, Project Apollo.** Confidential. Owned by team-architecture. Customer: Apollo.
- **Internal Security Architecture Standard.** Confidential. Owned by team-security.
- **Security Incident Postmortem.** Restricted. Owned by team-security.
- Plus two public documents and one internal runbook.

The people:

- **Sam.** Public clearance. No team, no customer.
- **Arjun.** Confidential clearance. Platform and SRE teams. Entitled to the Apollo customer.
- **Priya.** Restricted clearance, the highest in the organisation. Security team. Not on the Apollo account.

Everyone asks the same question: *"What is our approach to disaster recovery testing?"*

## What I built

- **Amazon EKS** cluster, two small nodes, created with Terraform (63 AWS resources).
- **S3** holds the documents and a `manifest.yaml` that classifies them. Documents are data, not code. A document missing from the manifest is skipped, not given a default.
- **An ingestion Job** reads the manifest and documents, chunks them, embeds each chunk with **Amazon Titan**, and writes them to **Qdrant** with their attributes.
- **Qdrant** is the search index. I chose it over FAISS for one reason: it applies metadata filters inside the vector search.
- **A FastAPI service** verifies the user's token, builds the filter, searches Qdrant, and sends only permitted chunks to **Claude on Amazon Bedrock**.
- **Claude runs through the APAC regional inference profile**, not a global one. In Part 1 I argued that confidential documents cannot go to a public AI service. A global profile can route a request anywhere in the world, which would undo that argument. The cost is an older model. I accepted that.
- **IRSA** gives pods temporary AWS credentials from their Kubernetes identity. There are no access keys anywhere in the cluster.

Tokens are signed by a small script that stands in for Entra, Okta or Keycloak. The service only verifies a signature and reads claims, which is exactly what it would do against a real identity provider.

Now the proof.

---

## Proof 1: the danger is real in this data

Before showing the fix, I wanted to see the problem in my own index. This runs a search with **no filter at all**, which is what a RAG system without access control sends to the model.

```bash
kubectl -n rag exec deploy/rag-api -- python -c "from src.common.retrieval import client, COLLECTION; from src.common.embeddings import embed; [print(f'{p.score:.3f}  {p.payload[\"classification\"]:<13} {p.payload[\"doc_title\"]}') for p in client().query_points(COLLECTION, query=embed('What is our approach to disaster recovery testing?'), limit=4, with_payload=True).points]"
```

The command runs a short Python snippet inside the API pod, using the project's own code. It embeds the question with Titan and asks Qdrant for the four most similar chunks.

```
0.421  public        AWS Well-Architected Summary, Reliability and Operations
0.369  confidential  Customer Architecture Review, Project Apollo
0.327  confidential  Customer Architecture Review, Project Apollo
0.305  public        AWS Well-Architected Summary, Reliability and Operations
```

Two of the four most relevant chunks are confidential. Without a filter they go into the prompt for everyone, including Sam, who has public clearance.

Nothing went wrong technically. The search did its job, which is to find the most relevant text. **Relevance is not permission.**

## Proof 2: the index holds no people

This fetches one stored Apollo chunk from Qdrant and prints everything stored with it.

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

Look at what is missing. No `allowed_users`. No groups. No access list. Nothing here names a person.

This is the direct answer to Xin's question. The vector store cannot become a second IAM system, because it holds no identity data that could drift. When someone leaves the Apollo account, this chunk does not change. It never knew about them.

A note on `team-architecture`: these are readable identifiers for the demo. In production they would be the identity provider's opaque group IDs, so a team rename never touches the index.

## Proof 3: the user comes from the token

```bash
curl -s localhost:8080/whoami -H "Authorization: Bearer $TOKEN_ARJUN"
```

This presents Arjun's signed token to the API and asks what the platform knows about him.

```json
{"subject":"arjun","name":"Arjun Mehta","clearance":"confidential","teams":["team-platform","team-sre"],"customers":["cust-apollo"],"source":"the token you presented, nothing was looked up"}
```

His clearance, teams and customer come from the token. The API verified the signature and read the claims. It did not query a directory, a database or the vector store.

The token is also what stops Sam editing his own claims. A JWT is signed. Change one character of the payload and the signature no longer matches, so the request is rejected before any authorization logic runs. In testing, a forged token returned `401 Unauthorized`.

## Proof 4: what each person's filter lets the search see

This builds the filter for each of the three users with the project's own `build_filter()` and asks Qdrant which chunks pass.

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

Each confidential document is visible to exactly one of these people, and a different one in each case. Arjun sees Apollo through his customer entitlement, not his team. Priya sees the security standard through her team.

Clearance decides how high you can reach. Team and customer decide which documents at that level are yours.

This list is the search's whole world for that user. Anything not on it can never be ranked, returned or sent to the model.

## Proof 5: same question, three people, three answers

This is the product endpoint. It verifies the token, builds the filter, embeds the question, searches Qdrant with the filter inside the search, sends the surviving chunks to Claude, and returns the answer with its sources.

```bash
curl -s localhost:8080/ask -H "Authorization: Bearer $TOKEN_SAM" -H "Content-Type: application/json" \
  -d '{"question":"What is our approach to disaster recovery testing?"}' | python -m json.tool
```

**Sam, public clearance.** Sources, abbreviated:

```
0.4206  public  AWS Well-Architected Summary
0.3053  public  AWS Well-Architected Summary
0.2908  public  AWS Well-Architected Summary
0.1951  public  SRE Runbook, Production API Latency
```

Answer, opening line: *"From the provided context, I cannot give a complete answer about our specific approach to disaster recovery testing."*

The two Apollo chunks that ranked second and third in Proof 1 are gone. They were not removed after the search. They were never candidates. Sam still received four results, the next best ones he is allowed to see. And the model said what was missing instead of inventing an answer.

**Arjun, confidential clearance, Apollo customer.** Same command with `$TOKEN_ARJUN`:

```
0.4206  public        AWS Well-Architected Summary
0.3688  confidential  Customer Architecture Review, Project Apollo
0.3274  confidential  Customer Architecture Review, Project Apollo
0.3053  public        AWS Well-Architected Summary
```

Answer: *"Disaster recovery designs should be validated through controlled exercises before production migration, rather than relying only on backups."*

This line comes straight from the Apollo review. Sam's answer could not contain it, because Sam's search never touched that document.

**Priya, restricted clearance, the highest in the organisation.** Same command with `$TOKEN_PRIYA`:

```
0.4206  public    AWS Well-Architected Summary
0.3053  public    AWS Well-Architected Summary
0.2908  public    AWS Well-Architected Summary
0.2095  internal  Platform Runbook, Kubernetes Node Replacement
```

No Apollo review. Priya outranks Arjun, and she does not get the document he gets, because she is neither on the owning team nor entitled to the customer. Her own confidential and restricted documents were eligible, but they are not about disaster recovery, so they ranked lower. The filter decides what the search may look at. Similarity decides what is best among those.

> Clearance alone is not the access model.

Each request took about four seconds, almost all of it the two Bedrock calls. The filter and search take milliseconds.

## Proof 6: the naive version is wrong even though it leaks nothing

The implementation most teams write first searches everything, takes the top four, and then removes what the user may not see. I kept it in the service as a comparison endpoint.

```bash
curl -s localhost:8080/compare -H "Authorization: Bearer $TOKEN_SAM" -H "Content-Type: application/json" \
  -d '{"question":"What is our approach to disaster recovery testing?"}' \
  | python -c "import json,sys; d=json.load(sys.stdin); [print(f\"{k:<13} searched {d[k]['searched']:>2}  returned {d[k]['returned']}  discarded {d[k]['discarded']}\") for k in ('prefiltered','postfiltered')]"
```

```
prefiltered   searched  5  returned 4  discarded 0
postfiltered  searched 17  returned 2  discarded 2
```

The post-filter path searched all 17 chunks, took the same top four as Proof 1, checked each one, and dropped the two Apollo chunks. Every verdict was correct. No restricted text reached the model. A code review of this version finds nothing wrong.

It is still wrong, in three ways:

1. **Sam got a worse answer because of other people's documents.** He asked for four and received two, while three more chunks he is allowed to read existed. With a narrower corpus he would receive nothing and be told no information exists.
2. **`discarded 2` is a fact about documents Sam cannot see.** It tells him something relevant to his question exists that he is not allowed to read. You do not need to read a document to learn from it.
3. **Restricted data entered the application.** The Apollo chunks were loaded into the API's memory for Sam's request before being dropped. One log line or one exception message, and they are exposed. With pre-filtering they never leave the database.

> The bug is not in a line of code. It is in the order of two steps.

## Proof 7: Xin's question, answered with a live change

The real test of "second, gradually wrong IAM system" is what happens when something changes.

**First, a person changes.** Suppose Priya joins the Apollo account. Her identity provider would issue her a new token with the new entitlement. I minted that token and touched nothing on the cluster.

```bash
export TOKEN_PRIYA_APOLLO=$(python -c "from src.api.tokens import issue; print(issue('priya', 'Priya Nair', 'restricted', ['team-security'], ['cust-apollo']))")
```

Asking the same question with it:

```
eligible 17 of 17
  0.421  public        AWS Well-Architected Summary
  0.369  confidential  Customer Architecture Review, Project Apollo
  0.327  confidential  Customer Architecture Review, Project Apollo
  0.305  public        AWS Well-Architected Summary
```

Her eligible chunks went from 14 to 17 and the Apollo review appeared on the next request. No sync job. No re-indexing. Nothing in Qdrant changed.

**Second, a document changes.** I reclassified the Apollo review from confidential to internal in the manifest, which makes it company-wide. Priya's original token, without the Apollo entitlement, should now see it.

```bash
sed '/04_customer_architecture_review/,/customer:/ s/classification: confidential/classification: internal/' manifest.yaml \
  | aws s3 cp - s3://<documents-bucket>/manifest.yaml
```

Before re-running ingestion, Priya asked again:

```
eligible 14 of 17
  0.421  public        AWS Well-Architected Summary
  0.305  public        AWS Well-Architected Summary
  0.291  public        AWS Well-Architected Summary
  0.209  internal      Platform Runbook, Kubernetes Node Replacement
```

Nothing changed. The source of truth said internal. The index still said confidential. That is a stale index, visible on screen.

Then I re-ran the ingestion Job:

```
    3 chunks   internal / cust-apollo              documents/04_customer_architecture_review.md
17 chunks indexed
```

And Priya asked once more:

```
eligible 17 of 17
  0.421  public        AWS Well-Architected Summary
  0.369  internal      Customer Architecture Review, Project Apollo
  0.327  internal      Customer Architecture Review, Project Apollo
  0.305  public        AWS Well-Architected Summary
```

So, side by side:

- **A person's access changed** in the token. Nothing on the platform had to happen. It took effect on the next request.
- **A document's classification changed** in the manifest. Re-ingestion had to happen. It took effect only after re-indexing.

People's access is never stored in the vector database, so it cannot drift. Document attributes are stored and can lag, but only by one known, visible, repeatable step, driven by a reviewed file rather than a sync job copying the company's entitlements.

The stale window has a dangerous direction. A document made less restricted shows up late, which is harmless. A document made more restricted stays visible to too many people until ingestion runs. That is why "how reclassification reaches the platform" is still an open question in my ADR.

The identity side has a limit too. A token issued before an access change keeps working until it expires, one hour in this build. Token lifetime is the real staleness bound on the user side.

## Proof 8: no keys in the cluster

Every AWS call the pods make, S3 reads and Bedrock calls, uses IRSA. This lists the AWS settings inside the running API pod.

```bash
kubectl -n rag exec deploy/rag-api -- env | grep '^AWS_'
```

```
AWS_REGION=ap-south-1
AWS_STS_REGIONAL_ENDPOINTS=regional
AWS_ROLE_ARN=arn:aws:iam::<account-id>:role/production-rag-rag-api
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
```

There is no access key and no secret key. The pod holds a role name and a token file. The token only proves "I am service account rag-api in namespace rag of this cluster". STS exchanges it for temporary credentials, and only for the one role whose trust policy names that exact service account.

```bash
kubectl -n rag exec deploy/rag-api -- python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
```

```
arn:aws:sts::<account-id>:assumed-role/production-rag-rag-api/botocore-session-1791092141
```

A temporary session, not a user. Nothing to rotate, nothing to leak.

## Proof 9: the index survives losing its database pod

```bash
kubectl -n rag delete pod qdrant-0
kubectl -n rag get pods -l app=qdrant -w
```

```
qdrant-0   1/1     Running   0          26s
```

A new pod with the same name, 26 seconds old, reattached to the same encrypted EBS volume.

```bash
kubectl -n rag exec deploy/rag-api -- python -c "from src.common.retrieval import client, COLLECTION; print('points in', COLLECTION, ':', client().count(COLLECTION, exact=True).count)"
```

```
points in enterprise_docs : 17
```

All 17 points survived. No re-ingestion. That is what makes it a database rather than a container.

## The lab and the cluster agree

Before any infrastructure existed, I proved the model in a local lab on my laptop: Docker, Qdrant and Python. On the cluster, with the same authorization and retrieval code, the numbers matched:

- 17 chunks in both.
- Arjun's Apollo chunks at 0.369 and 0.327 in both.
- Priya's fourth source, the Platform Runbook, at 0.209 in both.

The code that makes the authorization decision did not change between the laptop and EKS. That was the test of whether the lab was built correctly.

---

## What the build revealed

Building it found things that reading about it would not have.

**Fixed during the build:**

- **Windows line endings broke chunking.** Documents uploaded from my Windows laptop produced 6 chunks instead of 17, because paragraph splitting looked for `\n\n` and the files contained `\r\n\r\n`. Ingestion now normalises line endings, so it does not depend on who uploads.
- **Half a second of clock difference rejected every token.** My laptop's clock was half a second ahead of the cluster's. The verifier had zero tolerance, so freshly minted tokens looked like they were issued in the future. It now allows 30 seconds of leeway, as production verifiers do.

**Found while reviewing the finished build, being fixed next:**

- **The `/ask` response returns the total chunk count next to the user's eligible count.** "5 of 17" tells Sam that twelve chunks exist he cannot see. It is weaker than the post-filter leak, because it describes the whole corpus rather than his question, but it contradicts my own argument about counts. It belongs in server logs, not in the user's response.
- **The comparison endpoint ships in the running service.** It exists for the demo. Any user calling it loads restricted chunks into memory, which is exactly the third problem with post-filtering. It should exist only behind a demo flag.
- **The Bedrock permission allows the underlying model in any region**, so the APAC inference profile can route. That also lets the role call the model directly outside APAC. It should name the six APAC regions the profile actually uses.

Publishing these is the point. Each one is the kind of thing that passes a demo and fails a review.

## What is still open

The build answers the question I started with. It does not answer everything I published as open.

- **The middle approach is not built.** Pre-filter on document attributes, then a live check against the source system for high-consequence chunks. When that check times out, fail closed costs availability and fail open defeats the control.
- **Volatility and consequence are not yet a policy.** Today every document class takes the same path.
- **There is no durable audit trail of which documents shaped which answer.** Each request is logged with the user and counts, but not the sources, and the logs live only as long as the pod.
- **Reclassification is triggered by hand.** Ingestion should run when the manifest changes, and the ordering against in-flight queries needs defining.

Xin's follow-up post added three ideas that belong in the next decision record:

- **Fail closed at the chunk level, not the query level.** Drop what you cannot authorise and answer from the rest. Three sources is a degraded answer. Zero is an outage.
- **Consequence decides how certain you must be. Volatility decides how fresh the check must be.** They drive different mechanisms.
- **Context is the third input.** A contractor on an unmanaged device is a different risk against the same document than an employee on a managed one. That context should arrive in the token, computed by the identity provider. If the platform evaluated device posture itself, it would be rebuilding conditional access badly.

That is ADR-002. The principle from the last article now runs on real infrastructure. The next step is making it hold up when the systems it depends on do not.

The repository, including every command above with its full output, is here: https://github.com/chrahul/production-rag-eks
