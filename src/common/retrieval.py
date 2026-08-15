"""
Retrieval.

The same two implementations as the lab, moved into the service.

search_prefiltered is what the API uses. search_postfiltered exists so the
demo endpoint can show the difference on real infrastructure, and because an
argument you can only make in prose is weaker than one you can run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict

from qdrant_client import QdrantClient, models

from src.common.authorization import UserContext, build_filter, is_authorized
from src.common.embeddings import embed

COLLECTION = os.getenv("QDRANT_COLLECTION", "enterprise_docs")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
TOP_K = int(os.getenv("TOP_K", "4"))

_client = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


@dataclass
class Chunk:
    text: str
    score: float
    doc_title: str
    classification: str
    owning_team: str | None
    customer: str | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Retrieval:
    chunks: list[Chunk]
    eligible: int      # how many chunks this user could have matched
    total: int         # how many exist in the collection
    considered: int    # how many the search actually looked at
    discarded: int     # how many were thrown away after the search


def _to_chunk(point) -> Chunk:
    p = point.payload
    return Chunk(
        text=p["text"],
        score=point.score,
        doc_title=p["doc_title"],
        classification=p["classification"],
        owning_team=p.get("owning_team"),
        customer=p.get("customer"),
    )


def search_prefiltered(user: UserContext, question: str, k: int = TOP_K) -> Retrieval:
    """The correct implementation.

    The authorization filter is an argument to the search. Qdrant restricts the
    candidate set while searching, so the top k returned are the top k this
    person is permitted to see.
    """
    c = client()
    user_filter = build_filter(user)

    eligible = c.count(COLLECTION, count_filter=user_filter, exact=True).count
    total = c.count(COLLECTION, exact=True).count

    hits = c.query_points(
        collection_name=COLLECTION,
        query=embed(question),
        query_filter=user_filter,
        limit=k,
        with_payload=True,
    ).points

    return Retrieval(
        chunks=[_to_chunk(h) for h in hits],
        eligible=eligible,
        total=total,
        considered=eligible,
        discarded=0,
    )


def search_postfiltered(user: UserContext, question: str, k: int = TOP_K) -> Retrieval:
    """The implementation almost everyone writes first.

    Search everything, then drop what the user may not see. Every individual
    verdict is correct and nothing leaks into the answer. It is still wrong,
    because the user receives fewer results than requested and the number
    discarded describes documents they cannot see.
    """
    c = client()

    eligible = c.count(COLLECTION, count_filter=build_filter(user), exact=True).count
    total = c.count(COLLECTION, exact=True).count

    hits = c.query_points(
        collection_name=COLLECTION,
        query=embed(question),
        limit=k,
        with_payload=True,
    ).points

    allowed = [h for h in hits if is_authorized(user, h.payload)]

    return Retrieval(
        chunks=[_to_chunk(h) for h in allowed],
        eligible=eligible,
        total=total,
        considered=total,
        discarded=len(hits) - len(allowed),
    )
