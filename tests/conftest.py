"""Test setup: a throwaway SQLite file and upload folder, fresh tables for every test."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Must be set before the app (and its settings) are imported.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="taskmaster-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP_DIR / "uploads")
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256-signing"
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing for tests only
os.environ["ANTHROPIC_API_KEY"] = ""  # AI endpoints use the offline template in tests

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "Secret123"


@pytest.fixture(autouse=True)
def _fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class Account:
    def __init__(self, client: TestClient, username: str):
        self.username = username
        self.email = f"{username}@example.com"
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": self.email, "username": username, "password": PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        self.id = resp.json()["id"]
        login = client.post("/api/v1/auth/login", json={"email": self.email, "password": PASSWORD})
        assert login.status_code == 200, login.text
        self.token = login.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}


@pytest.fixture
def make_user(client):
    return lambda username: Account(client, username)


@pytest.fixture
def team_with_members(client, make_user):
    """alice owns a team that bob has joined. carol is not a member."""
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    team = client.post("/api/v1/teams", json={"name": "Platform"}, headers=alice.headers).json()
    invite = client.post(
        f"/api/v1/teams/{team['id']}/invitations", json={"email": bob.email}, headers=alice.headers
    ).json()
    resp = client.post(f"/api/v1/invitations/{invite['id']}/accept", headers=bob.headers)
    assert resp.status_code == 200, resp.text
    return team, alice, bob, carol
