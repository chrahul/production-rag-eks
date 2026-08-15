"""
The Phase 0 demo.

    python -m lab.demo

Three acts.

  1  Who can see what.
  2  Same question, three users, three different answers.
  3  The same question again, through the naive implementation.
"""

from lab.retrieval import Result, search_postfiltered, search_prefiltered
from lab.users import DESCRIPTIONS, USERS

QUESTION = "What is our approach to disaster recovery testing?"

W = 74


def rule(char: str = "-") -> None:
    print(char * W)


def heading(text: str) -> None:
    print()
    rule("=")
    print(text)
    rule("=")


def show(result: Result, show_discarded: bool = False) -> None:
    if not result.chunks:
        print("    no results")
    for c in result.chunks:
        tag = c.classification
        if c.customer:
            tag += f" / {c.customer}"
        elif c.owning_team:
            tag += f" / {c.owning_team}"
        print(f"    {c.score:.3f}  [{tag}]")
        print(f"           {c.doc_title}")

    if show_discarded and result.discarded:
        print()
        print(f"    searched {result.considered}, discarded {result.discarded} "
              f"after retrieval, returned {len(result.chunks)}")


def act_one() -> None:
    heading("1.  Who can see what")

    docs = [
        ("AWS Well-Architected summary", "public", "team-architecture", None),
        ("SRE runbook", "public", "team-sre", None),
        ("Platform runbook", "internal", "team-platform", None),
        ("Security data standard", "confidential", "team-security", None),
        ("Apollo architecture review", "confidential", "team-architecture", "cust-apollo"),
        ("Security incident postmortem", "restricted", "team-security", None),
    ]

    from lab.authorization import is_authorized

    print()
    print(f"  {'document':<32}" + "".join(f"{k:<10}" for k in USERS))
    rule()
    for title, classification, team, customer in docs:
        payload = {
            "classification": classification,
            "owning_team": team,
            "customer": customer,
        }
        row = f"  {title:<32}"
        for user in USERS.values():
            row += f"{'yes' if is_authorized(user, payload) else '-':<10}"
        print(row)

    print()
    for key, user in USERS.items():
        print(f"  {key:<8} {DESCRIPTIONS[key]}")

    print()
    print("  Priya holds the highest clearance in the organisation.")
    print("  Arjun can read the Apollo review and she cannot.")
    print("  Clearance alone is not the model.")


def act_two() -> None:
    heading("2.  Same question, three users")
    print(f'\n  "{QUESTION}"\n')

    for key, user in USERS.items():
        rule()
        print(f"  {user.username}  ({key})")
        print(f"  clearance={user.clearance}  teams={user.teams or 'none'}  "
              f"customers={user.customers or 'none'}")
        print()
        show(search_prefiltered(user, QUESTION))
        print()

    rule()
    print()
    print("  Every user asked for 4 results and received 4.")
    print("  The answers differ in substance, not only in count.")


def act_three() -> None:
    heading("3.  The same question, naive implementation")

    print()
    print("  Retrieve the top 4, then discard what the user may not see.")
    print("  The authorization rule is identical. Every verdict is correct.")
    print()

    for key, user in USERS.items():
        rule()
        print(f"  {user.username}  ({key})")
        print()
        show(search_postfiltered(user, QUESTION), show_discarded=True)
        print()

    rule()
    print()
    print("  Nothing leaked. Every discarded chunk was correctly discarded.")
    print()
    print("  Two things went wrong anyway.")
    print()
    print("  The user asked for 4 results and received fewer. A narrow-access")
    print("  user can receive nothing at all and be told no information exists,")
    print("  while the system holds material they are cleared to read.")
    print()
    print("  And the discard count is observable. It describes documents the")
    print("  user cannot see. Ask a question about an incident you are not")
    print("  cleared to know about, and the shape of the response tells you")
    print("  whether that incident exists.")
    print()
    print("  You do not need to read a document to learn something from it.")


def main() -> None:
    print()
    rule("=")
    print("  Phase 0   Enterprise Authorization Lab")
    print("  Two users. Same question. Different answers.")
    rule("=")

    act_one()
    act_two()
    act_three()

    print()
    rule("=")
    print("  The filter belongs inside the search, not after it.")
    print("  ADR-001: docs/adr/001-vector-store-is-not-an-authorization-system.md")
    rule("=")
    print()


if __name__ == "__main__":
    main()
