/* Nikan HRM — jsdom runtime smoke for the RTL UI shell, HRM menus,
   permission filtering, render dispatch and the add/edit dialog chain.

   Dev-only QA (jsdom is not shipped in the Windows runtime):
     npm install jsdom          # in a temporary folder, or set JSDOM_MODULE
     NODE_PATH=/path/to/node_modules node qa-hrm-ui.cjs
*/
const fs = require('fs');
const path = require('path');
const { JSDOM } = require(process.env.JSDOM_MODULE || 'jsdom');

const ROOT = __dirname;
const STATIC = path.join(ROOT, 'app/static');
const index = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
const html = index.replace(/<script src="\/static\/([^"]+)\.js"><\/script>/g, (_, name) => {
  const source = fs.readFileSync(path.join(STATIC, name + '.js'), 'utf8');
  return '<script>\n' + source + '\n</script>';
});

const calls = [];
const state = { attendancePosted: false };
const PERMS = [
  'personnel.view','personnel.edit','contract.view','contract.edit','attendance.view',
  'attendance.edit','attendance.import','performance.view','performance.edit',
  'benefits.view','benefits.edit','welfare.view','welfare.edit','welfare.reserve',
  'documents.view','documents.edit','requests.view','requests.create','requests.approve',
  'reports.view','reports.export','reports.management','settings.view','settings.edit',
  'users.view','users.edit','users.permissions'
];
const COMPANIES = [{ id: 1, name: 'شرکت الف' }, { id: 2, name: 'شرکت ب' }];
const ME = {
  deployment_mode: 'desktop', username: 'admin', role: 'admin', csrf: 'CSRF',
  desktop: false, network: false, companies: COMPANIES,
  hrm: {
    role: 'system_admin', scope: 'holding', permissions: PERMS, confidential: [],
    role_label: 'مدیر سیستم', scope_label: 'تمام شرکت\u200cهای مجاز',
    all_permissions: PERMS, confidential_domains: {}, roles: {}, scopes: {},
    employee_id: null, unit_id: null, legacy_role: 'admin'
  }
};
const LOOKUPS = {
  employees: [{ id: 1, code: 'P-1', name: 'علی احمدی' }],
  org_units: [{ id: 1, name: 'تولید', code: 'U1' }],
  positions: [{ id: 1, title: 'کارشناس تولید', code: 'J1' }],
  work_locations: [{ id: 1, name: 'تهران' }],
  contract_types: [{ id: 1, name: 'قرارداد مدت\u200cدار', code: 'fixed' }],
  leave_types: [{ id: 1, name: 'مرخصی استحقاقی', code: 'annual' }],
  request_types: [{ id: 1, name: 'درخواست مرخصی', code: 'leave', active: 1, approver_role: 'company_hr' }],
  welfare_services: [{ id: 1, name: 'بسته حمایتی', code: 'goods' }],
  accommodations: [{ id: 2, name: 'ویلای محمودآباد ۱', code: 'v1' }],
  performance_periods: [], performance_indicators: [],
  users: [{ id: 1, username: 'admin', hrm_role: 'system_admin', company_id: null }],
  confidential: []
};
const DASHBOARD = {
  company_id: 1, generated_at: '2026-11-11T09:00:00', confidential: [],
  personnel: { total: 2, active: 2, units: 1 },
  contracts: { active: 1, expiring: 0, alert_days: 30, details: [] },
  attendance: { absence: 0, delay: 0, leave: 0, overtime_hours: 0 },
  performance: { average: 0, periods: 0, records: 0 },
  training: { courses: 0, records: 0, certificates: 0, assessments: 0 },
  benefits: { loans: null, loan_amount: null, loans_restricted: true,
              welfare_usages: 0, reservations: 0, villas_mahmoudabad: 0 },
  requests: { pending: 0, approved: 0 },
  companies: COMPANIES.map(c => ({ ...c, employees: c.id, active_contracts: 0, pending_requests: 0 }))
};
const LEGACY_DASHBOARD = {
  employees: 2, skills: 0, gaps: 0, courses: 0, records: 0, budget: 0,
  companies: COMPANIES.map(c => ({ name: c.name, employees: c.id }))
};
const PERSONNEL_ROWS = [{
  id: 1, code: 'P-1', name: 'علی احمدی', department: 'تولید', job: 'کارشناس تولید',
  employment_status: 'active', unit_id: 1, national_id: '1234567890', phone: '09120000000'
}];
const PERSONNEL_DETAIL = {
  employee: PERSONNEL_ROWS[0],
  profile: { national_id: '1234567890', father_name: 'محمد', phone: '09120000000',
             start_date_fa: '۱۴۰۰/۰۱/۰۱', employment_status: 'active', gender: 'male' },
  status_labels: { active: 'فعال' }, history: [],
  training: { records: [], certificates: [], assessments: [] },
  performance_trend: [], documents: [], confidential: [], locked: true,
  contracts: [], disciplinary: [], loans: []
};
const REPORTS = [{ name: 'personnel', label: 'پرونده پرسنلی', available: true, confidential: null }];
const REPORT = {
  name: 'personnel', label: 'پرونده پرسنلی', columns: ['code', 'name'],
  headers: ['کد پرسنلی', 'نام و نام خانوادگی'],
  rows: [{ code: 'P-1', name: 'علی احمدی' }]
};
const SETTINGS = [{ key: 'leave.annual_limit', label: 'سقف مرخصی سالانه (روز)', value: '30' }];
const CATALOG = {
  roles: { system_admin: 'مدیر سیستم', holding_hr: 'مسئول منابع انسانی هلدینگ',
           company_hr: 'مسئول منابع انسانی شرکت', unit_manager: 'مدیر واحد',
           employee: 'کارمند', executive: 'مدیریت ارشد' },
  scopes: { holding: 'تمام شرکت\u200cهای مجاز', company: 'شرکت تعیین\u200cشده',
            unit: 'واحد تحت مسئولیت', self: 'فقط خود' },
  confidential: { contract: 'قراردادها', documents: 'مدارک شخصی',
                  discipline: 'سوابق انضباطی', loan: 'وام\u200cها' }
};
const USERS = [{ id: 1, username: 'admin', role: 'system_admin', scope: 'holding',
                 company_id: null, confidential: [], active: 1 }];
