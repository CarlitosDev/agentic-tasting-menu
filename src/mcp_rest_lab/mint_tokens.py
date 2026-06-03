"""mint_tokens.py — print copy-pasteable tokens for local use (§5.3).

Run:  uv run python -m mcp_rest_lab.mint_tokens

Prints 5 student tokens and 1 teacher token, one per line as ``<sub>  <token>``.
Also writes them to ``tokens.json`` next to the repo root so the Streamlit client
(§5.8) can offer an identity selector without anyone pasting tokens by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from .auth import STUDENTS, make_token

# Where the Streamlit client looks for minted tokens (overridable via env there).
TOKENS_FILE = Path("tokens.json")


def build_tokens() -> dict[str, str]:
    """Return a mapping of identity -> token (5 students + 1 teacher)."""
    tokens: dict[str, str] = {}
    for sub in STUDENTS:
        tokens[sub] = make_token(sub, role="student")
    # Teacher is authorized for exactly the five students (its `students` claim).
    tokens["teacher_001"] = make_token(
        "teacher_001", role="teacher", students=STUDENTS
    )
    return tokens


def main() -> None:
    tokens = build_tokens()
    for sub, token in tokens.items():
        print(f"{sub}  {token}")

    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"\n# wrote {len(tokens)} tokens to {TOKENS_FILE.resolve()}")


if __name__ == "__main__":
    main()
