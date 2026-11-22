"""Authorization rules for teams and tasks.

* A team task is visible to (and editable by) every member of its team.
* A personal task (no team) is visible only to its creator and assignee.
* Deleting a task needs its creator or a team owner/admin.
"""

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models import Task, Team, TeamMember, TeamRole, User

ADMIN_ROLES = {TeamRole.OWNER, TeamRole.ADMIN}


def get_team_or_404(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise NotFoundError("Team not found")
    return team


def get_membership(db: Session, team_id: int, user_id: int) -> TeamMember | None:
    return db.get(TeamMember, (team_id, user_id))


def require_team_member(db: Session, team_id: int, user: User) -> TeamMember:
    get_team_or_404(db, team_id)
    membership = get_membership(db, team_id, user.id)
    if membership is None:
        raise ForbiddenError("You are not a member of this team")
    return membership


def require_team_admin(db: Session, team_id: int, user: User) -> TeamMember:
    membership = require_team_member(db, team_id, user)
    if membership.role not in ADMIN_ROLES:
        raise ForbiddenError("Only the team owner or an admin can do this")
    return membership


def visible_tasks_clause(user_id: int) -> ColumnElement[bool]:
    """SQL condition matching every task the user may see."""
    my_teams = select(TeamMember.team_id).where(TeamMember.user_id == user_id)
    return or_(
        Task.created_by_id == user_id,
        Task.assignee_id == user_id,
        Task.team_id.in_(my_teams),
    )


def can_view_task(db: Session, task: Task, user: User) -> bool:
    if user.id in (task.created_by_id, task.assignee_id):
        return True
    return task.team_id is not None and get_membership(db, task.team_id, user.id) is not None


def can_manage_task(db: Session, task: Task, user: User) -> bool:
    """Creator, or an owner/admin of the task's team."""
    if task.created_by_id == user.id:
        return True
    if task.team_id is None:
        return False
    membership = get_membership(db, task.team_id, user.id)
    return membership is not None and membership.role in ADMIN_ROLES


def get_visible_task(db: Session, task_id: int, user: User) -> Task:
    """Load a task, answering 404 (not 403) when the user may not see it, so ids don't leak."""
    task = db.get(Task, task_id)
    if task is None or not can_view_task(db, task, user):
        raise NotFoundError("Task not found")
    return task


def resolve_assignee(
    db: Session, team_id: int | None, assignee_id: int | None, actor: User
) -> User | None:
    """Check that `assignee_id` may be given a task in `team_id` and return that user."""
    if assignee_id is None:
        return None
    assignee = db.get(User, assignee_id)
    if assignee is None or not assignee.is_active:
        raise NotFoundError("Assignee not found")
    if team_id is None:
        if assignee.id != actor.id:
            raise BadRequestError(
                "Personal tasks can only be assigned to yourself. "
                "Create the task in a team to assign it to a teammate."
            )
    elif get_membership(db, team_id, assignee.id) is None:
        raise BadRequestError("The assignee must be a member of the task's team")
    return assignee
