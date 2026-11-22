"""Generative AI helpers: draft task descriptions and summarize tasks.

Uses Claude through the official Anthropic SDK when ANTHROPIC_API_KEY is set.
Without a key, a simple built-in template is returned instead, so the endpoints
still work locally and in tests.
"""

import logging

import anthropic

from app.core.config import settings
from app.core.exceptions import AppError, ExternalServiceError
from app.models import Task

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You help people write tasks in a team task tracker. Write in plain, direct language "
    "for the teammate who will do the work. Do not invent facts, names, dates or numbers "
    "that are not in the input. Reply with the requested text only: no preamble, no "
    "headings like 'Description:', and no Markdown code fences."
)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)
    return _client


def _ask_claude(prompt: str) -> str:
    try:
        response = _get_client().beta.messages.create(
            model=settings.ai_model,
            max_tokens=16000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            # Short writing task: low effort keeps it fast and cheap.
            output_config={"effort": "low"},
            # If the model declines, let the API retry on a suitable fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise ExternalServiceError("The AI provider rejected the configured API key") from exc
    except anthropic.RateLimitError as exc:
        raise ExternalServiceError("The AI provider is rate limiting requests; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        logger.warning("Claude API error %s: %s", exc.status_code, exc.message)
        raise ExternalServiceError("The AI provider returned an error") from exc
    except anthropic.APIConnectionError as exc:
        raise ExternalServiceError("Could not reach the AI provider") from exc

    if response.stop_reason == "refusal":
        raise AppError("The AI model declined to answer this request")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ExternalServiceError("The AI provider returned an empty response")
    return text


# ---------- Template fallbacks (no API key) ----------


def _template_description(title: str, context: str | None) -> str:
    lines = [f"Goal: {title.rstrip('.')}."]
    if context:
        lines.append(f"Context: {context}")
    lines += [
        "",
        "Acceptance criteria:",
        "- The outcome described in the title is delivered and checked.",
        "- Open questions and blockers are recorded as comments on this task.",
        "- The task is marked completed and relevant teammates are informed.",
    ]
    return "\n".join(lines)


def _template_summary(task: Task) -> str:
    due = task.due_date.strftime("%Y-%m-%d") if task.due_date else "no due date"
    assignee = task.assignee.username if task.assignee else "nobody"
    text = (
        f"'{task.title}' is {task.status.value.replace('_', ' ')} with {task.priority.value} "
        f"priority, {due}, assigned to {assignee}. It has {len(task.comments)} comment(s)"
        f" and {len(task.attachments)} attachment(s)."
    )
    if task.comments:
        latest = task.comments[-1]
        text += f" Latest comment by {latest.author.username}: \"{latest.content[:200]}\""
    return text


# ---------- Public API ----------


def generate_description(title: str, context: str | None) -> tuple[str, str]:
    """Return (description, source) where source is 'ai' or 'template'."""
    if not settings.ai_enabled:
        return _template_description(title, context), "template"
    prompt = (
        "Write a task description for this task. Include a 1-2 sentence summary of the goal, "
        "then a short bulleted list of 3-5 acceptance criteria. Keep it under 150 words.\n\n"
        f"Task title: {title}\n"
        f"Extra context from the user: {context or '(none)'}"
    )
    return _ask_claude(prompt), "ai"


def summarize_task(task: Task) -> tuple[str, str]:
    """Return (summary, source) for a task and its discussion."""
    if not settings.ai_enabled:
        return _template_summary(task), "template"
    comments = "\n".join(
        f"- {c.author.username} ({c.created_at:%Y-%m-%d}): {c.content}" for c in task.comments[-50:]
    )
    prompt = (
        "Summarize the current state of this task for a teammate catching up: what it is, "
        "where it stands, decisions or blockers raised in the comments, and the next step. "
        "Use at most 5 short sentences.\n\n"
        f"Title: {task.title}\n"
        f"Description: {task.description or '(none)'}\n"
        f"Status: {task.status.value}\nPriority: {task.priority.value}\n"
        f"Due: {task.due_date.isoformat() if task.due_date else 'none'}\n"
        f"Assignee: {task.assignee.username if task.assignee else 'unassigned'}\n"
        f"Attachments: {', '.join(a.filename for a in task.attachments) or 'none'}\n"
        f"Comments (oldest first):\n{comments or '(none)'}"
    )
    return _ask_claude(prompt), "ai"
