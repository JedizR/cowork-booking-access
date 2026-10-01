"""Purchase rules for booking prices."""

from datetime import datetime


def calculate_booking_price_cents(
    hourly_rate_cents: int, start_time: datetime, end_time: datetime
) -> int:
    """Prorate an hourly rate, rounding half cents up.

    The caller supplies a validated interval and handles subscription coverage.
    Duration is truncated to whole seconds, preserving the existing calculation.
    """
    seconds = int((end_time - start_time).total_seconds())
    return (hourly_rate_cents * seconds + 1800) // 3600
