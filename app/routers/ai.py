"""Generative AI helpers for writing and summarizing tasks."""

from fastapi import APIRouter

from app.core.config import settings
from app.deps import CurrentUser, DbSession
from app.schemas import AIResult, DescriptionRequest
from app.services import ai
from app.services.access import get_visible_task

router = APIRouter(prefix="/ai", tags=["AI"])


def _result(text: str, source: str) -> AIResult:
    return AIResult(text=text, source=source, model=settings.ai_model if source == "ai" else None)


@router.post("/task-description", response_model=AIResult)
def generate_task_description(payload: DescriptionRequest, user: CurrentUser) -> AIResult:
    """Draft a task description from a title and optional notes.

    The result is not saved; use it as the `description` when creating or updating a task.
    """
    return _result(*ai.generate_description(payload.title, payload.context))


@router.post("/tasks/{task_id}/summary", response_model=AIResult)
def summarize_task(task_id: int, user: CurrentUser, db: DbSession) -> AIResult:
    """Summarize a task, its status and its comment thread."""
    task = get_visible_task(db, task_id, user)
    return _result(*ai.summarize_task(task))
