"""Daily World-Cup group-stage recommendations — the script Hermes runs once a day.

ADVISORY ONLY. This discovers every Group-X market (winners, qualifiers, props) across all 12
groups, builds flat-$5 recommendations sorted into risk tiers, and prints a report you read and
place yourself. It never trades, never touches position state, and needs no wallet.

Usage:
    python scripts/daily_recommender.py                 # human report to stdout
    python scripts/daily_recommender.py --json          # machine-readable JSON (for Hermes/logs)
    python scripts/daily_recommender.py --out report.md # also write the report to a file
    python scripts/daily_recommender.py --max-risk medium  # hide HIGH/FLAGGED noise
    python scripts/daily_recommender.py --venue polymarket # discover against the real venue

Requires SIMMER_API_KEY (read-only discovery). Cron (daily, 13:00 UTC):
    0 13 * * *  python scripts/daily_recommender.py --out reports/$(date +%F).md
"""

import argparse
import json
import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import recommendations as rec          # noqa: E402
from categories import discover_all    # noqa: E402

_RISK_FLOOR = {"low": (rec.LOW,),
               "medium": (rec.LOW, rec.MEDIUM),
               "high": (rec.LOW, rec.MEDIUM, rec.HIGH),
               "all": (rec.LOW, rec.MEDIUM, rec.HIGH, rec.FLAGGED)}


def _filter_by_risk(recs_by_group, max_risk):
    allowed = set(_RISK_FLOOR[max_risk])
    return {g: [r for r in recs if r["risk"] in allowed] for g, recs in recs_by_group.items()}


def _make_client(venue, live):
    from simmer_sdk import SimmerClient
    return SimmerClient.from_env(venue=venue) if live else \
        SimmerClient.from_env(venue=venue, live=False)


def run(venue="sim", live=False, as_json=False, out=None, max_risk="all"):
    cfg = rec.config()
    today = _date.today().isoformat()
    client = _make_client(venue, live)

    disc = discover_all(client)
    recs_by_group = rec.build_recommendations(disc, cfg)
    if max_risk != "all":
        recs_by_group = _filter_by_risk(recs_by_group, max_risk)

    if as_json:
        payload = rec.to_json(recs_by_group, cfg, date=today)
        payload["discovery_counts"] = disc["counts"]
        text = json.dumps(payload, indent=2, default=str)
    else:
        text = rec.format_report(recs_by_group, cfg, date=today)

    print(text)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as f:
            f.write(text + "\n")
        print(f"\n[written to {out}]", file=sys.stderr)
    return recs_by_group


def main():
    ap = argparse.ArgumentParser(description="Daily WC group-stage recommendations (advisory only)")
    ap.add_argument("--venue", default=os.getenv("TRADING_VENUE", "sim"),
                    help="sim (default) or polymarket — discovery source")
    ap.add_argument("--live", action="store_true",
                    help="use the live venue client for discovery (still read-only; no trades)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--out", help="also write the report/JSON to this file")
    ap.add_argument("--max-risk", choices=list(_RISK_FLOOR), default="all",
                    help="hide tiers riskier than this (low|medium|high|all)")
    args = ap.parse_args()
    run(venue=args.venue, live=args.live, as_json=args.json, out=args.out, max_risk=args.max_risk)


if __name__ == "__main__":
    main()
