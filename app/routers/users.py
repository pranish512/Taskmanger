"""Profile management for the logged-in user."""

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.deps import CurrentUser, DbSession
from app.models import User
from app.schemas import PasswordChange, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserOut)
def get_profile(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_profile(payload: UserUpdate, user: CurrentUser, db: DbSession) -> User:
    """Update any of email, username, full name or bio. Send `null` to clear name or bio."""
    data = payload.model_dump(exclude_unset=True)

    if "email" in data:
        if data["email"] is None:
            raise ConflictError("email cannot be null")
        data["email"] = data["email"].lower()
        taken = db.scalar(select(User.id).where(User.email == data["email"], User.id != user.id))
        if taken:
            raise ConflictError("An account with this email already exists")
    if "username" in data:
        if data["username"] is None:
            raise ConflictError("username cannot be null")
        taken = db.scalar(
            select(User.id).where(
                func.lower(User.username) == data["username"].lower(), User.id != user.id
            )
        )
        if taken:
            raise ConflictError("This username is already taken")

    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    return user


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, user: CurrentUser, db: DbSession) -> Response:
    if not verify_password(payload.current_password, user.hashed_password):
        raise UnauthorizedError("Current password is incorrect")
    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
