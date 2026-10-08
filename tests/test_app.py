from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server


CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')
CARD_ID_PATTERN = re.compile(r'"id":\s*(\d+)')
ATTACHMENT_PATTERN = re.compile(r'/attachments/(\d+)')


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "trubby-data"


@pytest.fixture
def client(data_dir: Path):
    app = server.create_app(data_dir, testing=True)
    with TestClient(app, follow_redirects=True) as test_client:
        yield test_client


def csrf_from(response) -> str:
    match = CSRF_PATTERN.search(response.text)
    assert match, response.text
    return match.group(1)


def current_csrf(client: TestClient, path: str = "/") -> str:
    return csrf_from(client.get(path))


def register(client: TestClient, name: str = "Sam", password: str = "not-serious") -> None:
    token = csrf_from(client.get("/register"))
    response = client.post(
        "/register",
        data={"csrf_token": token, "name": name, "password": password},
    )
    assert response.status_code == 200
    assert 'id="board-columns"' in response.text


def create_card(client: TestClient, title: str) -> int:
    response = client.post(
        "/cards",
        data={"csrf_token": current_csrf(client), "title": title},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger-After-Swap"])
    return int(trigger["trubby:card-created"]["id"])


def test_health_and_authentication_flow(client: TestClient):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    logged_out = client.get("/", follow_redirects=False)
    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == "/login"

    register(client, "Alex", "threadless")
    board = client.get("/")
    assert "Alex" in board.text
    assert "To do" in board.text

    token = csrf_from(board)
    logout = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    login_page = client.get("/login")
    login = client.post(
        "/login",
        data={
            "csrf_token": csrf_from(login_page),
            "name": "aLeX",
            "password": "threadless",
        },
    )
    assert login.status_code == 200
    assert 'id="board-columns"' in login.text


def test_registration_is_case_insensitively_unique(client: TestClient):
    register(client, "Taylor", "some-password")
    second = TestClient(client.app, follow_redirects=True)
    page = second.get("/register")
    response = second.post(
        "/register",
        data={
            "csrf_token": csrf_from(page),
            "name": "tAyLoR",
            "password": "some-password",
        },
    )
    assert "already registered" in response.text


def test_card_thread_attachment_archive_and_restore(client: TestClient, data_dir: Path):
    register(client)
    card_id = create_card(client, "Replace the mystery ZIP")

    drawer = client.get(f"/cards/{card_id}", headers={"HX-Request": "true"})
    assert "<html" not in drawer.text
    assert "Replace the mystery ZIP" in drawer.text

    edited = client.post(
        f"/cards/{card_id}/edit",
        data={
            "csrf_token": current_csrf(client),
            "title": "Replace the serious.zip",
            "description": "Put the useful files and decisions in one place.",
        },
        headers={"HX-Request": "true"},
    )
    assert edited.status_code == 200
    assert "Put the useful files" in edited.text
    assert 'hx-swap-oob="outerHTML"' in edited.text

    moved = client.post(
        f"/cards/{card_id}/move",
        data={
            "csrf_token": current_csrf(client),
            "status": "doing",
            "position": 0,
            "view": "drawer",
        },
        headers={"HX-Request": "true"},
    )
    assert "Doing" in moved.text

    entry = client.post(
        f"/cards/{card_id}/entries",
        data={"csrf_token": current_csrf(client), "body": "This is the only note we need."},
        files=[
            ("files", ("../../serious.zip", b"not actually a zip", "application/zip")),
            ("files", ("screen.png", b"fake png", "image/png")),
        ],
        headers={"HX-Request": "true"},
    )
    assert entry.status_code == 200
    assert "This is the only note" in entry.text
    assert "serious.zip" in entry.text
    assert "data-lightbox" in entry.text
    assert "?preview=true" in entry.text
    attachment_ids = sorted({int(value) for value in ATTACHMENT_PATTERN.findall(entry.text)})
    assert len(attachment_ids) == 2

    download = client.get(f"/attachments/{attachment_ids[0]}")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]

    with sqlite3.connect(data_dir / "trubby.sqlite3") as db:
        stored = db.execute(
            "SELECT original_name, stored_name FROM attachments ORDER BY id"
        ).fetchall()
    assert stored[0][0] == "serious.zip"
    assert "/" not in stored[0][1]
    assert (data_dir / "uploads" / stored[0][1]).is_file()

    archive = client.post(
        f"/cards/{card_id}/archive",
        data={"csrf_token": current_csrf(client)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert archive.headers["HX-Redirect"] == "/"
    assert "Replace the serious.zip" not in client.get("/").text
    archive_page = client.get("/archive")
    assert "Replace the serious.zip" in archive_page.text

    restore = client.post(
        f"/cards/{card_id}/restore",
        data={"csrf_token": csrf_from(archive_page)},
    )
    assert restore.status_code == 200
    assert "Replace the serious.zip" in client.get("/").text


def test_drag_order_is_persisted(client: TestClient, data_dir: Path):
    register(client)
    first = create_card(client, "First")
    second = create_card(client, "Second")
    third = create_card(client, "Third")

    response = client.post(
        f"/cards/{third}/move",
        data={
            "csrf_token": current_csrf(client),
            "status": "todo",
            "position": 0,
            "view": "board",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    with sqlite3.connect(data_dir / "trubby.sqlite3") as db:
        ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM cards WHERE status = 'todo' ORDER BY position"
            ).fetchall()
        ]
    assert ids == [third, first, second]


def test_cards_show_who_created_and_last_updated(client: TestClient):
    register(client, "Sam")
    card_id = create_card(client, "Shared chore")
    board = client.get("/").text
    assert "Created by Sam" in board
    assert "Updated by" not in board

    client.post("/logout", data={"csrf_token": current_csrf(client)})
    register(client, "Riley")
    moved = client.post(
        f"/cards/{card_id}/move",
        data={"csrf_token": current_csrf(client), "status": "doing", "view": "board"},
        headers={"HX-Request": "true"},
    )
    assert "Created by Sam" in moved.text
    assert "Updated by Riley" in moved.text

    drawer = client.get(f"/cards/{card_id}", headers={"HX-Request": "true"})
    assert "last updated by Riley" in drawer.text


def test_upload_limit_rejects_and_cleans_partial_file(client: TestClient, data_dir: Path, monkeypatch):
    register(client)
    card_id = create_card(client, "Too much stuff")
    monkeypatch.setattr(server, "MAX_FILE_SIZE", 8)

    response = client.post(
        f"/cards/{card_id}/entries",
        data={"csrf_token": current_csrf(client), "body": ""},
        files={"files": ("large.bin", b"123456789", "application/octet-stream")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 422
    assert "larger than 25 MB" in response.text
    assert list((data_dir / "uploads").iterdir()) == []


def test_attachment_requires_login(client: TestClient):
    register(client)
    card_id = create_card(client, "Private file")
    response = client.post(
        f"/cards/{card_id}/entries",
        data={"csrf_token": current_csrf(client), "body": ""},
        files={"files": ("notes.txt", b"hello", "text/plain")},
        headers={"HX-Request": "true"},
    )
    attachment_id = int(ATTACHMENT_PATTERN.search(response.text).group(1))

    stranger = TestClient(client.app, follow_redirects=False)
    denied = stranger.get(f"/attachments/{attachment_id}")
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"


def test_interaction_assets_include_modal_lightbox_and_drag_fallback(client: TestClient):
    register(client)
    board = client.get("/")
    assert 'id="image-lightbox"' in board.text
    assert 'aria-modal="true"' in board.text

    javascript = client.get("/static/app.js")
    assert javascript.status_code == 200
    assert "trubby:card-created" in javascript.text
    assert "forceFallback: true" in javascript.text
    assert 'filter: ".move-form, select, button, input, textarea, [data-no-drag]"' in javascript.text

    stylesheet = client.get("/static/app.css")
    assert stylesheet.status_code == 200
    assert "transform: translate(-50%, -50%)" in stylesheet.text
    assert ".image-lightbox" in stylesheet.text
