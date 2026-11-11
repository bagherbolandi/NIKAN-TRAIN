"""Small generic CRUD helper for reference/lookup tables (no duplicated pages)."""
import sqlite3

from flask import g, jsonify, request

from ..validation import number, positive_id
from . import access


def parse_values(ctx, body, fields, required=()):
    out = {}
    for name, kind in fields.items():
        if name not in body:
            continue
        raw = body[name]
        if kind == 'text':
            out[name] = ctx.text(body, name, required=name in required)
        elif kind == 'bool':
            out[name] = 1 if raw in (True, 1, '1', 'true', 'on', 'yes') else 0
        elif kind in ('int', 'float'):
            if raw in (None, ''):
                if name in required:
                    raise ValueError('فیلدهای الزامی را تکمیل کنید')
                out[name] = None
            else:
                out[name] = number(raw, integer=(kind == 'int'), minimum=0,
                                   nullable=True)
        elif kind == 'date':
            if raw in (None, ''):
                if name in required:
                    raise ValueError('فیلدهای الزامی را تکمیل کنید')
                out[name] = None
            else:
                out[name] = ctx.iso(raw, name)
        elif kind == 'json':
            import json
            if not isinstance(raw, (dict, list)):
                raise ValueError('ساختار اطلاعات معتبر نیست')
            out[name] = json.dumps(raw, ensure_ascii=False)
        else:
            raise ValueError('نوع فیلد تعریف‌نشده است')
    for name in required:
        if name not in body or body[name] in (None, ''):
            raise ValueError('فیلدهای الزامی را تکمیل کنید')
    return out


class Crud:
    """Register list/create/update/delete routes for one HRM table."""

    def __init__(self, ctx, path, table, fields, *, required=(), view, edit,
                 global_table=False, global_only=False, label='', order='id DESC',
                 extra_check=None, confidential=None):
        self.ctx = ctx
        self.path = path
        self.table = table
        self.fields = fields
        self.required = tuple(required)
        self.view = view
        self.edit = edit
        self.global_table = global_table
        self.global_only = global_only
        self.label = label or table
        self.order = order
        self.extra_check = extra_check
        self.confidential = confidential
        self.slug = path.replace('/', '_').replace('-', '_')

    def _scope(self):
        if self.global_table:
            return '(company_id=0 OR company_id IS NULL OR company_id=?)', (g.hrm_cid,)
        return 'company_id=?', (g.hrm_cid,)

    def _owned(self, rid):
        where, params = self._scope()
        row = self.ctx.c.execute(
            f'SELECT * FROM {self.table} WHERE id=? AND {where}', (rid,) + params).fetchone()
        if not row:
            return None
        if self.global_table and (row['company_id'] in (0, None)):
            if not (g.user['role'] == 'admin' or g.user['hrm_scope'] == 'holding'):
                raise PermissionError('ویرایش آیتم‌های مشترک هلدینگ فقط برای سطح هلدینگ مجاز است')
        return row

    def register(self):
        ctx = self.ctx

        def list_rows():
            where, params = self._scope()
            rows = ctx.rows(f'SELECT * FROM {self.table} WHERE {where} ORDER BY {self.order}', params)
            return jsonify(rows)

        def create_row():
            body = ctx.body()
            values = parse_values(ctx, body, self.fields, self.required)
            if self.extra_check:
                self.extra_check(values)
            holding_scope = bool(body.get('scope') == 'holding' and
                                 (g.user['role'] == 'admin' or g.user['hrm_scope'] == 'holding'))
            if self.global_table and holding_scope:
                company_id = None if self.table == 'accommodations' else 0
            elif self.global_table:
                company_id = g.hrm_cid
            else:
                company_id = g.hrm_cid
            values['company_id'] = company_id
            keys = list(values)
            cur = ctx.c.execute(
                f'INSERT INTO {self.table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',
                [values[k] for k in keys])
            ctx.audit_log('create_' + self.table, str(cur.lastrowid))
            ctx.c.commit()
            return jsonify(id=cur.lastrowid), 201

        def update_row(rid):
            rid = positive_id(rid)
            target = self._owned(rid)
            if not target:
                return ctx.error('رکورد یافت نشد', 404)
            values = parse_values(ctx, ctx.body(), self.fields)
            if not values:
                raise ValueError('تغییری ارسال نشده است')
            if self.extra_check:
                self.extra_check(values, target)
            ctx.c.execute(
                f'UPDATE {self.table} SET {",".join(k + "=?" for k in values)} WHERE id=?',
                list(values.values()) + [rid])
            ctx.audit_log('update_' + self.table, str(rid))
            ctx.c.commit()
            return ctx.ok()

        def delete_row(rid):
            rid = positive_id(rid)
            target = self._owned(rid)
            if not target:
                return ctx.error('رکورد یافت نشد', 404)
            ctx.c.execute(f'DELETE FROM {self.table} WHERE id=?', (rid,))
            ctx.audit_log('delete_' + self.table, str(rid))
            ctx.c.commit()
            return ctx.ok()

        list_rows.__name__ = f'hrm_{self.slug}_list'
        create_row.__name__ = f'hrm_{self.slug}_create'
        update_row.__name__ = f'hrm_{self.slug}_update'
        delete_row.__name__ = f'hrm_{self.slug}_delete'
        ctx.route(self.path, permission=self.view, confidential=self.confidential)(list_rows)
        ctx.route(self.path, methods=('POST',), permission=self.edit, confidential=self.confidential)(create_row)
        ctx.route(self.path + '/<int:rid>', methods=('PUT',), permission=self.edit, confidential=self.confidential)(update_row)
        ctx.route(self.path + '/<int:rid>', methods=('DELETE',), permission=self.edit, confidential=self.confidential)(delete_row)
        return self
