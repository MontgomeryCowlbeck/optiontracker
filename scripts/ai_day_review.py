#!/usr/bin/env python3
"""Nightly AI day-review cron (host-side, the watchtower pattern).

Asks the running app to generate + store today's review via
POST /api/journal/ai/day-review/{date} for every provisioned account.
The app does the work (numbers from code, prose from claude -p); this script
just triggers it after the trading day, so reviews appear without a click.

Cron (kaiju, after EOD collectors): 30 21 * * 1-5  (21:30 UTC = 5:30pm ET)
  30 21 * * 1-5 /projects/quant/venv/bin/python /projects/quant/port_tracker/scripts/ai_day_review.py >> /data/structured/tracker/ai_review_cron.log 2>&1

Auth: an API key in /projects/quant/.secrets/tracker_api_key.txt (create one in
the app: Settings -> API keys) or TRACKER_API_KEY env. Base URL via TRACKER_URL
(default http://127.0.0.1:8600). A day with no trades is skipped, not an error.
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = os.environ.get("TRACKER_URL", "http://127.0.0.1:8600").rstrip("/")
KEY_FILE = Path("/projects/quant/.secrets/tracker_api_key.txt")


def api_key() -> str:
    key = os.environ.get("TRACKER_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        sys.exit("no API key: set TRACKER_API_KEY or create .secrets/tracker_api_key.txt")
    return key


def main() -> None:
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    headers = {"X-API-Key": api_key()}
    stamp = datetime.now().isoformat(timespec="seconds")
    r = requests.post(f"{BASE}/api/journal/ai/day-review/{today}",
                      headers=headers, timeout=300)
    if r.status_code == 503 and "nothing to review" in r.text:
        print(f"{stamp} {today}: no trades — skipped")
        return
    r.raise_for_status()
    print(f"{stamp} {today}: review stored ({len(r.json().get('ai_feedback', ''))} chars)")


if __name__ == "__main__":
    main()
