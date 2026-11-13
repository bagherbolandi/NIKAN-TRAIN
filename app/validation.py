"""Small input validators shared by API writers."""

import math
import re
from decimal import Decimal, InvalidOperation

from flask import request


SQLITE_MAX_INTEGER = 9223372036854775807


def json_object():
    """Return an object or a controlled API error for malformed JSON bodies."""
    try:
        value = request.get_json(silent=True)
    except RecursionError:
        raise ValueError('ساختار اطلاعات درخواست بیش از حد تو در تو است') from None
    if not isinstance(value, dict):
        raise ValueError('اطلاعات درخواست باید یک شیء JSON معتبر باشد')
    # JSON may decode escaped, unpaired UTF-16 surrogates. Reject them before
    # hashing a password or passing text to SQLite's UTF-8 encoder.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeEncodeError:
                raise ValueError('متن درخواست دارای نویسه نامعتبر است') from None
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def positive_id(value):
    """Accept native integers and HTML integer strings without lossy coercion."""
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value.strip()):
        digits = value.strip().lstrip('0')
        if len(digits) > 19:
            raise ValueError('شناسه رکورد معتبر نیست')
        value = int(digits or '0')
    if type(value) is not int or not 1 <= value <= SQLITE_MAX_INTEGER:
        raise ValueError('شناسه رکورد باید عدد صحیح مثبت باشد')
    return value


def number(value, *, integer=False, minimum=None, maximum=None, positive=False, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('مقدار عددی نامعتبر است')
    try:
        text = str(value).strip()
        if len(text) > 128:
            raise ValueError()
        parsed = Decimal(text)
        if not parsed.is_finite():
            raise ValueError()
        if integer and parsed != parsed.to_integral_value():
            raise ValueError()
        if minimum is not None and parsed < minimum:
            raise ValueError()
        if maximum is not None and parsed > maximum:
            raise ValueError()
        if positive and parsed <= 0:
            raise ValueError()
        if integer:
            if not -SQLITE_MAX_INTEGER - 1 <= parsed <= SQLITE_MAX_INTEGER:
                raise ValueError()
            return int(parsed)
        result = float(parsed)
        if not math.isfinite(result) or (positive and result <= 0):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError('مقدار عددی یا محدوده آن نامعتبر است') from None


def jalali_date(value):
    """Validate and normalize supported Solar Hijri dates, including leap days."""
    if not isinstance(value, str):
        raise ValueError('تاریخ باید متنی باشد')
    value = value.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    if not re.fullmatch(r'1[34][0-9]{2}/(0[1-9]|1[0-2])/(0[1-9]|[12][0-9]|3[01])', value):
        raise ValueError('تاریخ شمسی با قالب 1405/03/25 و سال ۱۳۰۰ تا ۱۴۹۹ وارد شود')
    year, month, day = map(int, value.split('/'))
    # Borkowski's Jalali break-year algorithm: the complete supported range
    # 1300..1499 lies between break years 1210 and 1635. In this interval its
    # leap calculation reduces to these positions in the 33-year cycle.
    # Reference: K. M. Borkowski, Earth, Moon, and Planets 74 (1996), 223–230.
    leap = (year - 1210 + 1) % 33 in (1, 5, 9, 13, 17, 21, 25, 29)
    maximum = 31 if month <= 6 else 30 if month <= 11 or leap else 29
    if day > maximum:
        raise ValueError('روز برای ماه و سال انتخاب‌شده نامعتبر است')
    return value
