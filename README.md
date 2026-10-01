# cowork-booking-access

The Access context of Cowork Booking. It owns:

- **Grants**: one per booking reference, in state `issued`, `checked_in` or `revoked` (AXS-R01, AXS-R16).
- **Ticket codes**: 8 symbols shown `XXXX-XXXX`, issued once and reused (AXS-R05, AXS-R06).
- **The e-ticket** `GET /t/<ticket_token>`: a view-only bearer link with the code and its QR (AXS-R07 to AXS-R10).
- **The Staff check-in kiosk** `GET/POST /checkin` (AXS-R11 to AXS-R15).

**The door lock is mocked.** An ok scan or a `checked_in` grant is not proof that a door opened (ADR-0017).

Access calls no other service. Only Purchase calls its API (AXS-R04).

- Contract: [CONTRACT.md](CONTRACT.md) and [openapi.yaml](openapi.yaml) (tag `contract-v1`).
- Docs repo: <https://github.com/JedizR/cowork-booking-docs> (rules AXS-R01 to AXS-R19 in `RULES.md`, decisions, ADRs).
- Seed and prune record: [PROVENANCE.md](PROVENANCE.md).

## Quick start

Needs Docker. Ports 8003 (app) and 5443 (Postgres) must be free.

```sh
docker compose up -d --build --wait
curl http://localhost:8003/health
docker compose down
```

`/health` answers `{"revision": "compose", "status": "ok"}`. The kiosk is at
<http://localhost:8003/checkin> (any user name, password `local-staff-pass` in the local compose
file). A room appears in the kiosk after the first grant for it, for example:

```sh
curl -X POST http://localhost:8003/grants \
  -H "Authorization: Bearer local-only-access-api-token-0123456789abcdef" \
  -H "Content-Type: application/json" \
  -d '{"booking_reference": "BK-7KQ2M9", "member_ref": "17", "space_id": 1,
       "space_name": "Meeting Room A",
       "valid_from": "2026-10-07T09:00:00+07:00", "valid_until": "2026-10-07T10:30:00+07:00"}'
```

Open the returned `ticket_url` for the e-ticket. The secrets in `compose.yaml` are local-only
placeholders; override them with environment variables for anything else.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Postgres 16 URL. The app creates its tables at start and exits if the DB is unreachable |
| `SECRET_KEY` | yes | At least 32 characters, never the seed default. Signs the `access_session` cookie |
| `ACCESS_API_TOKEN` | yes | At least 32 characters. Bearer token Purchase sends to `/grants*` |
| `STAFF_PASSWORD` | yes | At least 12 characters. HTTP Basic password for `/checkin` |
| `PUBLIC_URL` | no | Base of `ticket_url`, default `http://localhost:8003`. `https` makes the cookie `Secure` |
| `APP_REVISION` | no | Shown by `/health`, default `local` |
| `TEST_CLOCK_ENABLED` | e2e only | Exactly `true` enables `POST /_test/clock`. Never set it in a deployment |

Copy `.env.example` to `.env` for local runs outside compose. Its secrets are empty on purpose, so
a copied example refuses to start.

## Ports

| Service | Host port | Container port |
|---|---|---|
| app (gunicorn, 2 workers) | 8003 | 8000 |
| db (Postgres 16) | 5443 | 5432 |

## Tests

pytest against a real Postgres 16:

```sh
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
docker run -d --rm --name access-test-pg -e POSTGRES_PASSWORD=postgres -p 55463:5432 postgres:16
DATABASE_URL=postgresql://postgres:postgres@localhost:55463/postgres \
  .venv/bin/python -m pytest -q --junitxml=reports/junit.xml
docker rm -f access-test-pg
```

Each test is named after the rule it proves (`test_axs_r13_window_edges`). Each test starts on a
clean schema.

## Dependencies

All pinned in `requirements.txt`: Flask 3.1.2, gunicorn 23.0.0, psycopg[binary] 3.2.13,
pytest 8.4.2, requests 2.32.3 (kept pinned for the shared stack; Access makes no outbound calls),
and **segno 1.6.6**, the one extra runtime dependency, which draws the ticket QR as inline SVG
(ADR-0010).

## Layout

`app.py` (routes, schema, startup checks), `access.py` (codes, validation, window and kiosk
decisions), `clock.py` (`clock.now()`, the test clock), `templates/` (`base.html`, `ticket.html`,
`checkin.html`, `404.html`), `static/style.css` (the shared design system, copied unchanged from
`drafts/design/style.css`), `static/access.css` (e-ticket and kiosk only) and `static/access.js`
(progressive enhancement: Print ticket, clearing an old kiosk result).
