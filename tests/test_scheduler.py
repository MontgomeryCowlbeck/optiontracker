"""Scheduler jobs must sweep EVERY mapped account, not just the default.

Regression for 2026-07-23: all three TT jobs resolved only the default account,
so a second account's fills, marks, and net-liq went permanently stale (the
Positions tab showed day-old P&L on a red day)."""
import pytest

from app.database import get_db
from app.config import Settings
from app.services import scheduler
import app.services.journal_settings as journal_settings


@pytest.fixture
def tt_configured(monkeypatch):
    """Force settings.tastytrade_configured True (a property — patch the class)
    and kill the live-API fallback for accounts with no stored TT number."""
    monkeypatch.setattr(Settings, "tastytrade_configured", property(lambda self: True))

    async def no_accounts():
        return []

    monkeypatch.setattr(journal_settings.tastytrade_client, "get_accounts", no_accounts)


async def _seed_two_accounts():
    """Two mapped accounts on top of whatever init_db auto-created (the admin
    default account has no journal_settings row and must be skipped)."""
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO users (username, email, hashed_password) VALUES (?, ?, ?)",
            ("tester", "tester@example.com", "x"),
        )
        user_id = cur.lastrowid
        ids = []
        for name in ("5WI88894", "5WI41395"):
            cur = await db.execute(
                "INSERT INTO accounts (user_id, name, is_default) VALUES (?, ?, 0)",
                (user_id, name),
            )
            ids.append(cur.lastrowid)
            await db.execute(
                "INSERT INTO journal_settings (account_id, tt_account_number) VALUES (?, ?)",
                (cur.lastrowid, name),
            )
        await db.commit()
    return ids


@pytest.mark.asyncio
async def test_resolve_accounts_returns_all_mapped(tt_configured):
    ids = await _seed_two_accounts()
    resolved = await scheduler._resolve_accounts()
    assert resolved == [(ids[0], "5WI88894"), (ids[1], "5WI41395")]


@pytest.mark.asyncio
async def test_resolve_accounts_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(Settings, "tastytrade_configured", property(lambda self: False))
    await _seed_two_accounts()
    assert await scheduler._resolve_accounts() == []


@pytest.mark.asyncio
async def test_greek_poll_sweeps_every_account(tt_configured, monkeypatch):
    ids = await _seed_two_accounts()
    polled = []

    async def fake_poll(account_id, number):
        polled.append((account_id, number))
        return {"entries": 0, "interims": 0, "exits": 0}

    import app.services.greek_poller as gp
    monkeypatch.setattr(gp, "poll_greeks_once", fake_poll)
    await scheduler.scheduled_greek_poll()
    assert polled == [(ids[0], "5WI88894"), (ids[1], "5WI41395")]


@pytest.mark.asyncio
async def test_fills_sync_sweeps_every_account_and_survives_one_failure(tt_configured, monkeypatch):
    ids = await _seed_two_accounts()
    synced = []

    async def fake_sync(account_id, number, since_date=None):
        if account_id == ids[0]:
            raise RuntimeError("boom")
        synced.append((account_id, number))
        return {"synced": 1, "skipped": 0}

    import app.services.fills_sync as fs
    monkeypatch.setattr(fs, "sync_fills", fake_sync)
    await scheduler.scheduled_fills_sync()
    # first mapped account blew up; the sweep must still reach the second
    assert synced == [(ids[1], "5WI41395")]
