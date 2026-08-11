# ADR-001: The vector store is a search index, not an authorization system

Status: Accepted
Date: 2026-08-07
Deciders: Rahul Chaubey
Informed by: a public discussion with Xin Xing, Enterprise Data Architect, who
identified the metadata drift problem in the original design.

## Context

This platform performs retrieval over a document set where not every document
is visible to every person. Retrieval must therefore respect who is asking.

The established starting point is that filtering has to happen inside the
vector search rather than after it. Retrieving candidates and then discarding
the ones a user cannot see is wrong in two ways. A user with narrow access can
have every result discarded and be told no information exists when plenty does.
And result counts and response times leak the existence of restricted material
to someone who never sees its contents.

That much was settled. What was not settled is how the search knows which
documents a person may see.

The obvious answer is to write permissions into the vector store as metadata at
ingestion time, then filter on that metadata at query time. This works on the
first day and degrades from then on.

Consider a document ingested today, tagged as belonging to the Security
department. Six months later the organisation reorganises. Security becomes
Cyber Defence. Three people leave the team, two join, one moves to another
business unit. The document is unchanged, but everything the vector store
believes about who may read it is now a photograph of a team that no longer
exists.

Two people leaving the team still retrieve the document. That is not a stale
cache. That is an authorisation failure.

The second-order problem is worse than the first. Fixing drift by continuously
synchronising permissions from source systems means the platform now maintains
its own entitlement model, with its own bugs, its own reconciliation logic, and
its own divergence from the systems that actually own those decisions. The
enterprise already has an identity and access management system. Building a
second, slightly wrong one inside the AI platform is not an improvement.

The opposite extreme is equally unworkable. Consulting the source systems on
every query means every retrieval depends on the identity provider, the
document management system, the entitlement service, and whatever else owns
part of the decision. Latency becomes the sum of those calls. Availability
becomes their product. A platform intended to serve thousands of users becomes
tightly coupled to systems it does not control.

Neither extreme is correct. The decision below is where the line sits.

## Decision

### Principle

The vector store is an optimised search index. It is not the source of truth
for identity, authorisation, or document governance. Those remain with the
enterprise systems that already own them.

Every decision below follows from that principle.

### 1. Documents store properties, not access lists

The vector store records what a document is, never who may read it.

Stored: security classification, document type, owning team identifier,
customer or project identifier, region, data residency requirement, regulatory
tags, lifecycle state, version.

Not stored: user identifiers, group membership, resolved access control lists,
or any other answer to the question of who may read this.

A resolved access list is stale the moment a person changes teams. A
classification label is a property of the document and changes rarely, and when
it does change that is a document event the platform can react to.

### 2. Owning teams are stable identifiers, never names

A document records the identifier of the team that owns it, not the team's
display name.

Team names change during reorganisations. Identifiers do not. When Security
becomes Cyber Defence, the document metadata is untouched, and the identity
provider reports current membership of that team identifier at query time.

This removes the single largest source of drift without any synchronisation.

### 3. User attributes come from the token and are never stored

Clearance level, department, region, project assignments, customer accounts and
group membership arrive in the token issued by the enterprise identity
provider. They are read at query time and discarded.

This side of the model cannot drift. The identity provider evaluates these
attributes on every authentication and token refresh. A person who leaves a
team carries different claims on their next token, with no action required by
this platform.

The asymmetry matters. Only the document side of the model can go stale, which
narrows the problem considerably.

### 4. Retrieval strategy is selected by volatility and consequence

Two questions determine how much the platform trusts its own metadata.

Volatility is how likely the document's authorisation is to have changed since
ingestion. Consequence is how much damage a stale answer causes.

They are different questions and both are required. A published reference
architecture that is reclassified frequently is high volatility and low
consequence, because nobody is harmed by a stale answer. A restricted incident
report unchanged for two years is low volatility and severe consequence, and it
is precisely the document where being wrong is unacceptable.

The rule:

| Volatility | Consequence | Strategy |
|---|---|---|
| Low | Low | Prefilter on stored attributes only |
| Low | High | Prefilter, then live authorisation check |
| High | Low | Prefilter, refresh metadata on event |
| High | High | Prefilter, then live authorisation check |

The live check runs only on the small number of chunks that survived the
filter, immediately before they enter the prompt. Making forty thousand
authorisation calls at ingestion is impossible. Making four at query time is
trivial.

This bounds coupling to the cases where being wrong is expensive, and leaves
the common path fast.

### 5. Volatility is declared now, measured later

Volatility cannot be inferred at ingestion. It is an observation about a
population of documents over time, and at ingestion there is no history.

For now, volatility is declared as policy per document class. Customer
documents and incident reports are high volatility. Published standards and
reference architecture are low. This is a human judgement made once per class,
not per document.

Once the platform has enough operational history, authorisation change events
per document class can drive the tier automatically. That evolution requires
the event pipeline, which does not yet exist. Declared volatility is the
starting point and measured volatility is the intended destination.

## Alternatives considered

### Copy resolved access lists into the vector store

Rejected. This is the design that motivated this record. It works on day one
and drifts continuously afterwards, and correcting the drift requires building
a second entitlement system inside the platform.

### Query source systems live on every retrieval

Rejected. Correct in principle and unusable in practice. Every query becomes
dependent on several enterprise systems the platform does not control. Latency
is the sum of their response times. Availability is the product of their uptime.

### Post-filter after retrieval

Rejected before this record was written, and worth restating. Retrieving top-k
and then discarding unauthorised results returns fewer than k results to users
with narrow access, and leaks the existence of restricted documents through
result counts and timing.

### Separate vector collection per team or classification

Rejected. It makes cross-cutting documents impossible to represent without
duplication, multiplies operational surface, and moves the drift problem into
collection membership instead of removing it.

## Consequences

### What this gives us

The platform enforces enterprise authorisation without owning it. The identity
provider remains authoritative for people. The document management system
remains authoritative for documents. This platform is a consumer of both.

Reorganisations do not require reindexing, because team identifiers survive
renames.

The identity provider is pluggable by construction. Entra, Okta, Cognito,
Keycloak and Ping all issue tokens carrying claims. Nothing in the retrieval
path is specific to any of them.

Query latency for the common path is a single vector search. No external
authorisation calls in the majority of requests.

### What this costs us

A bounded window of staleness on documents in the low volatility, low
consequence quadrant. This is accepted deliberately. Those documents are, by
definition, ones where a stale answer causes little harm.

Document attribute changes require an event pipeline to propagate. Until that
exists, reclassification means reingestion of the affected document.

The volatility classification is a human judgement and can be wrong. A document
class mistakenly marked low volatility will silently serve stale authorisation.
This is the primary residual risk of the decision.

Live authorisation checks introduce a dependency on source systems for the high
consequence path. That dependency needs a defined failure behaviour, which is
open below.

## Open questions

What happens when the live authorisation check fails or times out. Failing
closed is correct for security and creates an availability dependency. Failing
open is unacceptable for restricted documents. The likely answer is fail closed
with a short cache, but this needs to be decided before the live check is
implemented.

Whether volatility should be declared per document class or per source system.
Class is simpler. Source system may be more accurate, since a document
management system with frequent reorganisation churn affects everything within
it.

How reclassification events reach the platform, and what ordering guarantees
are required when a document is reclassified while queries are in flight.

These are deferred to the phase where event driven ingestion is built.

## Notes

This record exists because a public design discussion identified a flaw that
would otherwise have been built. Recording it is more useful than presenting a
clean design that appears to have arrived fully formed.
