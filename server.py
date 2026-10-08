from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pwdlib import PasswordHash
from starlette.middleware.sessions import SessionMiddleware


ROOT = Path(__file__).resolve().parent
STATUSES = ("todo", "doing", "done")
STATUS_LABELS = {"todo": "To do", "doing": "Doing", "done": "Done"}
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_FILES_PER_ENTRY = 10
SAFE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}
CHUNK_SIZE = 1024 * 1024
password_hash = PasswordHash.recommended()


class AuthenticationRequired(Exception):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_name(value: str) -> str:
    return " ".join(value.strip().split())


def format_size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    configured = data_dir or os.environ.get("TRUBBY_DATA_DIR") or ROOT / "data"
    return Path(configured).expanduser().resolve()


def load_session_secret(data_dir: Path) -> str:
    configured = os.environ.get("TRUBBY_SECRET")
    if configured:
        return configured

    secret_path = data_dir / ".session-secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()

    value = secrets.token_urlsafe(48)
    secret_path.write_text(value, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return value


def initialize_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]

        if version < 1:
            db.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'todo'
                        CHECK (status IN ('todo', 'doing', 'done')),
                    position INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT,
                    created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX cards_board_order
                    ON cards(archived_at, status, position);

                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    body TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX entries_card_time ON entries(card_id, created_at, id);

                CREATE TABLE attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                PRAGMA user_version = 1;
                """
            )

        if version < 2:
            db.executescript(
                """
                ALTER TABLE cards ADD COLUMN updated_by INTEGER REFERENCES users(id);

                PRAGMA user_version = 2;
                """
            )


def create_app(data_dir: str | Path | None = None, *, testing: bool = False) -> FastAPI:
    resolved_data_dir = resolve_data_dir(data_dir)
    upload_dir = resolved_data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    db_path = resolved_data_dir / "trubby.sqlite3"
    initialize_database(db_path)

    app = FastAPI(title="Trubby", docs_url=None, redoc_url=None)
    app.state.data_dir = resolved_data_dir
    app.state.db_path = db_path
    app.state.upload_dir = upload_dir
    app.state.testing = testing

    app.add_middleware(
        SessionMiddleware,
        secret_key=load_session_secret(resolved_data_dir),
        session_cookie="trubby_session",
        same_site="lax",
        https_only=os.environ.get("TRUBBY_SECURE_COOKIE", "").lower() in {"1", "true", "yes"},
        max_age=60 * 60 * 24 * 30,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.globals.update(
        format_size=format_size,
        status_labels=STATUS_LABELS,
        safe_image_types=SAFE_IMAGE_TYPES,
    )
    app.state.templates = templates

    register_routes(app)
    return app


@contextmanager
def database(request: Request) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(request.app.state.db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def verify_csrf(request: Request) -> None:
    provided = request.headers.get("X-CSRF-Token")
    if not provided:
        form = await request.form()
        value = form.get("csrf_token")
        provided = value if isinstance(value, str) else None
    expected = request.session.get("csrf_token", "")
    if not provided or not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="That form expired. Refresh and try again.")


def current_user(request: Request) -> sqlite3.Row | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with database(request) as db:
        return db.execute("SELECT id, name, created_at FROM users WHERE id = ?", (user_id,)).fetchone()


def require_user(request: Request) -> sqlite3.Row:
    user = current_user(request)
    if user is None:
        raise AuthenticationRequired
    return user


def render(request: Request, template_name: str, **context: Any) -> HTMLResponse:
    context.update(
        current_user=current_user(request),
        csrf_token=csrf_token(request),
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )


def fetch_board_cards(db: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = db.execute(
        """
        SELECT c.*,
               creator.name AS creator_name,
               updater.name AS updater_name,
               (SELECT COUNT(*) FROM entries e WHERE e.card_id = c.id) AS entry_count,
               (SELECT COUNT(*) FROM attachments a
                JOIN entries e ON e.id = a.entry_id WHERE e.card_id = c.id) AS attachment_count
        FROM cards c
        JOIN users creator ON creator.id = c.created_by
        LEFT JOIN users updater ON updater.id = c.updated_by
        WHERE c.archived_at IS NULL
        ORDER BY c.status, c.position, c.id
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {status: [] for status in STATUSES}
    for row in rows:
        grouped[row["status"]].append(row)
    return grouped


