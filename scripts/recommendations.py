"""Daily *manual* recommendation engine — advisory only, never trades.

Given the broadened three-family discovery (``categories.discover_all``), this builds a ranked list
of $5 betting recommendations for every group A..L, each tagged with a risk tier and a plain-English
rationale, so a human can read the daily report and place the bets themselves.

Design choices:
  * Sizing is flat ``STAKE_USD`` per bet (default $5) — the recommender never compounds or martingales.
  * Risk is structural, not a price model: it reflects how many teams clear the bar (2 of 4 advance
    vs 1 of 4 win), the team's Elo rank *within its group*, the implied price band, and whether the
    market is even tradeable yet. Anything we can't stand behind is FLAGGED, not hidden.
  * Group-stage *props* default to HIGH risk: they're model-light here and surfaced for the human to
    judge, not endorsed.
"""

import os

from strategy import ELO, team_for

STAKE_USD_DEFAULT = 5.0

# Risk tiers, ordered safest -> riskiest. FLAGGED is the catch-all for "surfaced, judge it yourself".
LOW, MEDIUM, HIGH, FLAGGED = "LOW", "MEDIUM", "HIGH", "FLAGGED"
RISK_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2, FLAGGED: 3}
_TIER_BY_SCORE = {0: LOW, 1: MEDIUM, 2: HIGH}

# Price bands for a YES back: below FLOOR is a longshot, above CEIL is (almost) no edge left.
PRICE_LONGSHOT = 0.22
PRICE_RICH = 0.92


def config():
    g = os.getenv
    return {
        "STAKE_USD": float(g("STAKE_USD", g("MAX_TRADE_USD", str(STAKE_USD_DEFAULT)))),
        "QUALIFIERS_PER_GROUP": int(g("QUALIFIERS_PER_GROUP", "2")),   # 2026: top 2 advance
        "MAX_PROPS_PER_GROUP": int(g("MAX_PROPS_PER_GROUP", "3")),
    }


def _price_of(entry):
    p = entry.get("yes_price")
    return float(p) if isinstance(p, (int, float)) else None


def _tradeable(entry):
    return entry.get("source") == "active"


def _group_rank(entry, group_entries):
    """1-based Elo rank of this entry's team among all teams named across the group (1 = strongest).
    Returns (rank, n_known). Unknown team -> rank None."""
    team = team_for(entry["question"])
    if not team:
        return (None, 0)
    teams = []
    for cat_entries in group_entries.values():
        for e in cat_entries:
            t = team_for(e["question"])
            if t and t not in teams:
                teams.append(t)
    ranked = sorted(teams, key=lambda t: ELO.get(t, 0), reverse=True)
    return (ranked.index(team) + 1, len(ranked))


def assess_risk(category, price, rank, n_known, tradeable):
    """Map structural features -> (tier, flags). Pure; easy to unit-test and tune."""
    flags = []
    # Base score by how many teams clear the bar.
    score = {"qualifier": 0, "winner": 1, "prop": 2}[category]

    if price is None:
        flags.append("no price on venue yet")
        score += 1
    else:
        if price < PRICE_LONGSHOT:
            flags.append(f"longshot ({price:.0%} implied)")
            score += 1
        elif price > PRICE_RICH:
            flags.append(f"little edge left ({price:.0%} priced in)")
        elif price >= 0.80:
            score -= 1            # strong, already-leading favorite -> safer

    # Elo rank within the group.
    if rank is None:
        flags.append("team unmapped (no Elo anchor)")
        score += 1
    elif rank == 1:
        score -= 1               # group favorite
    elif rank >= 3:
        flags.append(f"#{rank} in group by Elo")
        score += 1

    if not tradeable:
        flags.append("not tradeable yet (importable-only — watch for Simmer import)")
        score = max(score, 1)    # never LOW if you can't actually place it today

    score = max(0, min(2, score))
    tier = _TIER_BY_SCORE[score]
    if category == "prop":
        tier = FLAGGED           # props are surfaced for human judgement, not endorsed
    if any(f.startswith("longshot") for f in flags) and tier == LOW:
        tier = MEDIUM
    return tier, flags


def _payout_note(stake, price):
    if not price or price <= 0:
        return ""
    profit = stake * (1.0 / price - 1.0)
    return f"risk ${stake:.0f} to win ${profit:.2f} (returns ${stake + profit:.2f})"


def _recommend_entry(entry, category, group_entries, cfg):
    price = _price_of(entry)
    rank, n_known = _group_rank(entry, group_entries)
    tradeable = _tradeable(entry)
    tier, flags = assess_risk(category, price, rank, n_known, tradeable)
    team = team_for(entry["question"])

    if category == "qualifier":
        thesis = f"{team or 'This side'} to reach the knockouts (2 of 4 advance)"
    elif category == "winner":
        thesis = f"{team or 'This side'} to win the group (1 of 4)"
    else:
        thesis = "Group-stage prop — surfaced for your call"

    return {
        "group": None,                       # filled by caller
        "category": category,
        "side": "YES",
        "question": entry["question"],
        "market_id": entry.get("id"),
        "price": price,
        "stake_usd": cfg["STAKE_USD"],
        "team": team,
        "elo_rank": rank,
        "risk": tier,
        "tradeable": tradeable,
        "source": entry.get("source"),
        "flags": flags,
        "thesis": thesis,
        "payout": _payout_note(cfg["STAKE_USD"], price),
    }


def _by_elo(entries):
    return sorted(entries, key=lambda e: ELO.get(team_for(e["question"]) or "", 0), reverse=True)


