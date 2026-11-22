"""Notification inbox (REST) and the real-time WebSocket feed."""

from fastapi import APIRouter, Query, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.core.exceptions import AppError, NotFoundError
from app.deps import CurrentUser, DbSession, user_from_token
from app.models import Notification
from app.schemas import NotificationOut
from app.services.notifications import manager

router = APIRouter(tags=["Notifications"])


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
) -> list[Notification]:
    """Your notifications, newest first."""
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    return list(db.scalars(stmt))


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: CurrentUser, db: DbSession) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotFoundError("Notification not found")
    notification.is_read = True
    db.commit()
    return notification


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(user: CurrentUser, db: DbSession) -> Response:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/ws/notifications")
async def notifications_socket(websocket: WebSocket, token: str | None = Query(None)) -> None:
    """Real-time feed. Connect with `?token=<access token>`.

    Each new notification arrives as `{"event": "notification", "data": {...}}`.
    Send the text `ping` to receive `{"event": "pong"}` (useful as a keep-alive).
    """
    db = SessionLocal()
    try:
        user, _ = user_from_token(db, token)
    except AppError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    finally:
        db.close()

    await manager.connect(user.id, websocket)
    try:
        await websocket.send_json({"event": "connected", "data": {"user_id": user.id}})
        while True:
            if await websocket.receive_text() == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user.id, websocket)
