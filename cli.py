"""Command-line interface for Kingtrans tracker.

Features:
- Query one or multiple tracking numbers (via --tracking or --batch file)
- Persist state in JSON and compute diffs
- Pretty or JSON output; optional CSV export for newly added items
- Optional loop mode with interval (simple scheduler)

Examples:
    # Single run, pretty output
    python cli.py --tracking 1ZW1008Y6816279460

    # Multiple numbers + JSON output
    python cli.py --tracking 1ZW... 1ZB... --json

    # From file (one tracking per line), export newly added items to CSV
    python cli.py --batch tracklist.txt --csv added.csv

    # Loop every 120 minutes
    python cli.py --batch tracklist.txt --loop --interval 120
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterable, List

from kingtrans_client import KingtransClient
from storage import JsonStateStore, diff_result_pretty
from telegram_notify import send_telegram_message

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kingtrans tracking CLI")

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tracking", nargs="+", help="One or more tracking numbers")
    src.add_argument("--batch", type=str, help="File path with one tracking number per line")

    p.add_argument("--state-dir", default="state", help="Directory to store JSON state snapshots")
    p.add_argument("--language", default="zh", choices=["zh", "en"], help="Request language")
    p.add_argument("--max-num", type=int, default=10, help="Max rows for list request")
    p.add_argument("--retries", type=int, default=3, help="HTTP retry count")
    p.add_argument("--timeout-connect", type=float, default=5.0, help="Connect timeout seconds")
    p.add_argument("--timeout-read", type=float, default=15.0, help="Read timeout seconds")
    p.add_argument("--notify-telegram", action="store_true", help="Send Telegram message on updates")

    out = p.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="Print results as JSON lines")
    out.add_argument("--pretty", action="store_true", help="Pretty text output (default)")

    p.add_argument("--csv", type=str, help="Append newly added items to CSV file")
    p.add_argument("--sleep", type=float, default=1.5, help="Seconds to sleep between numbers (rate limit)")

    loop = p.add_argument_group("loop")
    loop.add_argument("--loop", action="store_true", help="Run in a loop")
    loop.add_argument("--interval", type=float, default=120.0, help="Loop interval in minutes")

    p.add_argument("--verbose", "-v", action="count", default=0, help="Increase verbosity (-v, -vv)")

    args = p.parse_args(argv)
    if not args.pretty and not args.json:
        args.pretty = True
    return args


def setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def iter_trackings(args: argparse.Namespace) -> Iterable[str]:
    if args.tracking:
        for t in args.tracking:
            yield t.strip()
    elif args.batch:
        p = Path(args.batch)
        if not p.exists():
            raise SystemExit(f"Batch file not found: {p}")
        for line in p.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                yield t


def run_once(args: argparse.Namespace) -> None:
    client = KingtransClient(
        language=args.language,
        max_num=args.max_num,
        retries=args.retries,
        timeout=(args.timeout_connect, args.timeout_read),
    )
    store = JsonStateStore(args.state_dir)

    # Deduplicate while preserving order
    seen = set()
    numbers = [t for t in iter_trackings(args) if not (t in seen or seen.add(t))]

    for i, tn in enumerate(numbers, 1):
        logging.getLogger("cli").info(f"[{i}/{len(numbers)}] Query {tn}")
        try:
            res = client.query(tn)
            diff = store.update_with_result(tn, res)
            if args.notify_telegram and diff.added_items:
                # diff.added_items in this project is a List[dict] with keys: sdate/place/intro
                lines = [f"📦 {tn} updates ({len(diff.added_items)} new):"]
                for it in diff.added_items:
                    sdate = it.get("sdate", "")
                    place = it.get("place", "")
                    intro = it.get("intro", "")
                    lines.append(f"- {sdate} | {place} | {intro}")
                send_telegram_message("\n".join(lines))

            if args.json:
                # JSON lines: one per tracking number
                payload = {
                    "tracking_no": tn,
                    "summary_changed": diff.summary_changed,
                    "added_items": diff.added_items or [],
                    "summary": res.summary.__dict__,
                }
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print("=" * 70)
                print(f"Tracking: {tn}")
                print(diff_result_pretty(diff))

            if args.csv and diff.added_items:
                from storage import export_items_to_csv
                # Export only NEWLY added items
                export_items_to_csv(diff.added_items, args.csv)
        except Exception as e:
            logging.getLogger("cli").error(f"{tn}: {e}")
        # Rate limit between numbers
        if i != len(numbers):
            time.sleep(args.sleep)


def main(argv: List[str]) -> None:
    args = parse_args(argv)
    setup_logging(args.verbose)

    if args.loop:
        logger = logging.getLogger("cli")
        logger.info(f"Entering loop mode every {args.interval} minutes")
        interval_sec = max(30.0, args.interval * 60.0)
        while True:
            start = time.time()
            run_once(args)
            elapsed = time.time() - start
            # Sleep remaining interval
            remaining = interval_sec - elapsed
            if remaining > 0:
                time.sleep(remaining)
    else:
        run_once(args)


if __name__ == "__main__":
    main(sys.argv[1:])