def recommend_for_group(letter, group_entries, cfg):
    """Build the recommendation list for one group across all three families."""
    recs = []

    # Qualifiers: back the top-N Elo teams (N = how many advance).
    for e in _by_elo(group_entries["qualifier"])[: cfg["QUALIFIERS_PER_GROUP"]]:
        recs.append(_recommend_entry(e, "qualifier", group_entries, cfg))

    # Winner: back the single strongest team's winner leg.
    winners = _by_elo(group_entries["winner"])
    if winners:
        recs.append(_recommend_entry(winners[0], "winner", group_entries, cfg))

    # Props: surface a capped few, flagged.
    for e in group_entries["prop"][: cfg["MAX_PROPS_PER_GROUP"]]:
        recs.append(_recommend_entry(e, "prop", group_entries, cfg))

    for r in recs:
        r["group"] = letter
    return recs


def build_recommendations(discovery_all, cfg=None):
    """Return ``{letter: [rec, ...]}`` for every group A..L (empty list where no markets exist)."""
    cfg = cfg or config()
    out = {}
    for letter, group_entries in discovery_all["groups"].items():
        out[letter] = recommend_for_group(letter, group_entries, cfg)
    return out


# === Reporting ==================================================================================
def _flat(recs_by_group):
    return [r for letter in sorted(recs_by_group) for r in recs_by_group[letter]]


def summarize(recs_by_group):
    flat = _flat(recs_by_group)
    by_risk = {t: 0 for t in (LOW, MEDIUM, HIGH, FLAGGED)}
    for r in flat:
        by_risk[r["risk"]] += 1
    total_stake = sum(r["stake_usd"] for r in flat)
    return {"total": len(flat), "by_risk": by_risk, "total_stake_usd": total_stake}


def _fmt_rec(r):
    px = f"{r['price']:.2f}" if isinstance(r["price"], (int, float)) else "n/a"
    head = (f"  • Group {r['group']} — {r['side']} @ {px}  (${r['stake_usd']:.0f})  "
            f"[{r['category']}]")
    lines = [head, f"      {r['question']}", f"      Thesis: {r['thesis']}"]
    if r["payout"]:
        lines.append(f"      Payout: {r['payout']}")
    if r["flags"]:
        lines.append("      Flags: " + "; ".join(r["flags"]))
    if not r["tradeable"]:
        lines.append("      ⚠ Not yet tradeable on Simmer (importable upstream — watch for import).")
    return "\n".join(lines)


def format_report(recs_by_group, cfg=None, date=None):
    """Human-readable daily report: a risk-sorted master list + per-group coverage for all 12 groups."""
    cfg = cfg or config()
    s = summarize(recs_by_group)
    flat = _flat(recs_by_group)

    lines = [
        "=" * 78,
        f"WORLD CUP 2026 — DAILY GROUP-STAGE RECOMMENDATIONS{('  ' + date) if date else ''}",
        "=" * 78,
        f"Stake per bet: ${cfg['STAKE_USD']:.0f}   |   Recommendations: {s['total']}   |   "
        f"Total if all placed: ${s['total_stake_usd']:.0f}",
        f"Risk mix — LOW:{s['by_risk'][LOW]}  MEDIUM:{s['by_risk'][MEDIUM]}  "
        f"HIGH:{s['by_risk'][HIGH]}  FLAGGED:{s['by_risk'][FLAGGED]}",
        "",
        "These are MANUAL recommendations for you to review and place yourself. Nothing is",
        "auto-traded. Sizing is flat $5; risk tiers are structural, not a guarantee. See DISCLAIMER.md.",
        "",
    ]

    # --- Master list, sorted by risk tier then by price strength ---
    lines.append("─" * 78)
    lines.append("RECOMMENDATIONS BY RISK TIER")
    lines.append("─" * 78)
    if not flat:
        lines.append("  (No group markets discovered this run — see coverage below.)")
    for tier in (LOW, MEDIUM, HIGH, FLAGGED):
        bucket = [r for r in flat if r["risk"] == tier]
        if not bucket:
            continue
        bucket.sort(key=lambda r: -(r["price"] or 0))
        lines.append("")
        lines.append(f"### {tier} RISK  ({len(bucket)})  {_tier_blurb(tier)}")
        for r in bucket:
            lines.append(_fmt_rec(r))

    # --- Per-group coverage for ALL 12 groups ---
    lines.append("")
    lines.append("─" * 78)
    lines.append("COVERAGE BY GROUP (all 12)")
    lines.append("─" * 78)
    for letter in sorted(recs_by_group):
        recs = recs_by_group[letter]
        if not recs:
            lines.append(f"  Group {letter}: no markets discovered yet.")
            continue
        cats = ", ".join(sorted({r["category"] for r in recs}))
        tiers = ", ".join(f"{r['side']}@{(r['price'] or 0):.2f}[{r['risk'][:1]}]" for r in recs)
        lines.append(f"  Group {letter}: {len(recs)} rec(s) [{cats}] — {tiers}")

    lines.append("")
    lines.append("Legend: LOW=structural favorite, tradeable.  MEDIUM=solid but 1-of-4 or borderline.")
    lines.append("        HIGH=longshot / weak-Elo / not-yet-tradeable.  FLAGGED=prop, your judgement.")
    lines.append("=" * 78)
    return "\n".join(lines)


def _tier_blurb(tier):
    return {
        LOW: "— structural favorites, tradeable now",
        MEDIUM: "— reasonable, but a coin-flip-ish edge",
        HIGH: "— risky: longshot, weak anchor, or not yet tradeable",
        FLAGGED: "— props & specials, surfaced for your judgement (not endorsed)",
    }[tier]


def to_json(recs_by_group, cfg=None, date=None):
    cfg = cfg or config()
    return {
        "date": date,
        "stake_usd": cfg["STAKE_USD"],
        "summary": summarize(recs_by_group),
        "groups": recs_by_group,
    }
