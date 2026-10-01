# Contract: Purchase → Access

| Field | Value |
|---|---|
| Provider | Access (`cowork-booking-access`), port 8003 |
| Consumer | Purchase (`cowork-booking-purchase`), through `access_client.py` only |
| Other users | The Member's browser (e-ticket `/t/<ticket_token>`); Staff (kiosk `/checkin`); the e2e suite and contract tests (GET /grants) |
| State | proposed (M2 draft). Becomes agreed at M4 sign-off (tag `contract-v1`), verified by the M6 e2e run |
| OpenAPI | [openapi.yaml](openapi.yaml) |
| Decisions | D2, D6, D17, D18, D19, D20, D21, D22, D27, D28; ADR-0004, ADR-0008, ADR-0010, ADR-0014, ADR-0017, ADR-0019 |

## 1. Purpose and parties

Purchase asks Access to "Issue an authorised grant" ([extraction site]). Access owns grants, their issuance, the ticket code, the e-ticket and the check-in kiosk.

- **Access does:** store one grant per booking reference, issue its ticket code once and reuse it, show the e-ticket, check codes at the kiosk, revoke on request (AXS-R01, AXS-R05, AXS-R13, AXS-R17).
- **Access never:** calls Purchase, Payment or a lock, knows a price, a coverage or a refund, or computes a check-in window (AXS-R04, AXS-R03).
- **Purchase does:** request the grant when a booking becomes confirmed (PUR-R26), send the window (PUR-R27), and revoke it as step 2 of a cancel (PUR-R32).
- **The lock is mocked.** A stored grant, a `checked_in` state or an ok scan is not proof that a door opened. "Payment success ≠ access provisioned ≠ a successful door opening." ([contexts site]; ADR-0017, AXS-R16).

## 2. Base URLs and authentication

| Setting | Example | Used by |
|---|---|---|
| `ACCESS_INTERNAL_URL` (Purchase env) | `http://access:8000` | Purchase, for every API call |
| `PUBLIC_URL` (Access env) | `http://localhost:8003` | Access, to build `ticket_url` |
| `ACCESS_PUBLIC_URL` (Purchase env) | `http://localhost:8003` | Purchase stores and shows a `ticket_url` only when it starts with `ACCESS_PUBLIC_URL/t/`; any other value is a config defect (PUR-R26, PUR-R35) |

Authentication (ADR-0019):

- **API** (`/grants*`): send `Authorization: Bearer <ACCESS_API_TOKEN>`. A missing token, a wrong token or another scheme gets 401 before any validation or lookup, and nothing changes (AXS-R04). Access compares in constant time and refuses to start when `ACCESS_API_TOKEN` is unset, empty or shorter than 32 characters (AXS-R04, ADR-0019).
- **E-ticket** (`/t/<ticket_token>`): no login and no token. The 128-bit ticket token is a view-only bearer link (AXS-R09, ADR-0008).
- **Kiosk** (`/checkin`): HTTP Basic, user `staff`, password `STAFF_PASSWORD`. Access checks only the password and refuses to start when `STAFF_PASSWORD` is unset, empty or shorter than 12 characters (AXS-R11).
- `/health` and `/_test/clock` need no credentials.

## 3. Conventions

- JSON in and out, `Content-Type: application/json`.
- Times: ISO 8601 with an offset. Access stores the instants as sent and answers in Bangkok time, `+07:00` (D2).
- IDs: grant `gr_` + 22 characters (D22). Ticket token: `secrets.token_urlsafe(16)`, 22 characters matching `^[A-Za-z0-9_-]{22}$`, 128 random bits: it is the bearer link to the e-ticket, so it must be unguessable (AXS-R09). Ticket code: 8 symbols from `23456789ABCDEFGHJKMNPQRSTVWXYZ`, stored without the hyphen, returned and shown `XXXX-XXXX`, never starting with `BK` (AXS-R06).
- Unknown request fields are ignored. Consumers ignore unknown response fields.
- Errors always have this shape. The rule rows quote only the text, which is `error.message`. Only `GET /health` keeps the seed shape, `{"status": "error", "error": "database unreachable"}` (section 4.7):

```json
{"error": {"code": "invalid_request", "message": "valid_from needs a UTC offset"}}
```

