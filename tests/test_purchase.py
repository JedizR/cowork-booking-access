from datetime import datetime, timedelta, timezone

import pytest

from purchase import calculate_booking_price_cents


@pytest.mark.parametrize(
    "hourly_rate_cents,seconds,expected_cents",
    [
        (1500, 1800, 750),
        (1000, 1200, 333),
        (100, 18, 1),
        (100, 17, 0),
        (3600, 1.9, 1),
        (0, 3600, 0),
    ],
)
def test_booking_price_examples(hourly_rate_cents, seconds, expected_cents):
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)

    assert calculate_booking_price_cents(
        hourly_rate_cents, start, start + timedelta(seconds=seconds)
    ) == expected_cents
