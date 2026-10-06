# Production deployment

[Back to the product overview](../README.en.md).

Build the backend and frontend production images from the repository root:

```sh
docker build -t hype-radar-backend ./backend
docker build -t hype-radar-frontend ./frontend
```

The backend image installs the locked runtime dependencies, runs as an unprivileged user, and starts
one Uvicorn worker on port 8000. The frontend image builds the Vite application and serves it with
Nginx on port 80. Nginx forwards `/api`, `/health`, and `/ws` to a container reachable as
`backend:8000`; API response buffering is disabled for chat streaming and WebSocket upgrades are
forwarded for live market data.

Database startup, migrations, secrets, and service ordering are deployment concerns and are not run
during image construction. Prepare the production environment from the provided template:

```sh
cp .env.production.example .env.production
chmod 600 .env.production
```

Replace every placeholder in `.env.production`. `POSTGRES_PASSWORD` must be URL-safe and must match
the password embedded in `DATABASE_URL`. Set `OPENROUTER_SITE_URL` to the public HTTPS URL used by
the deployment. The production environment file is ignored by Git.

Create the Basic Auth password file before starting the stack. The following command prompts for the
password instead of exposing it in shell history:

```sh
mkdir -p .secrets
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B -c /auth/htpasswd demo
chmod 644 .secrets/htpasswd
```

Use `-c` only when creating the file. Add another user without replacing existing users:

```sh
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B /auth/htpasswd teammate@example.com
```

An email address can be used as the username for clarity, but Basic Auth does not verify ownership of
that email account. The ignored password file stores bcrypt hashes rather than plaintext passwords.
The application must be accessed through HTTPS because Basic Auth credentials are otherwise only
base64-encoded, not encrypted.

Build and start the complete production stack:

```sh
docker compose --env-file .env.production -f compose.prod.yml up -d --build
docker compose --env-file .env.production -f compose.prod.yml ps --all
```

The stack starts PostgreSQL first, waits until it is healthy, runs `alembic upgrade head` once, then
starts the backend and finally Nginx. Only the configured frontend port is published on the host;
PostgreSQL and FastAPI remain reachable only through their Docker networks. Container logs are
rotated to prevent unbounded disk usage. Nginx protects the frontend and every `/api/` route with
Basic Auth. The `/ws/` routes stream public, read-only market data without a Basic Auth challenge so
browsers do not display a second login dialog during the WebSocket handshake; they are limited to
four simultaneous connections per source IP. General API traffic is limited to 30 requests per
second per source IP. Chat and transcription are additionally limited to 6 requests per minute per
Basic Auth credential, with a short burst allowance.

Inspect the stack or stop it without deleting PostgreSQL data:

```sh
docker compose --env-file .env.production -f compose.prod.yml logs --follow
docker compose --env-file .env.production -f compose.prod.yml down
```

Running `down --volumes` also deletes the PostgreSQL volume and all persisted application data.
