"""Access rules that need no request or database: codes, validation, the window."""

import re
import secrets
import string
from datetime import datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=7))  # Bangkok, no daylight saving (D2)
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # D22: no 0 O 1 I L U
BOOKING_REF = re.compile(r"^BK-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{6}$")
INT_MAX = 2**31 - 1


def new_grant_id() -> str:
    chars = string.ascii_letters + string.digits
    return "gr_" + "".join(secrets.choice(chars) for _ in range(22))


def new_ticket_code() -> str:
    """AXS-R06: 8 symbols, never starting with BK (UNIQUE is checked by the insert)."""
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        if not code.startswith("BK"):
            return code


def show_code(code: str | None) -> str | None:
    return f"{code[:4]}-{code[4:]}" if code else None


def normalise(raw: str) -> str:
    """AXS-R12: upper-case, strip spaces and hyphens, nothing else."""
    return re.sub(r"[\s-]", "", raw.upper())


def parse_instant(value, field: str) -> datetime:
    """ISO 8601 with an offset (D2); raises ValueError naming the field."""
    if value is None or value == "":
        raise ValueError(f"{field} is required")
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be ISO 8601 with a UTC offset") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field} needs a UTC offset")
    return parsed


def validate_grant(body) -> dict:
    """AXS-R03. Returns clean fields or raises ValueError with the 400 message."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    ref = body.get("booking_reference")
    if not isinstance(ref, str) or not BOOKING_REF.match(ref):
        raise ValueError("booking_reference must be BK- and 6 symbols")
    member_ref = body.get("member_ref")
    if not isinstance(member_ref, str) or not member_ref.strip():
        raise ValueError("member_ref is required")
    space_id = body.get("space_id")
    if space_id is None:
        raise ValueError("space_id is required")
    if isinstance(space_id, bool) or not isinstance(space_id, int):
        raise ValueError("space_id must be an integer")
    if not 1 <= space_id <= INT_MAX:
        raise ValueError("space_id is out of range")
    space_name = body.get("space_name")
    if not isinstance(space_name, str) or not space_name.strip():
        raise ValueError("space_name is required")
    valid_from = parse_instant(body.get("valid_from"), "valid_from")
    valid_until = parse_instant(body.get("valid_until"), "valid_until")
    if valid_until <= valid_from:
        raise ValueError("valid_until must be after valid_from")
    return {
        "booking_reference": ref,
        "member_ref": member_ref,
        "space_id": space_id,
        "space_name": space_name,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def condition(grant: dict, now: datetime) -> str | None:
    """AXS-R16: derived on read, never stored."""
    if grant["status"] == "revoked" or grant["valid_from"] is None:
        return None
    if now < grant["valid_from"]:
        return "not_yet_valid"
    if now >= grant["valid_until"]:
        return "no_show" if grant["status"] == "issued" else "expired"
    return None


def badge(grant: dict, now: datetime) -> str:
    """AXS-R10: Cancelled > Expired > Checked in > Issued."""
    if grant["status"] == "revoked":
        return "Cancelled"
    if now >= grant["valid_until"]:
        return "Expired"
    return "Checked in" if grant["status"] == "checked_in" else "Issued"


def when(value: datetime, now: datetime) -> str:
    """HH:MM on the same Bangkok date as now, else YYYY-MM-DD HH:MM."""
    local = value.astimezone(LOCAL_TZ)
    if local.date() == now.astimezone(LOCAL_TZ).date():
        return local.strftime("%H:%M")
    return local.strftime("%Y-%m-%d %H:%M")


def day(value: datetime, now: datetime) -> str:
    """"Sat 3 Oct" in Bangkok, the format Purchase uses; the year only when it is not this year."""
    local = value.astimezone(LOCAL_TZ)
    label = f"{local:%a} {local.day} {local:%b}"
    return label if local.year == now.astimezone(LOCAL_TZ).year else f"{label} {local.year}"


def duration(start: datetime, end: datetime) -> str:
    """"1 h 30 min", "2 h" or "30 min"."""
    hours, minutes = divmod(int((end - start).total_seconds()) // 60, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def room_label(space_id: int, space_name: str) -> str:
    return f"{space_name} (room {space_id})"


def decide(grant: dict | None, room_id: int, now: datetime) -> tuple[str, str]:
    """AXS-R14 order, then the AXS-R13 window [valid_from, valid_until)."""
    if grant is None:
        return "unknown_code", "Code not recognised"
    if grant["status"] == "revoked":
        return "revoked", "This ticket was cancelled"
    if grant["space_id"] != room_id:
        return "wrong_room", "This ticket is for " + room_label(
            grant["space_id"], grant["space_name"]
        )
    if now < grant["valid_from"]:
        return "not_open_yet", "opens " + when(grant["valid_from"], now)
    if now >= grant["valid_until"]:
        return "closed", "Check-in closed at " + when(grant["valid_until"], now)
    return "ok", "Door unlocked (mock)"
