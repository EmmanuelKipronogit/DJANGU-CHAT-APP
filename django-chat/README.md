# Django Chat

Real-time one-to-one and group chat built on Django + Channels (WebSockets).
Sign-in is **Google OAuth followed by a 6-digit code emailed to the user**
(a second factor), and nothing in the app is reachable until the code is entered.

## Quick start (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
python manage.py migrate
python manage.py runserver    # http://localhost:8000
```

With nothing else configured it uses SQLite, an in-memory channel layer and
prints verification emails to the **server console**, so you can develop with
no extra services. (The in-memory layer only works with a single process.)

### Google credentials

1. Google Cloud Console → *APIs & Services* → *Credentials* → **Create OAuth client ID** (Web application).
2. Authorised redirect URI: `http://localhost:8000/accounts/google/login/callback/`
   (add your production URL the same way later).
3. Put the client ID and secret in `.env`.

### Trying it without Google

Create a superuser and log in at `/admin/` — the code step still applies:

```bash
python manage.py createsuperuser
```

To test chat between two people, sign in with two different accounts in two
browsers (e.g. a normal and a private window).

## Full stack with Docker (Postgres + Redis)

```bash
cp .env.example .env
docker compose up --build
```

## How sign-in works

1. `Continue with Google` → django-allauth handles OAuth and creates the user.
2. Any login fires `user_logged_in` → `accounts/signals.py` marks the session
   **unverified** and emails a code (`accounts/two_factor.py`).
3. `TwoFactorMiddleware` redirects unverified users to `/verify/` (JSON API
   calls get `403`), and the WebSocket consumer refuses unverified sessions.
4. Codes are stored only as an HMAC, expire after 10 minutes, allow 5 wrong
   attempts, are single-use, and a new code invalidates older ones. Resend has a
   30 s cooldown. All of this is tunable via env vars (see `.env.example`).

## Layout

```
config/            settings (env driven), urls, asgi/wsgi
accounts/          custom email User, Google adapters, 2FA (models, service, middleware, views)
chat/
  models.py        Conversation (direct|group), Membership (role, last_read_at), Message
  services.py      ALL business rules (permissions, DM dedupe, unread, pagination)
  consumers.py     WebSocket protocol (documented at the top of the file)
  views.py         HTML shell + JSON API, thin wrappers over services
  realtime.py      push events to sockets from normal sync code
  static/chat/     chat.js / chat.css (no build step)
```

## Why it scales

- **One socket per user**, joined to a Channels *group per conversation*. A
  message is published once and the channel layer fans it out, so adding web
  servers is just running more Daphne processes pointed at the same Redis.
- **Stateless app servers**: sessions and data live in the database, fan-out in Redis.
- **Services layer**: a mobile app, bot or REST client can reuse `chat.services`
  and the existing `/api/...` endpoints without touching the consumer.
- **Indexed, cursor-paginated history** (`before=<message id>`), unread counts
  computed from `Membership.last_read_at` (no per-message read rows).
- DM uniqueness is enforced by the database (`direct_key`), not by application checks.

Production notes: run behind HTTPS (`DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`), set `REDIS_URL` and `DATABASE_URL`, and use a real
SMTP provider (`EMAIL_HOST`, …). Your proxy must forward WebSocket upgrades on `/ws/`.

## Tests

```bash
pytest
```

Covers the 2FA rules, the verify flow, chat services, the JSON API and the
WebSocket consumer (including cookie auth through the full ASGI stack,
unverified sessions, foreign origins, outsiders, rate limiting).

## Good next steps

- Leave group / remove member / promote admin (`Membership.role` is already there)
- Edit and delete messages, replies, reactions, attachments (add fields to `Message`)
- Presence (online/offline) via a Redis set; push/email notifications for offline users
- Message search (Postgres full-text) and moving sessions to Redis for very large loads
- Large groups: the group fan-out already handles this; move heavy work (notifications) to a task queue

## Known limitations / things to decide

- **User search** matches any active user by name/email (min. 3 characters).
  Fine for a closed community; for a public app switch to invite links or exact-email lookup.
- Messages are stored in plaintext (not end-to-end encrypted).
- Verification emails go out through whichever email backend you configure; if it
  is down, the user can use "Send a new code" once it recovers.
