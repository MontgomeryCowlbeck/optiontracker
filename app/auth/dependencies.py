"""FastAPI dependencies for authentication."""
import hashlib
import secrets
from datetime import datetime
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from app.auth.security import decode_token
from app.database import fetch_one, get_db

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


def generate_api_key() -> str:
    """Generate a cryptographically secure API key."""
    return secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


async def validate_api_key(key: str) -> Optional[dict]:
    """
    Validate an API key and return the associated account.
    Updates last_used_at on successful validation.
    """
    key_hash = hash_api_key(key)

    api_key = await fetch_one(
        """SELECT ak.*, a.user_id
           FROM api_keys ak
           JOIN accounts a ON ak.account_id = a.id
           WHERE ak.key_hash = ? AND ak.is_active = 1""",
        (key_hash,)
    )

    if not api_key:
        return None

    # Check expiration
    if api_key.get("expires_at"):
        expires_at = api_key["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at < datetime.now():
            return None

    # Update last_used_at
    async with get_db() as db:
        await db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (datetime.now().isoformat(), api_key["id"])
        )
        await db.commit()

    return api_key


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Get the current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise credentials_exception

    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise credentials_exception

    user = await fetch_one(
        "SELECT id, username, email, is_active, created_at FROM users WHERE id = ?",
        (user_id,)
    )

    if user is None:
        raise credentials_exception

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    return user


async def get_current_account(
    current_user: dict = Depends(get_current_user),
    x_account_id: Optional[str] = Header(None, alias="X-Account-ID")
) -> dict:
    """Get the current account context. Uses X-Account-ID header or default account."""

    # Convert header to int, ignoring invalid values
    account_id = None
    if x_account_id:
        try:
            account_id = int(x_account_id)
        except (ValueError, TypeError):
            # Invalid header value, ignore it
            pass

    if account_id:
        # Verify account belongs to user
        account = await fetch_one(
            "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
            (account_id, current_user["id"])
        )
        if not account:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not found or access denied"
            )
        return dict(account)

    # Fall back to default account
    account = await fetch_one(
        "SELECT * FROM accounts WHERE user_id = ? AND is_default = 1",
        (current_user["id"],)
    )

    if not account:
        # Get first account if no default
        account = await fetch_one(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1",
            (current_user["id"],)
        )

    if not account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No accounts found. Please create an account."
        )

    return dict(account)


class AuthContext:
    """Combined auth context with user and account info."""
    def __init__(self, user: dict, account: dict):
        self.user = user
        self.account = account
        self.user_id = user["id"]
        self.account_id = account["id"]


async def get_auth_context(
    current_user: dict = Depends(get_current_user),
    current_account: dict = Depends(get_current_account)
) -> AuthContext:
    """Get combined authentication context."""
    return AuthContext(current_user, current_account)


async def get_auth_context_with_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    x_account_id_header: Optional[str] = Header(None, alias="X-Account-ID")
) -> AuthContext:
    """
    Get auth context, supporting both API key and JWT authentication.
    Checks X-API-Key header first, then falls back to JWT Bearer token.
    """
    # Convert header to int, ignoring invalid values
    x_account_id = None
    if x_account_id_header:
        try:
            x_account_id = int(x_account_id_header)
        except (ValueError, TypeError):
            pass

    # Try API key first
    if x_api_key:
        api_key_data = await validate_api_key(x_api_key)
        if api_key_data:
            # Get the account
            account = await fetch_one(
                "SELECT * FROM accounts WHERE id = ?",
                (api_key_data["account_id"],)
            )
            if account:
                # Get the user
                user = await fetch_one(
                    "SELECT id, username, email, is_active, created_at FROM users WHERE id = ?",
                    (api_key_data["user_id"],)
                )
                if user and user["is_active"]:
                    return AuthContext(dict(user), dict(account))

        # Invalid API key
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fall back to JWT
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authentication provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise credentials_exception

    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise credentials_exception

    user = await fetch_one(
        "SELECT id, username, email, is_active, created_at FROM users WHERE id = ?",
        (user_id,)
    )

    if user is None:
        raise credentials_exception

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    # Get account (using X-Account-ID header or default)
    if x_account_id:
        account = await fetch_one(
            "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
            (x_account_id, user["id"])
        )
        if not account:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not found or access denied"
            )
    else:
        # Fall back to default account
        account = await fetch_one(
            "SELECT * FROM accounts WHERE user_id = ? AND is_default = 1",
            (user["id"],)
        )
        if not account:
            account = await fetch_one(
                "SELECT * FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1",
                (user["id"],)
            )

    if not account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No accounts found. Please create an account."
        )

    return AuthContext(dict(user), dict(account))
