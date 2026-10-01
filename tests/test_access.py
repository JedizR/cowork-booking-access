import re

import psycopg
import pytest
import segno

import access
from conftest import API, DATABASE_URL, GRANT, KIOSK, issue, scan, select_room, set_clock


def count(sql):
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute(sql).fetchone()[0]


# ---- API ----


def test_axs_r01_issue_returns_201_with_code_and_url(client):
    set_clock(client, "2026-10-05T10:05:00+07:00")
    res = issue(client)
    assert res.status_code == 201
    body = res.get_json()
    assert body["status"] == "issued"
    assert body["condition"] == "not_yet_valid"
    assert re.fullmatch(r"gr_[A-Za-z0-9]{22}", body["grant_id"])
    assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}", body["ticket_code"])
    assert re.fullmatch(r"http://localhost:8003/t/[A-Za-z0-9_-]{22}", body["ticket_url"])
    assert body["valid_from"] == "2026-10-07T09:00:00+07:00"
    assert "member_ref" not in body


def test_axs_r01_repeat_returns_stored_grant_unchanged(client):
    first = issue(client).get_json()
    again = issue(client, valid_from="2026-10-07T10:00:00+07:00", valid_until="2026-10-07T11:30:00+07:00")
    assert again.status_code == 200
    assert again.get_json() == first
    assert count("SELECT count(*) FROM grants") == 1


def test_axs_r02_revoke_unknown_stores_tombstone_and_issue_gives_nothing(client):
    set_clock(client, "2026-10-05T10:05:00+07:00")
    tomb = client.post("/grants/BK-P4W6RC/revoke", headers=API)
    assert tomb.status_code == 200
    assert tomb.get_json()["status"] == "revoked"
    assert tomb.get_json()["ticket_code"] is None
    res = issue(client, booking_reference="BK-P4W6RC")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "revoked" and body["ticket_code"] is None and body["ticket_url"] is None
    assert body["revoked_at"] == "2026-10-05T10:05:00+07:00"
    assert count("SELECT count(*) FROM grants WHERE ticket_code IS NOT NULL") == 0


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("valid_from", "2026-10-07T09:00:00", "valid_from needs a UTC offset"),
        ("valid_until", "2026-10-07T09:00:00+07:00", "valid_until must be after valid_from"),
        ("booking_reference", "7KQ2M9", "booking_reference must be BK- and 6 symbols"),
        ("space_name", "", "space_name is required"),
        ("space_id", 2147483648, "space_id is out of range"),
        ("member_ref", "", "member_ref is required"),
    ],
)
def test_axs_r03_invalid_request_400_names_field_stores_nothing(client, field, value, message):
    res = issue(client, **{field: value})
    assert res.status_code == 400
    assert res.get_json() == {"error": {"code": "invalid_request", "message": message}}
    assert count("SELECT count(*) FROM grants") == 0


def test_axs_r04_missing_or_wrong_token_401_before_validation(client):
    assert client.post("/grants", json={}).status_code == 401
    wrong = client.post("/grants", json={}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert wrong.get_json() == {"error": {"code": "unauthorized", "message": "unauthorized"}}
    basic = client.get("/grants/BK-7KQ2M9", headers=KIOSK)
    assert basic.status_code == 401
    assert client.post("/grants/BK-7KQ2M9/revoke").status_code == 401
    assert count("SELECT count(*) FROM grants") == 0


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("ACCESS_API_TOKEN", "", "ACCESS_API_TOKEN is required"),
        ("ACCESS_API_TOKEN", "short", "ACCESS_API_TOKEN must be at least 32 characters"),
        ("STAFF_PASSWORD", "x" * 11, "STAFF_PASSWORD must be at least 12 characters"),
    ],
)
def test_axs_r04_r11_refuse_to_start_on_weak_secret(env, name, value, message):
    from app import create_app

    env.setenv(name, value)
    with pytest.raises(SystemExit, match=message):
        create_app()


def test_axs_r05_code_issued_once_and_reused_everywhere(client):
    first = issue(client).get_json()
    code = first["ticket_code"]
    assert client.get("/grants/BK-7KQ2M9", headers=API).get_json()["ticket_code"] == code
    revoked = client.post("/grants/BK-7KQ2M9/revoke", headers=API).get_json()
    assert revoked["ticket_code"] == code and revoked["grant_id"] == first["grant_id"]
    page = client.get(first["ticket_url"].replace("http://localhost:8003", "")).get_data(as_text=True)
    assert f'data-ticket-code="{code}"' in page


def test_axs_r06_code_alphabet_never_starts_with_bk():
    for _ in range(3000):
        code = access.new_ticket_code()
        assert len(code) == 8 and not code.startswith("BK")
        assert set(code) <= set(access.ALPHABET)


