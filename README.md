# TaskMaster API

A backend for a task tracking and team collaboration app, built with **FastAPI** and **SQLite**.

Users can register and log in with JWT tokens, create and track tasks, organize work in teams,
invite teammates, assign tasks, discuss them with comments and attachments, get real-time
notifications over WebSockets, and draft task descriptions with generative AI.

## Features

| Area | What you get |
|---|---|
| Accounts | Registration, login, logout (token revocation), profile view/update, password change |
| Security | bcrypt password hashing, JWT access tokens, per-request authorization checks |
| Tasks | Full CRUD, status (`open`, `in_progress`, `completed`), priority, due date, complete/reopen |
| Finding tasks | Filter by status, priority, team, assignee, due dates, overdue; search title/description; sort; paginate |
| Teams | Create teams, invite by email, accept/decline, admin/member roles, leave/remove members |
| Collaboration | Assign tasks to teammates, comments, file attachments (upload/download) |
| Notifications | Stored inbox plus live push over WebSocket when you are assigned, or a task you own/are assigned changes |
| AI (optional) | Draft a task description from a title, or summarize a task and its comments (Claude) |

## Tech stack

- **FastAPI** (Python 3.12) with automatic OpenAPI docs
- **SQLAlchemy 2** ORM on a **SQLite** file (`taskmaster.db`)
- **Pydantic v2** for validation and settings
- **bcrypt** + **PyJWT** for authentication
- **WebSockets** for real-time notifications
- **Anthropic SDK** (Claude) for the optional AI endpoints
- **pytest** for tests

## Getting started

Requires Python 3.12+.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) configure: copy the example and edit values
cp .env.example .env

# 4. Run the server
uvicorn app.main:app --reload
```

The database file and the `uploads/` folder are created automatically on first start.

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- Health check: http://127.0.0.1:8000/health

To try authenticated endpoints in Swagger UI, call `POST /api/v1/auth/login`, copy the
`access_token`, click **Authorize**, and paste it.

### Configuration

All settings come from environment variables or a `.env` file (see [.env.example](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | dev placeholder | Signs JWTs. **Set a long random value outside local development.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost |
| `DATABASE_URL` | `sqlite:///<project>/taskmaster.db` | Database location |
| `UPLOAD_DIR` | `<project>/uploads` | Where attachments are stored |
| `MAX_UPLOAD_SIZE_MB` | `10` | Attachment size limit |
| `CORS_ORIGINS` | `["*"]` | Allowed browser origins (JSON list) |
| `ANTHROPIC_API_KEY` | empty | Turns on AI generation with Claude |
| `AI_MODEL` | `claude-opus-5-5` | Claude model used for AI endpoints |

### Running tests

```bash
pytest
```

Tests use a temporary database and upload folder, so your local data is never touched.

## API overview

All endpoints are under `/api/v1`. Everything except register and login needs the header
`Authorization: Bearer <access_token>`.

### Authentication & profile

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create an account (`email`, `username`, `password`, optional `full_name`) |
| POST | `/auth/login` | Get an access token (`email`, `password`) |
| POST | `/auth/logout` | Revoke the current token |
| GET | `/users/me` | View your profile |
| PATCH | `/users/me` | Update email, username, full name or bio |
| PUT | `/users/me/password` | Change password |

Passwords need at least 8 characters with a letter and a number.

### Tasks

| Method | Path | Description |
|---|---|---|
| POST | `/tasks` | Create a task (`title`, `description`, `due_date`, `priority`, `status`, `team_id`, `assignee_id`) |
| GET | `/tasks` | List, filter, search, sort and paginate tasks |
| GET | `/tasks/{id}` | Get one task |
| PATCH | `/tasks/{id}` | Update only the fields you send |
| PUT | `/tasks/{id}/assignee` | Assign to a teammate (`{"assignee_id": 5}`) or unassign (`null`) |
| POST | `/tasks/{id}/complete` | Mark as completed |
| POST | `/tasks/{id}/reopen` | Move a completed task back to open |
| DELETE | `/tasks/{id}` | Delete (creator or team owner/admin) |

Query parameters for `GET /tasks`:

| Parameter | Example | Meaning |
|---|---|---|
| `status` | `?status=open&status=in_progress` | One or more statuses |
| `priority` | `?priority=high` | `low`, `medium`, `high` |
| `assigned_to_me` | `?assigned_to_me=true` | Tasks assigned to you |
| `created_by_me` | `?created_by_me=true` | Tasks you created |
| `team_id`, `assignee_id` | `?team_id=2` | Narrow to a team or a person |
| `q` | `?q=login` | Search title and description (case-insensitive) |
| `due_before`, `due_after` | `?due_before=2030-01-01T00:00:00Z` | Due date range |
| `overdue` | `?overdue=true` | Past due and not completed |
| `sort_by` | `?sort_by=due_date` | `created_at`, `updated_at`, `due_date`, `priority`, `title`, `status` |
| `order` | `?order=asc` | `asc` or `desc` (default) |
| `page`, `page_size` | `?page=2&page_size=50` | Pagination (max 100 per page) |

Responses are paginated: `{"items": [...], "total": 42, "page": 1, "page_size": 20, "pages": 3}`.

### Teams & invitations

