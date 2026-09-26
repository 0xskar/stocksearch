from datetime import date, datetime

from app import _should_fire_overnight


def test_fires_at_exact_time_when_enabled_and_not_yet_fired_today():
    now = datetime(2026, 9, 26, 2, 0)
    assert _should_fire_overnight(True, "02:00", now, None) is True


def test_does_not_fire_when_disabled():
    now = datetime(2026, 9, 26, 2, 0)
    assert _should_fire_overnight(False, "02:00", now, None) is False


def test_does_not_fire_outside_the_configured_minute():
    now = datetime(2026, 9, 26, 2, 1)
    assert _should_fire_overnight(True, "02:00", now, None) is False


def test_does_not_fire_twice_the_same_day():
    now = datetime(2026, 9, 26, 2, 0)
    assert _should_fire_overnight(True, "02:00", now, date(2026, 9, 26)) is False


def test_fires_again_on_a_new_day():
    now = datetime(2026, 9, 27, 2, 0)
    assert _should_fire_overnight(True, "02:00", now, date(2026, 9, 26)) is True