| Status | error.code | When | Rule |
|---|---|---|---|
| 400 | `invalid_request` | A field is missing, has the wrong format or is out of range. The message names the field. The path's booking reference is checked too | AXS-R03 |
| 401 | `unauthorized` | No token, a wrong token, or the wrong scheme | AXS-R04 |
| 404 | `not_found` | GET of a reference with no grant and no tombstone, message "grant not found" | AXS-R02 |

## 4. Operations

### 4.1 Grant object

| Field | Type | Meaning |
|---|---|---|
| `grant_id` | string | `gr_` + 22 characters |
| `booking_reference` | string | As sent, e.g. `BK-7KQ2M9` |
| `status` | string | Stored state: `issued`, `checked_in` or `revoked` (AXS-R16) |
| `condition` | string or null | Derived on read: `not_yet_valid`, `expired`, `no_show`, or null. Always null for a revoked grant. Never stored. Purchase ignores it (AXS-R16) |
| `ticket_code` | string or null | `H7K3-9QXA`. Null for a tombstone |
| `ticket_url` | string or null | Access `PUBLIC_URL` + `/t/<ticket_token>`. Null for a tombstone |
| `space_id` | integer or null | As sent. Null for a tombstone |
| `space_name` | string or null | As sent. Null for a tombstone |
| `valid_from`, `valid_until` | string or null | The check-in window [valid_from, valid_until), as sent. Null for a tombstone |
| `revoked_at` | string or null | When the first revoke landed |

The API never returns `member_ref`, and the e-ticket never shows it (AXS-R09).

### 4.2 POST /grants

Issue the grant for one confirmed booking. Purchase calls it when the booking becomes confirmed, and only while it stays confirmed with no cancel in progress (PUR-R26, D20). Plan, free and paid bookings get the same call.

Request:

| Field | Type | Required | Constraints | Rule |
|---|---|---|---|---|
| `booking_reference` | string | yes | `^BK-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{6}$` | AXS-R03 |
| `member_ref` | string | yes | Non-empty. Purchase sends the Member's id as text, never the email (PUR-R26) | AXS-R03 |
| `space_id` | integer | yes | 1 to 2147483647 | AXS-R03 |
| `space_name` | string | yes | Non-empty, e.g. `Meeting Room A` | AXS-R03 |
| `valid_from` | string | yes | ISO 8601 with an offset. Purchase sends the booking start (PUR-R27) | AXS-R03 |
| `valid_until` | string | yes | ISO 8601 with an offset, after `valid_from`. Purchase sends the booking end | AXS-R03 |

Access stores the window exactly as sent. It never checks it against opening hours, blocks or buffers (AXS-R03).

Responses:

| Status | Body | When |
|---|---|---|
| 201 | Grant object, `status` issued | No grant and no tombstone for the reference. One grant, one ticket code, one ticket token stored |
| 200 | The stored grant, unchanged | Repeat: a grant exists. The body is ignored, even if it differs (AXS-R01) |
| 200 | Grant object, `status` revoked | The reference has a revoked grant or a tombstone. Nothing is issued (AXS-R02) |
| 400 | Error `invalid_request` | A field is invalid. Nothing stored |
| 401 | Error `unauthorized` | Token check fails. Checked first |

Order of checks: token (401), fields (400), then by `booking_reference`: a revoked grant or tombstone (200 revoked, nothing issued), an existing grant (200 unchanged, body ignored), else insert (201). So a repeat with an invalid body gets 400 even when a grant exists.

Two identical POSTs at the same instant store one grant. Both answers carry the same `grant_id` and code (UNIQUE `booking_reference`, insert that returns the existing row on conflict).

Idempotency: natural key `booking_reference` (ADR-0014). No Idempotency-Key header.

Implements: AXS-R01, AXS-R02, AXS-R03, AXS-R04, AXS-R05, AXS-R06. Consumer side: PUR-R20, PUR-R26, PUR-R27.

### 4.3 GET /grants/{booking_reference}

Read one grant. Purchase does not call it in v1 (PUR-R26). Contract tests and the e2e suite use it.

| Status | Body | When |
|---|---|---|
| 200 | Grant object with `condition` | A grant or a tombstone exists (a tombstone is 200 with `status` revoked, never 404) |
| 400 | Error `invalid_request` | The path is not `BK-` + 6 symbols |
| 401 | Error `unauthorized` | Token check fails |
| 404 | Error `not_found` | No grant and no tombstone |

Implements: AXS-R02, AXS-R04, AXS-R05, AXS-R08, AXS-R16.

### 4.4 POST /grants/{booking_reference}/revoke