| Method | Path | Description |
|---|---|---|
| POST | `/teams` | Create a team (you become the owner) |
| GET | `/teams` | Teams you belong to |
| GET | `/teams/{id}` | Team details with members |
| PATCH | `/teams/{id}` | Rename / edit description (owner, admin) |
| DELETE | `/teams/{id}` | Delete the team and its tasks (owner) |
| GET | `/teams/{id}/members` | List members |
| PATCH | `/teams/{id}/members/{user_id}` | Change role to `admin` or `member` (owner) |
| DELETE | `/teams/{id}/members/{user_id}` | Remove a member, or leave with your own id |
| POST | `/teams/{id}/invitations` | Invite a registered user by email (owner, admin) |
| GET | `/teams/{id}/invitations` | Invitations sent for the team (owner, admin) |
| GET | `/invitations` | Invitations you received (`?status=pending` by default) |
| POST | `/invitations/{id}/accept` | Join the team |
| POST | `/invitations/{id}/decline` | Decline |

### Comments & attachments

| Method | Path | Description |
|---|---|---|
| GET / POST | `/tasks/{id}/comments` | List / add comments |
| PATCH / DELETE | `/tasks/{id}/comments/{comment_id}` | Edit your comment / delete it |
| GET / POST | `/tasks/{id}/attachments` | List / upload (`multipart/form-data`, field `file`) |
| GET | `/tasks/{id}/attachments/{attachment_id}/download` | Download the file |
| DELETE | `/tasks/{id}/attachments/{attachment_id}` | Delete an attachment |

### Notifications

| Method | Path | Description |
|---|---|---|
| GET | `/notifications` | Your notifications, newest first (`?unread_only=true`) |
| POST | `/notifications/{id}/read` | Mark one as read |
| POST | `/notifications/read-all` | Mark all as read |
| WS | `/ws/notifications?token=<access_token>` | Live feed |

You get a notification when a task is assigned to you; when a task you created or are assigned
is updated, completed, commented on or gets an attachment; when you are invited to a team; and
when someone accepts your invitation. You are never notified about your own actions.

Live messages look like `{"event": "notification", "data": {"type": "task_assigned", ...}}`.
Send the text `ping` to get `{"event": "pong"}` back. Quick browser test:

```js
const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/notifications?token=YOUR_TOKEN");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### AI helpers

| Method | Path | Description |
|---|---|---|
| POST | `/ai/task-description` | Draft a description from `title` and optional `context` |
| POST | `/ai/tasks/{id}/summary` | Summarize a task, its status and its comments |

With `ANTHROPIC_API_KEY` set, these call Claude (`AI_MODEL`, default `claude-opus-5-5`); the
response has `"source": "ai"`. If the model declines a request, the API's server-side fallback
retries it on another model. Without a key they return a simple built-in template with
`"source": "template"`, so the endpoints still work offline. Drafted text is not saved; use it as
the `description` when creating or updating a task.

## Permissions

- **Personal tasks** (no `team_id`) are visible only to their creator and assignee, and can only
  be assigned to yourself.
- **Team tasks** are visible to and editable by every team member, and can be assigned to any
  member. Only the task creator or a team owner/admin can delete them.
- Asking for a task you can't see returns `404`, so task ids of other teams aren't revealed.
- When someone leaves or is removed from a team, its tasks assigned to them become unassigned.

## Errors

Every error has the same JSON shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [{"field": "password", "message": "Value error, Password must be at least 8 characters long"}]
  }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | The request is valid but not allowed (e.g. assigning outside the team) |
| 401 | `unauthorized` | Missing, invalid, expired or revoked token; wrong credentials |
| 403 | `forbidden` | Authenticated but lacking the role for this action |
| 404 | `not_found` | Resource doesn't exist or isn't visible to you |
| 409 | `conflict` | Duplicate email/username, existing member, already-answered invitation |
| 413 | `payload_too_large` | Attachment over the size limit |
| 422 | `validation_error` | Body or query parameters failed validation |
| 502 | `external_service_error` | The AI provider failed |

## Project structure

```
app/
  main.py               App setup, routers, lifespan (creates tables)
  core/
    config.py           Settings from env / .env
    database.py         SQLite engine, session, Base
    security.py         bcrypt hashing, JWT create/verify
    exceptions.py       Error classes and JSON error handlers
  models.py             SQLAlchemy models (User, Team, Task, Comment, ...)
  schemas.py            Pydantic request/response models
  deps.py               Auth and DB dependencies
  services/
    access.py           Who can see / edit / delete what
    notifications.py    Notification storage + WebSocket connection manager
    storage.py          Attachment files on disk
    ai.py               Claude integration with offline fallback
  routers/              One module per resource (auth, users, teams, tasks, ...)
tests/                  pytest suite
```

## Example session

```bash
# Register and log in
curl -X POST http://127.0.0.1:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"email":"ana@example.com","username":"ana","password":"Secret123"}'
curl -X POST http://127.0.0.1:8000/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"email":"ana@example.com","password":"Secret123"}'
# -> {"access_token": "...", ...}

TOKEN=...   # paste the token

# Create a team and a task in it
curl -X POST http://127.0.0.1:8000/api/v1/teams -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"Website"}'
curl -X POST http://127.0.0.1:8000/api/v1/tasks -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Launch landing page","due_date":"2030-06-01T17:00:00Z","priority":"high","team_id":1}'

# Open tasks, soonest due first
curl "http://127.0.0.1:8000/api/v1/tasks?status=open&sort_by=due_date&order=asc" \
  -H "Authorization: Bearer $TOKEN"
```

## Notes and possible extensions

- Tables are created automatically at startup. For a production schema history, add Alembic
  migrations.
- SQLite suits local use and small deployments. To use PostgreSQL, set `DATABASE_URL` and
  install a driver such as `psycopg`.
- The WebSocket connection manager is in-memory, so live push works within one server process.
  Running several workers would need a shared broker such as Redis pub/sub.
