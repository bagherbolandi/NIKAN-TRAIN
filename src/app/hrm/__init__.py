"""Nikan HRM — additive modules on the existing NikanTraining application.

Nothing here replaces an existing module: the training features, their routes and
their tables keep working. ``register`` only adds versioned migrations and new
/api/hrm/* endpoints behind server-side authorization.
"""
from .core import Hrm
from . import schema, access, settings, personnel, contracts, attendance, performance, benefits, requests, reports

MODULES = (settings, personnel, contracts, attendance, performance, benefits, requests, reports)


def register(app, db, authenticated, company, audit, error, *, migrate=True):
    if migrate:
        with app.app_context():
            schema.migrate(db())
    ctx = Hrm(app, db, authenticated, audit, error)
    for module in MODULES:
        module.register(ctx)
    app.extensions['hrm'] = ctx
    return ctx
