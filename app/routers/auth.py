"""Registration, login and logout."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_password,
)
from app.deps import DbSession, get_auth
from app.models import RevokedToken, User, utcnow
from app.schemas import LoginRequest, TokenResponse, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> User:
    """Create a new account."""
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("An account with this email already exists")
    if db.scalar(select(User.id).where(func.lower(User.username) == payload.username.lower())):
        raise ConflictError("This username is already taken")

    user = User(
        email=email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    """Exchange email and password for a JWT access token."""
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        verify_password(payload.password, DUMMY_PASSWORD_HASH)  # equalize timing
        raise UnauthorizedError("Incorrect email or password")
    if not verify_password(payload.password, user.hashed_password) or not user.is_active:
        raise UnauthorizedError("Incorrect email or password")

    token, _ = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: Annotated[tuple[User, dict], Depends(get_auth)], db: DbSession) -> Response:
    """Revoke the token used for this request. It can't be used again."""
    user, claims = auth
    db.add(
        RevokedToken(
            jti=claims["jti"],
            user_id=user.id,
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        )
    )
    # Housekeeping: tokens past their expiry are rejected anyway, so forget them.
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
