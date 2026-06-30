"""Хранение времени в UTC и отображение в локальном поясе (ТЗ §28)."""

from datetime import UTC, datetime

from app.utils.timezone import format_datetime, to_local


def test_to_local_naive_treated_as_utc():
    naive = datetime(2026, 6, 30, 12, 0, 0)
    local = to_local(naive)
    assert local.tzinfo is not None


def test_to_local_converts_offset():
    utc = datetime(2026, 6, 30, 0, 0, 0, tzinfo=UTC)
    # Europe/Berlin летом = UTC+2.
    local = to_local(utc)
    assert local.hour == 2


def test_format_datetime():
    utc = datetime(2026, 6, 30, 0, 0, 0, tzinfo=UTC)
    assert format_datetime(utc, "%d.%m.%Y") == "30.06.2026"
