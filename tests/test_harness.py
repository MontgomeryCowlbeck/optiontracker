from app.database import fetch_all


async def test_init_db_creates_core_tables():
    rows = await fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    names = {r["name"] for r in rows}
    assert "users" in names
    assert "accounts" in names


async def test_account_fixture_seeds_account(account):
    rows = await fetch_all("SELECT id, user_id FROM accounts WHERE id = ?",
                           (account["id"],))
    assert len(rows) == 1
    assert rows[0]["user_id"] == account["user_id"]


async def test_auth_client_health(auth_client):
    resp = await auth_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
