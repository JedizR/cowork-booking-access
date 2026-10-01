import base64
import hmac
import os
import secrets
from datetime import datetime

import psycopg
import segno
from flask import (
    Flask,
    abort,
    flash,
    get_flashed_messages,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from markupsafe import Markup
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

import access
import clock
from access import LOCAL_TZ

SEED_DEFAULT_KEY = "dev-secret-key-not-for-production"

SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    booking_reference TEXT NOT NULL UNIQUE,
    member_ref TEXT,
    status TEXT NOT NULL CHECK (status IN ('issued', 'checked_in', 'revoked')),
    ticket_code TEXT UNIQUE,
    ticket_token TEXT UNIQUE,
    space_id INTEGER,
    space_name TEXT,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS scans (
    id BIGSERIAL PRIMARY KEY,
    scanned_at TIMESTAMPTZ NOT NULL,
    space_id INTEGER NOT NULL,
    input TEXT NOT NULL,
    result TEXT NOT NULL,
    grant_id TEXT REFERENCES grants (grant_id)
);
CREATE INDEX IF NOT EXISTS scans_room_time ON scans (space_id, scanned_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS test_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    now_override TIMESTAMPTZ
);
"""


def require_secret(name: str, min_len: int) -> str:
    """ADR-0019: refuse to start on a missing, short or public secret."""
    value = os.getenv(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    if len(value) < min_len:
        raise SystemExit(f"{name} must be at least {min_len} characters")
    if name == "SECRET_KEY" and value == SEED_DEFAULT_KEY:
        raise SystemExit("SECRET_KEY is the public seed default")
    return value


def get_connection(database_url: str) -> psycopg.Connection:
    # ponytail: one connection per gunicorn worker (D28); psycopg_pool if load grows.
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    conn.execute(SCHEMA)
    return conn


def local_time(value: datetime) -> str:
    """For the HTML pages: Bangkok time, no seconds or offset clutter."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def iso(value: datetime | None) -> str | None:
    return value.astimezone(LOCAL_TZ).isoformat() if value else None


def error(status: int, code: str, message: str):
    return jsonify(error={"code": code, "message": message}), status


def create_app(database_url: str | None = None) -> Flask:
    secret_key = require_secret("SECRET_KEY", 32)
    api_token = require_secret("ACCESS_API_TOKEN", 32).encode()
    staff_password = require_secret("STAFF_PASSWORD", 12).encode()
    public_url = os.getenv("PUBLIC_URL", "http://localhost:8003").rstrip("/")

    app = Flask(__name__)
    app.secret_key = secret_key
    app.config.update(
        SESSION_COOKIE_NAME="access_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=public_url.startswith("https"),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.db = get_connection(database_url or os.getenv("DATABASE_URL", ""))

    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(lambda satang: f"THB {satang / 100:,.2f}", "money")

    def grant_json(g: dict) -> dict:
        return {
            "grant_id": g["grant_id"],
            "booking_reference": g["booking_reference"],
            "status": g["status"],
            "condition": access.condition(g, clock.now()),
            "ticket_code": access.show_code(g["ticket_code"]),
            "ticket_url": f"{public_url}/t/{g['ticket_token']}" if g["ticket_token"] else None,
            "space_id": g["space_id"],
            "space_name": g["space_name"],
            "valid_from": iso(g["valid_from"]),
            "valid_until": iso(g["valid_until"]),
            "revoked_at": iso(g["revoked_at"]),
        }

    def find_grant(ref: str) -> dict | None:
        with app.db.cursor() as cur:
            cur.execute("SELECT * FROM grants WHERE booking_reference = %s", (ref,))
            return cur.fetchone()

    # ---- API (bearer ACCESS_API_TOKEN, AXS-R04) ----

    @app.before_request
    def check_api_token():
        if not request.path.startswith("/grants"):
            return None
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(token.encode(), api_token):
            return error(401, "unauthorized", "unauthorized")
        return None

    @app.post("/grants")
    def issue_grant():
        """AXS-R01/R02/R03/R05/R06: rewritten seed issue_access_code, now persisted."""
        try:
            data = access.validate_grant(request.get_json(silent=True))
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        existing = find_grant(data["booking_reference"])
        if existing:
            return grant_json(existing), 200
        while True:
            try:
                with app.db.cursor() as cur:
                    cur.execute(
                        "INSERT INTO grants (grant_id, booking_reference, member_ref, status,"
                        " ticket_code, ticket_token, space_id, space_name, valid_from, valid_until)"
                        " VALUES (%s, %s, %s, 'issued', %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (booking_reference) DO NOTHING RETURNING *",
                        (
                            access.new_grant_id(),
                            data["booking_reference"],
                            data["member_ref"],
                            access.new_ticket_code(),
                            secrets.token_urlsafe(16),
                            data["space_id"],
                            data["space_name"],
                            data["valid_from"],
                            data["valid_until"],
                        ),
                    )
                    row = cur.fetchone()
            except UniqueViolation:
                continue  # ticket code (or token) collision: draw again (AXS-R06)
            if row:
                return grant_json(row), 201
            # A concurrent POST for the same reference won the insert.
            return grant_json(find_grant(data["booking_reference"])), 200

    @app.get("/grants/<booking_reference>")
    def get_grant(booking_reference):
        if not access.BOOKING_REF.match(booking_reference):
            return error(400, "invalid_request", "booking_reference must be BK- and 6 symbols")
        grant = find_grant(booking_reference)
        if grant is None:
            return error(404, "not_found", "grant not found")
        return grant_json(grant), 200

    @app.post("/grants/<booking_reference>/revoke")
    def revoke_grant(booking_reference):
        """AXS-R17: idempotent; an unknown reference stores a tombstone (AXS-R02)."""
        if not access.BOOKING_REF.match(booking_reference):
            return error(400, "invalid_request", "booking_reference must be BK- and 6 symbols")
        with app.db.cursor() as cur:
            cur.execute(
                "INSERT INTO grants (grant_id, booking_reference, status, revoked_at)"
                " VALUES (%s, %s, 'revoked', %s)"
                " ON CONFLICT (booking_reference) DO UPDATE SET status = 'revoked',"
                " revoked_at = COALESCE(grants.revoked_at, EXCLUDED.revoked_at)"
                " RETURNING *",
                (access.new_grant_id(), booking_reference, clock.now()),
            )
            return grant_json(cur.fetchone()), 200

    # ---- E-ticket (view-only bearer link, AXS-R09/R10) ----

    @app.get("/t/<ticket_token>")
    def ticket(ticket_token):
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM grants WHERE ticket_token = %s AND ticket_code IS NOT NULL",
                (ticket_token,),
            )
            grant = cur.fetchone()
        if grant is None:
            abort(404)
        now = clock.now()
        code = access.show_code(grant["ticket_code"])
        qr = Markup(segno.make(code, micro=False).svg_inline(scale=6))  # AXS-R07
        page = render_template(
            "ticket.html",
            grant=grant,
            code=code,
            qr=qr,
            badge=access.badge(grant, now),
            day=grant["valid_from"].astimezone(LOCAL_TZ).strftime("%Y-%m-%d"),
            start=grant["valid_from"].astimezone(LOCAL_TZ).strftime("%H:%M"),
            end=grant["valid_until"].astimezone(LOCAL_TZ).strftime("%H:%M"),
        )
        return page, 200, {"Referrer-Policy": "no-referrer"}

    # ---- Kiosk (HTTP Basic STAFF_PASSWORD, AXS-R11..R15) ----

    def staff_ok() -> bool:
        header = request.headers.get("Authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            _, _, password = base64.b64decode(encoded, validate=True).partition(b":")
        except ValueError:
            return False
        return hmac.compare_digest(password, staff_password)

    def rooms() -> list[dict]:
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (space_id) space_id, space_name FROM grants"
                " WHERE ticket_code IS NOT NULL ORDER BY space_id, valid_from DESC"
            )
            return [
                {"id": r["space_id"], "label": access.room_label(r["space_id"], r["space_name"])}
                for r in cur.fetchall()
            ]

    @app.route("/checkin", methods=["GET", "POST"])
    def checkin():
        if not staff_ok():
            return "Staff sign-in required", 401, {"WWW-Authenticate": 'Basic realm="kiosk"'}
        if request.method == "POST":
            if "code" in request.form:
                scan(request.form["code"])
            elif "space_id" in request.form:
                wanted = request.form["space_id"]
                if any(str(r["id"]) == wanted for r in rooms()):
                    session["space_id"] = int(wanted)
                else:
                    flash("Unknown room")
            return redirect("/checkin", 303)

        room_list = rooms()
        selected = next((r for r in room_list if r["id"] == session.get("space_id")), None)
        scans = []
        if selected:
            now = clock.now()
            with app.db.cursor() as cur:
                cur.execute(
                    "SELECT * FROM scans WHERE space_id = %s"
                    " ORDER BY scanned_at DESC, id DESC LIMIT 10",
                    (selected["id"],),
                )
                scans = [
                    {
                        "result": s["result"],
                        "last4": s["input"][-4:],
                        "time": access.when(s["scanned_at"], now),
                    }
                    for s in cur.fetchall()
                ]
        messages = get_flashed_messages(with_categories=True)
        return render_template(
            "checkin.html",
            rooms=room_list,
            selected=selected,
            scans=scans,
            results=[(c[7:], m) for c, m in messages if c.startswith("result:")],
            notes=[m for c, m in messages if not c.startswith("result:")],
        )

    def scan(raw: str) -> None:
        room_id = session.get("space_id")
        if room_id is None:
            flash("Select the room first")
            return
        code = access.normalise(raw)
        if not code:
            flash("Enter a code")
            return
        now = clock.now()
        with app.db.transaction(), app.db.cursor() as cur:
            cur.execute("SELECT * FROM grants WHERE ticket_code = %s FOR UPDATE", (code,))
            grant = cur.fetchone()
            result, reason = access.decide(grant, room_id, now)
            if result == "ok":
                cur.execute(
                    "UPDATE grants SET status = 'checked_in'"
                    " WHERE grant_id = %s AND status = 'issued'",
                    (grant["grant_id"],),
                )
            # ponytail: input capped at 200 chars; a longer scan is junk anyway.
            cur.execute(
                "INSERT INTO scans (scanned_at, space_id, input, result, grant_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                (now, room_id, code[:200], result, grant["grant_id"] if grant else None),
            )
        flash(reason, "result:" + result)

    # ---- Ops ----

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        return jsonify(status="ok", revision=os.getenv("APP_REVISION", "local"))

    @app.post("/_test/clock")
    def set_test_clock():
        """AXS-R19: 404 unless TEST_CLOCK_ENABLED is exactly "true"."""
        if not clock.enabled():
            abort(404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "now" not in body:
            return error(400, "invalid_request", "now is required")
        value = None
        if body["now"] is not None:
            try:
                value = access.parse_instant(body["now"], "now")
            except ValueError as exc:
                return error(400, "invalid_request", str(exc))
        with app.db.cursor() as cur:
            cur.execute(
                "INSERT INTO test_clock (id, now_override) VALUES (1, %s)"
                " ON CONFLICT (id) DO UPDATE SET now_override = EXCLUDED.now_override",
                (value,),
            )
        return jsonify(now=iso(value)), 200

    return app
