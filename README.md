# Trubby

**Tracking for people who don't want to track.**

Trubby is one shared, intentionally uncomplicated board for a small team. Cards move through To do, Doing, and Done. Open one to keep its notes, photos, and files together—without labels, sprints, points, workflows, or project-manager cosplay.

## Run it locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv run server.py
```

Open <http://127.0.0.1:8000>, create an account, and add the first card. Trubby creates `data/trubby.sqlite3` and `data/uploads/` automatically.

To share it on a trusted local network:

```bash
uv run server.py --host 0.0.0.0
```

## Run it with Docker

```bash
docker compose up --build
```

Open <http://127.0.0.1:8000>. The `trubby-data` Docker volume keeps the database, uploads, and session secret across container restarts.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRUBBY_HOST` | `127.0.0.1` | Address the server binds to |
| `PORT` | `8000` | HTTP port |
| `TRUBBY_DATA_DIR` | `./data` | SQLite, uploads, and generated session secret |
| `TRUBBY_SECRET` | generated | Optional fixed session-signing secret |
| `TRUBBY_SECURE_COOKIE` | `false` | Set to `true` when serving behind HTTPS |
| `TRUBBY_FORWARDED_ALLOW_IPS` | `127.0.0.1` (`*` in Docker) | Proxy IPs trusted for `X-Forwarded-Proto`/`-For`, so URLs use `https://` behind Traefik or another reverse proxy |

Trubby assumes a trusted private network. Registration is open, every account can change every card, and there is no password recovery or admin role.

## Backups

Stop Trubby, then copy the complete data directory or Docker volume. The database, `.session-secret`, and `uploads/` directory belong together.

## Test it

```bash
uv run pytest
```

