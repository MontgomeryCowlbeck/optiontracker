"""Scheduler for the trade journal: pull fills, poll greeks, snapshot equity.

All jobs resolve the Tastytrade account directly via the API (single-account
model) and the app's first account, so no sync-state table is required."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
from zoneinfo import ZoneInfo
from datetime import datetime

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ET = ZoneInfo("America/New_York")
scheduler = AsyncIOScheduler(timezone=ET)


async def _resolve_accounts():
    """Return [(app_account_id, tt_account_number), ...] for every account with a
    TT mapping. Scheduled jobs must sweep ALL accounts — resolving only the default
    left every other account's fills/marks/net-liq permanently stale."""
    from app.database import fetch_all
    from app.services.journal_settings import resolve_tt_account_number

    if not settings.tastytrade_configured:
        return []
    out = []
    for account in await fetch_all("SELECT id FROM accounts ORDER BY is_default DESC, id"):
        number = await resolve_tt_account_number(account["id"])
        if number:
            out.append((account["id"], number))
    return out


async def _sync_from_date(account_id):
    from app.services.journal_settings import get_settings
    return (await get_settings(account_id)).get("sync_from_date")


async def scheduled_fills_sync():
    """Pull Tastytrade option fills into the inbox, for every mapped account."""
    from app.services.fills_sync import sync_fills
    for account_id, number in await _resolve_accounts():
        try:
            since = await _sync_from_date(account_id)
            result = await sync_fills(account_id, number, since_date=since)
            logger.info("Scheduled fills sync: account=%d synced=%d skipped=%d",
                        account_id, result["synced"], result["skipped"])
        except Exception as e:
            logger.error("Scheduled fills sync failed: account=%d %s", account_id, e)


async def scheduled_greek_poll():
    """Tight market-hours poll capturing entry/exit greeks for open positions,
    for every mapped account."""
    from app.services.greek_poller import poll_greeks_once
    for account_id, number in await _resolve_accounts():
        try:
            await poll_greeks_once(account_id, number)
        except Exception as e:
            logger.warning("Scheduled greek poll failed: account=%d %s", account_id, e)


async def scheduled_tt_balance_snapshot():
    """Daily equity-curve point sourced from Tastytrade net-liq, per account."""
    from app.database import fetch_one, get_db
    from app.services.tastytrade import tastytrade_client

    accounts = await _resolve_accounts()
    if not accounts:
        return

    spy_price = 0
    try:
        spy_quote = await tastytrade_client.get_underlying_quote("SPY")
        spy_price = (spy_quote or {}).get("last", 0) or 0
    except Exception as e:
        logger.warning("TT balance snapshot: SPY price fetch failed: %s", e)

    today = datetime.now(ET).date().isoformat()
    for account_id, number in accounts:
        try:
            balances = await tastytrade_client.get_balances(number)
            if not balances:
                continue
            net_liq = float(balances.get("net-liquidating-value") or 0)
            cash = float(balances.get("cash-balance") or 0)

            existing = await fetch_one(
                "SELECT id FROM benchmark_snapshots WHERE snapshot_date = ? AND account_id = ?",
                (today, account_id),
            )
            async with get_db() as db:
                if existing:
                    await db.execute(
                        "UPDATE benchmark_snapshots SET spy_price=?, portfolio_value=?, cash_balance=? WHERE id=?",
                        (spy_price, net_liq, cash, existing["id"]),
                    )
                else:
                    await db.execute(
                        """INSERT INTO benchmark_snapshots
                           (account_id, snapshot_date, spy_price, portfolio_value, cash_balance)
                           VALUES (?, ?, ?, ?, ?)""",
                        (account_id, today, spy_price, net_liq, cash),
                    )
                await db.commit()
            logger.info("TT balance snapshot: account=%d net_liq=%.2f", account_id, net_liq)
        except Exception as e:
            logger.error("Scheduled TT balance snapshot failed: account=%d %s", account_id, e)


async def scheduled_vol_snapshots():
    """Nightly vol-watchlist snapshot (IV/RV/term/straddle per marked name) —
    the record that IV rank and IV-crush forward review are built on."""
    from app.database import fetch_all
    from app.services.strangle import snapshot_all
    try:
        accounts = await fetch_all("SELECT DISTINCT account_id FROM vol_watchlist")
        for a in accounts:
            result = await snapshot_all(a["account_id"])
            logger.info("Vol snapshots account=%d %s", a["account_id"], result)
    except Exception as e:
        logger.error("Scheduled vol snapshots failed: %s", e)


