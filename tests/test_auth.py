from tests.conftest import PASSWORD

API = "/api/v1"


def test_register_login_profile_logout(client):
    resp = client.post(
        f"{API}/auth/register",
        json={"email": "Dev@Example.com", "username": "dev", "password": PASSWORD, "full_name": "Dev"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "dev@example.com"
    assert "hashed_password" not in body and "password" not in body

    login = client.post(f"{API}/auth/login", json={"email": "dev@example.com", "password": PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get(f"{API}/users/me", headers=headers).json()["username"] == "dev"

    updated = client.patch(f"{API}/users/me", json={"bio": "Backend dev"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["bio"] == "Backend dev"

    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    after = client.get(f"{API}/users/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "unauthorized"


def test_duplicate_email_and_username_rejected(client, make_user):
    make_user("sam")
    dup_email = client.post(
        f"{API}/auth/register",
        json={"email": "sam@example.com", "username": "other", "password": PASSWORD},
    )
    assert dup_email.status_code == 409
    dup_name = client.post(
        f"{API}/auth/register",
        json={"email": "new@example.com", "username": "SAM", "password": PASSWORD},
    )
    assert dup_name.status_code == 409


def test_weak_password_and_bad_email_fail_validation(client):
    resp = client.post(
        f"{API}/auth/register",
        json={"email": "not-an-email", "username": "x!", "password": "short"},
    )
    assert resp.status_code == 422
    fields = {d["field"] for d in resp.json()["error"]["details"]}
    assert {"email", "username", "password"} <= fields


def test_wrong_password_and_missing_token(client, make_user):
    make_user("kim")
    bad = client.post(f"{API}/auth/login", json={"email": "kim@example.com", "password": "Wrong1234"})
    assert bad.status_code == 401
    assert client.get(f"{API}/users/me").status_code == 401
    assert client.get(f"{API}/users/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_change_password(client, make_user):
    kim = make_user("kim")
    wrong = client.put(
        f"{API}/users/me/password",
        json={"current_password": "nope", "new_password": "NewPass123"},
        headers=kim.headers,
    )
    assert wrong.status_code == 401
    ok = client.put(
        f"{API}/users/me/password",
        json={"current_password": PASSWORD, "new_password": "NewPass123"},
        headers=kim.headers,
    )
    assert ok.status_code == 204
    login = client.post(f"{API}/auth/login", json={"email": kim.email, "password": "NewPass123"})
    assert login.status_code == 200
