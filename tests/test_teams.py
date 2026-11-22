API = "/api/v1"


def test_create_team_and_invite_flow(client, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    team = client.post(
        f"{API}/teams", json={"name": "Mobile", "description": "iOS + Android"}, headers=alice.headers
    )
    assert team.status_code == 201
    team = team.json()
    assert team["member_count"] == 1 and team["members"][0]["role"] == "owner"

    invite = client.post(
        f"{API}/teams/{team['id']}/invitations", json={"email": bob.email}, headers=alice.headers
    )
    assert invite.status_code == 201
    again = client.post(
        f"{API}/teams/{team['id']}/invitations", json={"email": bob.email}, headers=alice.headers
    )
    assert again.status_code == 409

    pending = client.get(f"{API}/invitations", headers=bob.headers).json()
    assert [i["team"]["name"] for i in pending] == ["Mobile"]

    # bob can't see the team until he accepts
    assert client.get(f"{API}/teams/{team['id']}", headers=bob.headers).status_code == 403
    accepted = client.post(f"{API}/invitations/{pending[0]['id']}/accept", headers=bob.headers)
    assert accepted.json()["status"] == "accepted"
    assert client.get(f"{API}/teams/{team['id']}", headers=bob.headers).json()["member_count"] == 2
    assert [t["name"] for t in client.get(f"{API}/teams", headers=bob.headers).json()] == ["Mobile"]

    # accepting twice is refused
    twice = client.post(f"{API}/invitations/{pending[0]['id']}/accept", headers=bob.headers)
    assert twice.status_code == 409


def test_invite_unknown_email_and_non_admin(client, team_with_members):
    team, alice, bob, carol = team_with_members
    unknown = client.post(
        f"{API}/teams/{team['id']}/invitations", json={"email": "ghost@example.com"}, headers=alice.headers
    )
    assert unknown.status_code == 404
    by_member = client.post(
        f"{API}/teams/{team['id']}/invitations", json={"email": carol.email}, headers=bob.headers
    )
    assert by_member.status_code == 403


def test_roles_leave_and_unassign(client, team_with_members):
    team, alice, bob, _ = team_with_members
    task = client.post(
        f"{API}/tasks",
        json={"title": "Bob's job", "team_id": team["id"], "assignee_id": bob.id},
        headers=alice.headers,
    ).json()

    promoted = client.patch(
        f"{API}/teams/{team['id']}/members/{bob.id}", json={"role": "admin"}, headers=alice.headers
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"

    # The owner can't leave; bob can.
    assert client.delete(f"{API}/teams/{team['id']}/members/{alice.id}", headers=alice.headers).status_code == 400
    assert client.delete(f"{API}/teams/{team['id']}/members/{bob.id}", headers=bob.headers).status_code == 204

    # His team task is now unassigned and he can no longer see it.
    assert client.get(f"{API}/tasks/{task['id']}", headers=alice.headers).json()["assignee"] is None
    assert client.get(f"{API}/tasks/{task['id']}", headers=bob.headers).status_code == 404


def test_delete_team_owner_only(client, team_with_members):
    team, alice, bob, _ = team_with_members
    client.post(f"{API}/tasks", json={"title": "t", "team_id": team["id"]}, headers=alice.headers)
    assert client.delete(f"{API}/teams/{team['id']}", headers=bob.headers).status_code == 403
    assert client.delete(f"{API}/teams/{team['id']}", headers=alice.headers).status_code == 204
    assert client.get(f"{API}/tasks", headers=alice.headers).json()["total"] == 0