def test_axs_r06_collision_draws_again(client, monkeypatch):
    first = issue(client).get_json()
    taken = first["ticket_code"].replace("-", "")
    draws = iter([taken, "M4TR8WCE"])
    monkeypatch.setattr(access, "new_ticket_code", lambda: next(draws))
    res = issue(client, booking_reference="BK-3MZ8QT")
    assert res.status_code == 201
    assert res.get_json()["ticket_code"] == "M4TR-8WCE"


def test_axs_r16_condition_derived_no_show_and_expired(client):
    issue(client)
    set_clock(client, "2026-10-07T10:30:00+07:00")
    body = client.get("/grants/BK-7KQ2M9", headers=API).get_json()
    assert (body["status"], body["condition"]) == ("issued", "no_show")
    assert client.get("/grants/BK-9QXA2M", headers=API).status_code == 404
    assert client.get("/grants/nope", headers=API).status_code == 400


def test_axs_r17_revoke_idempotent_keeps_revoked_at(client):
    issue(client)
    set_clock(client, "2026-10-05T11:00:00+07:00")
    first = client.post("/grants/BK-7KQ2M9/revoke", headers=API).get_json()
    set_clock(client, "2026-10-05T12:00:00+07:00")
    again = client.post("/grants/BK-7KQ2M9/revoke", headers=API)
    assert again.status_code == 200
    assert again.get_json() == first
    assert first["revoked_at"] == "2026-10-05T11:00:00+07:00"
    assert first["condition"] is None


# ---- E-ticket ----


def ticket_page(client, url):
    return client.get(url.replace("http://localhost:8003", ""))


def test_axs_r07_qr_encodes_exactly_the_code(client):
    body = issue(client).get_json()
    page = ticket_page(client, body["ticket_url"]).get_data(as_text=True)
    assert segno.make(body["ticket_code"], micro=False).svg_inline(scale=6) in page


def test_axs_r09_ticket_is_view_only_bearer_link(client):
    body = issue(client, member_ref="member-17-private").get_json()
    res = ticket_page(client, body["ticket_url"])
    assert res.status_code == 200
    assert res.headers["Referrer-Policy"] == "no-referrer"
    page = res.get_data(as_text=True)
    assert "<form" not in page and "member-17-private" not in page and "@" not in page
    assert "Booking ref (not for entry) BK-7KQ2M9" in page
    assert client.get("/t/AAAAAAAAAAAAAAAAAAAAAA").status_code == 404
    assert client.get("/t/x").status_code == 404


def test_axs_r10_badge_follows_state_and_clock(client):
    body = issue(client).get_json()
    set_clock(client, "2026-10-07T09:00:00+07:00")
    page = ticket_page(client, body["ticket_url"]).get_data(as_text=True)
    assert 'data-status="issued">Issued<' in page and "Check-in 09:00–10:30" in page
    set_clock(client, "2026-10-07T10:30:00+07:00")
    assert ">Expired<" in ticket_page(client, body["ticket_url"]).get_data(as_text=True)
    client.post("/grants/BK-7KQ2M9/revoke", headers=API)
    page = ticket_page(client, body["ticket_url"]).get_data(as_text=True)
    assert 'data-status="revoked">Cancelled<' in page and "CANCELLED" in page
    # The band leads the code half, right above the code and the QR; nothing is drawn over them.
    assert page.index("ticket-body") < page.index("CANCELLED") < page.index("data-ticket-code")
    assert "ticket-stamp" not in page


# ---- Kiosk ----


def test_axs_r11_kiosk_needs_staff_password(client):
    assert client.get("/checkin").status_code == 401
    bad = client.get("/checkin", headers={"Authorization": "Basic c3RhZmY6d3Jvbmc="})
    assert bad.status_code == 401 and bad.headers["WWW-Authenticate"].startswith("Basic")
    # non-ASCII password: 401, never 500
    assert client.get("/checkin", headers={"Authorization": "Basic c3RhZmY6w6k="}).status_code == 401
    page = client.get("/checkin", headers=KIOSK).get_data(as_text=True)
    assert "No rooms yet: a room appears after its first ticket is issued" in page


def test_axs_r11_scan_without_room_refused_and_unknown_room(client):
    code = issue(client).get_json()["ticket_code"]
    page = scan(client, code)
    assert "Select the room first" in page and "data-result" not in page
    assert count("SELECT count(*) FROM scans") == 0
    select_room(client, 3)
    assert "Unknown room" in client.get("/checkin", headers=KIOSK).get_data(as_text=True)
    select_room(client, 1)
    assert 'data-selected-space-id="1"' in client.get("/checkin", headers=KIOSK).get_data(as_text=True)
    assert "Enter a code" in scan(client, "  - ")
    assert count("SELECT count(*) FROM scans") == 0


