"""
The RAG API.

One service, four endpoints.

  GET  /healthz     liveness, no auth
  GET  /readyz      readiness, checks Qdrant
  GET  /whoami      what the platform sees in your token
  POST /ask         the actual product
  POST /compare     the same question both ways, for the demo

Every request that touches documents requires a bearer token. The claims in
that token become a search filter before anything is retrieved.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from src.api.tokens import Claims, TokenError, verify
from src.common.authorization import UserContext
from src.common.generation import generate
from src.common.retrieval import (
    Retrieval,
    client,
    search_postfiltered,
    search_prefiltered,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("rag-api")

app = FastAPI(
    title="Enterprise RAG API",
    description="Retrieval with document level authorization",
    version="0.1.0",
)


# ─────────────────────────────────────────────────────────────────────────────
# Auth dependency
# ─────────────────────────────────────────────────────────────────────────────

def current_user(authorization: str = Header(None)) -> Claims:
    if not authorization:
        raise HTTPException(401, "missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "expected a bearer token")

    try:
        return verify(authorization[7:])
    except TokenError as exc:
        raise HTTPException(401, str(exc))


def to_context(claims: Claims) -> UserContext:
    """Claims from the token become the authorization context.

    Nothing is looked up. Nothing is stored. The token is the source.
    """
    return UserContext(
        username=claims.name,
        clearance=claims.clearance,
        teams=claims.teams,
        customers=claims.customers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


class SourceOut(BaseModel):
    doc_title: str
    classification: str
    owning_team: str | None = None
    customer: str | None = None
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    asked_by: str
    clearance: str
    eligible_chunks: int
    total_chunks: int
    latency_ms: int


class ComparePath(BaseModel):
    answer: str
    sources: list[SourceOut]
    searched: int
    returned: int
    discarded: int


class CompareResponse(BaseModel):
    question: str
    asked_by: str
    clearance: str
    prefiltered: ComparePath
    postfiltered: ComparePath


def to_sources(r: Retrieval) -> list[SourceOut]:
    return [
        SourceOut(
            doc_title=c.doc_title,
            classification=c.classification,
            owning_team=c.owning_team,
            customer=c.customer,
            score=round(c.score, 4),
        )
        for c in r.chunks
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Liveness. Says nothing about dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness. Fails if Qdrant is unreachable, so Kubernetes does not send
    traffic to a pod that cannot serve it."""
    try:
        collections = client().get_collections()
        return {
            "status": "ready",
            "collections": [c.name for c in collections.collections],
        }
    except Exception as exc:
        raise HTTPException(503, f"qdrant unreachable: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Identity
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/whoami")
def whoami(claims: Claims = Depends(current_user)):
    """Everything the platform knows about you, and where it came from.

    Useful in a demo because it makes the point that this is read from the
    token rather than looked up anywhere.
    """
    return {
        "subject": claims.subject,
        "name": claims.name,
        "clearance": claims.clearance,
        "teams": claims.teams,
        "customers": claims.customers,
        "source": "the token you presented, nothing was looked up",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ask
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, claims: Claims = Depends(current_user)):
    """Answer a question using only documents this person may read.

    The order matters. The filter is built from the token, passed into the
    search, and the model only ever receives chunks that survived it.
    """
    started = time.perf_counter()
    user = to_context(claims)

    retrieval = search_prefiltered(user, req.question)
    answer = generate(req.question, [c.as_dict() for c in retrieval.chunks])

    elapsed = int((time.perf_counter() - started) * 1000)

    log.info(
        "ask user=%s clearance=%s eligible=%d/%d returned=%d ms=%d",
        claims.subject,
        claims.clearance,
        retrieval.eligible,
        retrieval.total,
        len(retrieval.chunks),
        elapsed,
    )

    return AskResponse(
        answer=answer,
        sources=to_sources(retrieval),
        asked_by=claims.name,
        clearance=claims.clearance,
        eligible_chunks=retrieval.eligible,
        total_chunks=retrieval.total,
        latency_ms=elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Compare
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/compare", response_model=CompareResponse)
def compare(req: AskRequest, claims: Claims = Depends(current_user)):
    """Run the same question both ways.

    This endpoint exists for the demo. It is not something you would ship, and
    the naive path would not exist in a real deployment at all.

    What it shows: the post-filtered path leaks nothing, and is still wrong.
    The user asked for four results and receives however many survived. The
    number discarded is a fact about documents they cannot see.
    """
    user = to_context(claims)

    pre = search_prefiltered(user, req.question)
    post = search_postfiltered(user, req.question)

    return CompareResponse(
        question=req.question,
        asked_by=claims.name,
        clearance=claims.clearance,
        prefiltered=ComparePath(
            answer=generate(req.question, [c.as_dict() for c in pre.chunks]),
            sources=to_sources(pre),
            searched=pre.considered,
            returned=len(pre.chunks),
            discarded=pre.discarded,
        ),
        postfiltered=ComparePath(
            answer=generate(req.question, [c.as_dict() for c in post.chunks]),
            sources=to_sources(post),
            searched=post.considered,
            returned=len(post.chunks),
            discarded=post.discarded,
        ),
    )
