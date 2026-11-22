"""Pydantic request/response schemas."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models import InvitationStatus, TaskPriority, TaskStatus, TeamRole

T = TypeVar("T")

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.-]{3,30}$")
]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _to_utc(value: datetime) -> datetime:
    """Treat times without a zone as UTC, and convert the rest to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UTCDatetime = Annotated[datetime, AfterValidator(_to_utc)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def _check_password_strength(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if len(password.encode("utf-8")) > 72:  # bcrypt only uses the first 72 bytes
        raise ValueError("Password must be at most 72 bytes long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one number")
    return password


Password = Annotated[str, AfterValidator(_check_password_strength)]


# ---------- Users & auth ----------


class UserCreate(BaseModel):
    email: EmailStr
    username: Username
    password: Password
    full_name: ShortName | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserSummary(ORMModel):
    id: int
    username: str
    full_name: str | None


class UserOut(ORMModel):
    id: int
    email: EmailStr
    username: str
    full_name: str | None
    bio: str | None
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")
    user: UserOut


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: Username | None = None
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: Password


# ---------- Teams ----------


class TeamCreate(BaseModel):
    name: ShortName
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None


class TeamUpdate(BaseModel):
    name: ShortName | None = None
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None

    @model_validator(mode="after")
    def _name_not_null(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class TeamBrief(ORMModel):
    id: int
    name: str


class TeamOut(ORMModel):
    id: int
    name: str
    description: str | None
    owner: UserSummary
    member_count: int
    created_at: datetime
    updated_at: datetime


class MemberOut(ORMModel):
    user: UserSummary
    role: TeamRole
    joined_at: datetime


class TeamDetail(TeamOut):
    members: list[MemberOut]


class MemberRoleUpdate(BaseModel):
    role: TeamRole

    @field_validator("role")
    @classmethod
    def _not_owner(cls, role: TeamRole) -> TeamRole:
        if role == TeamRole.OWNER:
            raise ValueError("Ownership cannot be assigned through this endpoint")
        return role


class InvitationCreate(BaseModel):
    email: EmailStr


class InvitationOut(ORMModel):
    id: int
    team: TeamBrief
    invitee: UserSummary
    inviter: UserSummary
    status: InvitationStatus
    created_at: datetime
    responded_at: datetime | None


# ---------- Tasks ----------


class TaskCreate(BaseModel):
    title: Title
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    due_date: UTCDatetime | None = Field(
        default=None, description="ISO 8601. Times without a zone are treated as UTC."
    )
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.OPEN
    team_id: int | None = Field(default=None, description="Omit for a personal task")
    assignee_id: int | None = None


class TaskUpdate(BaseModel):
    """Partial update: only the fields you send are changed. Send `null` to clear an optional field."""

    title: Title | None = None
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    due_date: UTCDatetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    assignee_id: int | None = None

    @model_validator(mode="after")
    def _required_fields_not_null(self):
        for name in ("title", "priority", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class TaskAssign(BaseModel):
    assignee_id: int | None = Field(description="User to assign, or null to unassign")


class TaskOut(ORMModel):
    id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_date: datetime | None
    completed_at: datetime | None
    team_id: int | None
    creator: UserSummary
    assignee: UserSummary | None
    created_at: datetime
    updated_at: datetime


class TaskSortField(StrEnum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    DUE_DATE = "due_date"
    PRIORITY = "priority"
    TITLE = "title"
    STATUS = "status"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class TaskFilterParams(BaseModel):
    """Query parameters for `GET /tasks`."""

    model_config = ConfigDict(extra="forbid")

    status: list[TaskStatus] = Field(
        default_factory=list, description="Repeat to match several: ?status=open&status=in_progress"
    )
    priority: TaskPriority | None = None
    team_id: int | None = None
    assignee_id: int | None = None
    assigned_to_me: bool = False
    created_by_me: bool = False
    q: str | None = Field(
        default=None, min_length=1, max_length=200, description="Search title and description"
    )
    due_before: UTCDatetime | None = None
    due_after: UTCDatetime | None = None
    overdue: bool = Field(default=False, description="Past due and not completed")
    sort_by: TaskSortField = TaskSortField.CREATED_AT
    order: SortOrder = SortOrder.DESC
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# ---------- Comments & attachments ----------


class CommentCreate(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class CommentOut(ORMModel):
    id: int
    task_id: int
    author: UserSummary
    content: str
    created_at: datetime
    updated_at: datetime


class AttachmentOut(ORMModel):
    id: int
    task_id: int
    filename: str
    content_type: str
    size_bytes: int
    uploader: UserSummary
    created_at: datetime


# ---------- Notifications ----------


class NotificationOut(ORMModel):
    id: int
    type: str
    message: str
    task_id: int | None
    team_id: int | None
    is_read: bool
    created_at: datetime


# ---------- AI ----------


class DescriptionRequest(BaseModel):
    title: Title
    context: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = Field(
        default=None, description="Optional notes, e.g. 'needs to work on mobile, due before launch'"
    )


class AIResult(BaseModel):
    text: str
    source: str = Field(description="'ai' when generated by the model, 'template' when no API key is set")
    model: str | None = None
