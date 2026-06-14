# telegram_notify.py
from __future__ import annotations
import os
import requests
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")

    url = TELEGRAM_API.format(token=token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
    resp.raise_for_status()

if __name__ == "__main__":
    # 本地快速测试：
    # 运行前请确保已设置环境变量：
    # export TELEGRAM_BOT_TOKEN="xxx"
    # export TELEGRAM_CHAT_ID="xxx"

    test_message = "✅ Telegram test message from kingtrans tracker."
    try:
        send_telegram_message(test_message)
        print("Message sent successfully.")
    except Exception as e:
        print(f"Failed to send message: {e}")