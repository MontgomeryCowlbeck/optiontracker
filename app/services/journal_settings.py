"""Journal settings — which Tastytrade account to sync and from what date."""
from typing import Optional

from app.database import fetch_one, get_db
from app.services.tastytrade import tastytrade_client

# The journal epoch — Monty's reset date; nothing earlier is ever pulled.
# An unset sync_from_date falls back to this so a fresh account can never
# accidentally ingest years of pre-reset history.
JOURNAL_EPOCH = "2026-07-14"


async def list_tt_accounts() -> list[dict]:
    """All Tastytrade accounts visible to the configured login."""
    accts = await tastytrade_client.get_accounts()
    return [
        {
            "number": a.get("account-number"),
            "nickname": a.get("nickname"),
            "type": a.get("account-type-name"),
        }
        for a in (accts or [])
        if a.get("account-number")
    ]


async def get_settings(account_id: int) -> dict:
    """Stored settings for an account (empty dict-ish if unset)."""
    row = await fetch_one(
        "SELECT tt_account_number, sync_from_date FROM journal_settings WHERE account_id = ?",
        (account_id,),
    )
    return {
        "tt_account_number": row["tt_account_number"] if row else None,
        "sync_from_date": (row["sync_from_date"] if row and row["sync_from_date"]
                           else JOURNAL_EPOCH),
    }


async def save_settings(account_id: int, tt_account_number: Optional[str],
                        sync_from_date: Optional[str]) -> dict:
    """Upsert settings; returns the stored values."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_settings (account_id, tt_account_number, sync_from_date, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(account_id) DO UPDATE SET
                   tt_account_number = COALESCE(excluded.tt_account_number, journal_settings.tt_account_number),
                   sync_from_date = COALESCE(excluded.sync_from_date, journal_settings.sync_from_date),
                   updated_at = CURRENT_TIMESTAMP""",
            (account_id, tt_account_number, sync_from_date),
        )
        await db.commit()
    return await get_settings(account_id)


async def resolve_tt_account_number(account_id: int) -> Optional[str]:
    """The configured TT account number, else the first account TT returns."""
    settings = await get_settings(account_id)
    if settings["tt_account_number"]:
        return settings["tt_account_number"]
    accts = await tastytrade_client.get_accounts()
    if accts and accts[0].get("account-number"):
        return accts[0]["account-number"]
    return None
