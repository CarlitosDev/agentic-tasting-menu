"""core.py — the single source of truth for student status.

Pure domain logic: no I/O, no auth, no framework imports. Both the MCP server
(§5.4) and the REST learning track (§5.5) call this directly. Keeping it pure is
what lets every layer above it stay a thin boundary.
"""

import random


def get_student_status(student_id: str) -> str:
    """Return a random homework status string for the given student.

    Pure function. The randomness stands in for "real data" so the rest of the
    system has something non-trivial to shape and authorize.
    """
    return random.choice(
        [
            f"{student_id} has done the homework",
            f"{student_id} hasn't done any homework",
        ]
    )
