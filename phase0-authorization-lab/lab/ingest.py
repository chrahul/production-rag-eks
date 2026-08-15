"""
Ingestion.

Reads the corpus, chunks it, embeds each chunk, and writes points to Qdrant
with the document's attributes attached.

The attributes are the point of this file. Every chunk carries what its
document IS: classification, owning team, customer, region. No chunk carries
a list of who may read it.

Run once:

    python -m lab.ingest
"""

import sys
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from lab.authorization import DocumentAttributes
from lab.embeddings import DIMENSIONS, embed

COLLECTION = "enterprise_docs"
DOCUMENTS_DIR = Path(__file__).parent.parent / "documents"

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120


# ─────────────────────────────────────────────────────────────────────────────
# The corpus and its attributes.
#
# In the real platform these come from the S3 prefix the document was uploaded
# under, or from a sidecar manifest. Here they are declared, because Phase 0 is
# about proving the retrieval model, not the ingestion pipeline.
#
# Note owning_team values. Stable identifiers, never display names. When
# Security is renamed to Cyber Defense, nothing here changes.
# ─────────────────────────────────────────────────────────────────────────────

CORPUS = [
    (
        "01_public_aws_well_architected_summary.md",
        DocumentAttributes(
            classification="public",
            owning_team="team-architecture",
            customer=None,
            region="global",
            doc_id="doc-aws-wa-summary",
            doc_title="AWS Well-Architected Summary, Reliability and Operations",
        ),
    ),
    (
        "02_platform_runbook.md",
        DocumentAttributes(
            classification="internal",
            owning_team="team-platform",
            customer=None,
            region="india",
            doc_id="doc-platform-node-replacement",
            doc_title="Platform Runbook, Kubernetes Node Replacement",
        ),
    ),
    (
        "03_security_incident_postmortem.md",
        DocumentAttributes(
            classification="restricted",
            owning_team="team-security",
            customer=None,
            region="india",
            doc_id="doc-sec-incident-credential-exposure",
            doc_title="Security Incident Postmortem, Temporary Credential Exposure",
        ),
    ),
    (
        "04_customer_architecture_review.md",
        DocumentAttributes(
            classification="confidential",
            owning_team="team-architecture",
            customer="cust-apollo",
            region="india",
            doc_id="doc-apollo-architecture-review",
            doc_title="Customer Architecture Review, Project Apollo",
        ),
    ),
    (
        "05_sre_incident_runbook.md",
        DocumentAttributes(
            classification="public",
            owning_team="team-sre",
            customer=None,
            region="global",
            doc_id="doc-sre-api-latency",
            doc_title="SRE Runbook, Production API Latency",
        ),
    ),
    (
        "06_security_architecture_standard.md",
        DocumentAttributes(
            classification="confidential",
            owning_team="team-security",
            customer=None,
            region="global",
            doc_id="doc-sec-data-handling-standard",
            doc_title="Internal Security Architecture Standard, Data Handling",
        ),
    ),
]


def chunk(text: str) -> list[str]:
    """Split on paragraphs, pack up to CHUNK_SIZE, overlap by tail."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 > CHUNK_SIZE:
            chunks.append(current)
            tail = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
            current = tail + "\n\n" + para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current:
        chunks.append(current)
    return chunks


def create_collection(client: QdrantClient) -> None:
    """Recreate the collection and index the fields we filter on.

    The payload indexes are not optional. Without them Qdrant still applies
    the filter correctly, but it degrades toward a scan as the collection
    grows. The index is what keeps pre-filtering fast at scale.
    """
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size=DIMENSIONS, distance=models.Distance.COSINE
        ),
    )

    for field in ("classification", "owning_team", "customer", "region"):
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def main() -> None:
    if not DOCUMENTS_DIR.exists():
        sys.exit(f"documents directory not found: {DOCUMENTS_DIR}")

    client = QdrantClient(url="http://localhost:6333")

    print("=" * 68)
    print("Phase 0 ingestion")
    print("=" * 68)

    create_collection(client)
    print(f"\ncollection '{COLLECTION}' created, payload indexes in place\n")

    total = 0
    for filename, attrs in CORPUS:
        path = DOCUMENTS_DIR / filename
        if not path.exists():
            print(f"  SKIP  {filename}  (not found)")
            continue

        chunks = chunk(path.read_text(encoding="utf-8"))
        points = []

        for i, text in enumerate(chunks):
            payload = attrs.to_payload()
            payload["text"] = text
            payload["chunk_index"] = i
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embed(text),
                    payload=payload,
                )
            )

        client.upsert(collection_name=COLLECTION, points=points)
        total += len(points)

        label = f"{attrs.classification}"
        if attrs.customer:
            label += f" / {attrs.customer}"
        elif attrs.owning_team:
            label += f" / {attrs.owning_team}"

        print(f"  {len(points):>3} chunks   {label:<34}  {filename}")

    print(f"\n{total} chunks indexed")
    print("\nnext:  python -m lab.demo")


if __name__ == "__main__":
    main()
