import os
import tempfile

# Must be set BEFORE importing any app module (config reads env at import time).
_TMP_DB = os.path.join(tempfile.gettempdir(), "optiontracker_test.db")
os.environ["DATABASE_PATH"] = _TMP_DB
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-32+chars"
os.environ["TASTYTRADE_CLIENT_ID"] = ""
os.environ["TASTYTRADE_CLIENT_SECRET"] = ""
os.environ["TASTYTRADE_REFRESH_TOKEN"] = ""
# Never let a test post to the real Discord webhook.
os.environ["TRACKER_DISABLE_DISCORD"] = "1"

import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport

from app.database import init_db, get_db
from app.main import app
from app.auth.dependencies import get_auth_context_with_api_key, AuthContext


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Fresh database for every test."""
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    await init_db()
    yield
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


@pytest_asyncio.fixture
async def account():
    """Seed one user + one default account; return the account dict."""
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO users (username, email, hashed_password) VALUES (?, ?, ?)",
            ("tester", "tester@example.com", "x"),
        )
        user_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO accounts (user_id, name, is_default) VALUES (?, ?, 1)",
            (user_id, "Test Account"),
        )
        account_id = cur.lastrowid
        await db.commit()
    return {"id": account_id, "user_id": user_id, "name": "Test Account"}


@pytest_asyncio.fixture
async def auth_client(account):
    """httpx AsyncClient against the ASGI app with auth overridden to `account`."""
    user = {"id": account["user_id"], "username": "tester",
            "email": "tester@example.com", "is_active": 1}

    def _override():
        return AuthContext(user, account)

    app.dependency_overrides[get_auth_context_with_api_key] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
