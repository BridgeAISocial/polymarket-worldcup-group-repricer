"""Broadened World-Cup group-market classification and discovery.

The strict ``discovery.py`` deliberately sees ONLY group-WINNER legs (it powers the coherence arb,
which must never mistake an "advance" market for a "win" market). The daily *recommender* needs a
wider lens — three families per group:

    * ``winner``    — "win Group X" / "finish first in Group X" (1 of 4 teams).
    * ``qualifier`` — "advance / qualify / reach the knockout from Group X" (2 of 4 teams).
    * ``prop``      — any other Group-X market (placement, most goals, both-favorites-advance, ...).

This module is additive: it reuses discovery's fetchers and normaliser, and leaves the strict
winner pipeline untouched.
"""

import re

from discovery import (  # reuse the low-level plumbing, don't re-implement it
    _normalize,
    _qkey,
    _question_of,
    _status_of,
    _norm,
    extract_group_letter,
    fetch_active_markets,
    fetch_importable_worldcup,
)

GROUP_LETTERS = [chr(c) for c in range(ord("A"), ord("L") + 1)]  # 2026 = Groups A..L

# A bare "Group X" letter anywhere in the title (A..L only — M+ are not 2026 groups).
_GROUP_LETTER_RE = re.compile(r"\bgroup\s+([A-L])\b", re.IGNORECASE)

# Advancement family — a team reaching the knockout stage out of its group (2 of 4 advance).
_QUALIFIER_RE = re.compile(
    r"advanc|qualif|knockout|reach(?:es)?|round\s+of\s+(?:32|16)|through\s+to|"
    r"progress|make\s+the\s+(?:last|knockout)",
    re.IGNORECASE,
)

# Context that confirms a generic "Group X" title is actually a World-Cup market (guards props
# against unrelated "group" usage when scanning the whole active venue).
_WC_CONTEXT_RE = re.compile(r"world\s*cup|fifa|2026|group\s+stage|national\s+team", re.IGNORECASE)

CATEGORIES = ("winner", "qualifier", "prop")


def group_letter(question):
    """Bare group letter A..L anywhere in the title, else None."""
    m = _GROUP_LETTER_RE.search(_norm(question))
    return m.group(1).upper() if m else None


def classify_market(question, source=None):
    """Return ``(category, letter)`` for a Group-X market, else ``(None, None)``.

    Precedence: winner (strict) -> qualifier -> prop. A ``prop`` requires either an importable
    World-Cup source or explicit WC context in the title, so scanning the full active venue does
    not sweep in unrelated 'group' markets.
    """
    q = _norm(question)
    if not q:
        return (None, None)

    winner = extract_group_letter(q)            # strict winner-family matcher
    if winner:
        return ("winner", winner)

    letter = group_letter(q)
    if not letter:
        return (None, None)

    if _QUALIFIER_RE.search(q):
        return ("qualifier", letter)

    wc_context = source == "importable" or _WC_CONTEXT_RE.search(q) or _names_a_known_team(q)
    if wc_context:
        return ("prop", letter)
    return (None, None)


def _names_a_known_team(question):
    # Late import keeps categories.py importable without strategy at module load in odd setups.
    from strategy import team_for
    return team_for(question) is not None


def _empty_group():
    return {"winner": [], "qualifier": [], "prop": []}


def discover_all(client):
    """Discover every Group-X market across the three families from both sources.

    Returns ``{"groups": {letter: {category: [normalized_entry, ...]}}, "counts": {...}}`` covering
    all 12 letters (A..L) even when empty, so the recommender can report on *every* group.
    Importable entries that duplicate an active title are dropped (already tradeable).
    """
    active_raw = fetch_active_markets(client)
    importable_raw = fetch_importable_worldcup(client)

    groups = {letter: _empty_group() for letter in GROUP_LETTERS}
    active_qkeys = set()
    counts = {c: 0 for c in CATEGORIES}
    counts["importable"] = 0

    for m in active_raw:
        if _status_of(m) != "active":
            continue
        cat, letter = classify_market(_question_of(m), source="active")
        if not cat:
            continue
        entry = _normalize(m, "active")
        entry["category"] = cat
        groups[letter][cat].append(entry)
        active_qkeys.add(_qkey(entry["question"]))
        counts[cat] += 1

    for m in importable_raw:
        cat, letter = classify_market(_question_of(m), source="importable")
        if not cat:
            continue
        entry = _normalize(m, "importable")
        if _qkey(entry["question"]) in active_qkeys:
            continue
        entry["category"] = cat
        groups[letter][cat].append(entry)
        counts[cat] += 1
        counts["importable"] += 1

    counts["active_total"] = len(active_raw)
    counts["importable_total"] = len(importable_raw)
    counts["groups_with_markets"] = sum(
        1 for g in groups.values() if any(g[c] for c in CATEGORIES)
    )
    return {"groups": groups, "counts": counts}
