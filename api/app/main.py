"""Hisaab API. Phase 0 ships the health endpoint only.

The six real endpoints from plan section 16.4 arrive in Phase 6. The rule that
protects the project: this layer only *reads* results. The pipeline and the eval
harness must run from the command line without it.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import router
from app.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(
    title="Hisaab",
    description="An AI finance controller that matches invoices to bank payments.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    """Proves the container is up AND the database answers a real query."""
    db_ok = db.ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "llm_key_configured": settings.has_real_llm_key,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "version": app.version,
    }