async def scheduled_eod_sweep():
    """End-of-day journal sweep (after the post-close sync): today's closes +
    anything still unjournaled, as one Discord card per account. Silent when
    there is nothing to say."""
    from app.database import fetch_all
    from app.services import notify
    from app.services.journal_analytics import journal_debt

    today = datetime.now(ET).date().isoformat()
    for account_id, _number in await _resolve_accounts():
        try:
            rows = await fetch_all(
                "SELECT * FROM journal_trades WHERE account_id = ?", (account_id,))
            closed_today = [r for r in rows if r["status"] == "closed"
                            and str(r.get("exit_at") or "")[:10] == today]
            await notify.notify_eod(closed_today, journal_debt(rows)["items"])
        except Exception as e:
            logger.error("EOD sweep failed: account=%d %s", account_id, e)


async def scheduled_research_refresh():
    """Nightly DD snapshot for every research-watchlist name (per account).
    Feeds the Research tab's instant loads, trend history, and condition flags."""
    from app.database import fetch_all
    from app.services.research import daily_update_all, refresh_all
    try:
        accounts = await fetch_all(
            "SELECT DISTINCT account_id FROM research_watchlist")
        for a in accounts:
            result = await refresh_all(a["account_id"])
            logger.info("Research refresh account=%d ok=%d failed=%s",
                        a["account_id"], result["refreshed"], result["failed"])
            # After the numbers land, write the per-name AI daily cards
            # (skips cleanly when the claude CLI is absent).
            upd = await daily_update_all(a["account_id"])
            logger.info("Research daily updates account=%d %s", a["account_id"], upd)
    except Exception as e:
        logger.error("Scheduled research refresh failed: %s", e)


def setup_scheduler():
    """Configure and start the scheduler."""
    if scheduler.running:
        logger.info("Scheduler already running")
        return

    # Pull fills every 30 min during market hours + post-close at 5pm ET
    scheduler.add_job(
        scheduled_fills_sync,
        CronTrigger(minute="*/30", hour="9-16", day_of_week="mon-fri", timezone=ET),
        id="fills_sync_intraday", replace_existing=True,
    )
    scheduler.add_job(
        scheduled_fills_sync,
        CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=ET),
        id="fills_sync_postclose", replace_existing=True,
    )

    # Greek poller: every 2 min during market hours for entry/exit delta capture
    scheduler.add_job(
        scheduled_greek_poll,
        CronTrigger(minute="*/2", hour="9-16", day_of_week="mon-fri", timezone=ET),
        id="greek_poll_intraday", replace_existing=True,
    )

    # Daily equity-curve point from Tastytrade net-liq (4:20 PM ET)
    scheduler.add_job(
        scheduled_tt_balance_snapshot,
        CronTrigger(hour=16, minute=20, day_of_week="mon-fri", timezone=ET),
        id="tt_balance_snapshot_daily", replace_existing=True,
    )

    # Nightly research-watchlist snapshot (5:30 PM ET, after the close settles)
    scheduler.add_job(
        scheduled_research_refresh,
        CronTrigger(hour=17, minute=30, day_of_week="mon-fri", timezone=ET),
        id="research_refresh_nightly", replace_existing=True,
    )

    # EOD journal sweep to Discord (5:10 PM ET, after the post-close sync)
    scheduler.add_job(
        scheduled_eod_sweep,
        CronTrigger(hour=17, minute=10, day_of_week="mon-fri", timezone=ET),
        id="eod_journal_sweep", replace_existing=True,
    )

    # Nightly vol-watchlist snapshot (5:40 PM ET, after research refresh kicks off)
    scheduler.add_job(
        scheduled_vol_snapshots,
        CronTrigger(hour=17, minute=40, day_of_week="mon-fri", timezone=ET),
        id="vol_snapshots_nightly", replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started: fills sync, greek poll, equity snapshot, "
                "research refresh, EOD journal sweep")


def shutdown_scheduler():
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown")