const REQUESTS = { rows: [], pending: [], mine: [], can_approve: true };
const ATTENDANCE_ROW = {
  id: 2, employee_name: 'علی احمدی', unit_name: 'تولید', kind_label: 'مرخصی',
  date: '2026-11-11', end_date: '2026-11-11', minutes: 0, hours: 0,
  status: 'recorded', source: 'manual'
};

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: () => 'application/json; charset=utf-8' },
    json: async () => body,
    blob: async () => new Blob([JSON.stringify(body)], { type: 'application/json' })
  };
}

function route(url, options) {
  const method = options.method || 'GET';
  const u = new URL(url, 'http://localhost');
  const p = u.pathname;
  calls.push({ path: p, query: u.search, method });
  if (p === '/api/me') return jsonResponse(ME);
  if (p === '/api/status') return jsonResponse({ setup_required: false });
  if (p === '/api/dashboard') return jsonResponse(LEGACY_DASHBOARD);
  if (p === '/api/branding') return jsonResponse([]);
  if (p === '/api/reference-options') return jsonResponse({});
  if (p.startsWith('/api/data/')) return jsonResponse([]);
  if (p === '/api/hrm/lookups') return jsonResponse(LOOKUPS);
  if (p === '/api/hrm/dashboard') return jsonResponse(DASHBOARD);
  if (p === '/api/hrm/catalog') return jsonResponse(CATALOG);
  if (p === '/api/hrm/users') return jsonResponse(USERS);
  if (p === '/api/hrm/settings') return jsonResponse(SETTINGS);
  if (p === '/api/hrm/reports') return jsonResponse(REPORTS);
  if (p === '/api/hrm/reports/personnel') return jsonResponse(REPORT);
  if (p === '/api/hrm/personnel/1') return jsonResponse(PERSONNEL_DETAIL);
  if (p === '/api/hrm/personnel') return jsonResponse(PERSONNEL_ROWS);
  if (p === '/api/hrm/requests') return jsonResponse(REQUESTS);
  if (p === '/api/hrm/attendance') {
    if (method === 'POST') { state.attendancePosted = true; return jsonResponse({ id: 2, ok: true }, 201); }
    return jsonResponse(state.attendancePosted ? [ATTENDANCE_ROW] : []);
  }
  if (p.startsWith('/api/hrm/')) {
    if (method === 'GET') return jsonResponse([]);
    return jsonResponse({ ok: true }, method === 'POST' ? 201 : 200);
  }
  return jsonResponse({}, 404);
}

const dom = new JSDOM(html, {
  url: 'http://localhost/',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = (url, options = {}) => Promise.resolve(route(String(url), options));
    window.confirm = () => true;
    window.URL.createObjectURL = () => 'blob:mock';
    window.URL.revokeObjectURL = () => {};
    if (window.HTMLDialogElement) {
      window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
      window.HTMLDialogElement.prototype.close = function () {
        this.removeAttribute('open');
        this.dispatchEvent(new window.Event('close'));
      };
    }
    window.__calls = calls;
    window.__state = state;
  }
});

const w = dom.window;
const d = w.document;
const $ = s => d.querySelector(s);
const text = () => $('#content').textContent;
async function waitFor(fn, label, timeout = 4000) {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > timeout) throw new Error('timeout: ' + label + ' | content=' + text().slice(0, 160));
    await new Promise(r => setTimeout(r, 20));
  }
}
function assert(condition, message) {
  if (!condition) throw new Error('ASSERT FAILED: ' + message);
}
async function clickPage(page, expected) {
  const button = $('[data-page="' + page + '"]');
  assert(button, 'nav button missing: ' + page);
  button.click();
  await waitFor(() => text().includes(expected), 'render ' + page);
  assert($('#page-title').textContent.length > 0, 'page title empty on ' + page);
}

