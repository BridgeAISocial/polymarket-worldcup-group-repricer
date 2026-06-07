"""Find World Cup group-winner market sets and confirm mutual exclusivity from market TEXT.

Rule (per campaign spec): if we can't confirm a set is mutually exclusive AND exhaustive from the
market text itself, we never arb it — alert only. For 2026 (48 teams) a complete group set is
exactly 4 'win Group X' legs.

NOTE: the canonical WC tag/series slug is unverified upstream; v0.1 discovers via text search.
If coverage is thin at launch, switch to the paginated sports-markets workaround
(/v2/markets/sports?limit=100&offset=N filtered on 'fifa'/'world-cup'/slug prefix 'fifwc-').
"""

import re
import unicodedata

GROUP_RE = re.compile(r"win\s+group\s+([A-L])\b", re.IGNORECASE)
SEARCH_QUERIES = ["win Group", "World Cup Group"]
COMPLETE_SET_SIZE = 4   # 2026 format: 4 teams per group


def _norm(text):
    return unicodedata.normalize("NFKD", text or "")


def find_group_sets(client, limit=120):
    """Return {letter: [markets]} for active group-winner markets, deduped by id."""
    seen = {}
    for q in SEARCH_QUERIES:
        try:
            for m in client.get_markets(q=q, limit=limit):
                if getattr(m, "status", "active") != "active":
                    continue
                match = GROUP_RE.search(_norm(getattr(m, "question", "")))
                if match:
                    seen.setdefault(match.group(1).upper(), {})[m.id] = m
        except Exception as exc:                          # one bad query must not kill the run
            print(f"  ! discovery query '{q}' failed: {exc}")
    return {letter: list(d.values()) for letter, d in seen.items()}


def is_confirmed_exclusive(legs):
    """Complete 4-leg group set, all 'win Group X' for the SAME letter -> arbable.
    Anything else (3 legs visible, mixed letters, advance/qualify wording) -> alert only."""
    if len(legs) != COMPLETE_SET_SIZE:
        return False
    letters = {GROUP_RE.search(_norm(m.question)).group(1).upper() for m in legs
               if GROUP_RE.search(_norm(m.question))}
    if len(letters) != 1:
        return False
    bad = re.compile(r"advance|qualif|knockout|reach", re.IGNORECASE)
    return not any(bad.search(_norm(m.question)) for m in legs)
