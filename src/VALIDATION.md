# Validation / Release scope — 0.11.0

Base: recovered desktop application 0.10, exact Git blob match with current main installer. The existing source already includes SQLite, company isolation, job and individual documents, skill matrices, calendar, attendance and cost allocation. These features were retained, not invented anew.

Changes: holding-wide needs queue and live gap summary; holding-only decisions with reasons/history/reopen; reviewed-request locking in API and Excel import; optimistic versions for decisions and development plans; measured objectives, owners, deadlines and evidence; latest explicit assessment takes precedence over historic course peak; offline Windows Server bootstrap, startup task, daily consistent database backup; standalone Windows installer and source distribution.

Verified locally:

- Ten integration tests: company isolation, admin-only holding operations, approval bypass rejection, review lock/reopen/version conflict/history, internal courses, foreign-company references, measurable plans and mandatory evidence, CSRF/anonymous rejection, gap regression, migration idempotency and SQLite integrity/FKs.
- Headless Microsoft Edge: administrator login, holding needs year selection, approval with reason, plan creation, company login/navigation isolation, screenshots and zero JavaScript errors.
- Real 0.10 database upgraded with an existing employee preserved; new server started on loopback and online SQLite backup verified.
- Python application starts using the bundled Python 3.13 runtime; all 59 API routes register.
- PowerShell installation scripts parse successfully; JavaScript syntax verified.

Limitations: actual target Windows Server, Local Service startup at boot, scheduled backup execution, organization firewall/routing, HTTPS certificate/proxy, load testing and commercial code signing are not validated on the customer's server. Existing network installer was not extracted. This is not a certification of ISO 10015 compliance. SQLite is local to the server, not an SMB-shared database. Independent desktop databases do not synchronize or merge automatically.

Run API tests from the source root: `runtime\python.exe test_governance.py`, or install `requirements.txt` in an isolated interpreter and run `python test_governance.py`.

Browser test requires Playwright and a fixture server on loopback port 18876; install Playwright locally or set PLAYWRIGHT_MODULE to its module directory. QA fixture passwords are test-only and no users are included in the shipped database.

Password recovery 0.11.2: seven additional API tests cover one-use tokens, expiry, reissue, hashing, account binding, attempt limit, password constraints, explicit reset and session revocation.

## Nikan HRM 0.12.0 — additive validation

Base: the same recovered 0.10/0.11 application, extended in place. All 17 pre-existing integration tests still pass unchanged. 18 new integration tests in test_hrm.py cover: migration on the shipped 0.11.2 database and on a bare legacy schema (idempotent, rows preserved), Jalali/ISO conversion, personnel history and automatic department/job synchronisation, company/unit/self isolation, confidential-document grants (system administrator denied without explicit grant), renewal limits driven by editable settings, attendance duplicate detection and real .xlsx import, weighted performance scores and trends, configurable loan/welfare limits, reservation overlap rejection inside BEGIN IMMEDIATE, request approval materialising the underlying attendance record, dashboard/report/Excel permission checks, CSRF/anonymous rejection and the legacy encrypted-free SQLite backup containing the new tables.

Installer: build_hrm.py --check generates two NSIS scripts, Offline.zip, Source.zip and SHA256SUMS.txt. Verified: no ExecutionPolicy Bypass and no nsExec in the install section; server uninstall uses schtasks.exe/netsh.exe; VIProductVersion/VIAddVersionKey present.

UI smoke: qa-hrm-ui.cjs loads the real index.html script chain in jsdom, stubs /api/*, and verifies 11 allowed HRM menus, 5 hidden confidential menus, legacy menus preserved, dashboard, CRUD dialog submit/close/re-render, personnel master/detail + training tab, cartable, reports, settings and users. Run with NODE_PATH pointing to a jsdom install: `NODE_PATH=/path/to/node_modules node qa-hrm-ui.cjs`.

Not validated here (requires the customer's Windows environment): makensis execution, Authenticode signing, actual Windows Server install, Local Service scheduled tasks, organisation firewall/HTTPS proxy, AV/EDR policy, load testing and UI browser walkthrough on Edge. Commercial code signing remains an IT responsibility.
