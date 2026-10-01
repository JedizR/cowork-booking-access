import base64
import os

import psycopg
import pytest

TOKEN = "test-access-api-token-0123456789abcdef"
STAFF = "staff-password-12"
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:55463/postgres"
)

GRANT = {
    "booking_reference": "BK-7KQ2M9",
    "member_ref": "17",
    "space_id": 1,
    "space_name": "Meeting Room A",
    "valid_from": "2026-10-07T09:00:00+07:00",
    "valid_until": "2026-10-07T10:30:00+07:00",
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdefghij")
    monkeypatch.setenv("ACCESS_API_TOKEN", TOKEN)
    monkeypatch.setenv("STAFF_PASSWORD", STAFF)
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8003")
    monkeypatch.setenv("TEST_CLOCK_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS scans, grants, test_clock CASCADE")
    return monkeypatch


@pytest.fixture
def app(env):
    from app import create_app

    app = create_app()
    yield app
    app.db.close()


@pytest.fixture
def client(app):
    return app.test_client()


API = {"Authorization": f"Bearer {TOKEN}"}
KIOSK = {"Authorization": "Basic " + base64.b64encode(f"staff:{STAFF}".encode()).decode()}


def set_clock(client, now):
    assert client.post("/_test/clock", json={"now": now}).status_code == 200


def issue(client, **overrides):
    return client.post("/grants", json={**GRANT, **overrides}, headers=API)


def select_room(client, space_id):
    return client.post("/checkin", data={"space_id": str(space_id)}, headers=KIOSK)


def scan(client, code):
    """POST a scan, follow the 303 and return the kiosk page HTML."""
    res = client.post("/checkin", data={"code": code}, headers=KIOSK)
    assert res.status_code == 303
    return client.get("/checkin", headers=KIOSK).get_data(as_text=True)
