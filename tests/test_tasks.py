API = "/api/v1"


def _create(client, user, **fields):
    resp = client.post(f"{API}/tasks", json=fields, headers=user.headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_task_crud(client, make_user):
    dev = make_user("dev")
    task = _create(
        client, dev, title="  Write docs  ", description="API docs", due_date="2030-01-15T12:00:00"
    )
    assert task["title"] == "Write docs"
    assert task["status"] == "open" and task["priority"] == "medium"
    assert task["due_date"].startswith("2030-01-15T12:00:00")  # naive input treated as UTC
    assert task["creator"]["username"] == "dev"

    got = client.get(f"{API}/tasks/{task['id']}", headers=dev.headers)
    assert got.status_code == 200

    patched = client.patch(
        f"{API}/tasks/{task['id']}",
        json={"priority": "high", "description": None},
        headers=dev.headers,
    ).json()
    assert patched["priority"] == "high" and patched["description"] is None

    assert client.delete(f"{API}/tasks/{task['id']}", headers=dev.headers).status_code == 204
    assert client.get(f"{API}/tasks/{task['id']}", headers=dev.headers).status_code == 404


def test_complete_and_reopen(client, make_user):
    dev = make_user("dev")
    task = _create(client, dev, title="Ship it")
    done = client.post(f"{API}/tasks/{task['id']}/complete", headers=dev.headers).json()
    assert done["status"] == "completed" and done["completed_at"] is not None
    reopened = client.post(f"{API}/tasks/{task['id']}/reopen", headers=dev.headers).json()
    assert reopened["status"] == "open" and reopened["completed_at"] is None


def test_title_cannot_be_nulled(client, make_user):
    dev = make_user("dev")
    task = _create(client, dev, title="Keep title")
    resp = client.patch(f"{API}/tasks/{task['id']}", json={"title": None}, headers=dev.headers)
    assert resp.status_code == 422


def test_personal_tasks_are_private(client, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    task = _create(client, alice, title="Private")
    assert client.get(f"{API}/tasks/{task['id']}", headers=bob.headers).status_code == 404
    assert client.get(f"{API}/tasks", headers=bob.headers).json()["total"] == 0
    # A personal task can't be handed to someone else.
    resp = client.put(
        f"{API}/tasks/{task['id']}/assignee", json={"assignee_id": bob.id}, headers=alice.headers
    )
    assert resp.status_code == 400


def test_filter_search_sort_paginate(client, make_user):
    dev = make_user("dev")
    _create(client, dev, title="Fix login bug", priority="high", due_date="2030-03-01T00:00:00Z")
    _create(client, dev, title="Update README", description="mention login flow", priority="low")
    t3 = _create(client, dev, title="Refactor tasks", priority="medium", due_date="2030-01-01T00:00:00Z")
    client.post(f"{API}/tasks/{t3['id']}/complete", headers=dev.headers)

    def titles(**params):
        resp = client.get(f"{API}/tasks", params=params, headers=dev.headers)
        assert resp.status_code == 200, resp.text
        return [t["title"] for t in resp.json()["items"]]

    assert titles(status="completed") == ["Refactor tasks"]
    assert set(titles(status=["open", "in_progress"])) == {"Fix login bug", "Update README"}
    assert set(titles(q="LOGIN")) == {"Fix login bug", "Update README"}  # title or description
    assert titles(sort_by="priority", order="desc") == ["Fix login bug", "Refactor tasks", "Update README"]
    # Due date ascending; the task with no due date goes last.
    assert titles(sort_by="due_date", order="asc") == ["Refactor tasks", "Fix login bug", "Update README"]
    assert titles(due_before="2030-02-01T00:00:00Z") == ["Refactor tasks"]

    page = client.get(f"{API}/tasks", params={"page_size": 2, "page": 2}, headers=dev.headers).json()
    assert page["total"] == 3 and page["pages"] == 2 and len(page["items"]) == 1

    bad = client.get(f"{API}/tasks", params={"sort_by": "nope"}, headers=dev.headers)
    assert bad.status_code == 422


def test_team_task_assignment_and_assigned_to_me(client, team_with_members):
    team, alice, bob, carol = team_with_members
    task = _create(client, alice, title="Team task", team_id=team["id"], assignee_id=bob.id)
    assert task["assignee"]["username"] == "bob"

    mine = client.get(f"{API}/tasks", params={"assigned_to_me": True}, headers=bob.headers).json()
    assert [t["id"] for t in mine["items"]] == [task["id"]]

    # Non-members can't see it or be assigned to it.
    assert client.get(f"{API}/tasks/{task['id']}", headers=carol.headers).status_code == 404
    resp = client.put(
        f"{API}/tasks/{task['id']}/assignee", json={"assignee_id": carol.id}, headers=alice.headers
    )
    assert resp.status_code == 400

    # A plain member can edit the task but not delete it.
    assert client.post(f"{API}/tasks/{task['id']}/complete", headers=bob.headers).status_code == 200
    assert client.delete(f"{API}/tasks/{task['id']}", headers=bob.headers).status_code == 403
    assert client.delete(f"{API}/tasks/{task['id']}", headers=alice.headers).status_code == 204


def test_cannot_create_task_in_foreign_team(client, team_with_members):
    team, _, _, carol = team_with_members
    resp = client.post(f"{API}/tasks", json={"title": "x", "team_id": team["id"]}, headers=carol.headers)
    assert resp.status_code == 403
