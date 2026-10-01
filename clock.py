"""The only place that reads time (D27, AXS-R19)."""

import os
from datetime import datetime, timezone

from flask import current_app, has_app_context


def enabled() -> bool:
    return os.getenv("TEST_CLOCK_ENABLED") == "true"


def now() -> datetime:
    # With the flag on, read the one-row test_clock table on every call (no
    # per-process cache), so both gunicorn workers see a new instant at once.
    if enabled() and has_app_context():
        with current_app.db.cursor() as cur:
            cur.execute("SELECT now_override FROM test_clock WHERE id = 1")
            row = cur.fetchone()
        if row and row["now_override"] is not None:
            return row["now_override"]
    return datetime.now(timezone.utc)
