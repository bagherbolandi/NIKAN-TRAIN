"""Traceable API failures without disclosing server/database internals."""
import secrets
import sqlite3

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException


def register(app):
    @app.before_request
    def request_identity():
        # Do not trust caller-supplied identifiers in diagnostic logs.
        g.request_id = secrets.token_hex(8)

    @app.after_request
    def response_identity(response):
        response.headers['X-Request-ID'] = g.request_id
        return response

    @app.errorhandler(HTTPException)
    def http_failure(exc):
        if not request.path.startswith('/api/'):
            return exc
        messages = {400: 'درخواست نامعتبر است', 404: 'مسیر درخواست یافت نشد',
                    405: 'روش ارسال این درخواست مجاز نیست',
                    413: 'حجم فایل یا درخواست بیشتر از حد مجاز است',
                    429: 'تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کنید'}
        response = jsonify(error=messages.get(exc.code, 'درخواست قابل انجام نیست'),
                           request_id=g.request_id)
        response.status_code = exc.code
        for key, value in exc.get_headers():
            if key.lower() not in ('content-type', 'content-length'):
                response.headers[key] = value
        return response

    @app.errorhandler(Exception)
    def unexpected_failure(exc):
        # A busy database is temporary. Other OperationalErrors can mean a
        # defect or disk failure, so must not be presented as a retryable lock.
        busy = isinstance(exc, sqlite3.OperationalError) and str(exc).lower() in (
            'database is locked', 'database table is locked')
        app.logger.error('Request %s failed', g.request_id, exc_info=True)
        response = jsonify(
            error=('سامانه در حال ثبت اطلاعات است؛ کمی بعد دوباره تلاش کنید' if busy
                   else 'عملیات انجام نشد؛ کد پیگیری را برای مسئول سامانه ارسال کنید'),
            request_id=g.request_id)
        response.status_code = 503 if busy else 500
        if busy:
            response.headers['Retry-After'] = '2'
        return response
