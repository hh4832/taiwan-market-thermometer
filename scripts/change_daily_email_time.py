from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "email_notification.json"


def valid_time(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


def main() -> int:
    if not CONFIG_PATH.exists():
        raise SystemExit("Email configuration not found. Run setup_daily_email.bat first.")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    current = str(config.get("scheduled_time", "20:00"))
    value = input(f"New daily email time in HH:MM format (current {current}): ").strip()
    if not valid_time(value):
        raise SystemExit("Invalid time. Example: 18:30 or 20:00.")
    config["scheduled_time"] = value
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Email time updated to {value}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
