"""mint_tokens.py — print copy-pasteable tokens for local use (§5.3).

Run:  uv run python -m mcp_rest_lab.mint_tokens

Prints student tokens and 2 teacher tokens, one per line as ``<sub>  <token>``.
Also writes them to ``tokens.json`` next to the repo root so the Streamlit client
(§5.8) can offer an identity selector without anyone pasting tokens by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import core
from .auth import STUDENTS, make_token

# Where the Streamlit client looks for minted tokens (overridable via env there).
TOKENS_FILE = Path("tokens.json")


def build_tokens() -> dict[str, str]:
    """Return a mapping of identity -> token.

    Prefer real artifact ids from LEARNER_STATE_ROOT so local auth follows the
    data; fall back to the demo roster when no artifacts are present.
    """
    student_ids = core.list_student_ids() or STUDENTS
    tokens: dict[str, str] = {}
    for sub in student_ids:
        tokens[sub] = make_token(sub, role="student")
    # Teachers are authorized for exactly these students (their `students`
    # claim). Add more teacher subs here and they flow to the client automatically.
    for teacher in ("teacher_MichaelKnight", "teacher_MacGyver"):
        tokens[teacher] = make_token(teacher, role="teacher", students=student_ids)
    return tokens


def main() -> None:
    tokens = build_tokens()
    for sub, token in tokens.items():
        print(f"{sub}  {token}")

    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"\n# wrote {len(tokens)} tokens to {TOKENS_FILE.resolve()}")


if __name__ == "__main__":
    main()
