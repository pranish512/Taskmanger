"""Notifications: stored in the database and pushed live over WebSockets.

Endpoints call `notify(...)` inside their transaction. The rows are saved with the
rest of the change, and only after a successful commit are they pushed to any open
WebSocket connections of the recipients. A rolled-back request pushes nothing.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Notification, utcnow
from app.schemas import NotificationOut

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_notifications"


class ConnectionManager:
    """Tracks open WebSocket connections per user."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the server's event loop so sync endpoints (worker threads) can push."""
        self._loop = loop

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del self._connections[user_id]

    async def send_to_user(self, user_id: int, message: dict) -> None:
        for websocket in list(self._connections.get(user_id, ())):
            try:
                await websocket.send_json(message)
            except Exception:  # connection already gone
                self.disconnect(user_id, websocket)

    def push(self, user_id: int, message: dict) -> None:
        """Schedule a send from any thread. Does nothing if the user isn't connected."""
        loop = self._loop
        if loop is None or loop.is_closed() or not self._connections.get(user_id):
            return
        coro = self.send_to_user(user_id, message)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)


manager = ConnectionManager()


def notify(
    db: Session,
    *,
    recipient_ids: Iterable[int | None],
    type: str,
    message: str,
    actor_id: int | None = None,
    task_id: int | None = None,
    team_id: int | None = None,
) -> None:
    """Queue a notification for each recipient (skipping the actor and duplicates)."""
    for user_id in {uid for uid in recipient_ids if uid is not None and uid != actor_id}:
        notification = Notification(
            user_id=user_id,
            type=type,
            message=message[:500],
            task_id=task_id,
            team_id=team_id,
            is_read=False,
            created_at=utcnow(),
        )
        db.add(notification)
        db.info.setdefault(_PENDING_KEY, []).append(notification)


@event.listens_for(SessionLocal, "after_commit")
def _push_after_commit(session: Session) -> None:
    for notification in session.info.pop(_PENDING_KEY, []):
        try:
            payload = NotificationOut.model_validate(notification).model_dump(mode="json")
            manager.push(notification.user_id, {"event": "notification", "data": payload})
        except Exception:
            logger.exception("Failed to push notification %s", notification.id)


@event.listens_for(SessionLocal, "after_rollback")
def _discard_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
