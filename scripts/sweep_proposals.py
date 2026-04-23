"""CLI runner for the Strategy Proposal ledger expiry sweep.

Intended for cron or systemd timers. Exits 0 on success; prints a summary of
how many proposals were swept.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services import strategy_proposal_ledger as ledger  # noqa: E402
from services.db_service import init_db  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep expired Strategy Inbox proposals")
    parser.add_argument(
        "--min-interval-seconds",
        type=int,
        default=0,
        help="Skip sweep if the previous run occurred less than this many seconds ago",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    init_db()
    outcome = ledger.run_startup_sweep(
        min_interval_seconds=int(args.min_interval_seconds),
    )
    expired = outcome.get("expired_ids") or []
    if outcome.get("skipped"):
        print("sweep skipped (throttled)")
        return 0
    print(f"swept {len(expired)} expired proposal(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
