import secrets
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache

# Single source of truth for credentials: the quant platform's .secrets/ dir (chmod 640,
# git-ignored). Env vars still WIN (that's how Docker on kaiju injects them); these files are
# the fallback for running locally without duplicating the refresh token into a second .env.
_SECRET_DIR = Path("/projects/quant/.secrets")


def _secret_file(basename: str, default: str = "") -> str:
    """Read a secret from .secrets/<basename>.txt; '' if absent/unreadable."""
    f = _SECRET_DIR / f"{basename}.txt"
    try:
        if f.exists():
            return f.read_text().strip()
    except OSError:
        pass
    return default


def _resolve_tt(field_env: str, secret_basename: str) -> str:
    """Env var wins (Docker), else the shared .secrets/ file (local)."""
    return os.environ.get(field_env, "").strip() or _secret_file(secret_basename)


def _resolve_jwt_secret() -> str:
    """Stable JWT secret: env wins; else a persisted one in .secrets/ (generated once so
    tokens survive restarts). Never regenerated per boot."""
    env = os.environ.get("JWT_SECRET_KEY", "").strip()
    if env:
        return env
    existing = _secret_file("dashboard_jwt_secret")
    if existing:
        return existing
    token = secrets.token_urlsafe(48)
    try:
        p = _SECRET_DIR / "dashboard_jwt_secret.txt"
        p.write_text(token)
        p.chmod(0o640)
    except OSError:
        pass  # not writable here -> use the in-memory token (still valid for this process)
    return token


class Settings(BaseSettings):
    tastytrade_client_id: str = ""
    tastytrade_client_secret: str = ""
    tastytrade_refresh_token: str = ""
    tastytrade_sandbox: bool = False
    database_path: str = "/app/data/options.db"
    fetch_schedule: str = "0 10,12,15 * * 1-5"

    # AI layer: path to the claude CLI (headless `claude -p`, the watchtower pattern).
    # Empty/absent binary -> AI endpoints return 503; everything else works without it.
    claude_bin: str = "claude"
    ai_timeout_seconds: int = 180

    # Signal feeds written by the quant platform's crons (read-only here).
    watchtower_dir: str = "/data/structured/watchtower"
    live_signals_dir: str = "/data/structured/live"

    # Quant platform source tree — the DD panel imports watchtower level
    # detection from here (read-only reuse; empty disables the DD endpoints).
    quant_src_dir: str = "/projects/quant/src"

    @property
    def tastytrade_configured(self) -> bool:
        return bool(self.tastytrade_client_id and self.tastytrade_client_secret and self.tastytrade_refresh_token)

    # JWT Authentication settings
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30

    @field_validator('jwt_secret_key', mode='before')
    @classmethod
    def set_jwt_secret(cls, v):
        if not v or v.strip() == "":
            raise ValueError(
                "JWT_SECRET_KEY environment variable must be set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters for security")
        return v

    @property
    def tastytrade_base_url(self) -> str:
        if self.tastytrade_sandbox:
            return "https://api.cert.tastyworks.com"
        return "https://api.tastyworks.com"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    # Explicit kwargs take precedence in pydantic-settings; each resolver checks env first,
    # then the shared .secrets/ files — so Docker (env) and local (.secrets) both work.
    return Settings(
        tastytrade_client_id=_resolve_tt("TASTYTRADE_CLIENT_ID", "tt_client_id"),
        tastytrade_client_secret=_resolve_tt("TASTYTRADE_CLIENT_SECRET", "tt_client_secret"),
        tastytrade_refresh_token=_resolve_tt("TASTYTRADE_REFRESH_TOKEN", "tt_refresh_token"),
        jwt_secret_key=_resolve_jwt_secret(),
    )
