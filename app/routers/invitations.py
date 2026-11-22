"""Invitations received by the current user."""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError
from app.deps import CurrentUser, DbSession
from app.models import InvitationStatus, TeamInvitation, TeamMember, TeamRole, utcnow
from app.schemas import InvitationOut
from app.services.access import get_membership
from app.services.notifications import notify

router = APIRouter(prefix="/invitations", tags=["Invitations"])


def _get_own_pending(db, invitation_id: int, user) -> TeamInvitation:
    invitation = db.get(TeamInvitation, invitation_id)
    if invitation is None or invitation.invitee_id != user.id:
        raise NotFoundError("Invitation not found")
    if invitation.status != InvitationStatus.PENDING:
        raise ConflictError(f"This invitation was already {invitation.status.value}")
    return invitation


@router.get("", response_model=list[InvitationOut])
def list_my_invitations(
    user: CurrentUser,
    db: DbSession,
    status: InvitationStatus | None = Query(
        InvitationStatus.PENDING, description="Leave empty to list all"
    ),
) -> list[TeamInvitation]:
    stmt = select(TeamInvitation).where(TeamInvitation.invitee_id == user.id)
    if status is not None:
        stmt = stmt.where(TeamInvitation.status == status)
    return list(db.scalars(stmt.order_by(TeamInvitation.created_at.desc())))


@router.post("/{invitation_id}/accept", response_model=InvitationOut)
def accept_invitation(invitation_id: int, user: CurrentUser, db: DbSession) -> TeamInvitation:
    """Join the team."""
    invitation = _get_own_pending(db, invitation_id, user)
    invitation.status = InvitationStatus.ACCEPTED
    invitation.responded_at = utcnow()
    if get_membership(db, invitation.team_id, user.id) is None:
        db.add(TeamMember(team_id=invitation.team_id, user_id=user.id, role=TeamRole.MEMBER))
    notify(
        db,
        recipient_ids=[invitation.inviter_id],
        actor_id=user.id,
        type="invitation_accepted",
        message=f"{user.username} joined the team '{invitation.team.name}'",
        team_id=invitation.team_id,
    )
    db.commit()
    return invitation


@router.post("/{invitation_id}/decline", response_model=InvitationOut)
def decline_invitation(invitation_id: int, user: CurrentUser, db: DbSession) -> TeamInvitation:
    invitation = _get_own_pending(db, invitation_id, user)
    invitation.status = InvitationStatus.DECLINED
    invitation.responded_at = utcnow()
    db.commit()
    return invitation
