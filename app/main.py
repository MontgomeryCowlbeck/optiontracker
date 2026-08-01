from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging
import os

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.database import init_db
from app.routers import auth, journal
from app.services.scheduler import setup_scheduler, shutdown_scheduler
from app.rate_limit import limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Trade Journal...")
    await init_db()
    logger.info("Database initialized")
    setup_scheduler()
    logger.info("Scheduler started")
    yield
    shutdown_scheduler()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="Trade Journal",
    description="A disciplined, edge-based options trade journal",
    version="2.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth.router)
app.include_router(journal.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """Serve the platform SPA — the React build when present, else the legacy Logbook.

    no-cache on the shell is load-bearing: every build renames the hashed JS
    bundle and deletes the old one, so a browser that heuristically cached
    index.html (iOS Safari especially) requests a bundle that no longer exists
    and the app dead-screens until the cache expires. no-cache = always
    revalidate (304 when unchanged), never serve a stale shell.
    """
    headers = {"Cache-Control": "no-cache"}
    dist_index = os.path.join(static_dir, "dist", "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index, headers=headers)
    return FileResponse(os.path.join(static_dir, "index.html"), headers=headers)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