(async () => {
  await waitFor(() => !$('#shell').hidden && d.querySelectorAll('#nav button').length > 0, 'initial load');
  const hrmButtons = [...d.querySelectorAll('#nav button')].filter(b => b.dataset.page.startsWith('hrm_'));
  assert(hrmButtons.length === 11, 'expected 11 allowed HRM menus, got ' + hrmButtons.length);
  for (const denied of ['hrm_contracts', 'hrm_actions', 'hrm_documents', 'hrm_disciplinary', 'hrm_loans']) {
    assert(!$('[data-page="' + denied + '"]'), 'confidential menu must be hidden: ' + denied);
  }
  assert($('[data-page="dashboard"]'), 'legacy training dashboard menu must survive');
  console.log('OK  shell loaded; 11 HRM menus visible; 5 confidential menus hidden; legacy menus kept');

  await clickPage('hrm_dashboard', 'داشبورد مدیریتی منابع انسانی');
  assert(text().includes('شرکت الف'), 'dashboard company breakdown missing');
  assert(text().includes('دسترسی به اسناد محرمانه'), 'dashboard confidentiality note missing');
  console.log('OK  hrm_dashboard rendered with company separation + confidentiality note');

  await clickPage('hrm_attendance', 'حضور، غیاب، مرخصی');
  assert($('#hrm-add'), 'add button missing on CRUD page');
  $('#hrm-add').click();
  await waitFor(() => $('#hrm-dialog') && $('#hrm-dialog').hasAttribute('open'), 'dialog open');
  const form = $('#hrm-dialog-form');
  assert(form.querySelector('[name="employee_id"]'), 'employee select missing');
  assert(form.querySelector('[name="date"]'), 'date input missing');
  assert(form.querySelector('[name="kind"]'), 'kind select missing');
  form.querySelector('[name="employee_id"]').value = '1';
  form.querySelector('[name="kind"]').value = 'leave';
  form.querySelector('[name="date"]').value = '۱۴۰۵/۰۸/۲۰';
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await waitFor(() => w.__state.attendancePosted, 'attendance POST');
  await waitFor(() => !$('#hrm-dialog').hasAttribute('open'), 'dialog close');
  await waitFor(() => text().includes('علی احمدی'), 'attendance row after save');
  assert(w.__calls.some(c => c.path === '/api/hrm/attendance' && c.method === 'POST'), 'POST not recorded');
  console.log('OK  hrm_attendance CRUD dialog opened, validated, posted, closed and re-rendered');

  await clickPage('hrm_personnel', 'پرونده پرسنلی');
  await waitFor(() => $('[data-person="1"]'), 'personnel list');
  $('[data-person="1"]').click();
  await waitFor(() => $('#hrm-person-detail') && $('#hrm-person-detail').textContent.includes('علی احمدی'), 'personnel detail');
  assert($('#hrm-person-detail [data-tab="training"]'), 'training tab missing');
  $('#hrm-person-detail [data-tab="training"]').click();
  await waitFor(() => text().includes('سوابق آموزشی مستقیماً'), 'training link note');
  console.log('OK  hrm_personnel master/detail + training linkage tab rendered');

  await clickPage('hrm_requests', 'کارتابل شخصی و درخواست\u200cها');
  assert($('[data-rtab="pending"]'), 'request approval tab missing');
  console.log('OK  hrm_requests cartable rendered');

  await clickPage('hrm_reports', 'گزارش\u200cها و خروجی Excel');
  await waitFor(() => $('#hrm-report-body tbody tr'), 'report rows');
  assert($('#hrm-report-name'), 'report selector missing');
  console.log('OK  hrm_reports list + JSON report rows rendered');

  await clickPage('hrm_settings', 'ضوابط و تنظیمات منابع انسانی');
  assert($('[data-setting="leave.annual_limit"]'), 'configurable setting input missing');
  console.log('OK  hrm_settings configurable rules rendered');

  await clickPage('hrm_users', 'کاربران، نقش\u200cها و مجوزها');
  assert($('#hrm-user-table'), 'users table missing');
  assert(text().includes('مجوز اسناد محرمانه جداگانه'), 'explicit-grant note missing');
  console.log('OK  hrm_users roles/permissions rendered');

  const paths = [...new Set(w.__calls.map(c => c.path))];
  assert(paths.includes('/api/hrm/lookups'), 'lookups not called');
  assert(paths.includes('/api/hrm/dashboard'), 'dashboard not called');
  assert(paths.includes('/api/hrm/reports/personnel'), 'report not called');
  console.log('OK  fetch chain used real /api/hrm/* endpoints (' + paths.length + ' distinct paths)');
  console.log('JSDOM_UI_SMOKE_OK');
  dom.window.close();
})().catch(err => {
  console.error(err.stack || err);
  process.exit(1);
});
