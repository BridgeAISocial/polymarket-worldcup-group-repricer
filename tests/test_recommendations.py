"""Tests for the daily recommendation engine (categories.py + recommendations.py).

These cover the three families (winner / qualifier / prop), risk-tier assignment, $5 sizing,
all-12-group coverage, and the report/JSON shape — all without the SDK (FakeClient doubles).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

import recommendations as rec  # noqa: E402
from categories import (  # noqa: E402
    GROUP_LETTERS,
    classify_market,
    discover_all,
    group_letter,
)


# --- Test doubles (mirrors tests/test_discovery.py) ---------------------------------------------
class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def amarket(id, question, status="active", **kw):
    return Obj(id=id, question=question, status=status, **kw)


class FakeClient:
    def __init__(self, active=None, importable=None):
        self._active = list(active or [])
        self._importable = list(importable or [])

    def get_markets(self, status="active", import_source=None, limit=50, include=None):
        return list(self._active)

    def list_importable_markets(self, q=None, limit=50, min_volume=0):
        return list(self._importable)


# === classify_market ============================================================================
def test_classify_winner():
    assert classify_market("Will Spain win Group A?") == ("winner", "A")
    assert classify_market("England finishes first in Group C") == ("winner", "C")


def test_classify_qualifier():
    assert classify_market("Will Mexico advance from Group A?") == ("qualifier", "A")
    assert classify_market("Will Japan qualify from Group F?") == ("qualifier", "F")
    assert classify_market("USA to reach the knockout stage from Group D") == ("qualifier", "D")


def test_classify_prop_requires_wc_context():
    # bare 'Group B' with no WC context and no known team -> not ours
    assert classify_market("Acme Group B quarterly earnings") == (None, None)
    # WC context present -> prop
    assert classify_market("Most goals scored in World Cup Group B") == ("prop", "B")
    # known team present -> prop
    assert classify_market("Brazil and a teammate both score in Group G") == ("prop", "G")
    # importable source is itself WC context
    assert classify_market("Top scorer of Group H", source="importable") == ("prop", "H")


def test_classify_none_for_non_group():
    assert classify_market("Will Brazil win the World Cup?") == (None, None)
    assert classify_market("Group M winner") == (None, None)   # only A..L
    assert classify_market("") == (None, None)


def test_group_letter_helper():
    assert group_letter("anything Group K something") == "K"
    assert group_letter("no letter here") is None


# === discover_all ===============================================================================
def test_discover_all_buckets_three_families_and_covers_all_groups():
    client = FakeClient(
        active=[
            amarket(1, "Spain win Group A"),
            amarket(2, "France advance from Group A"),
            amarket(3, "Argentina win Group B"),
        ],
        importable=[
            amarket(4, "Most goals in World Cup Group A"),
        ],
    )
    disc = discover_all(client)
    assert set(disc["groups"]) == set(GROUP_LETTERS)        # all 12 present
    gA = disc["groups"]["A"]
    assert len(gA["winner"]) == 1
    assert len(gA["qualifier"]) == 1
    assert len(gA["prop"]) == 1
    assert disc["counts"]["winner"] == 2
    assert disc["counts"]["qualifier"] == 1
    assert disc["counts"]["prop"] == 1


def test_discover_all_drops_importable_dup_of_active():
    client = FakeClient(
        active=[amarket(1, "Spain win Group A")],
        importable=[amarket(2, "Spain win Group A")],
    )
    disc = discover_all(client)
    assert len(disc["groups"]["A"]["winner"]) == 1


# === assess_risk ================================================================================
def test_qualifier_favorite_is_low_risk():
    tier, flags = rec.assess_risk("qualifier", price=0.70, rank=1, n_known=4, tradeable=True)
    assert tier == rec.LOW


def test_winner_is_at_least_medium():
    tier, _ = rec.assess_risk("winner", price=0.45, rank=1, n_known=4, tradeable=True)
    assert tier in (rec.LOW, rec.MEDIUM)
    tier2, _ = rec.assess_risk("winner", price=0.30, rank=3, n_known=4, tradeable=True)
    assert tier2 == rec.HIGH


def test_longshot_is_flagged_and_not_low():
    tier, flags = rec.assess_risk("qualifier", price=0.10, rank=2, n_known=4, tradeable=True)
    assert tier != rec.LOW
    assert any("longshot" in f for f in flags)


def test_importable_only_never_low():
    tier, flags = rec.assess_risk("qualifier", price=0.70, rank=1, n_known=4, tradeable=False)
    assert tier != rec.LOW
    assert any("not tradeable" in f for f in flags)


def test_prop_always_flagged():
    tier, _ = rec.assess_risk("prop", price=0.50, rank=1, n_known=4, tradeable=True)
    assert tier == rec.FLAGGED


def test_unmapped_team_flagged():
    tier, flags = rec.assess_risk("qualifier", price=0.5, rank=None, n_known=0, tradeable=True)
    assert any("unmapped" in f for f in flags)


# === build_recommendations + sizing + coverage ==================================================
def _disc_from(active=None, importable=None):
    return discover_all(FakeClient(active=active, importable=importable))


def test_recommendations_size_is_five_dollars():
    disc = _disc_from(active=[amarket(1, "Argentina advance from Group A", yes_price=0.7)])
    recs = rec.build_recommendations(disc)
    assert recs["A"][0]["stake_usd"] == 5.0
    assert recs["A"][0]["side"] == "YES"


def test_qualifiers_picks_top_two_by_elo():
    disc = _disc_from(active=[
        amarket(1, "Argentina advance from Group A", yes_price=0.8),   # Elo 2140
        amarket(2, "France advance from Group A", yes_price=0.75),     # Elo 2100
        amarket(3, "Jordan advance from Group A", yes_price=0.3),      # weak
        amarket(4, "Panama advance from Group A", yes_price=0.25),     # weak
    ])
    recs = rec.build_recommendations(disc)
    teams = [r["team"] for r in recs["A"] if r["category"] == "qualifier"]
    assert teams == ["Argentina", "France"]       # top 2 by Elo, in order


def test_all_twelve_groups_present_even_when_empty():
    recs = rec.build_recommendations(_disc_from(active=[amarket(1, "Spain win Group A")]))
    assert set(recs) == set(GROUP_LETTERS)
    assert recs["B"] == []                         # empty group still represented


def test_winner_recommendation_uses_strongest_team():
    disc = _disc_from(active=[
        amarket(1, "Morocco win Group C", yes_price=0.4),
        amarket(2, "England win Group C", yes_price=0.5),   # higher Elo
    ])
    recs = rec.build_recommendations(disc)
    winners = [r for r in recs["C"] if r["category"] == "winner"]
    assert len(winners) == 1
    assert winners[0]["team"] == "England"


# === reporting ==================================================================================
def test_format_report_has_risk_sections_and_all_groups():
    disc = _disc_from(active=[
        amarket(1, "Argentina advance from Group A", yes_price=0.78),
        amarket(2, "Spain win Group B", yes_price=0.45),
    ])
    recs = rec.build_recommendations(disc)
    report = rec.format_report(recs, date="2026-06-12")
    assert "DAILY GROUP-STAGE RECOMMENDATIONS" in report
    assert "RECOMMENDATIONS BY RISK TIER" in report
    assert "COVERAGE BY GROUP" in report
    assert "Group L" in report                     # all 12 covered
    assert "$5" in report


def test_to_json_shape():
    disc = _disc_from(active=[amarket(1, "Argentina advance from Group A", yes_price=0.7)])
    recs = rec.build_recommendations(disc)
    payload = rec.to_json(recs, date="2026-06-12")
    assert payload["stake_usd"] == 5.0
    assert payload["summary"]["total"] == 1
    assert "A" in payload["groups"]


def test_summary_counts_by_risk():
    disc = _disc_from(active=[
        amarket(1, "Argentina advance from Group A", yes_price=0.78),   # low
        amarket(2, "Most goals in World Cup Group A", yes_price=0.3),   # flagged prop
    ])
    recs = rec.build_recommendations(disc)
    s = rec.summarize(recs)
    assert s["by_risk"][rec.FLAGGED] >= 1
    assert s["total_stake_usd"] == s["total"] * 5.0
