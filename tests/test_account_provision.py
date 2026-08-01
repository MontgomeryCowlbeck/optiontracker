from app.routers import journal as journal_router
from app.database import fetch_all


def _mock_tt(monkeypatch, accts):
    async def fake_list():
        return accts
    monkeypatch.setattr(journal_router, "list_tt_accounts", fake_list)


async def test_provision_creates_one_account_per_tt(auth_client, account, monkeypatch):
    _mock_tt(monkeypatch, [
        {"number": "5WI88894", "nickname": "Individual", "type": "Individual"},
        {"number": "5WI41395", "nickname": "Individual", "type": "Individual"},
    ])
    resp = await auth_client.post("/api/journal/accounts/provision")
    assert resp.status_code == 200
    accts = resp.json()["accounts"]
    assert {a["tt_account_number"] for a in accts} == {"5WI88894", "5WI41395"}
    assert {a["name"] for a in accts} == {"5WI88894", "5WI41395"}
    assert "Test Account" not in {a["name"] for a in accts}  # the seed account was renamed/bound
    # exactly two app accounts for this user
    rows = await fetch_all("SELECT id FROM accounts WHERE user_id = ?", (account["user_id"],))
    assert len(rows) == 2


async def test_provision_is_idempotent(auth_client, account, monkeypatch):
    _mock_tt(monkeypatch, [
        {"number": "5WI88894", "nickname": "Individual", "type": "Individual"},
        {"number": "5WI41395", "nickname": "Individual", "type": "Individual"},
    ])
    await auth_client.post("/api/journal/accounts/provision")
    await auth_client.post("/api/journal/accounts/provision")
    rows = await fetch_all("SELECT id FROM accounts WHERE user_id = ?", (account["user_id"],))
    assert len(rows) == 2  # no duplicates on re-run


async def test_provision_no_tt_accounts_400(auth_client, account, monkeypatch):
    _mock_tt(monkeypatch, [])
    resp = await auth_client.post("/api/journal/accounts/provision")
    assert resp.status_code == 400