def test_axs_r12_r13_normalised_code_opens_inside_window_and_reentry(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:00:00+07:00")
    page = scan(client, " " + code.lower().replace("-", " - ") + " ")
    assert 'data-result="ok"' in page and "Door unlocked (mock)" in re.sub(r"<[^>]+>", "", page)
    set_clock(client, "2026-10-07T10:29:00+07:00")
    assert 'data-result="ok"' in scan(client, code)


def test_axs_r13_window_edges(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T08:59:00+07:00")
    page = scan(client, code)
    assert 'data-result="not_open_yet"' in page and "opens 09:00" in page
    set_clock(client, "2026-10-05T10:00:00+07:00")
    page = scan(client, code)
    assert "opens 2026-10-07 09:00" in page and 'come back on <span class="nowrap">Wed 7 Oct</span>.' in page
    set_clock(client, "2026-10-07T10:30:00+07:00")
    page = scan(client, code)
    assert 'data-result="closed"' in page and "Check-in closed at 10:30" in page


def test_axs_r08_booking_reference_is_unknown_code(client):
    issue(client)
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:00:00+07:00")
    page = scan(client, "BK-7KQ2M9")
    assert 'data-result="unknown_code"' in page and "Code not recognised" in page


def test_axs_r14_order_revoked_before_wrong_room(client):
    code = issue(client).get_json()["ticket_code"]
    issue(client, booking_reference="BK-3MZ8QT", space_id=2, space_name="Focus Pod 1")
    select_room(client, 2)
    set_clock(client, "2026-10-07T09:00:00+07:00")
    page = scan(client, code)
    assert 'data-result="wrong_room"' in page and ">Wrong room</p>" in page
    assert "This ticket is for Meeting Room A (room 1). Send the guest there." in page
    client.post("/grants/BK-7KQ2M9/revoke", headers=API)
    page = scan(client, code)
    assert 'data-result="revoked"' in page and "This ticket was cancelled" in page


def test_axs_r15_every_scan_logged_last_10_masked(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:00:00+07:00")
    for _ in range(11):
        scan(client, "ZZZZ")
    page = scan(client, code)
    assert count("SELECT count(*) FROM scans") == 12
    assert page.count("data-scan-result=") == 10
    rows = re.findall(r'data-scan-result="(\w+)" data-scan-last4="(\w+)"', page)
    assert rows[0] == ("ok", code[-4:])
    assert f"••••-{code[-4:]}" in page and code not in page.split("Last 10")[1]


def test_axs_r16_first_ok_scan_checks_in_and_revoke_from_checked_in(client):
    issue(client)
    code = client.get("/grants/BK-7KQ2M9", headers=API).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:02:00+07:00")
    scan(client, code)
    body = client.get("/grants/BK-7KQ2M9", headers=API).get_json()
    assert (body["status"], body["condition"]) == ("checked_in", None)
    set_clock(client, "2026-10-07T10:30:00+07:00")
    assert client.get("/grants/BK-7KQ2M9", headers=API).get_json()["condition"] == "expired"
    assert client.post("/grants/BK-7KQ2M9/revoke", headers=API).get_json()["status"] == "revoked"


# ---- Ops ----


def test_axs_r18_cookie_flags_and_seed_secret_refused(env, client):
    issue(client)
    res = select_room(client, 1)
    cookie = res.headers["Set-Cookie"]
    assert cookie.startswith("access_session=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie
    from app import create_app

    env.setenv("SECRET_KEY", "dev-secret-key-not-for-production")
    with pytest.raises(SystemExit, match="SECRET_KEY is the public seed default"):
        create_app()


def test_axs_r19_test_clock_off_unless_flag_true(env, client):
    assert client.post("/_test/clock", json={"now": "2026-10-07T09:00:00"}).status_code == 400
    env.setenv("TEST_CLOCK_ENABLED", "1")
    assert client.post("/_test/clock", json={"now": "2026-10-07T09:00:00+07:00"}).status_code == 404
    health = client.get("/health")
    assert health.status_code == 200 and set(health.get_json()) == {"status", "revision"}


# ---- Page states (m8 UX) ----


def test_axs_r10_ticket_state_line_follows_window_and_status(client):
    url = issue(client).get_json()["ticket_url"]
    states = [
        ("2026-10-05T11:00:00+07:00", "Valid only in this window"),
        ("2026-10-07T09:00:00+07:00", "Open now"),
        ("2026-10-07T10:30:00+07:00", "This code no longer opens the door"),
    ]
    for now, line in states:
        set_clock(client, now)
        page = ticket_page(client, url).get_data(as_text=True)
        assert line in page and "<dd>Wed 7 Oct</dd>" in page and "1 h 30 min" in page
    assert "Show the code or QR" not in page and "Anyone with this link" not in page
    client.post("/grants/BK-7KQ2M9/revoke", headers=API)
    page = ticket_page(client, url).get_data(as_text=True)
    assert "No longer valid" in page and "Show the code or QR" not in page
    missing = client.get("/t/AAAAAAAAAAAAAAAAAAAAAA")
    assert missing.status_code == 404 and "This page does not exist" in missing.get_data(as_text=True)


def test_axs_r10_checked_in_ticket_says_reentry_until_end(client):
    body = issue(client).get_json()
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:05:00+07:00")
    scan(client, body["ticket_code"])
    page = ticket_page(client, body["ticket_url"]).get_data(as_text=True)
    assert 'data-status="checked_in">Checked in<' in page and "re-entry until 10:30" in page


def test_axs_r13_r15_ok_result_shows_window_and_never_the_full_code(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T09:05:00+07:00")
    page = scan(client, code)
    assert "Booked 09:00–10:30. Re-entry is fine until 10:30." in page
    assert f"Code ••••-{code[-4:]} at Meeting Room A (room 1)" in page
    assert code not in page and code.replace("-", "") not in page


def test_axs_r11_room_picker_lists_rooms_as_cards_and_hides_input_until_chosen(client):
    issue(client)
    issue(client, booking_reference="BK-3MZ8QT", space_id=2, space_name="Focus Pod 1")
    page = client.get("/checkin", headers=KIOSK).get_data(as_text=True)
    assert 'name="space_id" value="1"' in page and 'name="space_id" value="2"' in page
    assert page.count(">Use this room</span>") == 2  # each card says what a tap does
    assert 'name="code"' not in page and "data-selected-space-id" not in page
    select_room(client, 2)
    page = client.get("/checkin", headers=KIOSK).get_data(as_text=True)
    assert 'name="code"' in page and "autofocus" in page and 'name="space_id"' not in page
    assert 'name="change" value="room">Change room</button>' in page  # a real button, GET only
    # "Change room" is its own screen; a GET shows the picker but never selects (row 7).
    page = client.get("/checkin?change=room&space_id=1", headers=KIOSK).get_data(as_text=True)
    assert 'value="2" aria-current="true"' in page and 'name="code"' not in page
    assert 'data-selected-space-id="2"' in page and "Keep Focus Pod 1 (room 2)" in page


def test_day_wording_adds_the_year_only_off_this_year():
    from datetime import datetime

    start = datetime(2026, 10, 3, 9, 0, tzinfo=access.LOCAL_TZ)
    assert access.day(start, datetime(2026, 10, 1, 12, 0, tzinfo=access.LOCAL_TZ)) == "Sat 3 Oct"
    assert access.day(start, datetime(2027, 1, 2, 12, 0, tzinfo=access.LOCAL_TZ)) == "Sat 3 Oct 2026"
    # Bangkok's date, not UTC's: 23:30 UTC on 2 Oct is 06:30 on Sat 3 Oct.
    late = datetime(2026, 10, 2, 23, 30, tzinfo=access.timezone.utc)
    assert access.day(late, start) == "Sat 3 Oct"


def test_duration_wording():
    from datetime import datetime, timedelta

    start = datetime(2026, 10, 7, 9, 0)
    assert [access.duration(start, start + timedelta(minutes=m)) for m in (30, 60, 90, 240)] == [
        "30 min", "1 h", "1 h 30 min", "4 h"]


def test_axs_r08_r13_kiosk_result_comes_first_and_names_the_next_step(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T08:50:00+07:00")
    page = scan(client, code)
    shown = re.sub(r"<[^>]+>", "", page)
    assert ">Not open yet</p>" in page and "Check-in opens 09:00 today. Ask the guest to come back then." in shown
    assert '<time class="num">08:50</time>' in page  # the bar's "Now" follows clock.now()
    assert page.index("data-result=") < page.index('name="code"')
    page = scan(client, "bk-7kq2m9")
    assert 'data-result="unknown_code"' in page and "Code not recognised" in page
    assert "That is a booking reference (BK-…), not a ticket code." in page
    assert ">Booking ref</span>" in page and "••••-Q2M9" in page  # still masked (AXS-R15 row 3)
    missing = client.get("/t/AAAAAAAAAAAAAAAAAAAAAA").get_data(as_text=True)
    assert 'href="http://localhost:8001/bookings/mine"' in missing


def test_axs_r15_log_badges_are_solid_only_for_door_unlocked(client):
    code = issue(client).get_json()["ticket_code"]
    select_room(client, 1)
    set_clock(client, "2026-10-07T08:50:00+07:00")
    scan(client, code)
    set_clock(client, "2026-10-07T09:00:00+07:00")
    page = scan(client, code)
    assert '<span class="badge badge-solid">Door unlocked</span>' in page
    assert '<span class="badge badge-muted">Not open yet</span>' in page and "badge-outline" not in page
    assert "an ok result" not in page and "“Door unlocked” means the scan was accepted" in page
