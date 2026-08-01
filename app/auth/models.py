"""Pydantic models for authentication and accounts."""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# User models
class UserCreate(BaseModel):
    """Schema for creating a new user."""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)


class UserLogin(BaseModel):
    """Schema for user login."""
    username: str
    password: str


class UserResponse(BaseModel):
    """Schema for user response (no password)."""
    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Schema for JWT token response."""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    accounts: list["AccountResponse"]
    active_account_id: Optional[int] = None


class PasswordChange(BaseModel):
    """Schema for password change."""
    current_password: str
    new_password: str = Field(..., min_length=8)


# Account models
class AccountCreate(BaseModel):
    """Schema for creating an account."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    is_default: bool = False


class AccountUpdate(BaseModel):
    """Schema for updating an account."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_default: Optional[bool] = None


class AccountResponse(BaseModel):
    """Schema for account response."""
    id: int
    user_id: int
    name: str
    description: Optional[str]
    is_default: bool
    created_at: datetime

    class Config:
        from_attributes = True


# Update forward references
Token.model_rebuild()
