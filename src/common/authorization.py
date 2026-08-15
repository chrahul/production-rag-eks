"""
The authorization model.

This module is the whole point of Phase 0. Everything else is plumbing.

ADR-001 decided that the vector store records what a document IS, never who
may read it. This file is the code expression of that decision.

Two things live here:

  DocumentAttributes  what we store on every chunk
  UserContext         what arrives in the token at query time

And one function, build_filter, which turns a UserContext into a Qdrant filter.
That filter is passed into the search, not applied after it.
"""

from dataclasses import dataclass, field
from typing import Optional

from qdrant_client import models


# ─────────────────────────────────────────────────────────────────────────────
# Classification
#
# Ordered. A user's clearance must be at or above a document's classification
# before any other rule is considered.
# ─────────────────────────────────────────────────────────────────────────────

PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"
RESTRICTED = "restricted"

CLEARANCE_ORDER = [PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED]


def levels_up_to(clearance: str) -> list[str]:
    """Every classification a user at this clearance may reach."""
    if clearance not in CLEARANCE_ORDER:
        raise ValueError(f"unknown clearance: {clearance}")
    return CLEARANCE_ORDER[: CLEARANCE_ORDER.index(clearance) + 1]


# ─────────────────────────────────────────────────────────────────────────────
# What a document carries
#
# Note what is absent. There is no allowed_users field, no group list, no
# resolved ACL. Those go stale the moment someone changes teams.
#
# owning_team is an identifier, never a display name. When Security is renamed
# to Cyber Defense, this value does not change and nothing is reindexed.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DocumentAttributes:
    classification: str
    owning_team: Optional[str] = None      # stable ID, e.g. team-security
    customer: Optional[str] = None         # stable ID, e.g. cust-apollo
    region: str = "global"
    doc_id: str = ""
    doc_title: str = ""

    def to_payload(self) -> dict:
        return {
            "classification": self.classification,
            "owning_team": self.owning_team,
            "customer": self.customer,
            "region": self.region,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
        }


# ─────────────────────────────────────────────────────────────────────────────
# What a user brings
#
# This arrives in the token, is read at query time, and is thrown away. It is
# never written to the vector store. That is why this side cannot drift: the
# identity provider re-evaluates it on every token refresh.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserContext:
    username: str
    clearance: str
    teams: list[str] = field(default_factory=list)
    customers: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# The rule
#
#   public        anyone
#   internal      clearance >= internal
#   confidential  clearance >= confidential AND (owning team OR entitled customer)
#   restricted    clearance >= restricted AND owning team
#
# Expressed as a Qdrant filter so it runs inside the search.
# ─────────────────────────────────────────────────────────────────────────────

def build_filter(user: UserContext) -> models.Filter:
    reachable = levels_up_to(user.clearance)
    branches: list[models.Filter] = []

    # public
    branches.append(
        models.Filter(must=[
            models.FieldCondition(key="classification", match=models.MatchValue(value=PUBLIC))
        ])
    )

    # internal
    if INTERNAL in reachable:
        branches.append(
            models.Filter(must=[
                models.FieldCondition(key="classification", match=models.MatchValue(value=INTERNAL))
            ])
        )

    # confidential: owning team OR entitled customer
    if CONFIDENTIAL in reachable:
        need_to_know: list[models.Filter] = []
        if user.teams:
            need_to_know.append(
                models.Filter(must=[
                    models.FieldCondition(key="owning_team", match=models.MatchAny(any=user.teams))
                ])
            )
        if user.customers:
            need_to_know.append(
                models.Filter(must=[
                    models.FieldCondition(key="customer", match=models.MatchAny(any=user.customers))
                ])
            )
        if need_to_know:
            branches.append(
                models.Filter(
                    must=[
                        models.FieldCondition(
                            key="classification", match=models.MatchValue(value=CONFIDENTIAL)
                        )
                    ],
                    should=need_to_know,
                    min_should=models.MinShould(conditions=need_to_know, min_count=1),
                )
            )

    # restricted: owning team only
    if RESTRICTED in reachable and user.teams:
        branches.append(
            models.Filter(must=[
                models.FieldCondition(
                    key="classification", match=models.MatchValue(value=RESTRICTED)
                ),
                models.FieldCondition(key="owning_team", match=models.MatchAny(any=user.teams)),
            ])
        )

    return models.Filter(
        should=branches,
        min_should=models.MinShould(conditions=branches, min_count=1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The same rule in plain Python.
#
# Used by the post-filter path, which is deliberately kept as a comparison.
# It produces the correct verdict per chunk. That is precisely why post-filter
# is dangerous: the verdict is right, and the architecture is still wrong.
# ─────────────────────────────────────────────────────────────────────────────

def is_authorized(user: UserContext, doc: dict) -> bool:
    classification = doc.get("classification")
    if classification not in CLEARANCE_ORDER:
        return False
    if classification not in levels_up_to(user.clearance):
        return False

    if classification in (PUBLIC, INTERNAL):
        return True

    if classification == CONFIDENTIAL:
        by_team = doc.get("owning_team") in user.teams
        by_customer = doc.get("customer") in user.customers
        return by_team or by_customer

    if classification == RESTRICTED:
        return doc.get("owning_team") in user.teams

    return False
