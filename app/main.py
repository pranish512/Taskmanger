"""FastAPI application entry point.

Run locally with:  uvicorn app.main:app --reload
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import register_exception_handlers
from app.routers import (
    ai,
    attachments,
    auth,
    comments,
    invitations,
    notifications,
    tasks,
    teams,
    users,
)
from app.services.notifications import manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    manager.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Backend for a task tracking and team collaboration app: accounts and JWT auth, "
        "tasks with filtering/sorting/search, teams and invitations, comments, attachments, "
        "real-time notifications over WebSockets, and AI-drafted task descriptions."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

for module in (auth, users, teams, invitations, tasks, comments, attachments, notifications, ai):
    app.include_router(module.router, prefix=settings.api_prefix)


@app.get("/", tags=["Meta"])
def root() -> dict:
    return {"name": settings.app_name, "version": __version__, "docs": "/docs"}


@app.get("/health", tags=["Meta"])
def health() -> dict:
    return {"status": "ok"}
