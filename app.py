import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from flask import Flask, jsonify, render_template
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://access:access@localhost:5443/access")
SECRET_KEY = os.getenv("SECRET_KEY", "")


def get_connection(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        # Fail fast with a clear pointer instead of a raw traceback.
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    return conn


LOCAL_TZ = timezone(timedelta(hours=7))  # Bangkok, no daylight saving


def local_time(value: datetime) -> str:
    """For the HTML pages: Bangkok time, no seconds or offset clutter."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def create_app(database_url: str = DATABASE_URL) -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.db = get_connection(database_url)

    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(lambda satang: f"THB {satang / 100:,.2f}", "money")

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        return jsonify(status="ok", revision=os.getenv("APP_REVISION", "local"))

    def issue_access_code(booking_reference):
        """Kept from the seed (mocked lock code, never stored).
        M5 rewrites it to persist one grant and ticket code per booking."""
        access_code = secrets.token_hex(4)  # mocked lock integration
        return {"booking_reference": booking_reference, "access_code": access_code}, 200

    return app


app = create_app()
