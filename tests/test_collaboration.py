"""Comments, attachments, notifications (REST + WebSocket) and AI helpers."""

import pytest
from starlette.websockets import WebSocketDisconnect

API = "/api/v1"


@pytest.fixture
def team_task(client, team_with_members):
    team, alice, bob, carol = team_with_members
    task = client.post(
        f"{API}/tasks", json={"title": "Design API", "team_id": team["id"]}, headers=alice.headers
    ).json()
    return task, alice, bob, carol


def test_comments(client, team_task):
    task, alice, bob, carol = team_task
    url = f"{API}/tasks/{task['id']}/comments"
    created = client.post(url, json={"content": "Started on this"}, headers=bob.headers)
    assert created.status_code == 201
    comment = created.json()

    assert [c["content"] for c in client.get(url, headers=alice.headers).json()] == ["Started on this"]
    assert client.get(url, headers=carol.headers).status_code == 404  # not on the team

    assert client.patch(f"{url}/{comment['id']}", json={"content": "x"}, headers=alice.headers).status_code == 403
    edited = client.patch(f"{url}/{comment['id']}", json={"content": "Halfway"}, headers=bob.headers)
    assert edited.json()["content"] == "Halfway"
    assert client.post(url, json={"content": "   "}, headers=bob.headers).status_code == 422
    # The task creator may remove any comment on it.
    assert client.delete(f"{url}/{comment['id']}", headers=alice.headers).status_code == 204


def test_attachments(client, team_task):
    task, alice, bob, _ = team_task
    url = f"{API}/tasks/{task['id']}/attachments"
    up = client.post(
        url, files={"file": ("../../notes.txt", b"hello world", "text/plain")}, headers=bob.headers
    )
    assert up.status_code == 201, up.text
    att = up.json()
    assert att["filename"] == "notes.txt" and att["size_bytes"] == 11

    download = client.get(f"{url}/{att['id']}/download", headers=alice.headers)
    assert download.status_code == 200 and download.content == b"hello world"

    empty = client.post(url, files={"file": ("e.txt", b"", "text/plain")}, headers=bob.headers)
    assert empty.status_code == 400

    assert client.delete(f"{url}/{att['id']}", headers=bob.headers).status_code == 204
    assert client.get(url, headers=bob.headers).json() == []


def test_notifications_on_assign_and_comment(client, team_task):
    task, alice, bob, _ = team_task
    client.put(f"{API}/tasks/{task['id']}/assignee", json={"assignee_id": bob.id}, headers=alice.headers)
    client.post(f"{API}/tasks/{task['id']}/comments", json={"content": "On it"}, headers=bob.headers)

    bob_types = [n["type"] for n in client.get(f"{API}/notifications", headers=bob.headers).json()]
    assert "task_assigned" in bob_types
    alice_notes = client.get(f"{API}/notifications", params={"unread_only": True}, headers=alice.headers).json()
    assert alice_notes[0]["type"] == "task_commented"

    read = client.post(f"{API}/notifications/{alice_notes[0]['id']}/read", headers=alice.headers)
    assert read.json()["is_read"] is True
    assert client.post(f"{API}/notifications/read-all", headers=bob.headers).status_code == 204
    assert client.get(f"{API}/notifications", params={"unread_only": True}, headers=bob.headers).json() == []


def test_websocket_receives_live_notification(client, team_task):
    task, alice, bob, _ = team_task
    with client.websocket_connect(f"{API}/ws/notifications?token={bob.token}") as ws:
        assert ws.receive_json()["event"] == "connected"
        client.put(
            f"{API}/tasks/{task['id']}/assignee", json={"assignee_id": bob.id}, headers=alice.headers
        )
        message = ws.receive_json()
        assert message["event"] == "notification"
        assert message["data"]["type"] == "task_assigned"
        assert message["data"]["task_id"] == task["id"]
        ws.send_text("ping")
        assert ws.receive_json() == {"event": "pong"}


def test_websocket_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/ws/notifications?token=bad") as ws:
            ws.receive_json()


def test_ai_helpers_fall_back_to_template(client, team_task):
    task, alice, _, _ = team_task
    resp = client.post(
        f"{API}/ai/task-description",
        json={"title": "Add dark mode", "context": "settings page"},
        headers=alice.headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "template" and "Add dark mode" in body["text"]

    summary = client.post(f"{API}/ai/tasks/{task['id']}/summary", headers=alice.headers)
    assert summary.status_code == 200 and "Design API" in summary.json()["text"]
