"""Comments on tasks."""

from fastapi import APIRouter, Response, status

from app.core.exceptions import ForbiddenError, NotFoundError
from app.deps import CurrentUser, DbSession
from app.models import Comment
from app.schemas import CommentCreate, CommentOut
from app.services.access import can_manage_task, get_visible_task
from app.services.notifications import notify

router = APIRouter(prefix="/tasks/{task_id}/comments", tags=["Comments"])


def _get_comment(db, task_id: int, comment_id: int) -> Comment:
    comment = db.get(Comment, comment_id)
    if comment is None or comment.task_id != task_id:
        raise NotFoundError("Comment not found")
    return comment


@router.get("", response_model=list[CommentOut])
def list_comments(task_id: int, user: CurrentUser, db: DbSession) -> list[Comment]:
    """All comments on the task, oldest first."""
    return get_visible_task(db, task_id, user).comments


@router.post("", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(task_id: int, payload: CommentCreate, user: CurrentUser, db: DbSession) -> Comment:
    task = get_visible_task(db, task_id, user)
    comment = Comment(task_id=task.id, author_id=user.id, content=payload.content)
    db.add(comment)
    notify(
        db,
        recipient_ids=[task.created_by_id, task.assignee_id],
        actor_id=user.id,
        type="task_commented",
        message=f"{user.username} commented on '{task.title}': {payload.content[:120]}",
        task_id=task.id,
        team_id=task.team_id,
    )
    db.commit()
    return comment


@router.patch("/{comment_id}", response_model=CommentOut)
def edit_comment(
    task_id: int, comment_id: int, payload: CommentCreate, user: CurrentUser, db: DbSession
) -> Comment:
    """Edit your own comment."""
    get_visible_task(db, task_id, user)
    comment = _get_comment(db, task_id, comment_id)
    if comment.author_id != user.id:
        raise ForbiddenError("You can only edit your own comments")
    comment.content = payload.content
    db.commit()
    return comment


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(task_id: int, comment_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Delete your own comment (task creators and team admins can delete any)."""
    task = get_visible_task(db, task_id, user)
    comment = _get_comment(db, task_id, comment_id)
    if comment.author_id != user.id and not can_manage_task(db, task, user):
        raise ForbiddenError("You can't delete this comment")
    db.delete(comment)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
