"""Send a Telegram smoke-test message.

Usage:
    python send_test_telegram.py
    python send_test_telegram.py --message "TransTrack Telegram test"

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import List, Optional

from telegram_notify import send_telegram_message


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a Telegram smoke-test message")
    parser.add_argument(
        "--message",
        help="Custom message text. Defaults to a timestamped TransTrack test message.",
    )
    return parser.parse_args(argv)


def build_default_message() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"TransTrack Telegram test OK\nTime: {now}"


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    text = args.message or build_default_message()
    send_telegram_message(text)
    print("Telegram test message sent.")


if __name__ == "__main__":
    main()