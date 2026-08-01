"""Authentication and account management router."""
from fastapi import APIRouter, HTTPException, status, Depends, Request, Body
from typing import List, Optional

from app.auth.models import (
    UserCreate, UserLogin, UserResponse, Token,
    PasswordChange, AccountCreate, AccountUpdate, AccountResponse
)
from app.auth.security import verify_password, get_password_hash, create_access_token
from app.auth.dependencies import (
    get_current_user, get_current_account, get_auth_context, AuthContext,
    get_auth_context_with_api_key, generate_api_key, hash_api_key
)
from app.database import fetch_one, fetch_all, get_db
from app.models import APIKeyCreate, APIKeyResponse, APIKeyCreateResponse, APIKeyListResponse
from app.rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Registration is disabled - accounts are created by admin only
# @router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
# async def register(user_data: UserCreate):
#     """Register a new user."""
#     ...


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login(request: Request, credentials: UserLogin):
    """Login and get access token. Rate limited to 5 attempts per minute."""
    # Find user by username
    user = await fetch_one(
        "SELECT * FROM users WHERE username = ?",
        (credentials.username,)
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify password
    if not verify_password(credentials.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is active
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    # Get user's accounts
    accounts = await fetch_all(
        "SELECT * FROM accounts WHERE user_id = ?",
        (user["id"],)
    )

    # Find default account
    default_account = next(
        (acc for acc in accounts if acc["is_default"]),
        accounts[0] if accounts else None
    )

    # Create access token
    access_token = create_access_token(data={"sub": str(user["id"])})

    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            email=user["email"],
            is_active=user["is_active"],
            created_at=user["created_at"]
        ),
        accounts=[AccountResponse(**acc) for acc in accounts],
        active_account_id=default_account["id"] if default_account else None
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse(**current_user)


@router.put("/password")
async def change_password(
    password_data: PasswordChange,
    current_user: dict = Depends(get_current_user)
):
    """Change user password."""
    # Get user with password hash
    user = await fetch_one(
        "SELECT hashed_password FROM users WHERE id = ?",
        (current_user["id"],)
    )

    # Verify current password
    if not verify_password(password_data.current_password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Update password
    new_hash = get_password_hash(password_data.new_password)
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET hashed_password = ? WHERE id = ?",
            (new_hash, current_user["id"])
        )
        await db.commit()

    return {"message": "Password updated successfully"}


@router.delete("/user")
async def delete_user_account(
    current_user: dict = Depends(get_current_user)
):
    """Delete the current user's account and all associated data."""
    user_id = current_user["id"]

    async with get_db() as db:
        # Get all account IDs for this user
        accounts = await fetch_all(
            "SELECT id FROM accounts WHERE user_id = ?",
            (user_id,)
        )
        account_ids = [acc["id"] for acc in accounts]

        if account_ids:
            placeholders = ",".join("?" * len(account_ids))

            # Delete all trades for user's accounts
            await db.execute(
                f"DELETE FROM trades WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all stock positions
            await db.execute(
                f"DELETE FROM stock_positions WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all cash transactions
            await db.execute(
                f"DELETE FROM cash_transactions WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all prospects
            await db.execute(
                f"DELETE FROM prospects WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all benchmark snapshots
            await db.execute(
                f"DELETE FROM benchmark_snapshots WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all strategy groups
            await db.execute(
                f"DELETE FROM strategy_groups WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all stock watchlist items
            await db.execute(
                f"DELETE FROM stock_watchlist WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

            # Delete all API keys
            await db.execute(
                f"DELETE FROM api_keys WHERE account_id IN ({placeholders})",
                tuple(account_ids)
            )

        # Delete all accounts for this user
        await db.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))

        # Delete the user
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))

        await db.commit()

    return {"message": "User account deleted successfully"}


# Account management endpoints

@router.get("/accounts", response_model=List[AccountResponse])
async def get_accounts(current_user: dict = Depends(get_current_user)):
    """Get all accounts for current user."""
    accounts = await fetch_all(
        "SELECT * FROM accounts WHERE user_id = ? ORDER BY is_default DESC, name",
        (current_user["id"],)
    )
    return [AccountResponse(**acc) for acc in accounts]


@router.post("/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    account_data: AccountCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new account."""
    async with get_db() as db:
        # If this is set as default, unset other defaults
        if account_data.is_default:
            await db.execute(
                "UPDATE accounts SET is_default = 0 WHERE user_id = ?",
                (current_user["id"],)
            )

        cursor = await db.execute(
            """INSERT INTO accounts (user_id, name, description, is_default)
               VALUES (?, ?, ?, ?)""",
            (current_user["id"], account_data.name, account_data.description, account_data.is_default)
        )
        account_id = cursor.lastrowid
        await db.commit()

    account = await fetch_one(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,)
    )
    return AccountResponse(**account)


@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    account_data: AccountUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update an account."""
    # Verify ownership
    account = await fetch_one(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, current_user["id"])
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found"
        )

    async with get_db() as db:
        # If setting as default, unset other defaults first
        if account_data.is_default:
            await db.execute(
                "UPDATE accounts SET is_default = 0 WHERE user_id = ?",
                (current_user["id"],)
            )

        # Build update query
        updates = []
        params = []
        if account_data.name is not None:
            updates.append("name = ?")
            params.append(account_data.name)
        if account_data.description is not None:
            updates.append("description = ?")
            params.append(account_data.description)
        if account_data.is_default is not None:
            updates.append("is_default = ?")
            params.append(account_data.is_default)

        if updates:
            params.append(account_id)
            await db.execute(
                f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
                tuple(params)
            )
            await db.commit()

    account = await fetch_one(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,)
    )
    return AccountResponse(**account)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Delete an account. Cannot delete the last account."""
    # Verify ownership
    account = await fetch_one(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, current_user["id"])
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found"
        )

    # Check if this is the last account
    count = await fetch_one(
        "SELECT COUNT(*) as count FROM accounts WHERE user_id = ?",
        (current_user["id"],)
    )
    if count["count"] <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your only account"
        )

    # Check if account has data
    trades_count = await fetch_one(
        "SELECT COUNT(*) as count FROM trades WHERE account_id = ?",
        (account_id,)
    )
    if trades_count["count"] > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete account with {trades_count['count']} trades. Transfer or delete trades first."
        )

    async with get_db() as db:
        await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

        # If deleted account was default, set another as default
        if account["is_default"]:
            await db.execute(
                """UPDATE accounts SET is_default = 1
                   WHERE user_id = ? AND id != ? LIMIT 1""",
                (current_user["id"], account_id)
            )
        await db.commit()

    return {"message": "Account deleted successfully"}


# API Key management endpoints

@router.post("/api-keys", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreate,
    auth: AuthContext = Depends(get_auth_context)
):
    """
    Create a new API key for the current account.

    The API key is returned only once in the response. Store it securely.
    """
    # Generate the key
    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)

    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO api_keys (account_id, key_hash, name, expires_at)
               VALUES (?, ?, ?, ?)""",
            (auth.account_id, key_hash, key_data.name,
             key_data.expires_at.isoformat() if key_data.expires_at else None)
        )
        key_id = cursor.lastrowid
        await db.commit()

    # Fetch the created key
    created = await fetch_one(
        "SELECT * FROM api_keys WHERE id = ?",
        (key_id,)
    )

    return APIKeyCreateResponse(
        **created,
        key=plain_key  # Return the plain key only once
    )


@router.get("/api-keys", response_model=APIKeyListResponse)
async def list_api_keys(auth: AuthContext = Depends(get_auth_context)):
    """List all API keys for the current account (without the key values)."""
    keys = await fetch_all(
        "SELECT * FROM api_keys WHERE account_id = ? ORDER BY created_at DESC",
        (auth.account_id,)
    )
    return APIKeyListResponse(
        api_keys=[APIKeyResponse(**k) for k in keys],
        count=len(keys)
    )


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: int,
    auth: AuthContext = Depends(get_auth_context)
):
    """Revoke an API key."""
    existing = await fetch_one(
        "SELECT id FROM api_keys WHERE id = ? AND account_id = ?",
        (key_id, auth.account_id)
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    async with get_db() as db:
        await db.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ?",
            (key_id,)
        )
        await db.commit()

    return {"message": "API key revoked successfully"}


# ============================================================
# USER PREFERENCES
# ============================================================

@router.get("/preferences/{pref_key}")
async def get_preference(
    pref_key: str,
    auth: AuthContext = Depends(get_auth_context_with_api_key)
):
    """Get a user preference by key, scoped to user + account."""
    row = await fetch_one(
        "SELECT pref_value FROM user_preferences WHERE user_id = ? AND account_id = ? AND pref_key = ?",
        (auth.user_id, auth.account_id, pref_key)
    )
    return {"key": pref_key, "value": row["pref_value"] if row else None}


@router.put("/preferences/{pref_key}")
async def set_preference(
    pref_key: str,
    body: dict,
    auth: AuthContext = Depends(get_auth_context_with_api_key)
):
    """Set a user preference (upsert), scoped to user + account."""
    value = body.get("value")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO user_preferences (user_id, account_id, pref_key, pref_value, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, account_id, pref_key) DO UPDATE SET
                   pref_value = excluded.pref_value,
                   updated_at = CURRENT_TIMESTAMP""",
            (auth.user_id, auth.account_id, pref_key, value)
        )
        await db.commit()
    return {"key": pref_key, "value": value}


@router.delete("/preferences/{pref_key}")
async def delete_preference(
    pref_key: str,
    auth: AuthContext = Depends(get_auth_context_with_api_key)
):
    """Delete a user preference."""
    async with get_db() as db:
        await db.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND account_id = ? AND pref_key = ?",
            (auth.user_id, auth.account_id, pref_key)
        )
        await db.commit()
    return {"message": "Preference deleted"}
