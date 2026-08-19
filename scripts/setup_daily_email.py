from __future__ import annotations

from getpass import getpass
import json
from pathlib import Path
import re

import keyring

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "email_notification.json"
KEYRING_SERVICE = "taiwan-market-thermometer"


def valid_gmail(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@gmail\.com", value.strip(), flags=re.IGNORECASE))


def main() -> int:
    print("\n=== 臺股市場溫度計：每日Email設定 ===")
    sender = input("Gmail寄件地址：").strip()
    if not valid_gmail(sender):
        raise SystemExit("寄件地址必須是有效的@gmail.com地址。")
    recipient = input(f"收件地址（直接Enter表示 {sender}）：").strip() or sender
    app_password = getpass("Gmail應用程式密碼（輸入時不顯示）：").replace(" ", "")
    finlab_token = getpass("FinLab Token（輸入時不顯示）：").strip()
    if len(app_password) != 16:
        raise SystemExit("Gmail應用程式密碼應為16碼；請勿輸入一般Google密碼。")
    if not finlab_token:
        raise SystemExit("FinLab Token不可空白。")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "sender_email": sender,
                "recipient_email": recipient,
                "timezone": "Asia/Taipei",
                "scheduled_time": "20:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    keyring.set_password(KEYRING_SERVICE, "gmail_app_password", app_password)
    keyring.set_password(KEYRING_SERVICE, "finlab_token", finlab_token)
    print("設定完成：密碼與Token已存入目前Windows帳號的認證管理員。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
