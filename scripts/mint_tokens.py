#!/usr/bin/env python3
"""
Mint demo tokens.

    python -m scripts.mint_tokens              print all three
    python -m scripts.mint_tokens arjun        print one, for scripting

This script stands in for an identity provider. It is the only thing in the
repository that issues tokens, and it is not part of the service.

In production nothing here exists. Entra, Okta or Cognito issues the token, the
application fetches the public key from a JWKS endpoint, and the verification
code in src/api/tokens.py does not change.
"""

import sys

from src.api.tokens import issue

DEMO_USERS = {
    "sam": {
        "name": "Sam Patel",
        "clearance": "public",
        "teams": [],
        "customers": [],
        "note": "Contractor. No team, no customer entitlements.",
    },
    "arjun": {
        "name": "Arjun Mehta",
        "clearance": "confidential",
        "teams": ["team-platform", "team-sre"],
        "customers": ["cust-apollo"],
        "note": "Platform engineer, works on the Apollo account.",
    },
    "priya": {
        "name": "Priya Nair",
        "clearance": "restricted",
        "teams": ["team-security"],
        "customers": [],
        "note": "Security engineer. Highest clearance, no customer accounts.",
    },
}


def token_for(key: str) -> str:
    u = DEMO_USERS[key]
    return issue(
        subject=key,
        name=u["name"],
        clearance=u["clearance"],
        teams=u["teams"],
        customers=u["customers"],
    )


def main() -> None:
    if len(sys.argv) > 1:
        key = sys.argv[1]
        if key not in DEMO_USERS:
            sys.exit(f"unknown user: {key}. one of {list(DEMO_USERS)}")
        print(token_for(key))
        return

    for key, u in DEMO_USERS.items():
        print(f"# {u['name']}  ({u['clearance']})")
        print(f"# {u['note']}")
        print(f"export TOKEN_{key.upper()}='{token_for(key)}'")
        print()


if __name__ == "__main__":
    main()
