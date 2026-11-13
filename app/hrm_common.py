"""Small shared validators for the management modules; no new runtime service."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import secrets

from .validation import jalali_date

MAX_MONEY = 9_007_199_254_740_991
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


class HrmConflict(ValueError):
    """An existing record or concurrent writer prevents this operation."""


def text_value(value, label='', required=False, max_length=4000):
    if value is None and not required:
        return ''
    if not isinstance(value, str):
        raise ValueError((label or 'مقدار') + ' باید متن باشد')
    value = value.strip()
    if (required and not value) or len(value) > max_length:
        raise ValueError((label or 'متن') + f' الزامی است یا بیش از {max_length} نویسه دارد')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('متن دارای نویسه نامعتبر است') from None
    return value


def integer_value(value, label='', minimum=0, maximum=MAX_MONEY):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError((label or 'مقدار') + ' باید عدد صحیح باشد')
    try:
        source = str(value).translate(DIGITS).strip()
        if len(source) > 64:
            raise ValueError()
        parsed = Decimal(source)
        if not parsed.is_finite() or parsed != parsed.to_integral_value():
            raise ValueError()
        if (minimum is not None and parsed < minimum) or (maximum is not None and parsed > maximum):
            raise ValueError()
        return int(parsed)
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError((label or 'عدد') + ' خارج از محدوده یا نامعتبر است') from None


def money_value(value, label='مبلغ', minimum=0, maximum=MAX_MONEY):
    """Financial records use exact whole rials, never binary floating amounts."""
    return integer_value(value, label, minimum, maximum)


def _gregorian(jy, jm, jd):
    # Arithmetic Jalali conversion in the same supported modern interval as
    # the existing training date validator. Validate before converting.
    jy += 1595
    days = (-355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33 + 3) // 4)
            + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186))
    gy, days = 400 * (days // 146097), days % 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    months = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    for length in months:
        if gd <= length:
            break
        gd -= length
        gm += 1
    return date(gy, gm, gd)


def jalali_display(value):
    if not value:
        return ''
    if isinstance(value, date):
        gregorian = value
    else:
        try:
            gregorian = date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return str(value)
    gy, gm, gd = gregorian.year, gregorian.month, gregorian.day
    totals = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
            + (gy2 + 399) // 400 + gd + totals[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return f'{jy:04d}/{jm:02d}/{jd:02d}'


def date_value(value, required=False):
    if value in (None, ''):
        if required:
            raise ValueError('تاریخ را وارد کنید')
        return ''
    if not isinstance(value, str):
        raise ValueError('تاریخ باید متن شمسی یا ISO باشد')
    source = value.translate(DIGITS).strip()
    if '/' in source:
        normalized = jalali_date(source)
        result = _gregorian(*map(int, normalized.split('/')))
        # Round-trip makes boundary discrepancies fail visibly rather than
        # silently filing a document under the following day.
        if jalali_display(result) != normalized:
            raise ValueError('تاریخ شمسی قابل تبدیل نیست')
        return result.isoformat()
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', source):
        raise ValueError('تاریخ شمسی مانند 1405/08/21 وارد شود')
    try:
        result = date.fromisoformat(source)
    except ValueError:
        raise ValueError('تاریخ معتبر نیست') from None
    if not 1900 <= result.year <= 2200:
        raise ValueError('تاریخ خارج از محدوده پشتیبانی است')
    return result.isoformat()


def today_iso():
    return datetime.now(timezone(timedelta(hours=3, minutes=30))).date().isoformat()


def check_version(data, row):
    if type(data.get('version')) is not int or data['version'] != row['version']:
        raise HrmConflict('اطلاعات توسط کاربر دیگری تغییر کرده است؛ دوباره بارگذاری کنید')


@contextmanager
def begin_write(conn):
    """Lock validations and writes together; nested domain work uses savepoints."""
    if conn.in_transaction:
        name = 'hrm_' + secrets.token_hex(8)
        conn.execute('SAVEPOINT ' + name)
        try:
            yield conn
            conn.execute('RELEASE SAVEPOINT ' + name)
        except BaseException:
            conn.execute('ROLLBACK TO SAVEPOINT ' + name)
            conn.execute('RELEASE SAVEPOINT ' + name)
            raise
    else:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
