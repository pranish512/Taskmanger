"""Task CRUD, assignment, completion, and filtering / sorting / searching."""

import math
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ForbiddenError
from app.deps import CurrentUser, DbSession
from app.models import Task, TaskPriority, TaskStatus, User, utcnow
from app.schemas import (
    Page,
    SortOrder,
    TaskAssign,
    TaskCreate,
    TaskFilterParams,
    TaskOut,
    TaskSortField,
    TaskUpdate,
)
from app.services import storage
from app.services.access import (
    can_manage_task,
    get_visible_task,
    require_team_member,
    resolve_assignee,
    visible_tasks_clause,
)
from app.services.notifications import notify

router = APIRouter(prefix="/tasks", tags=["Tasks"])

_PRIORITY_RANK = case(
    {TaskPriority.LOW: 1, TaskPriority.MEDIUM: 2, TaskPriority.HIGH: 3},
    value=Task.priority,
)
_STATUS_RANK = case(
    {TaskStatus.OPEN: 1, TaskStatus.IN_PROGRESS: 2, TaskStatus.COMPLETED: 3},
    value=Task.status,
)
_SORT_COLUMNS = {
    TaskSortField.CREATED_AT: Task.created_at,
    TaskSortField.UPDATED_AT: Task.updated_at,
    TaskSortField.DUE_DATE: Task.due_date,
    TaskSortField.PRIORITY: _PRIORITY_RANK,
    TaskSortField.TITLE: func.lower(Task.title),
    TaskSortField.STATUS: _STATUS_RANK,
}


def _set_status(task: Task, new_status: TaskStatus) -> None:
    """Change status and keep `completed_at` in step with it."""
    if new_status == task.status:
        return
    task.status = new_status
    task.completed_at = utcnow() if new_status == TaskStatus.COMPLETED else None


def _notify_assigned(db: Session, task: Task, actor: User) -> None:
    notify(
        db,
        recipient_ids=[task.assignee_id],
        actor_id=actor.id,
        type="task_assigned",
        message=f"{actor.username} assigned you the task '{task.title}'",
        task_id=task.id,
        team_id=task.team_id,
    )


def _apply_filters(stmt: Select, f: TaskFilterParams, user: User) -> Select:
    if f.status:
        stmt = stmt.where(Task.status.in_(f.status))
    if f.priority is not None:
        stmt = stmt.where(Task.priority == f.priority)
    if f.team_id is not None:
        stmt = stmt.where(Task.team_id == f.team_id)
    if f.assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == f.assignee_id)
    if f.assigned_to_me:
        stmt = stmt.where(Task.assignee_id == user.id)
    if f.created_by_me:
        stmt = stmt.where(Task.created_by_id == user.id)
    if f.q:
        pattern = f"%{f.q}%"
        stmt = stmt.where(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
    if f.due_before is not None:
        stmt = stmt.where(Task.due_date < f.due_before)
    if f.due_after is not None:
        stmt = stmt.where(Task.due_date > f.due_after)
    if f.overdue:
        stmt = stmt.where(Task.due_date < utcnow(), Task.status != TaskStatus.COMPLETED)
    return stmt


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, user: CurrentUser, db: DbSession) -> Task:
    """Create a personal task, or a team task by passing `team_id` (you must be a member)."""
    if payload.team_id is not None:
        require_team_member(db, payload.team_id, user)
    assignee = resolve_assignee(db, payload.team_id, payload.assignee_id, user)

    task = Task(
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
        priority=payload.priority,
        status=payload.status,
        team_id=payload.team_id,
        created_by_id=user.id,
        assignee_id=assignee.id if assignee else None,
        completed_at=utcnow() if payload.status == TaskStatus.COMPLETED else None,
    )
    db.add(task)
    db.flush()  # assigns task.id for the notification
    if assignee is not None:
        _notify_assigned(db, task, user)
    db.commit()
    return task


