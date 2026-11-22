"""File attachments on tasks, stored on local disk (UPLOAD_DIR)."""

from fastapi import APIRouter, File, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.core.exceptions import ForbiddenError, NotFoundError
from app.deps import CurrentUser, DbSession
from app.models import Attachment
from app.schemas import AttachmentOut
from app.services import storage
from app.services.access import can_manage_task, get_visible_task
from app.services.notifications import notify

router = APIRouter(prefix="/tasks/{task_id}/attachments", tags=["Attachments"])


def _get_attachment(db, task_id: int, attachment_id: int) -> Attachment:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.task_id != task_id:
        raise NotFoundError("Attachment not found")
    return attachment


@router.get("", response_model=list[AttachmentOut])
def list_attachments(task_id: int, user: CurrentUser, db: DbSession) -> list[Attachment]:
    return get_visible_task(db, task_id, user).attachments


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
def upload_attachment(
    task_id: int, user: CurrentUser, db: DbSession, file: UploadFile = File(...)
) -> Attachment:
    """Upload a file (multipart/form-data, field name `file`)."""
    task = get_visible_task(db, task_id, user)
    stored_name, size = storage.save_upload(file)
    attachment = Attachment(
        task_id=task.id,
        uploader_id=user.id,
        filename=storage.safe_filename(file.filename),
        stored_name=stored_name,
        content_type=(file.content_type or "application/octet-stream")[:100],
        size_bytes=size,
    )
    db.add(attachment)
    notify(
        db,
        recipient_ids=[task.created_by_id, task.assignee_id],
        actor_id=user.id,
        type="task_attachment",
        message=f"{user.username} attached '{attachment.filename}' to '{task.title}'",
        task_id=task.id,
        team_id=task.team_id,
    )
    try:
        db.commit()
    except Exception:
        storage.delete_files([stored_name])
        raise
    return attachment


@router.get("/{attachment_id}/download", response_class=FileResponse)
def download_attachment(
    task_id: int, attachment_id: int, user: CurrentUser, db: DbSession
) -> FileResponse:
    get_visible_task(db, task_id, user)
    attachment = _get_attachment(db, task_id, attachment_id)
    path = storage.file_path(attachment.stored_name)
    if not path.is_file():
        raise NotFoundError("The attachment file is missing from storage")
    return FileResponse(path, media_type=attachment.content_type, filename=attachment.filename)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    task_id: int, attachment_id: int, user: CurrentUser, db: DbSession
) -> Response:
    """Delete an attachment you uploaded (task creators and team admins can delete any)."""
    task = get_visible_task(db, task_id, user)
    attachment = _get_attachment(db, task_id, attachment_id)
    if attachment.uploader_id != user.id and not can_manage_task(db, task, user):
        raise ForbiddenError("You can't delete this attachment")
    stored_name = attachment.stored_name
    db.delete(attachment)
    db.commit()
    storage.delete_files([stored_name])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
