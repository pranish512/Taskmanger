"""Shared FastAPI dependencies: database session and the authenticated user."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.models import RevokedToken, User

_bearer = HTTPBearer(auto_error=False, description="Paste the access_token from /auth/login")

DbSession = Annotated[Session, Depends(get_db)]


def user_from_token(db: Session, token: str | None) -> tuple[User, dict]:
    """Validate a raw JWT and return the active user it belongs to, plus its claims."""
    if not token:
        raise UnauthorizedError("Not authenticated")
    payload = decode_access_token(token)
    if db.get(RevokedToken, payload["jti"]) is not None:
        raise UnauthorizedError("Token has been revoked; please log in again")
    try:
        user = db.get(User, int(payload["sub"]))
    except ValueError as exc:
        raise UnauthorizedError("Invalid authentication token") from exc
    if user is None or not user.is_active:
        raise UnauthorizedError("User account not found or disabled")
    return user, payload


def get_auth(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tuple[User, dict]:
    return user_from_token(db, credentials.credentials if credentials else None)


def get_current_user(auth: Annotated[tuple[User, dict], Depends(get_auth)]) -> User:
    return auth[0]


CurrentUser = Annotated[User, Depends(get_current_user)]
