"""
Ingestion.

Reads the manifest from S3, reads each document it lists, chunks, embeds, and
writes points into Qdrant with the document's attributes attached.

Runs as a Kubernetes Job, once, then exits.

Two things about this file are worth reading rather than skimming.

The manifest is the source of a document's attributes. A document present in
the bucket but absent from the manifest is skipped, not ingested with a
default. An unclassified document defaulting to public is how leaks happen.

What gets written to Qdrant is what a document IS. Classification, owning team,
customer, region. There is no list of users, no group list, no resolved access
control list anywhere in the payload. See ADR-001.
"""

from __future__ import annotations

import os
import sys
import uuid

import boto3
import yaml
from qdrant_client import QdrantClient, models

from src.common.embeddings import DIMENSIONS, embed

BUCKET = os.environ["DOCUMENTS_BUCKET"]
MANIFEST_KEY = os.getenv("MANIFEST_KEY", "manifest.yaml")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "enterprise_docs")
REGION = os.getenv("AWS_REGION", "ap-south-1")

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120

VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}


def s3():
    return boto3.client("s3", region_name=REGION)


def read_manifest() -> list[dict]:
    """Read and validate the manifest.

    Validation is strict on purpose. A manifest entry with a classification we
    do not recognise is an error, not something to guess at.
    """
    body = s3().get_object(Bucket=BUCKET, Key=MANIFEST_KEY)["Body"].read()
    parsed = yaml.safe_load(body)

    entries = parsed.get("documents") or []
    if not entries:
        sys.exit(f"manifest at s3://{BUCKET}/{MANIFEST_KEY} lists no documents")

    for entry in entries:
        for required in ("key", "title", "doc_id", "classification"):
            if required not in entry:
                sys.exit(f"manifest entry missing '{required}': {entry}")

        if entry["classification"] not in VALID_CLASSIFICATIONS:
            sys.exit(
                f"unknown classification '{entry['classification']}' "
                f"for {entry['key']}"
            )

    return entries


def read_document(key: str) -> str:
    return s3().get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")


def chunk(text: str) -> list[str]:
    """Split on paragraphs, pack up to CHUNK_SIZE, carry a tail for overlap."""
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
    """Recreate the collection with payload indexes on the filterable fields.

    The indexes are not optional. Without them Qdrant still filters correctly
    but degrades toward a scan as the collection grows. The index is what keeps
    filtering inside the search fast at a scale that matters.
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
    print("=" * 68)
    print("Ingestion")
    print(f"  bucket     s3://{BUCKET}")
    print(f"  manifest   {MANIFEST_KEY}")
    print(f"  qdrant     {QDRANT_URL}")
    print("=" * 68)

    entries = read_manifest()
    print(f"\nmanifest lists {len(entries)} documents\n")

    client = QdrantClient(url=QDRANT_URL)
    create_collection(client)
    print(f"collection '{COLLECTION}' created with payload indexes\n")

    total = 0
    for entry in entries:
        try:
            text = read_document(entry["key"])
        except Exception as exc:
            print(f"  SKIP  {entry['key']}  ({exc})")
            continue

        pieces = chunk(text)
        points = []

        for i, piece in enumerate(pieces):
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embed(piece),
                    payload={
                        # what the document IS
                        "classification": entry["classification"],
                        "owning_team": entry.get("owning_team"),
                        "customer": entry.get("customer"),
                        "region": entry.get("region", "global"),
                        # identity of the source
                        "doc_id": entry["doc_id"],
                        "doc_title": entry["title"],
                        "source_key": entry["key"],
                        # the chunk itself
                        "text": piece,
                        "chunk_index": i,
                    },
                )
            )

        client.upsert(collection_name=COLLECTION, points=points)
        total += len(points)

        label = entry["classification"]
        if entry.get("customer"):
            label += f" / {entry['customer']}"
        elif entry.get("owning_team"):
            label += f" / {entry['owning_team']}"

        print(f"  {len(points):>3} chunks   {label:<34}  {entry['key']}")

    print(f"\n{total} chunks indexed")


if __name__ == "__main__":
    main()
