"""Solar Hijri ↔ ISO (Gregorian) conversion for Nikan HRM.

The UI speaks Shamsi (``1405/03/25``); the database stores standard ISO dates
(``YYYY-MM-DD``) so comparisons, indexes and reports work without parsing text.
The leap rule is exactly the one already used by ``app.validation.jalali_date``
(Borkowski's 33-year sub-cycle, valid for 1300..1499), so a date accepted by the
validator always round-trips here.
"""
import re
from datetime import date, datetime, timezone

from ..validation import jalali_date

_LEAP_POSITIONS = (1, 5, 9, 13, 17, 21, 25, 29)
# 1300/01/01 is 1921-03-21 in the Gregorian calendar.
_EPOCH = date(1921, 3, 21).toordinal()
_START_YEAR = 1300


def _leap(year):
    return (year - 1210 + 1) % 33 in _LEAP_POSITIONS


def _days_in_month(year, month):
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if _leap(year) else 29


def _ordinal(year, month, day):
    """Days since 1300/01/01; matches jalali_date() for the supported range."""
    days = 0
    for y in range(_START_YEAR, year):
        days += 366 if _leap(y) else 365
    days += (month - 1) * 31 if month <= 6 else 186 + (month - 7) * 30
    return days + day - 1


def _from_ordinal(days):
    year = _START_YEAR
    while True:
        length = 366 if _leap(year) else 365
        if days < length:
            break
        days -= length
        year += 1
    month = 1
    while days >= _days_in_month(year, month):
        days -= _days_in_month(year, month)
        month += 1
    return year, month, days + 1


def jalali_to_iso(value):
    """Validate a Persian date string and return an ISO ``YYYY-MM-DD`` string."""
    normalized = jalali_date(value)
    year, month, day = (int(part) for part in normalized.split('/'))
    return date.fromordinal(_EPOCH + _ordinal(year, month, day)).isoformat()


def iso_to_jalali(value):
    """Return ``YYYY/MM/DD`` (Persian calendar) for an ISO date string."""
    if not isinstance(value, str):
        raise ValueError('تاریخ معتبر نیست')
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        raise ValueError('تاریخ ذخیره‌شده معتبر نیست') from None
    year, month, day = _from_ordinal(parsed.toordinal() - _EPOCH)
    return f'{year:04d}/{month:02d}/{day:02d}'


def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


def add_days(iso_value, count):
    try:
        return date.fromordinal(date.fromisoformat(iso_value).toordinal() + count).isoformat()
    except (ValueError, TypeError):
        raise ValueError('تاریخ معتبر نیست') from None


def days_between(start_iso, end_iso):
    try:
        return date.fromisoformat(end_iso).toordinal() - date.fromisoformat(start_iso).toordinal()
    except (ValueError, TypeError):
        raise ValueError('بازه تاریخ معتبر نیست') from None


def optional_iso(value, field='تاریخ'):
    if value in (None, ''):
        return None
    try:
        return jalali_to_iso(value)
    except ValueError as exc:
        raise ValueError(f'{field} شمسی معتبر نیست: ' + str(exc)) from None


def display_row(row, extra_fields=()):
    """Attach ``*_fa`` Shamsi mirrors for every ISO date column in a row."""
    result = dict(row)
    for key, value in list(result.items()):
        if key in extra_fields or (('date' in key or key.endswith('_at')) and isinstance(value, str) and len(value) >= 10):
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}.*', value):
                try:
                    result[key + '_fa'] = iso_to_jalali(value)
                except ValueError:
                    pass
    return result
