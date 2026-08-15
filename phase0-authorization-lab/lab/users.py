"""
Test users.

In Phase 4 these become real tokens issued by Authentik. Nothing in the
retrieval path changes when that happens, because the application only ever
sees a UserContext.

That is the boundary this file exists to prove.
"""

from lab.authorization import UserContext

USERS = {
    "sam": UserContext(
        username="Sam Patel",
        clearance="public",
        teams=[],
        customers=[],
    ),
    "arjun": UserContext(
        username="Arjun Mehta",
        clearance="confidential",
        teams=["team-platform", "team-sre"],
        customers=["cust-apollo"],
    ),
    "priya": UserContext(
        username="Priya Nair",
        clearance="restricted",
        teams=["team-security"],
        customers=[],
    ),
}

DESCRIPTIONS = {
    "sam": "Contractor. Public clearance, no team, no customer entitlements.",
    "arjun": "Platform engineer. Confidential clearance, on the Apollo account.",
    "priya": "Security engineer. Restricted clearance, no customer entitlements.",
}


def get_user(key: str) -> UserContext:
    if key not in USERS:
        raise KeyError(f"unknown user: {key}. one of {list(USERS)}")
    return USERS[key]