def fetch_card(db: sqlite3.Connection, card_id: int) -> dict[str, Any] | None:
    card = db.execute(
        """
        SELECT c.*, u.name AS creator_name, updater.name AS updater_name
        FROM cards c
        JOIN users u ON u.id = c.created_by
        LEFT JOIN users updater ON updater.id = c.updated_by
        WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    if card is None:
        return None

    entries = db.execute(
        """
        SELECT e.*, u.name AS author_name
        FROM entries e JOIN users u ON u.id = e.user_id
        WHERE e.card_id = ?
        ORDER BY e.created_at, e.id
        """,
        (card_id,),
    ).fetchall()
    attachments = db.execute(
        """
        SELECT a.*
        FROM attachments a
        JOIN entries e ON e.id = a.entry_id
        WHERE e.card_id = ?
        ORDER BY a.id
        """,
        (card_id,),
    ).fetchall()
    by_entry: dict[int, list[sqlite3.Row]] = {}
    for attachment in attachments:
        by_entry.setdefault(attachment["entry_id"], []).append(attachment)
    return {"card": card, "entries": entries, "attachments_by_entry": by_entry}


def normalize_positions(db: sqlite3.Connection, status: str) -> None:
    ids = db.execute(
        """
        SELECT id FROM cards
        WHERE status = ? AND archived_at IS NULL
        ORDER BY position, id
        """,
        (status,),
    ).fetchall()
    for position, row in enumerate(ids):
        db.execute("UPDATE cards SET position = ? WHERE id = ?", (position, row["id"]))


def next_position(db: sqlite3.Connection, status: str) -> int:
    value = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE status = ? AND archived_at IS NULL",
        (status,),
    ).fetchone()[0]
    return int(value)


def board_columns_response(request: Request) -> HTMLResponse:
    with database(request) as db:
        cards = fetch_board_cards(db)
    return render(request, "partials/board_columns.html", cards=cards)


def card_update_response(request: Request, card_id: int) -> HTMLResponse:
    with database(request) as db:
        detail = fetch_card(db, card_id)
        cards = fetch_board_cards(db)
    if detail is None:
        raise HTTPException(status_code=404)
    return render(request, "partials/card_update.html", detail=detail, cards=cards)


def form_error(message: str, target: str, *, status_code: int = 422) -> HTMLResponse:
    return HTMLResponse(
        f'<p class="form-error" role="alert">{html_escape(message)}</p>',
        status_code=status_code,
        headers={"HX-Retarget": target, "HX-Reswap": "innerHTML"},
    )


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def register_routes(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationRequired)
    async def authentication_required(request: Request, _exc: AuthenticationRequired) -> Response:
        if is_htmx(request):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        try:
            with database(request) as db:
                db.execute("SELECT 1").fetchone()
            return JSONResponse({"status": "ok"})
        except sqlite3.Error:
            return JSONResponse({"status": "error"}, status_code=503)

    @app.get("/register", response_class=HTMLResponse)
    async def register_page(request: Request) -> Response:
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "register.html")

    @app.post("/register", response_class=HTMLResponse)
    async def register(
        request: Request,
        name: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        await verify_csrf(request)
        name = clean_name(name)
        error: str | None = None
        if not 2 <= len(name) <= 40:
            error = "Use a name between 2 and 40 characters."
        elif len(password) < 8:
            error = "Use at least 8 characters for your password."
        if error:
            return render(request, "register.html", error=error, entered_name=name)

        with database(request) as db:
            try:
                cursor = db.execute(
                    "INSERT INTO users(name, password_hash, created_at) VALUES (?, ?, ?)",
                    (name, password_hash.hash(password), utc_now()),
                )
            except sqlite3.IntegrityError:
                return render(
                    request,
                    "register.html",
                    error="That name is already registered.",
                    entered_name=name,
                )
            user_id = cursor.lastrowid

        request.session.clear()
        request.session["user_id"] = user_id
        csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login", response_class=HTMLResponse)
    async def login(
        request: Request,
        name: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        await verify_csrf(request)
        name = clean_name(name)
        with database(request) as db:
            user = db.execute(
                "SELECT id, name, password_hash FROM users WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        if user is None or not password_hash.verify(password, user["password_hash"]):
            return render(
                request,
                "login.html",
                error="That name and password do not match.",
                entered_name=name,
            )
        request.session.clear()
        request.session["user_id"] = user["id"]
        csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        require_user(request)
        await verify_csrf(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def board(request: Request) -> HTMLResponse:
        require_user(request)
        with database(request) as db:
            cards = fetch_board_cards(db)
        return render(request, "board.html", cards=cards, detail=None)

    @app.get("/board", response_class=HTMLResponse)
    async def board_fragment(request: Request) -> HTMLResponse:
        require_user(request)
        return board_columns_response(request)

    @app.post("/cards", response_class=HTMLResponse)
    async def create_card(
        request: Request,
        title: str = Form(...),
    ) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        title = title.strip()
        if not 1 <= len(title) <= 120:
            return form_error("Give the card a title between 1 and 120 characters.", "#new-card-error")
        now = utc_now()
        with database(request) as db:
            cursor = db.execute(
                """
                INSERT INTO cards(title, description, status, position, created_by, created_at, updated_at)
                VALUES (?, '', 'todo', ?, ?, ?, ?)
                """,
                (title, next_position(db, "todo"), user["id"], now, now),
            )
            card_id = cursor.lastrowid
        response = board_columns_response(request)
        response.headers["HX-Trigger-After-Swap"] = (
            f'{{"trubby:card-created": {{"id": {card_id}}}}}'
        )
        return response

    @app.get("/cards/{card_id}", response_class=HTMLResponse)
    async def card_detail(request: Request, card_id: int) -> HTMLResponse:
        require_user(request)
        with database(request) as db:
            detail = fetch_card(db, card_id)
            if detail is None:
                raise HTTPException(status_code=404)
            if is_htmx(request):
                return render(request, "partials/card_drawer.html", detail=detail)
            cards = fetch_board_cards(db)
        return render(request, "board.html", cards=cards, detail=detail)

    @app.get("/drawer/close", response_class=HTMLResponse)
    async def close_drawer(request: Request) -> HTMLResponse:
        require_user(request)
        return HTMLResponse('<div id="card-drawer-container"></div>')

    @app.post("/cards/{card_id}/edit", response_class=HTMLResponse)
    async def edit_card(
        request: Request,
        card_id: int,
        title: str = Form(...),
        description: str = Form(""),
    ) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        title = title.strip()
        description = description.strip()
        if not 1 <= len(title) <= 120:
            return form_error("Use a title between 1 and 120 characters.", "#card-edit-error")
        if len(description) > 5000:
            return form_error("Keep the description under 5,000 characters.", "#card-edit-error")
        with database(request) as db:
            cursor = db.execute(
                "UPDATE cards SET title = ?, description = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (title, description, utc_now(), user["id"], card_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404)
        return card_update_response(request, card_id)

    @app.post("/cards/{card_id}/move", response_class=HTMLResponse)
    async def move_card(
        request: Request,
        card_id: int,
        status: str = Form(...),
        position: int | None = Form(None),
        view: str = Form("board"),
    ) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail="Unknown status")
        with database(request) as db:
            card = db.execute(
                "SELECT status FROM cards WHERE id = ? AND archived_at IS NULL",
                (card_id,),
            ).fetchone()
            if card is None:
                raise HTTPException(status_code=404)
            old_status = card["status"]
            target_ids = [
                row["id"]
                for row in db.execute(
                    """
                    SELECT id FROM cards
                    WHERE status = ? AND archived_at IS NULL AND id != ?
                    ORDER BY position, id
                    """,
                    (status, card_id),
                ).fetchall()
            ]
            target_position = len(target_ids) if position is None else max(0, min(position, len(target_ids)))
            target_ids.insert(target_position, card_id)
            now = utc_now()
            db.execute(
                "UPDATE cards SET status = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (status, now, user["id"], card_id),
            )
            for index, target_id in enumerate(target_ids):
                db.execute("UPDATE cards SET position = ? WHERE id = ?", (index, target_id))
            if old_status != status:
                normalize_positions(db, old_status)
        if view == "drawer":
            return card_update_response(request, card_id)
        return board_columns_response(request)

    @app.post("/cards/{card_id}/archive")
    async def archive_card(request: Request, card_id: int) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        with database(request) as db:
            card = db.execute(
                "SELECT status FROM cards WHERE id = ? AND archived_at IS NULL",
                (card_id,),
            ).fetchone()
            if card is None:
                raise HTTPException(status_code=404)
            db.execute(
                "UPDATE cards SET archived_at = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (utc_now(), utc_now(), user["id"], card_id),
            )
            normalize_positions(db, card["status"])
        if is_htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": "/"})
        return RedirectResponse("/", status_code=303)

    @app.get("/archive", response_class=HTMLResponse)
    async def archive(request: Request) -> HTMLResponse:
        require_user(request)
        with database(request) as db:
            cards = db.execute(
                """
                SELECT c.*, u.name AS creator_name
                FROM cards c JOIN users u ON u.id = c.created_by
                WHERE c.archived_at IS NOT NULL
                ORDER BY c.archived_at DESC, c.id DESC
                """
            ).fetchall()
        return render(request, "archive.html", cards=cards)

    @app.post("/cards/{card_id}/restore")
    async def restore_card(request: Request, card_id: int) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        with database(request) as db:
            cursor = db.execute(
                """
                UPDATE cards
                SET archived_at = NULL, status = 'todo', position = ?, updated_at = ?, updated_by = ?
                WHERE id = ? AND archived_at IS NOT NULL
                """,
                (next_position(db, "todo"), utc_now(), user["id"], card_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404)
        return RedirectResponse("/archive", status_code=303)

    @app.post("/cards/{card_id}/entries", response_class=HTMLResponse)
    async def add_entry(
        request: Request,
        card_id: int,
        body: str = Form(""),
        files: list[UploadFile] = File(default=[]),
    ) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        body = body.strip()
        usable_files = [upload for upload in files if upload.filename]
        if not body and not usable_files:
            return form_error("Add a note or choose at least one file.", "#entry-error")
        if len(body) > 10_000:
            return form_error("Keep notes under 10,000 characters.", "#entry-error")
        if len(usable_files) > MAX_FILES_PER_ENTRY:
            return form_error(f"Upload at most {MAX_FILES_PER_ENTRY} files at once.", "#entry-error")

        saved: list[dict[str, Any]] = []
        created_paths: list[Path] = []
        try:
            for upload in usable_files:
                stored_name = secrets.token_hex(24)
                destination = request.app.state.upload_dir / stored_name
                created_paths.append(destination)
                size = 0
                with destination.open("wb") as output:
                    while chunk := await upload.read(CHUNK_SIZE):
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            raise ValueError(f"{Path(upload.filename or 'File').name} is larger than 25 MB.")
                        output.write(chunk)
                saved.append(
                    {
                        "original_name": Path(upload.filename or "file").name[:255] or "file",
                        "stored_name": stored_name,
                        "content_type": (upload.content_type or "application/octet-stream")[:255],
                        "size": size,
                    }
                )

            with database(request) as db:
                exists = db.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404)
                now = utc_now()
                cursor = db.execute(
                    "INSERT INTO entries(card_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                    (card_id, user["id"], body, now),
                )
                entry_id = cursor.lastrowid
                for item in saved:
                    db.execute(
                        """
                        INSERT INTO attachments(
                            entry_id, original_name, stored_name, content_type, size, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            item["original_name"],
                            item["stored_name"],
                            item["content_type"],
                            item["size"],
                            now,
                        ),
                    )
                db.execute(
                    "UPDATE cards SET updated_at = ?, updated_by = ? WHERE id = ?",
                    (now, user["id"], card_id),
                )
        except ValueError as exc:
            for path in created_paths:
                path.unlink(missing_ok=True)
            return form_error(str(exc), "#entry-error")
        except Exception:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            for upload in usable_files:
                await upload.close()

        return card_update_response(request, card_id)

    @app.get("/attachments/{attachment_id}")
    async def attachment(
        request: Request,
        attachment_id: int,
        preview: bool = False,
    ) -> Response:
        require_user(request)
        with database(request) as db:
            item = db.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        if item is None:
            raise HTTPException(status_code=404)
        path = request.app.state.upload_dir / item["stored_name"]
        if not path.is_file():
            raise HTTPException(status_code=404)
        can_preview = preview and item["content_type"] in SAFE_IMAGE_TYPES
        disposition = "inline" if can_preview else "attachment"
        return FileResponse(
            path,
            media_type=item["content_type"] if can_preview else "application/octet-stream",
            filename=item["original_name"],
            content_disposition_type=disposition,
        )

    @app.post("/attachments/{attachment_id}/remove", response_class=HTMLResponse)
    async def remove_attachment(request: Request, attachment_id: int) -> Response:
        user = require_user(request)
        await verify_csrf(request)
        with database(request) as db:
            item = db.execute(
                """
                SELECT a.*, e.card_id, e.body
                FROM attachments a JOIN entries e ON e.id = a.entry_id
                WHERE a.id = ?
                """,
                (attachment_id,),
            ).fetchone()
            if item is None:
                raise HTTPException(status_code=404)
            db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            remaining = db.execute(
                "SELECT COUNT(*) FROM attachments WHERE entry_id = ?",
                (item["entry_id"],),
            ).fetchone()[0]
            if remaining == 0 and not item["body"]:
                db.execute("DELETE FROM entries WHERE id = ?", (item["entry_id"],))
            db.execute(
                "UPDATE cards SET updated_at = ?, updated_by = ? WHERE id = ?",
                (utc_now(), user["id"], item["card_id"]),
            )
            card_id = item["card_id"]
            stored_name = item["stored_name"]
        (request.app.state.upload_dir / stored_name).unlink(missing_ok=True)
        return card_update_response(request, card_id)


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Trubby.")
    parser.add_argument("--host", default=os.environ.get("TRUBBY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--forwarded-allow-ips",
        default=os.environ.get("TRUBBY_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        help="Comma-separated proxy IPs trusted for X-Forwarded-* headers, or '*'",
    )
    args = parser.parse_args()
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
