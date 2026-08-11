# Phase 0 — Synthetic Enterprise RAG Corpus

This corpus is intentionally synthetic and created for the Enterprise AI Platform Phase 0 authorization lab.

The documents represent different sensitivity and ownership scenarios:

| Document | Classification | Department | Customer |
|---|---|---|---|
| Public AWS Well-Architected Summary | Public | Architecture | None |
| Kubernetes Node Replacement Runbook | Internal | Platform Engineering | None |
| Security Incident Postmortem | Restricted | Security | Internal |
| Project Apollo Architecture Review | Confidential | Cloud Architecture | Apollo Financial Services |
| Production API Latency Runbook | Internal | SRE | None |
| Data Handling Security Standard | Confidential | Security | Internal |

Important: these are fictional training documents. The AWS document is a synthetic summary, not an AWS publication.

Phase 0 should use the document metadata as attributes of the document, not as a list of users who may access it.