Stop the ticket code from opening. Purchase calls it as step 2 of a confirmed-booking cancel, after step 1 commits (PUR-R32, D19). No request body.

| Status | Body | When |
|---|---|---|
| 200 | Grant object, `status` revoked | The grant was issued or checked_in. It is now revoked |
| 200 | The same grant object, `revoked_at` unchanged | Repeat: already revoked. Nothing changes |
| 200 | Tombstone: `status` revoked, `ticket_code` and `ticket_url` null | No grant yet. A tombstone is stored so that a later POST /grants issues nothing (AXS-R02) |
| 400 | Error `invalid_request` | The path is not `BK-` + 6 symbols |
| 401 | Error `unauthorized` | Token check fails |

Revoke changes nothing outside Access. "The booking and successful payment remain recorded. No automatic refund is implied." ([contexts site]; AXS-R17). The refund is between Purchase and Payment.

Idempotency: natural key `booking_reference`. `revoked` is final.

Implements: AXS-R02, AXS-R04, AXS-R16, AXS-R17. Consumer side: PUR-R31, PUR-R32.

### 4.5 GET /t/{ticket_token} (browser)

The e-ticket. Purchase shows the "View e-ticket" link only to the owner or an operator (PUR-R05); the link itself is a bearer link (D17).

| Case | Status | Page |
|---|---|---|
| Known token | 200 | Space name, date and time in Bangkok, "Check-in 09:00-10:30", a status badge, the code `H7K3-9QXA` in large text with its QR (inline SVG from segno, ADR-0010), and "Booking ref (not for entry) BK-7KQ2M9" in small type. No email, no `member_ref`, no form |
| Revoked grant | 200 | Badge Cancelled and a CANCELLED overlay over the code and QR |
| Unknown token | 404 | No hint whether any ticket exists |

The badge follows the stored state and the clock: Issued, Checked in, Expired (now at or after `valid_until`) or Cancelled; when two apply, Cancelled wins over Expired, Expired over Checked in, and Checked in over Issued (AXS-R10). The page never shows a code from the URL. It sends `Referrer-Policy: no-referrer` and prints on one page. It has no navigation; Purchase's "View e-ticket" link opens it in a new tab, so the booking page stays open (purchase-public.md section 7).

Implements: AXS-R05, AXS-R07, AXS-R08, AXS-R09, AXS-R10.

### 4.6 GET and POST /checkin (Staff)

