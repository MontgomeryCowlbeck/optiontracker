#!/usr/bin/env python3
"""Re-run the strategy classifier over every journal trade's opening fills and
update `direction`. One-off after classifier changes; safe to re-run anytime.

Usage: DATABASE_PATH=/data/structured/tracker/options.db \
       python scripts/reclassify_trades.py
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.calculations import classify_strategy  # noqa: E402


def main() -> None:
    db_path = os.environ.get("DATABASE_PATH", "/data/structured/tracker/options.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    changed = 0
    for t in con.execute("SELECT id, direction FROM journal_trades").fetchall():
        fills = [dict(r) for r in con.execute(
            "SELECT * FROM tt_fills WHERE journal_trade_id = ? AND is_opening = 1",
            (t["id"],)).fetchall()]
        if not fills:
            continue
        new = classify_strategy(fills)
        if new != t["direction"]:
            con.execute(
                "UPDATE journal_trades SET direction = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?", (new, t["id"]))
            print(f"  #{t['id']}: {t['direction']} -> {new}")
            changed += 1
    con.commit()
    con.close()
    print(f"reclassified {changed} trade(s)")


if __name__ == "__main__":
    main()
