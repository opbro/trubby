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

## Serve HTTPS

Give Trubby a certificate and its private key, and it serves HTTPS on port 443 instead of HTTP on 8000:

```bash
uv run server.py --host 0.0.0.0 --ssl-certfile certs/trubby.crt --ssl-keyfile certs/trubby.key
```

Open <https://your-host>. The `.crt` must be PEM-encoded (include any intermediate certificates after your own), and the `.key` must be an unencrypted PEM key. Linux only lets root bind ports below 1024, so add `--port 8443` there (or use Docker). Session cookies are marked secure automatically.

With Docker, put `trubby.crt` and `trubby.key` in `./certs`, then uncomment the HTTPS lines in `compose.yaml`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRUBBY_HOST` | `127.0.0.1` | Address the server binds to |
| `PORT` | `8000` (`443` with TLS) | Listening port |
| `TRUBBY_SSL_CERTFILE` | unset | TLS certificate (`.crt`); with `TRUBBY_SSL_KEYFILE`, Trubby serves HTTPS |
| `TRUBBY_SSL_KEYFILE` | unset | Unencrypted TLS private key (`.key`) |
| `TRUBBY_DATA_DIR` | `./data` | SQLite, uploads, and generated session secret |
| `TRUBBY_SECRET` | generated | Optional fixed session-signing secret |
| `TRUBBY_SECURE_COOKIE` | `false` (`true` with TLS) | Set to `true` when serving behind HTTPS |
| `TRUBBY_FORWARDED_ALLOW_IPS` | `127.0.0.1` (`*` in Docker) | Proxy IPs trusted for `X-Forwarded-Proto`/`-For`, so URLs use `https://` behind Traefik or another reverse proxy |

Trubby assumes a trusted private network. Registration is open, every account can change every card, and there is no password recovery or admin role.

## Backups

Stop Trubby, then copy the complete data directory or Docker volume. The database, `.session-secret`, and `uploads/` directory belong together.

## Test it

```bash
uv run pytest
```

