# Provenance

This repository was seeded from `cs403bkk-2026/spacey` at commit
`5a1cf3d90e538f431625cbb959987b7bdbe3c946` by copy-and-prune (ADR-0006), then pruned to the
Access context.

## Removed before the seed commit

`STARTUP_LOG.md`, `LOAD_TEST.md`, `CONTRIBUTING.md`, `scripts/`, `deploy/` (the Nomad job) and
`.github/workflows/delivery.yml` (it pushed to ghcr and deployed on every push to main).
The seed's persona names were replaced by Member A, Member B and Member C.

## What the prune kept

| Kept | Why |
|---|---|
| `get_connection` fail-fast DB connect | Every service keeps it |
| `/health` with `revision` | Every service keeps it |
| `gunicorn.conf.py` query-string scrubbing access log | Every service keeps it |
| `templates/base.html`, `static/style.css` | One shared look |
| `local_time` and `money` filters (money adapted to THB) | Every service keeps them |
| `issue_access_code` | Rewritten in M5 to persist one grant and ticket code per booking (AXS-R01, AXS-R05) |
| Unlock part of `templates/confirmation.html` | Becomes the e-ticket page in M5 (AXS-R10) |

## What the prune deleted

Spaces, bookings, subscriptions, users and members, register/login/logout, metrics and the
dashboard, pay and `validate_card`, `purchase.py`, and their templates (`index.html`,
`register.html`, `login.html`, `my_bookings.html`, `dashboard.html`, `booking_not_found.html`)
and tests (`tests/test_app.py`, `tests/test_purchase.py`). Those belong to Purchase or Payment.

## Seed flaws relevant to Access

From `cowork-booking-docs/DECISIONS.md`, "Seed flaws and where they are handled".

| Flaw | Summary | Handled by | Fixed or out of scope |
|---|---|---|---|
| F4 | Access code regenerated on every unlock, never stored; a spoofable ?code= value is rendered | D20, D22, D28; AXS-R01, AXS-R05, AXS-R06, AXS-R10 | Fixed: one stored grant and ticket code per booking, reused on repeat; the ticket page shows only the stored code |
| F5 | Cancel hard-deletes the booking | D18, D19; AXS-R17 | Fixed: revoke is a state, the grant row and code stay stored |
| F7 | No ownership checks: unlock is anonymous | D17, D22; AXS-R04, AXS-R09, AXS-R11 | Fixed: bearer-token API, view-only ticket link, Staff kiosk behind HTTP Basic |
| F9 | INTEGER overflow gives a 500 | D1; AXS-R03 | Fixed: a space_id outside the INTEGER range gets 400 |
| F10 | Default SECRET_KEY used in deploy | D15; AXS-R18 | Fixed: SECRET_KEY required, fail fast |
| F12 | ?error= text is reflected into pages | D28; AXS-R10, AXS-R14 | Fixed: flash() only |
| F13 | No CSRF tokens on POST forms | D15; AXS-R11, AXS-R18 | Fixed by mitigation: SameSite=Lax cookie holds the kiosk room (ADR-0009, ADR-0016) |
| A2 | Unlock ignores the booking time | D21; AXS-R13 | Fixed: check-in only inside [valid_from, valid_until) |
| A6 | Test hook live in production | D27; AXS-R19 | Fixed: the only test hook is the clock, 404 unless TEST_CLOCK_ENABLED=true |
| A7 | Naive time accepted by one channel | D2; AXS-R03 | Fixed: JSON needs an offset; a naive time gives 400 |
| A8 | One shared connection, DDL at import | D28 | Fixed in part: one connection per worker, 2 workers. Out of scope: reconnect; psycopg_pool is the upgrade path |
| A11 | No injectable clock | D27; AXS-R13, AXS-R19 | Fixed: clock.now() everywhere |
