"""Dates, exact accounting and transactions used by all management modules."""
from datetime import date, timedelta
import sqlite3

import pytest

from app.hrm_common import (begin_write, date_value, jalali_display,
                            integer_value, money_value, HrmConflict, check_version)


@pytest.mark.parametrize('jalali,iso', [
    ('1300/01/01', '1921-03-21'), ('1399/12/30', '2021-03-20'),
    ('1400/01/01', '2021-03-21'), ('1403/12/30', '2025-03-20'),
    ('1404/01/01', '2025-03-21'), ('۱۴۰۵/۰۸/۲۱', '2026-11-12'),
])
def test_calendar_golden_dates(jalali, iso):
    assert date_value(jalali) == iso
    assert date_value(jalali_display(iso)) == iso


def test_daily_round_trip_covers_leap_boundaries():
    start = date(2023, 1, 1)
    for offset in range(1462):
        current = start + timedelta(days=offset)
        assert date_value(jalali_display(current)) == current.isoformat()


@pytest.mark.parametrize('value', ['1404/12/30', '1405/07/31', '2026-02-30',
                                   '2026-2-01', True, '1405/08/21<script>'])
def test_invalid_dates_are_not_silently_normalized(value):
    with pytest.raises(ValueError):
        date_value(value, required=True)


def test_financial_values_are_exact_whole_rials():
    assert money_value('۱۲۳۴۵۶۷۸۹') == 123456789
    assert money_value('9007199254740991') == 9007199254740991
    for value in (True, '1.5', 'NaN', float('inf'), -1, '9007199254740992'):
        with pytest.raises(ValueError):
            money_value(value)
    with pytest.raises(ValueError):
        integer_value('1e999999')


def test_nested_transaction_preserves_atomic_rollback():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE facts(id INTEGER PRIMARY KEY)')
    with pytest.raises(ValueError):
        with begin_write(conn):
            conn.execute('INSERT INTO facts VALUES (1)')
            with begin_write(conn):
                conn.execute('INSERT INTO facts VALUES (2)')
            raise ValueError('fail final validation')
    assert conn.execute('SELECT * FROM facts').fetchall() == []
    with begin_write(conn):
        conn.execute('INSERT INTO facts VALUES (3)')
    assert conn.execute('SELECT * FROM facts').fetchall() == [(3,)]


def test_stale_or_non_integer_versions_do_not_overwrite():
    for value in (None, True, '2', 1):
        with pytest.raises(HrmConflict):
            check_version({'version': value}, {'version': 2})
    check_version({'version': 2}, {'version': 2})