HTTP Basic, user `staff`. Without the right password: 401 with `WWW-Authenticate: Basic`, and no scan is stored (gunicorn's access log keeps the 401 line).

- `GET /checkin`: the room list (from `space_id` and `space_name` in stored grants, tombstones excluded; one entry per `space_id`, listed by `space_id` ascending, labelled with the `space_name` of its grant with the greatest `valid_from` and the room number, "Meeting Room A (room 1)", because an archived space's name can be used again; a room whose space Purchase archived stays listed), the selected room with the same label beside every result, the selected room, an autofocused code input, the last result, and the last 10 scans at that room, newest first by scan time and then by scan id (a frozen test clock gives equal times), with codes masked (`••••-9QXA`) and times as HH:MM for today, YYYY-MM-DD HH:MM for older scans.
- With no grant stored: "No rooms yet: a room appears after its first ticket is issued" and no code input (AXS-R11).
- `POST /checkin` with form field `space_id` and no `code`: select the room. Access stores it in the `access_session` cookie. 303 to `/checkin`. A `space_id` that is not in the room list gets 303 with the flash "Unknown room", and the selection does not change.
- `POST /checkin` with form field `code`: scan at the room in `access_session`. A `space_id` in the same POST is ignored: only a POST without `code` selects the room (AXS-R11). So a cross-site form, which arrives without the SameSite=Lax cookie, has no room and is refused even when it sends a `space_id` (AXS-R18). 303 to `/checkin`, where the result shows once (flashed, never in a query string).
- A scan is therefore two POSTs: first `space_id=1` (select), then `code=H7K3-9QXA` (scan) with the same cookie. The e2e suite does the same.
- A scan with no room selected: 303 to `/checkin` with the flash "Select the room first". No scan stored.
- A code that is blank after normalisation (a scanner's double Enter): 303 with the flash "Enter a code". No scan stored.
- The e2e wrong_room case first issues a grant in a second space, so that Staff can select that room.
- A cross-site form can change the selected room, because the browser resends cached Basic credentials; it cannot scan. The kiosk shows its room with every result (accepted, ADR-0016).

The kiosk upper-cases the input and strips spaces and hyphens, then decides in this order (AXS-R12, AXS-R14, AXS-R13):

| Order | Result | Reason text |
|---|---|---|
| 1 | `unknown_code` | "Code not recognised". Also for a booking reference (BK7KQ2M9) or a ticket token |
| 2 | `revoked` | "This ticket was cancelled" |
| 3 | `wrong_room` | "This ticket is for Meeting Room A (room 1)": the ticket's room with its room number |
| 4 | `not_open_yet` | "opens 09:00" (or "opens 2026-10-07 09:00" on another date) |
| 4 | `closed` | "Check-in closed at 10:30" |
| 4 | `ok` | "Door unlocked (mock)". Re-entry inside the window is ok again |

Every scan is stored with the time, room, normalised input, result and matched grant (AXS-R15). The first ok scan sets the grant `checked_in` (AXS-R16).

Implements: AXS-R11, AXS-R12, AXS-R13, AXS-R14, AXS-R15, AXS-R16, AXS-R18.

### 4.7 GET /health and POST /_test/clock

- `GET /health`: 200 `{"status": "ok", "revision": "<APP_REVISION>"}`; 503 `{"status": "error", "error": "database unreachable"}`.
- `POST /_test/clock` `{"now": "2026-10-07T09:00:00+07:00"}` or `{"now": null}`: 200 `{"now": ...}` only when `TEST_CLOCK_ENABLED` is exactly `true`. Otherwise 404 and nothing stored, and `clock.now()` never reads `test_clock` (AXS-R19, D27, ADR-0013). A `now` without an offset gets 400 and nothing is stored.

## 5. Examples

Standard data: BK-7KQ2M9 (Member A, Meeting Room A, space_id 1, 2026-10-07 09:00-10:30) is confirmed at 2026-10-05 10:05. Every API request carries `Authorization: Bearer <ACCESS_API_TOKEN>`.

### 5.1 Success

```http
POST /grants
Authorization: Bearer <ACCESS_API_TOKEN>
Content-Type: application/json

{"booking_reference": "BK-7KQ2M9", "member_ref": "17", "space_id": 1,
 "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T09:00:00+07:00", "valid_until": "2026-10-07T10:30:00+07:00"}
```

```http
HTTP/1.1 201 Created

{"grant_id": "gr_8Fv2Lq9Xk3Tw7Mz1Rb5Nc4", "booking_reference": "BK-7KQ2M9", "status": "issued",
 "condition": "not_yet_valid", "ticket_code": "H7K3-9QXA",
 "ticket_url": "http://localhost:8003/t/q3Vt9XbLk2Rm8PzW4nHc7A",
 "space_id": 1, "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T09:00:00+07:00", "valid_until": "2026-10-07T10:30:00+07:00",
 "revoked_at": null}
```

Purchase stores `grant_id`, `ticket_url` and `grant_status` issued. The booking page links "View e-ticket" (PUR-R26).

Revoke after Member A cancels at 2026-10-05 11:00 (PUR-R32 step 2):

```http
POST /grants/BK-7KQ2M9/revoke
```

```http
HTTP/1.1 200 OK

{"grant_id": "gr_8Fv2Lq9Xk3Tw7Mz1Rb5Nc4", "booking_reference": "BK-7KQ2M9", "status": "revoked",
 "condition": null, "ticket_code": "H7K3-9QXA",
 "ticket_url": "http://localhost:8003/t/q3Vt9XbLk2Rm8PzW4nHc7A",
 "space_id": 1, "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T09:00:00+07:00", "valid_until": "2026-10-07T10:30:00+07:00",
 "revoked_at": "2026-10-05T11:00:00+07:00"}
```

Check-in at 2026-10-07 09:00, Staff at Meeting Room A, grant issued: `POST /checkin` with `code=H7K3-9QXA`, then 303 to `/checkin`, which shows `data-result="ok"` and "Door unlocked (mock)" (AXS-R13).

### 5.2 Invalid input

A window without an offset (AXS-R03, D2):

```http
POST /grants

{"booking_reference": "BK-7KQ2M9", "member_ref": "17", "space_id": 1,
 "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T09:00:00", "valid_until": "2026-10-07T10:30:00+07:00"}
```

```http
HTTP/1.1 400 Bad Request

{"error": {"code": "invalid_request", "message": "valid_from needs a UTC offset"}}
```

Other 400 messages: `"valid_until must be after valid_from"` (equal instants), `"booking_reference must be BK- and 6 symbols"` (`7KQ2M9`), `"space_name is required"`, `"space_id is out of range"` (2147483648). Nothing is stored.

No token (AXS-R04). The token is checked before the body:

```http
POST /grants/BK-7KQ2M9/revoke
```

```http
HTTP/1.1 401 Unauthorized

{"error": {"code": "unauthorized", "message": "unauthorized"}}
```

The grant stays issued.

### 5.3 Failure

Access does not answer within 5 s during issuance. Purchase keeps the booking confirmed and stores `grant_status` pending. `GET /api/bookings/BK-7KQ2M9` on Purchase then shows `"status": "confirmed", "grant_status": "pending", "ticket_url": null`, and the booking page says "Your e-ticket is being prepared" (PUR-R26). The next booking page open (or GET /api/bookings/<ref>), the owner's Retry or the Operator's Retry resends the same POST /grants (PUR-R26).

The revoke gets no answer. Purchase stores `grant_status` revoke_pending; the booking page shows "Your ticket is still being cancelled; any refund follows." and "Refund of THB 450.00 pending.", the Operator's list flags "Revocation pending" and "Refund pending", and no refund is sent yet (PUR-R32, PUR-Q10). Until the revoke lands, the old code still opens (PUR-Q12).

A late issuance retry meets a tombstone. BK-P4W6RC was revoked before any grant existed (AXS-R02):

```http
POST /grants

{"booking_reference": "BK-P4W6RC", "member_ref": "17", "space_id": 2,
 "space_name": "Focus Pod 1",
 "valid_from": "2026-10-05T13:00:00+07:00", "valid_until": "2026-10-05T13:30:00+07:00"}
```

```http
HTTP/1.1 200 OK

{"grant_id": "gr_4Hs9Pd2Wm6Qx8Kn3Tf7Lz1", "booking_reference": "BK-P4W6RC", "status": "revoked",
 "condition": null, "ticket_code": null, "ticket_url": null,
 "space_id": null, "space_name": null, "valid_from": null, "valid_until": null,
 "revoked_at": "2026-10-05T10:05:00+07:00"}
```

Purchase stores `grant_status` revoked and shows no e-ticket link (PUR-R26 row 6).

Kiosk refusals (AXS-R14): a revoked grant scanned anywhere gives `data-result="revoked"`; the right code at Board Room gives `wrong_room`; the booking reference `BK-7KQ2M9` gives `unknown_code`; H7K3-9QXA at 08:59 gives `not_open_yet` (opens 09:00); at 10:30 it gives `closed`.

### 5.4 Repeat

Purchase's first POST /grants timed out after Access stored the grant. Purchase resends the same body (AXS-R01, AXS-R05):

```http
HTTP/1.1 200 OK

{"grant_id": "gr_8Fv2Lq9Xk3Tw7Mz1Rb5Nc4", "booking_reference": "BK-7KQ2M9", "status": "issued",
 "condition": "not_yet_valid", "ticket_code": "H7K3-9QXA",
 "ticket_url": "http://localhost:8003/t/q3Vt9XbLk2Rm8PzW4nHc7A",
 "space_id": 1, "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T09:00:00+07:00", "valid_until": "2026-10-07T10:30:00+07:00",
 "revoked_at": null}
```

The same `grant_id`, the same code, still one grant. A repeat with another window (10:00-11:30) gets the same 200 with the stored 09:00-10:30 window: a repeat never updates or re-issues.

A repeated revoke returns 200 with `status` revoked and `revoked_at` still `2026-10-05T11:00:00+07:00` (AXS-R17).

### 5.5 Coverage skips collection

**No call is made to Payment.** Member B (plan_active true) books Meeting Room A 2026-10-07 10:30-11:30 as BK-3MZ8QT. Purchase confirms it at once with coverage `plan` (PUR-R19, PUR-R20). The only outbound call is the same POST /grants a paid booking gets:

```http
POST /grants

{"booking_reference": "BK-3MZ8QT", "member_ref": "18", "space_id": 1,
 "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T10:30:00+07:00", "valid_until": "2026-10-07T11:30:00+07:00"}
```

```http
HTTP/1.1 201 Created

{"grant_id": "gr_6Wc3Nv8Jt2Xq5Pb9Hm4Rd7", "booking_reference": "BK-3MZ8QT", "status": "issued",
 "condition": "not_yet_valid", "ticket_code": "M4TR-8WCE",
 "ticket_url": "http://localhost:8003/t/Zk7Pq2Wn9Tb4Xm6Rc3Lv8H",
 "space_id": 1, "space_name": "Meeting Room A",
 "valid_from": "2026-10-07T10:30:00+07:00", "valid_until": "2026-10-07T11:30:00+07:00",
 "revoked_at": null}
```

The request carries no price, amount or coverage, and Access does not ask for one (AXS-R01 row 5). On cancel, Purchase revokes the grant and makes no Payment call (PUR-R30).

## 6. How Purchase applies each answer

| Purchase call | Answer | Purchase does | Rule |
|---|---|---|---|
| POST /grants | 201 or 200 issued or checked_in | Store `grant_id`, `ticket_url`, `grant_status` issued | PUR-R26 |
| POST /grants | 200 revoked | Store `grant_status` revoked. No e-ticket link | PUR-R26 |
| POST /grants | No answer or 5xx | `grant_status` stays pending (set when the booking was confirmed), "being prepared". Retried at the retry points: the booking page (the page or `GET /api/bookings/<ref>`), the owner's Retry and the Operator's Retry | PUR-R26 |
| revoke | 200 revoked | `grant_status` revoked. Then the refund step may run | PUR-R32 |
| revoke | No answer or 5xx | `grant_status` stays revoke_pending (set in cancel step 1), "revocation pending". The refund waits | PUR-R32, PUR-Q10 |

Purchase never stores `checked_in` or a no-show: `grant_status` is Purchase's own record of what it asked for (PUR-T34).

## 7. Timeouts and retries

- Every call uses `timeout=5` seconds (D28, PUR-R35).
- A timeout, a connection error or a 5xx answer means "unreachable". Purchase stores no result for it and marks the step pending.
- Purchase never calls Access while a database transaction is open. For a cancel, step 1 commits first; then Purchase revokes (PUR-R32, PUR-R35).
- Purchase makes one attempt per call inside a request, with no loop or sleep. It retries on the next touch, at the retry points: the booking page (the page or `GET /api/bookings/<ref>`), the owner's Retry (`POST /bookings/<ref>/retry`) and the Operator's Retry (`POST /operator/bookings/<ref>/retry`). Revoke first, then the refund (PUR-R26, PUR-R32).
- Purchase never retries POST /grants for a booking that is cancelled or has a cancel in progress (PUR-R26).
- Retries are safe: POST /grants and revoke are idempotent on `booking_reference` (ADR-0014), and a revoke that lands before an issue leaves a tombstone (AXS-R02).
- Any other 4xx (400, 401) is a defect in Purchase or its config. Purchase logs the operation, the booking reference and the status (never the token), stores nothing and handles the step like "unreachable": pending (PUR-R35).
- Access never retries anything and never calls back (AXS-R04, ADR-0004).

## 8. Test-observable page markers

| Page | Marker | Values |
|---|---|---|
| GET `/t/<ticket_token>` | element `data-ticket-code="H7K3-9QXA"` holding the large code | The code as shown, `XXXX-XXXX` |
| GET `/t/<ticket_token>` | element `data-status="issued"` on the badge | The stored state: `issued`, `checked_in` or `revoked`. The badge text may say Expired; `data-status` stays the stored state |
| GET `/checkin` after a scan | element `data-result="ok"` with the reason text; only the flashed result carries `data-result` | `ok`, `not_open_yet`, `closed`, `revoked`, `wrong_room`, `unknown_code` |
| GET `/checkin` | element `data-selected-space-id="1"` on the selected room | The `space_id` in `access_session`; absent when none is selected |
| GET `/checkin`, each of the last 10 scan rows | `data-scan-result="ok"`, `data-scan-last4="9QXA"` | The same result values; the last 4 symbols shown. Shared kiosk lists are asserted as before-and-after deltas |

## 9. Versioning

- This draft is state proposed. At M4 a consumer-lens reviewer signs it off in `REVIEW_LOG.md`; then `cowork-booking-access` is tagged `contract-v1`, and this text moves to `cowork-booking-access/CONTRACT.md` with `openapi.yaml` (ADR-0005).
- Any change after `contract-v1`, even an added response field, goes through a CONTRACT_CHANGE_REQUEST and the tag `contract-v2`.
- The paths carry no version prefix. Consumers ignore response fields they do not know.
