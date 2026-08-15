"""
Retrieval, two ways.

This file exists to show the difference between two implementations that look
equivalent in a code review and are not.

  search_prefiltered   the filter is an argument to the search
  search_postfiltered  the filter runs on the results

Both apply the same authorization rule. Both reach the same verdict on every
individual chunk. Only one of them is safe.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from lab.authorization import UserContext, build_filter, is_authorized
from lab.embeddings import embed

COLLECTION = "enterprise_docs"
TOP_K = 4


@dataclass
class Chunk:
    text: str
    score: float
    doc_title: str
    classification: str
    owning_team: str | None
    customer: str | None


@dataclass
class Result:
    chunks: list[Chunk]
    considered: int      # how many the vector search looked at
    discarded: int       # how many were thrown away after the search


def _client() -> QdrantClient:
    return QdrantClient(url="http://localhost:6333")


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


# ─────────────────────────────────────────────────────────────────────────────
# Pre-filter. The correct implementation.
#
# The authorization filter is passed into the search call. Qdrant restricts the
# candidate set while searching, so the top K it returns are the top K the user
# is permitted to see.
#
# Ask for four, get four. The user's experience does not depend on other
# people's permissions.
# ─────────────────────────────────────────────────────────────────────────────

def search_prefiltered(user: UserContext, question: str, k: int = TOP_K) -> Result:
    hits = _client().query_points(
        collection_name=COLLECTION,
        query=embed(question),
        query_filter=build_filter(user),
        limit=k,
        with_payload=True,
    ).points

    return Result(chunks=[_to_chunk(h) for h in hits], considered=len(hits), discarded=0)


# ─────────────────────────────────────────────────────────────────────────────
# Post-filter. The implementation almost everyone writes first.
#
# Search the whole corpus, then drop what the user may not see.
#
# Every individual verdict here is correct. is_authorized is the same rule the
# pre-filter uses. Nothing leaks into the answer.
#
# It is still wrong, for two reasons that only appear at the system level:
#
#   1. The user asked for four results and receives however many survived.
#      A narrow-access user can receive zero and be told nothing exists, while
#      the system holds material they are cleared to read.
#
#   2. How many were discarded, and how long the query took, are both
#      observable. They describe documents the user cannot see. You do not need
#      to read a document to learn something from it.
# ─────────────────────────────────────────────────────────────────────────────

def search_postfiltered(user: UserContext, question: str, k: int = TOP_K) -> Result:
    hits = _client().query_points(
        collection_name=COLLECTION,
        query=embed(question),
        limit=k,                # no filter, the search sees everything
        with_payload=True,
    ).points

    allowed = [h for h in hits if is_authorized(user, h.payload)]

    return Result(
        chunks=[_to_chunk(h) for h in allowed],
        considered=len(hits),
        discarded=len(hits) - len(allowed),
    )
