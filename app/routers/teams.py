"""Teams / projects: create, manage members, and invite people."""

from fastapi import APIRouter, Response, status
from sqlalchemy import select, update

from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.deps import CurrentUser, DbSession
from app.models import (
    Attachment,
    InvitationStatus,
    Task,
    Team,
    TeamInvitation,
    TeamMember,
    TeamRole,
    User,
)
from app.schemas import (
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberRoleUpdate,
    TeamCreate,
    TeamDetail,
    TeamOut,
    TeamUpdate,
)
from app.services import storage
from app.services.access import (
    get_membership,
    get_team_or_404,
    require_team_admin,
    require_team_member,
)
from app.services.notifications import notify

router = APIRouter(prefix="/teams", tags=["Teams"])


@router.post("", response_model=TeamDetail, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, user: CurrentUser, db: DbSession) -> Team:
    """Create a team. The creator becomes its owner."""
    team = Team(name=payload.name, description=payload.description, owner_id=user.id)
    team.members.append(TeamMember(user_id=user.id, role=TeamRole.OWNER))
    db.add(team)
    db.commit()
    return team


@router.get("", response_model=list[TeamOut])
def list_my_teams(user: CurrentUser, db: DbSession) -> list[Team]:
    """Teams the current user belongs to."""
    stmt = (
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
        .order_by(Team.name)
    )
    return list(db.scalars(stmt))


@router.get("/{team_id}", response_model=TeamDetail)
def get_team(team_id: int, user: CurrentUser, db: DbSession) -> Team:
    require_team_member(db, team_id, user)
    return get_team_or_404(db, team_id)


@router.patch("/{team_id}", response_model=TeamDetail)
def update_team(team_id: int, payload: TeamUpdate, user: CurrentUser, db: DbSession) -> Team:
    require_team_admin(db, team_id, user)
    team = get_team_or_404(db, team_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(team, field, value)
    db.commit()
    return team


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Delete a team with all its tasks, comments and attachments. Owner only."""
    team = get_team_or_404(db, team_id)
    if team.owner_id != user.id:
        raise ForbiddenError("Only the team owner can delete the team")
    stored_names = list(
        db.scalars(
            select(Attachment.stored_name).join(Task).where(Task.team_id == team_id)
        )
    )
    db.delete(team)
    db.commit()
    storage.delete_files(stored_names)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Members ----------


@router.get("/{team_id}/members", response_model=list[MemberOut])
def list_members(team_id: int, user: CurrentUser, db: DbSession) -> list[TeamMember]:
    require_team_member(db, team_id, user)
    return get_team_or_404(db, team_id).members


@router.patch("/{team_id}/members/{user_id}", response_model=MemberOut)
def change_member_role(
    team_id: int, user_id: int, payload: MemberRoleUpdate, user: CurrentUser, db: DbSession
) -> TeamMember:
    """Promote a member to admin or demote an admin to member. Owner only."""
    team = get_team_or_404(db, team_id)
    if team.owner_id != user.id:
        raise ForbiddenError("Only the team owner can change roles")
    membership = get_membership(db, team_id, user_id)
    if membership is None:
        raise NotFoundError("That user is not a member of this team")
    if membership.role == TeamRole.OWNER:
        raise BadRequestError("The owner's role cannot be changed")
    membership.role = payload.role
    db.commit()
    return membership


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(team_id: int, user_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Remove a member (owner/admin), or leave the team (pass your own id).

    Team tasks assigned to the removed member become unassigned.
    """
    actor = require_team_member(db, team_id, user)
    target = get_membership(db, team_id, user_id)
    if target is None:
        raise NotFoundError("That user is not a member of this team")
    if target.role == TeamRole.OWNER:
        raise BadRequestError("The owner can't leave or be removed; delete the team instead")
    if user_id != user.id:
        if actor.role == TeamRole.MEMBER:
            raise ForbiddenError("Only the team owner or an admin can remove members")
        if actor.role == TeamRole.ADMIN and target.role == TeamRole.ADMIN:
            raise ForbiddenError("Admins can't remove other admins")

    db.execute(
        update(Task)
        .where(Task.team_id == team_id, Task.assignee_id == user_id)
        .values(assignee_id=None)
    )
    db.delete(target)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Invitations ----------


@router.post(
    "/{team_id}/invitations",
    response_model=InvitationOut,
    status_code=status.HTTP_201_CREATED,
)
def invite_member(
    team_id: int, payload: InvitationCreate, user: CurrentUser, db: DbSession
) -> TeamInvitation:
    """Invite a registered user by email. They join when they accept."""
    require_team_admin(db, team_id, user)
    team = get_team_or_404(db, team_id)

    invitee = db.scalar(select(User).where(User.email == payload.email.lower()))
    if invitee is None or not invitee.is_active:
        raise NotFoundError("No user is registered with that email")
    if get_membership(db, team_id, invitee.id) is not None:
        raise ConflictError("That user is already a member of this team")
    pending = db.scalar(
        select(TeamInvitation.id).where(
            TeamInvitation.team_id == team_id,
            TeamInvitation.invitee_id == invitee.id,
            TeamInvitation.status == InvitationStatus.PENDING,
        )
    )
    if pending is not None:
        raise ConflictError("That user already has a pending invitation to this team")

    invitation = TeamInvitation(team_id=team_id, invitee_id=invitee.id, inviter_id=user.id)
    db.add(invitation)
    notify(
        db,
        recipient_ids=[invitee.id],
        actor_id=user.id,
        type="team_invitation",
        message=f"{user.username} invited you to join the team '{team.name}'",
        team_id=team_id,
    )
    db.commit()
    return invitation


@router.get("/{team_id}/invitations", response_model=list[InvitationOut])
def list_team_invitations(team_id: int, user: CurrentUser, db: DbSession) -> list[TeamInvitation]:
    require_team_admin(db, team_id, user)
    stmt = (
        select(TeamInvitation)
        .where(TeamInvitation.team_id == team_id)
        .order_by(TeamInvitation.created_at.desc())
    )
    return list(db.scalars(stmt))