@router.get("", response_model=Page[TaskOut])
def list_tasks(
    filters: Annotated[TaskFilterParams, Query()], user: CurrentUser, db: DbSession
) -> Page[TaskOut]:
    """List tasks you can see: ones you created, ones assigned to you, and your teams' tasks.

    Examples: `?assigned_to_me=true`, `?status=open&status=in_progress`,
    `?q=login&sort_by=due_date&order=asc`.
    """
    if filters.team_id is not None:
        require_team_member(db, filters.team_id, user)

    base = _apply_filters(select(Task).where(visible_tasks_clause(user.id)), filters, user)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    sort_column = _SORT_COLUMNS[filters.sort_by]
    ordering = sort_column.asc() if filters.order == SortOrder.ASC else sort_column.desc()
    if filters.sort_by == TaskSortField.DUE_DATE:
        ordering = ordering.nulls_last()  # tasks without a due date go last either way

    stmt = (
        base.options(selectinload(Task.creator), selectinload(Task.assignee))
        .order_by(ordering, Task.id.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    )
    items = [TaskOut.model_validate(task) for task in db.scalars(stmt)]
    return Page[TaskOut](
        items=items,
        total=total,
        page=filters.page,
        page_size=filters.page_size,
        pages=math.ceil(total / filters.page_size) if total else 0,
    )


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    return get_visible_task(db, task_id, user)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, user: CurrentUser, db: DbSession) -> Task:
    """Update only the fields you send. Anyone who can see the task can edit it."""
    task = get_visible_task(db, task_id, user)
    data = payload.model_dump(exclude_unset=True)

    changed: list[str] = []

    new_assignee_id = data.pop("assignee_id", task.assignee_id)
    if new_assignee_id != task.assignee_id:
        assignee = resolve_assignee(db, task.team_id, new_assignee_id, user)
        task.assignee_id = assignee.id if assignee else None
        changed.append("assignee")

    new_status = data.pop("status", task.status)
    if new_status != task.status:
        _set_status(task, new_status)
        changed.append("status")

    for name, value in data.items():
        if getattr(task, name) != value:
            setattr(task, name, value)
            changed.append(name)

    if not changed:
        return task

    assignee_changed = "assignee" in changed
    if assignee_changed and task.assignee_id is not None:
        _notify_assigned(db, task, user)
    # Tell the creator and (unless just notified of the assignment) the assignee.
    notify(
        db,
        recipient_ids=[task.created_by_id] + ([] if assignee_changed else [task.assignee_id]),
        actor_id=user.id,
        type="task_updated",
        message=f"{user.username} updated '{task.title}' ({', '.join(changed)})",
        task_id=task.id,
        team_id=task.team_id,
    )
    db.commit()
    return task


@router.put("/{task_id}/assignee", response_model=TaskOut)
def assign_task(task_id: int, payload: TaskAssign, user: CurrentUser, db: DbSession) -> Task:
    """Assign the task to a team member, or pass `null` to unassign."""
    task = get_visible_task(db, task_id, user)
    assignee = resolve_assignee(db, task.team_id, payload.assignee_id, user)
    new_id = assignee.id if assignee else None
    if new_id != task.assignee_id:
        task.assignee_id = new_id
        if new_id is not None:
            _notify_assigned(db, task, user)
        db.commit()
    return task


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    """Mark the task as completed."""
    task = get_visible_task(db, task_id, user)
    if task.status != TaskStatus.COMPLETED:
        _set_status(task, TaskStatus.COMPLETED)
        notify(
            db,
            recipient_ids=[task.created_by_id, task.assignee_id],
            actor_id=user.id,
            type="task_completed",
            message=f"{user.username} completed '{task.title}'",
            task_id=task.id,
            team_id=task.team_id,
        )
        db.commit()
    return task


@router.post("/{task_id}/reopen", response_model=TaskOut)
def reopen_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    """Move a completed task back to open."""
    task = get_visible_task(db, task_id, user)
    if task.status == TaskStatus.COMPLETED:
        _set_status(task, TaskStatus.OPEN)
        db.commit()
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Delete a task with its comments and attachments. Creator or team owner/admin only."""
    task = get_visible_task(db, task_id, user)
    if not can_manage_task(db, task, user):
        raise ForbiddenError("Only the task's creator or a team owner/admin can delete it")
    stored_names = [a.stored_name for a in task.attachments]
    db.delete(task)
    db.commit()
    storage.delete_files(stored_names)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
