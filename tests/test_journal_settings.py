from app.routers import journal as journal_router
from app.services import journal_settings


def _mock_accounts(monkeypatch, accounts):
    async def fake_get_accounts():
        return accounts
    monkeypatch.setattr(journal_settings.tastytrade_client, "get_accounts", fake_get_accounts)


async def test_settings_default_empty(auth_client, account):
    resp = await auth_client.get("/api/journal/settings")
    assert resp.status_code == 200
    # sync_from_date falls back to the journal epoch so a fresh account can
    # never accidentally pull pre-reset history.
    assert resp.json() == {"tt_account_number": None, "sync_from_date": "2026-07-14"}


async def test_save_and_read_settings(auth_client, account):
    put = await auth_client.put("/api/journal/settings", json={
        "tt_account_number": "5WI41395", "sync_from_date": "2026-06-26"})
    assert put.status_code == 200
    assert put.json()["tt_account_number"] == "5WI41395"
    got = (await auth_client.get("/api/journal/settings")).json()
    assert got["tt_account_number"] == "5WI41395"
    assert got["sync_from_date"] == "2026-06-26"


async def test_tt_accounts_list(auth_client, account, monkeypatch):
    _mock_accounts(monkeypatch, [
        {"account-number": "5WI88894", "nickname": "Individual", "account-type-name": "Individual"},
        {"account-number": "5WI41395", "nickname": "Test", "account-type-name": "Individual"},
    ])
    resp = await auth_client.get("/api/journal/tt-accounts")
    assert resp.status_code == 200
    nums = {a["number"] for a in resp.json()["accounts"]}
    assert nums == {"5WI88894", "5WI41395"}


async def test_sync_uses_configured_account_and_date(auth_client, account, monkeypatch):
    await auth_client.put("/api/journal/settings", json={
        "tt_account_number": "5WI41395", "sync_from_date": "2026-06-26"})

    captured = {}

    async def fake_sync(account_id, account_number, since_date=None):
        captured["number"] = account_number
        captured["since"] = since_date
        return {"synced": 0, "skipped": 0}

    monkeypatch.setattr(journal_router, "sync_fills", fake_sync)
    resp = await auth_client.post("/api/journal/fills/sync")
    assert resp.status_code == 200
    assert captured["number"] == "5WI41395"
    assert captured["since"] == "2026-06-26"


async def test_saving_date_preserves_tt_binding(auth_client, account):
    await auth_client.put("/api/journal/settings", json={
        "tt_account_number": "5WI88894", "sync_from_date": "2026-06-26"})
    # a later save of only the date must NOT wipe the bound account
    await auth_client.put("/api/journal/settings", json={"sync_from_date": "2026-06-29"})
    got = (await auth_client.get("/api/journal/settings")).json()
    assert got["tt_account_number"] == "5WI88894"
    assert got["sync_from_date"] == "2026-06-29"


async def test_resolve_falls_back_to_first_account(account, monkeypatch):
    _mock_accounts(monkeypatch, [{"account-number": "5WI88894"}])
    num = await journal_settings.resolve_tt_account_number(account["id"])
    assert num == "5WI88894"
