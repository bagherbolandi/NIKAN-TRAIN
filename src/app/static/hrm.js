/* Nikan HRM — modular RTL interface added on top of the existing application.
   The training pages, their render chain and their styles are untouched. */
const HRM_MENUS = [
 ['hrm_dashboard','◈','داشبورد منابع انسانی'],
 ['hrm_personnel','♙','پرونده پرسنلی'],
 ['hrm_org','▤','ساختار سازمانی'],
 ['hrm_contracts','▣','قراردادها و کارگزینی'],
 ['hrm_actions','✎','احکام و تسویه‌حساب'],
 ['hrm_documents','▧','اسناد استخدامی'],
 ['hrm_attendance','◷','حضور، غیاب و مرخصی'],
 ['hrm_performance','◎','ارزیابی عملکرد'],
 ['hrm_disciplinary','⚑','سوابق تشویقی و انضباطی'],
 ['hrm_loans','◈','وام‌ها و تسهیلات'],
 ['hrm_welfare','✦','خدمات رفاهی'],
 ['hrm_reservations','⌂','رزرو مهمانسرا و ویلاها'],
 ['hrm_requests','✉','کارتابل و درخواست‌ها'],
 ['hrm_reports','▥','گزارش‌ها و خروجی اکسل'],
 ['hrm_users','♧','کاربران و مجوزها'],
 ['hrm_settings','⚙','ضوابط و تنظیمات'],
];
const HRM_PERMS = {
 hrm_dashboard: 'reports.view', hrm_personnel: 'personnel.view', hrm_org: 'personnel.view',
 hrm_contracts: 'contract.view', hrm_actions: 'contract.view', hrm_documents: 'documents.view',
 hrm_attendance: 'attendance.view', hrm_performance: 'performance.view',
 hrm_disciplinary: 'performance.view', hrm_loans: 'benefits.view', hrm_welfare: 'welfare.view',
 hrm_reservations: 'welfare.view', hrm_requests: 'requests.view', hrm_reports: 'reports.view',
 hrm_users: 'users.view', hrm_settings: 'settings.view',
};
const HRM_CONF = {
 hrm_contracts: 'contract', hrm_actions: 'contract', hrm_documents: 'documents',
 hrm_disciplinary: 'discipline', hrm_loans: 'loan',
};
const HRM_GROUPS = [
 ['پرونده و کارگزینی', ['hrm_dashboard','hrm_personnel','hrm_org','hrm_contracts','hrm_actions','hrm_documents']],
 ['حضور، عملکرد و خدمات', ['hrm_attendance','hrm_performance','hrm_disciplinary','hrm_loans','hrm_welfare','hrm_reservations','hrm_requests']],
 ['گزارش‌ها و سامانه', ['hrm_reports','hrm_users','hrm_settings']],
];
const HRM_PAGES = new Set(HRM_MENUS.map(item => item[0]));
HRM_MENUS.forEach(item => menus.push(item));
companyIndependentPages.add('hrm_users');
const hrmCache = {lookups: {}, cid: null};

function hrmPerm(code){ return !!(user && user.hrm && user.hrm.permissions.includes(code)) }
function hrmConf(code){ return !!(user && user.hrm && user.hrm.confidential.includes(code)) }
function hrmUrl(path){ return 'hrm/' + path + (path.includes('?') ? '&' : '?') + 'company_id=' + encodeURIComponent(cid) }
function hrmApi(path, options){ return api('hrm/' + path, options) }
function hrmFmt(v){ return v === null || v === undefined || v === '' ? '—' : Number(v).toLocaleString ? String(v) : String(v) }
function faDate(value){
 if(!value) return '—';
 const text = String(value).slice(0,10);
 if(!/^\d{4}-\d{2}-\d{2}$/.test(text)) return String(value);
 try { return new Intl.DateTimeFormat('fa-IR-u-ca-persian-nu-latn',{timeZone:'UTC',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(text+'T00:00:00Z')) }
 catch(err){ return text }
}
function hrmCell(row, key, type){
 if(row[key+'_fa']) return esc(row[key+'_fa']);
 const value = row[key];
 if(type === 'date') return esc(faDate(value));
 if(type === 'bool') return value ? 'بله' : 'خیر';
 if(value === null || value === undefined || value === '') return '—';
 if(typeof value === 'number') return esc(value.toLocaleString('fa-IR'));
 return esc(value);
}
async function hrmLookups(force){
 const key = String(cid);
 if(!force && hrmCache.cid === key && hrmCache.lookups[key]) return hrmCache.lookups[key];
 const data = await api(hrmUrl('lookups'));
 hrmCache.cid = key; hrmCache.lookups[key] = data;
 return data;
}
function hrmOpts(list, value='id', label='name'){
 return (list||[]).map(row => [row[value], row[label] + (row.code ? ' · ' + row.code : '')]);
}
function hrmEmployees(L){ return (L.employees||[]).map(e => [e.id, e.name + ' · ' + e.code]) }
function hrmStatic(list){ return list.map(pair => [pair[0], pair[1]]) }
const HRM_CHOICES = {
 employment_status: [['active','فعال'],['probation','آزمایشی'],['suspended','تعلیق'],['terminated','قطع همکاری'],['retired','بازنشسته']],
 gender: [['','—'],['male','مرد'],['female','زن']],
 marital_status: [['','—'],['single','مجرد'],['married','متأهل']],
 kind_attendance: [['leave','مرخصی'],['mission','مأموریت'],['absence','غیبت'],['delay','تأخیر'],['overtime','اضافه‌کاری']],
 kind_discipline: [['commendation','تشویق'],['notice','تذکر'],['warning','اخطار'],['reprimand','توبیخ']],
 kind_action: [['decree','حکم کارگزینی'],['promotion','تغییر سمت / ارتقا'],['transfer','انتقال'],['termination','قطع همکاری'],['settlement','تسویه‌حساب'],['other','سایر']],
 kind_loan: [['internal','وام داخلی سازمان'],['bank','تسهیلات بانکی']],
 status_contract: [['draft','پیش‌نویس'],['active','فعال'],['expired','منقضی'],['terminated','خاتمه‌یافته'],['renewed','تمدیدشده']],
 status_loan: [['submitted','در انتظار'],['approved','تأییدشده'],['rejected','ردشده'],['active','فعال'],['settled','تسویه‌شده'],['cancelled','لغوشده']],
 status_attendance: [['pending','در انتظار تأیید'],['recorded','ثبت‌شده'],['approved','تأییدشده'],['rejected','ردشده']],
 status_request: [['draft','پیش‌نویس'],['submitted','در انتظار تأیید'],['approved','تأییدشده'],['rejected','ردشده'],['cancelled','لغوشده']],
 status_reservation: [['submitted','در انتظار'],['approved','تأییدشده'],['rejected','ردشده'],['cancelled','لغوشده'],['completed','انجام‌شده']],
 scope: [['','—'],['holding','تمام شرکت‌های مجاز'],['company','شرکت تعیین‌شده'],['unit','واحد تحت مسئولیت'],['self','فقط خود']],
 period: [['month','ماهانه'],['year','سالانه'],['once','یک‌بار']],
 period_status: [['open','باز'],['closed','بسته']],
 service_kind: [['goods','اقلام غیرنقدی'],['stationery','لوازم‌التحریر'],['gift','هدایا'],['accommodation','اقامتگاه'],['other','سایر']],
 place_kind: [['guest_house','مهمانسرا'],['villa','ویلا']],
 scope_rule: [['','مخصوص این شرکت'],['holding','مشترک هلدینگ']],
};

/* ---------------- dialog & forms ---------------- */
let hrmDialogNode = null;
function hrmDialogElement(){
 if(hrmDialogNode) return hrmDialogNode;
 const node = document.createElement('dialog');
 node.id = 'hrm-dialog';
 node.setAttribute('aria-labelledby','hrm-dialog-title');
 node.innerHTML = '<div class="dialog-head"><h2 id="hrm-dialog-title"></h2><button type="button" class="quiet" id="hrm-dialog-close" aria-label="بستن">✕</button></div><form id="hrm-dialog-form"><div id="hrm-dialog-fields" class="hrm-form"></div><p id="hrm-dialog-error" class="error" role="alert"></p><button type="submit" id="hrm-dialog-submit">ذخیره اطلاعات</button></form>';
 document.body.append(node);
 node.querySelector('#hrm-dialog-close').onclick = () => node.close();
 hrmDialogNode = node;
 return node;
}
function hrmFieldHtml(field, values, L){
 const name = field.name, label = field.label || name;
 const required = field.required ? 'required' : '';
 let value = values?.[name];
 if(field.type === 'date') value = values?.[name+'_fa'] || (value ? faDate(value) : (field.default || ''));
 else if(value === undefined || value === null) value = field.default ?? '';
 const wide = field.wide ? ' wide' : '';
 let control;
 if(field.type === 'static') control = `<div class="hrm-note">${esc(value ?? field.default ?? '')}</div>`;
 else if(field.type === 'select'){
   const options = typeof field.options === 'function' ? field.options(L) : (field.options || []);
   control = `<select name="${esc(name)}" ${required}>${options.map(o => `<option value="${esc(o[0])}" ${String(value) === String(o[0]) ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select>`;
 } else if(field.type === 'textarea') control = `<textarea name="${esc(name)}" ${required} rows="3">${esc(value)}</textarea>`;
 else if(field.type === 'checkbox') control = `<div class="hrm-check"><input type="checkbox" name="${esc(name)}" ${Number(value) ? 'checked' : ''}><span>${esc(field.hint || '')}</span></div>`;
 else if(field.type === 'file') control = `<input type="file" name="${esc(name)}" ${required} ${field.accept ? `accept="${esc(field.accept)}"` : ''}>`;
 else {
   const type = field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text';
   const extra = field.type === 'number' ? 'step="any"' + (field.min !== undefined ? ` min="${field.min}"` : '') : '';
   control = `<input type="${type}" name="${esc(name)}" value="${esc(value)}" ${required} ${extra} ${field.type === 'date' ? 'dir="ltr" placeholder="۱۴۰۵/۰۳/۲۵"' : ''}>`;
 }
 return `<label class="${wide ? 'wide' : ''}">${esc(label)}${control}${field.help ? `<p class="help">${esc(field.help)}</p>` : ''}</label>`;
}
function hrmDialog({title, fields, values={}, submitLabel='ذخیره اطلاعات', onSubmit}){
 const node = hrmDialogElement();
 if(node.open){ setTimeout(() => hrmDialog({title, fields, values, submitLabel, onSubmit}), 0); return }
 node.querySelector('#hrm-dialog-title').textContent = title;
 node.querySelector('#hrm-dialog-error').textContent = '';
 node.querySelector('#hrm-dialog-submit').textContent = submitLabel;
 const form = node.querySelector('#hrm-dialog-form');
 const L = hrmCache.lookups[String(cid)] || {};
 node.querySelector('#hrm-dialog-fields').innerHTML = fields.map(f => hrmFieldHtml(f, values, L)).join('');
 form.onsubmit = async event => {
   event.preventDefault();
   if(form.dataset.busy) return;
   const data = new FormData(form);
   const payload = {};
   let hasFile = false;
   for(const field of fields){
     if(field.type === 'static') continue;
     if(field.type === 'checkbox') payload[field.name] = data.get(field.name) ? 1 : 0;
     else if(field.type === 'file'){ const file = data.get(field.name); if(file && file.size){ payload[field.name] = file; hasFile = true } }
     else { const v = data.get(field.name); if(v !== null) payload[field.name] = v }
   }
   form.dataset.busy = '1';
   const button = node.querySelector('#hrm-dialog-submit'); button.disabled = true;
   try {
     await onSubmit(payload, hasFile);
     node.close();
   } catch(err){ node.querySelector('#hrm-dialog-error').textContent = err.message || String(err) }
   finally { delete form.dataset.busy; button.disabled = false }
 };
 node.showModal();
}
async function hrmSend(endpoint, payload, hasFile, method, id){
 let body = payload;
 if(hasFile){
   body = new FormData();
   for(const [k,v] of Object.entries(payload)) if(v !== undefined && v !== null) body.append(k, v);
   body.append('company_id', String(cid));
 }
 return api(hrmUrl(endpoint + (id ? '/'+id : '')), {method: method || (id ? 'PUT' : 'POST'), body});
}

/* ---------------- navigation ---------------- */
const hrmLegacyDrawNavigation = drawNavigation;
drawNavigation = function(){
 if(!user) return hrmLegacyDrawNavigation();
 const perms = new Set(user.hrm?.permissions || []), confidential = new Set(user.hrm?.confidential || []);
 const legacy = menus.filter(item => !HRM_PAGES.has(item[0]) &&
   (user.role === 'admin' || !['users','backup','branding','companies','governance'].includes(item[0])));
 const hrmItems = HRM_MENUS.filter(([key]) => {
   const perm = HRM_PERMS[key], conf = HRM_CONF[key];
   return (!perm || perms.has(perm)) && (!conf || confidential.has(conf));
 });
 const button = item => `<button data-page="${item[0]}"><span aria-hidden="true">${item[1]}</span>${esc(item[2])}</button>`;
 const legacyGroups = [
   ['پرسنل و شایستگی', ['employees','passports','bank','skills','requirements','job_profiles','assessments','gaps']],
   ['برنامه و اجرا', ['governance','courses','needs','instructors','records','certificates','report']],
   ['مدیریت سامانه', ['companies','import','audit','users','backup','branding']],
 ];
 let html = legacy.filter(item => item[0] === 'dashboard').map(button).join('');
 html += legacyGroups.map(([name,keys]) => {
   const items = legacy.filter(item => keys.includes(item[0]));
   return items.length ? `<section class="nav-group"><h2 class="nav-group-title">${name}</h2>${items.map(button).join('')}</section>` : '';
 }).join('');
 html += HRM_GROUPS.map(([name,keys]) => {
   const items = hrmItems.filter(item => keys.includes(item[0]));
   return items.length ? `<section class="nav-group"><h2 class="nav-group-title">${name}</h2>${items.map(button).join('')}</section>` : '';
 }).join('');
 $('#nav').innerHTML = html;
 $('#nav').onclick = event => { const target = event.target.closest('[data-page]'); if(target) navigateTo(target.dataset.page) };
};

/* ---------------- generic CRUD page ---------------- */
function hrmSelectFilter(name, label, options, current){
 return `<label>${esc(label)}<select data-filter="${esc(name)}"><option value="">همه</option>${options.map(o => `<option value="${esc(o[0])}" ${String(current) === String(o[0]) ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select></label>`;
}
async function hrmCrudPage(config){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const L = await hrmLookups();
 const state = {search: '', filters: {}, offset: 0};
 const canWrite = hrmPerm(config.write);
 const draw = async () => {
   const params = new URLSearchParams();
   Object.entries(state.filters).forEach(([k,v]) => { if(v) params.set(k,v) });
   const endpoint = config.endpoint + (params.toString() ? '?' + params.toString() : '');
   const rows = await api(hrmUrl(endpoint));
   const filtered = rows.filter(row => !state.search || config.columns.some(([key]) => searchMatch(String(row[key] ?? ''), state.search)));
   const actions = (canWrite && config.editable !== false) || (config.rowActions || []).length || (canWrite && config.deletable !== false);
   el.innerHTML = `<div class="toolbar"><div><h2>${esc(config.title)}</h2><p class="help">${esc(config.help || 'اطلاعات این بخش فقط در محدوده دسترسی شما نمایش داده می‌شود.')}</p></div><div>
     ${canWrite ? '<button id="hrm-add">＋ ثبت اطلاعات جدید</button>' : ''}
     ${config.report && hrmPerm('reports.export') ? `<button class="quiet" id="hrm-export">خروجی Excel</button>` : ''}
     ${(config.extraButtons || []).map(b => `<button class="quiet" data-extra="${esc(b.id)}">${esc(b.label)}</button>`).join('')}
     <button class="quiet" id="hrm-refresh">بازخوانی</button></div></div>
     <div class="toolbar"><input id="hrm-search" placeholder="جستجو در اطلاعات…" aria-label="جستجو">
     ${(config.filters || []).map(f => hrmSelectFilter(f.name, f.label, typeof f.options === 'function' ? f.options(L) : f.options, state.filters[f.name])).join('')}</div>
     <div id="hrm-table"></div>`;
   const box = $('#hrm-table');
   if(!filtered.length){ box.innerHTML = '<div class="empty"><h2>اطلاعاتی برای نمایش وجود ندارد</h2><p>با دکمهٔ «ثبت اطلاعات جدید» آغاز کنید یا فیلترها را تغییر دهید.</p></div>' }
   else {
     const visible = filtered.slice(state.offset, state.offset + 25);
     box.innerHTML = `<div class="table-wrap"><table><thead><tr>${config.columns.map(c => `<th>${esc(c[1])}</th>`).join('')}${actions ? '<th>عملیات</th>' : ''}</tr></thead><tbody>${visible.map(row => `<tr>${config.columns.map(c => `<td>${hrmCell(row,c[0],c[2])}</td>`).join('')}${actions ? `<td class="hrm-table-actions">
       ${canWrite && config.editable !== false ? `<button class="quiet" data-edit="${row.id}">ویرایش</button>` : ''}
       ${(config.rowActions || []).filter(a => !a.show || a.show(row)).map(a => `<button class="quiet" data-row="${esc(a.id)}" data-id="${row.id}">${esc(a.label)}</button>`).join('')}
       ${canWrite && config.deletable !== false ? `<button class="quiet" data-delete="${row.id}">حذف</button>` : ''}</td>` : ''}</tr>`).join('')}</tbody></table></div>${pager(state.offset,25,filtered.length)}`;
     bindPager(box, () => { state.offset -= 25; draw() }, () => { state.offset += 25; draw() });
   }
   $('#hrm-search').value = state.search;
   $('#hrm-search').oninput = event => { state.search = event.target.value; state.offset = 0; draw() };
   el.querySelectorAll('[data-filter]').forEach(select => select.onchange = () => {
     state.filters[select.dataset.filter] = select.value; state.offset = 0; draw();
   });
   if($('#hrm-add')) $('#hrm-add').onclick = () => hrmDialog({
     title: 'ثبت ' + config.title, fields: config.fields, values: config.defaults || {},
     onSubmit: async (payload, hasFile) => { await hrmSend(config.endpoint, payload, hasFile, 'POST'); notice('اطلاعات ثبت شد.'); await draw() }
   });
   if($('#hrm-export')) $('#hrm-export').onclick = async () => {
     try { download(await api(hrmUrl('reports/' + config.report + '.xlsx')), 'Nikan-HRM-' + config.report + '.xlsx') }
     catch(err){ notice(err.message) }
   };
   $('#hrm-refresh').onclick = () => { hrmCache.lookups[String(cid)] = null; draw() };
   (config.extraButtons || []).forEach(b => { const node = el.querySelector(`[data-extra="${b.id}"]`); if(node) node.onclick = () => b.run(L, draw) });
   box.onclick = async event => {
     const edit = event.target.closest('[data-edit]'), del = event.target.closest('[data-delete]'), rowButton = event.target.closest('[data-row]');
     const id = edit?.dataset.edit || del?.dataset.delete || rowButton?.dataset.id;
     if(!id) return;
     const row = filtered.find(r => String(r.id) === String(id));
     if(edit && config.editable !== false) hrmDialog({
       title: 'ویرایش ' + config.title, fields: config.fields, values: row || {},
       onSubmit: async (payload, hasFile) => { await hrmSend(config.endpoint, payload, hasFile, 'PUT', id); notice('تغییرات ذخیره شد.'); await draw() }
     });
     if(rowButton){ const action = config.rowActions.find(a => a.id === rowButton.dataset.row); if(action) { try { await action.run(row, L, draw) } catch(err){ notice(err.message) } } }
     if(del && confirm('این رکورد حذف شود؟')){
       try { await api(hrmUrl(config.endpoint + '/' + id), {method:'DELETE'}); notice('رکورد حذف شد.'); await draw() }
       catch(err){ notice(err.message) }
     }
   };
 };
 await draw();
}

/* ---------------- dashboard ---------------- */
async function hrmDashboardPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const d = await api(hrmUrl('dashboard'));
 const cards = [
  ['کارکنان ثبت‌شده', d.personnel.total, 'پرونده پرسنلی'],
  ['کارکنان فعال', d.personnel.active, 'وضعیت اشتغال'],
  ['واحدهای سازمانی', d.personnel.units, 'ساختار سازمانی'],
  ['قراردادهای فعال', d.contracts.active, 'امور کارگزینی'],
  ['قراردادهای نزدیک انقضا', d.contracts.expiring, `هشدار ${d.contracts.alert_days} روزه`],
  ['غیبت‌ها', d.attendance.absence, 'حضور و غیاب'],
  ['تأخیرها', d.attendance.delay, 'حضور و غیاب'],
  ['اضافه‌کاری (ساعت)', d.attendance.overtime_hours, 'حضور و غیاب'],
  ['میانگین ارزیابی', d.performance.average, `${d.performance.periods} دوره`],
  ['دوره‌های آموزشی', d.training.courses, `${d.training.records} سابقه آموزش`],
  ['درخواست‌های در انتظار', d.requests.pending, 'کارتابل'],
  ['استفاده از خدمات رفاهی', d.benefits.welfare_usages, `${d.benefits.reservations} رزرو فعال`],
 ];
 el.innerHTML = `<div class="toolbar"><div><h2>داشبورد مدیریتی منابع انسانی</h2><p class="help">شاخص‌های شرکت انتخاب‌شده و تفکیک شرکت‌های مجاز. تاریخ‌ها شمسی و مقادیر از پایگاه داده مرکزی خوانده می‌شوند.</p></div><button class="quiet" id="hrm-dash-refresh">بازخوانی</button></div>
   <div class="metrics">${cards.map((c,i) => `<div class="card"><small>${esc(c[0])}</small><strong class="${i===4?'accent':''}">${esc(String(c[1]))}</strong><p>${esc(c[2])}</p></div>`).join('')}</div>
   <div class="grid" style="margin-top:20px"><div class="panel"><h2>وضعیت شرکت‌های تحت دسترسی</h2>
     ${d.companies.map(c => `<div class="company-row"><span>${esc(c.name)}</span><div><span class="hrm-badge">${esc(String(c.employees))} پرسنل</span> <span class="hrm-badge ok">${esc(String(c.active_contracts))} قرارداد فعال</span> <span class="hrm-badge warn">${esc(String(c.pending_requests))} درخواست</span></div></div>`).join('')}
   </div><div class="panel"><h2>وضعیت آموزش، وام و رفاهیات</h2>
     <div class="company-row"><span>سوابق و گواهینامه‌های آموزشی</span><b>${esc(String(d.training.records + d.training.certificates))}</b></div>
     <div class="company-row"><span>وام‌ها و تسهیلات</span><b>${esc(String(d.benefits.loans))}</b></div>
     <div class="company-row"><span>رزرو ویلاهای محمودآباد</span><b>${esc(String(d.benefits.villas_mahmoudabad))}</b></div>
     <div class="company-row"><span>درخواست‌های تأییدشده</span><b>${esc(String(d.requests.approved))}</b></div>
     <p class="help">${d.confidential.length ? 'مجوز اسناد محرمانه: ' + d.confidential.join('، ') : 'دسترسی به اسناد محرمانه (قرارداد، مدارک، انضباطی، وام) برای این حساب صادر نشده است؛ فقط شمارش کلی نمایش داده می‌شود.'}</p>
   </div></div>
   ${d.contracts.details.length ? `<div class="panel"><h2>قراردادهای نزدیک به انقضا</h2><div class="table-wrap"><table><thead><tr><th>پرسنل</th><th>پایان قرارداد</th><th>روز باقی‌مانده</th></tr></thead><tbody>${d.contracts.details.map(r => `<tr><td>${esc(r.employee_name)}</td><td>${esc(r.end_date_fa || faDate(r.end_date))}</td><td>${esc(String(r.days_to_expiry ?? '—'))}</td></tr>`).join('')}</tbody></table></div></div>` : ''}`;
 $('#hrm-dash-refresh').onclick = hrmDashboardPage;
}

/* ---------------- personnel file ---------------- */
const HRM_PERSONNEL_FIELDS = [
 {name:'code',label:'کد پرسنلی',required:true}, {name:'name',label:'نام و نام خانوادگی',required:true},
 {name:'national_id',label:'کد ملی'}, {name:'father_name',label:'نام پدر'},
 {name:'birth_date',label:'تاریخ تولد (شمسی)',type:'date'}, {name:'gender',label:'جنسیت',type:'select',options:HRM_CHOICES.gender},
 {name:'marital_status',label:'وضعیت تأهل',type:'select',options:HRM_CHOICES.marital_status},
 {name:'phone',label:'تلفن تماس'}, {name:'email',label:'رایانامه'},
 {name:'emergency_contact',label:'تماس اضطراری'}, {name:'address',label:'نشانی',type:'textarea',wide:true},
 {name:'education_level',label:'مقطع تحصیلی'}, {name:'education_field',label:'رشته تحصیلی'},
 {name:'military_status',label:'وضعیت نظام وظیفه'}, {name:'insurance_no',label:'شماره بیمه'},
 {name:'bank_account',label:'شماره حساب'},
 {name:'start_date',label:'تاریخ شروع همکاری (شمسی)',type:'date'},
 {name:'end_date',label:'تاریخ پایان همکاری (شمسی)',type:'date'},
 {name:'employment_status',label:'وضعیت اشتغال',type:'select',options:HRM_CHOICES.employment_status,default:'active'},
 {name:'unit_id',label:'واحد سازمانی',type:'select',options:L=>[[0,'—']].concat(hrmOpts(L.org_units,'id','name'))},
 {name:'position_id',label:'سمت سازمانی',type:'select',options:L=>[[0,'—']].concat(hrmOpts(L.positions,'id','title'))},
 {name:'work_location_id',label:'محل خدمت',type:'select',options:L=>[[0,'—']].concat(hrmOpts(L.work_locations,'id','name'))},
 {name:'manager_id',label:'مدیر مستقیم',type:'select',options:L=>[[0,'—']].concat(hrmEmployees(L))},
 {name:'note',label:'توضیحات پرونده',type:'textarea',wide:true},
];
async function hrmPersonnelPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const L = await hrmLookups();
 const rows = await api(hrmUrl('personnel'));
 let selected = null, tab = 'profile', query = '', status = '', unit = '';
 const renderList = async () => {
   const filtered = rows.filter(r => (!status || r.employment_status === status) && (!unit || String(r.unit_id) === String(unit)) &&
     (!query || [r.name,r.code,r.department,r.job,r.national_id,r.phone].some(v => searchMatch(String(v ?? ''), query))));
   el.innerHTML = `<div class="toolbar"><div><h2>پرونده پرسنلی</h2><p class="help">اطلاعات فردی، سوابق استخدام، مدارک، ارزیابی و آموزش در یک پرونده واحد؛ ورود مجدد اطلاعات مشترک حذف شده است.</p></div><div>${hrmPerm('personnel.edit') ? '<button id="hrm-person-add">＋ پرونده جدید</button>' : ''}${hrmPerm('reports.export') ? '<button class="quiet" id="hrm-person-export">خروجی Excel</button>' : ''}</div></div>
     <div class="hrm-filter"><label>جستجو<input id="hrm-person-search" placeholder="نام، کد، سمت، کد ملی"></label>
     ${hrmSelectFilter('status','وضعیت اشتغال',HRM_CHOICES.employment_status,status)}
     ${hrmSelectFilter('unit','واحد سازمانی',hrmOpts(L.org_units,'id','name'),unit)}</div>
     <div class="hrm-master"><div class="master-panel"><div id="hrm-person-list"></div><div id="hrm-person-pager"></div></div>
     <div class="detail-panel" id="hrm-person-detail"><div class="empty">فردی را از فهرست انتخاب کنید.</div></div></div>`;
   const list = $('#hrm-person-list'), offset = 0;
   list.innerHTML = filtered.slice(0,40).map(r => `<button class="hrm-item ${selected === r.id ? 'selected' : ''}" data-person="${r.id}"><b>${esc(r.name)}</b><small>${esc(r.code)} · ${esc(r.job || r.department || 'سمت ثبت‌نشده')}</small></button>`).join('') || '<div class="empty">موردی یافت نشد.</div>';
   $('#hrm-person-pager').textContent = `${filtered.length} پرونده`;
   $('#hrm-person-search').value = query;
   $('#hrm-person-search').oninput = e => { query = e.target.value; renderList() };
   el.querySelectorAll('[data-filter]').forEach(sel => sel.onchange = () => { if(sel.dataset.filter === 'status') status = sel.value; else unit = sel.value; renderList() });
   list.onclick = async e => { const b = e.target.closest('[data-person]'); if(!b) return; selected = Number(b.dataset.person); renderList(); await showDetail(selected) };
   if($('#hrm-person-add')) $('#hrm-person-add').onclick = () => hrmDialog({
     title:'ثبت پرونده پرسنلی', fields:HRM_PERSONNEL_FIELDS, values:{employment_status:'active'},
     onSubmit: async (payload,hasFile) => { await hrmSend('personnel',payload,hasFile,'POST'); notice('پرونده ثبت شد.'); await hrmPersonnelPage() }
   });
   if($('#hrm-person-export')) $('#hrm-person-export').onclick = async () => { try { download(await api(hrmUrl('reports/personnel.xlsx')),'Nikan-HRM-personnel.xlsx') } catch(err){ notice(err.message) } };
   if(selected) await showDetail(selected);
 };
 const showDetail = async id => {
   const detail = await api(hrmUrl('personnel/' + id));
   const box = $('#hrm-person-detail'); if(!box) return;
   const e = detail.employee, p = detail.profile || {};
   const kv = (pairs) => `<div class="hrm-kv">${pairs.map(([label,value]) => `<div><small>${esc(label)}</small>${esc(value ?? '—')}</div>`).join('')}</div>`;
   const tabs = [['profile','مشخصات'],['history','تاریخچه تغییرات'],['training','سوابق آموزشی و شایستگی'],['performance','روند عملکرد'],['documents','مدارک'],['confidential','اسناد محرمانه']];
   const panes = {
     profile: `<div class="hrm-actions">${hrmPerm('personnel.edit') ? '<button id="hrm-person-edit">ویرایش پرونده</button>' : ''}</div>` + kv([
       ['کد پرسنلی',e.code],['نام',e.name],['واحد سازمانی',e.department],['سمت',e.job],
       ['کد ملی',p.national_id],['نام پدر',p.father_name],['تاریخ تولد',p.birth_date_fa],['جنسیت',p.gender],
       ['تلفن',p.phone],['رایانامه',p.email],['نشانی',p.address],
       ['تاریخ شروع همکاری',p.start_date_fa],['وضعیت اشتغال',detail.status_labels?.[p.employment_status] || p.employment_status],
       ['مدرک تحصیلی',[p.education_level,p.education_field].filter(Boolean).join(' / ')],
       ['شماره بیمه',p.insurance_no],['شماره حساب',p.bank_account],
     ]),
     history: detail.history.length ? `<div class="table-wrap hrm-scroll"><table><thead><tr><th>زمان</th><th>فیلد</th><th>پیشین</th><th>جدید</th><th>کاربر</th></tr></thead><tbody>${detail.history.map(h => `<tr><td>${esc(h.changed_at_fa || String(h.changed_at).slice(0,10))}</td><td>${esc(h.field)}</td><td>${esc(h.old_value)}</td><td>${esc(h.new_value)}</td><td>${esc(h.changed_by_name || '—')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">تغییری ثبت نشده است.</div>',
     training: `<h3>سوابق دوره‌ها</h3>${detail.training.records.length ? `<div class="table-wrap"><table><thead><tr><th>دوره</th><th>شایستگی</th><th>تاریخ</th><th>سطح</th></tr></thead><tbody>${detail.training.records.map(r => `<tr><td>${esc(r.course_title)}</td><td>${esc(r.skill_title)}</td><td>${esc(r.course_date_fa || r.course_date)}</td><td>${esc(r.level)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="help">سابقه آموزشی ثبت‌شده‌ای وجود ندارد.</p>'}
       <h3>گواهینامه‌ها</h3><p class="help">${detail.training.certificates.length} گواهینامه · ${detail.training.assessments.length} ارزیابی شایستگی</p>
       <div class="hrm-note">سوابق آموزشی مستقیماً از جداول دوره‌ها، سوابق و ارزیابی‌های همان نرم‌افزار آموزش خوانده می‌شوند و دوباره وارد نمی‌شوند.</div>`,
     performance: detail.performance_trend.length ? `<div class="table-wrap"><table><thead><tr><th>دوره</th><th>شروع</th><th>میانگین وزنی</th><th>شاخص‌ها</th></tr></thead><tbody>${detail.performance_trend.map(r => `<tr><td>${esc(r.period_title)}</td><td>${esc(r.start_date_fa || faDate(r.start_date))}</td><td>${esc(r.average)}</td><td>${esc(r.indicators)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">ارزیابی‌ای ثبت نشده است.</div>',
     documents: `${detail.confidential.includes('documents') && hrmPerm('documents.edit') ? '<div class="hrm-actions"><button id="hrm-doc-add">＋ افزودن سند</button></div>' : ''}
       ${detail.confidential.includes('documents') && detail.documents.length ? `<div class="table-wrap"><table><thead><tr><th>عنوان</th><th>دسته</th><th>فایل</th><th>عملیات</th></tr></thead><tbody>${detail.documents.map(d => `<tr><td>${esc(d.title)}</td><td>${esc(d.category)}</td><td>${esc(d.filename)}</td><td><button class="quiet" data-doc-dl="${d.id}">دانلود</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">سندی ثبت نشده است.</div>'}`,
     confidential: detail.locked ? '<div class="hrm-note">دسترسی به قراردادها، مدارک شخصی، سوابق انضباطی و وام‌ها نیازمند مجوز صریح جداگانه است. نقش مدیر سیستم به‌تنهایی این دسترسی را نمی‌دهد.</div>' :
       `<h3>قراردادها</h3><p class="help">${detail.contracts.length} قرارداد</p><h3>سوابق انضباطی</h3><p class="help">${detail.disciplinary.length} رکورد</p><h3>وام‌ها</h3><p class="help">${detail.loans.length} رکورد</p>`,
   };
   box.innerHTML = `<div class="panel"><h2>${esc(e.name)}</h2><p class="help">${esc(e.code)} · ${esc(e.job || e.department || 'سمت ثبت‌نشده')}</p>
     <div class="hrm-tabs">${tabs.map(([id,label]) => `<button class="${tab===id?'selected':''}" data-tab="${id}">${esc(label)}</button>`).join('')}</div>
     <div id="hrm-person-pane">${panes[tab] || panes.profile}</div></div>`;
   box.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; showDetail(id) });
   if($('#hrm-person-edit')) $('#hrm-person-edit').onclick = () => hrmDialog({
     title:'ویرایش پرونده ' + e.name, fields:HRM_PERSONNEL_FIELDS, values:{...e,...p},
     onSubmit: async (payload,hasFile) => { await hrmSend('personnel',payload,hasFile,'PUT',id); notice('تغییرات پرونده ثبت شد.'); await hrmPersonnelPage() }
   });
   if($('#hrm-doc-add')) $('#hrm-doc-add').onclick = () => hrmDialog({
     title:'افزودن سند استخدامی', fields:[
       {name:'title',label:'عنوان سند',required:true}, {name:'category',label:'دسته‌بندی'},
       {name:'entity_type',label:'نوع رکورد مرتبط',type:'select',options:[['','—'],['contract','قرارداد'],['action','حکم'],['disciplinary','سابقه انضباطی'],['loan','وام'],['request','درخواست']]},
       {name:'confidential',label:'محرمانه',type:'checkbox',default:1,hint:'دسترسی جداگانه لازم است'},
       {name:'file',label:'فایل',type:'file',required:true,wide:true},
     ], values:{confidential:1},
     onSubmit: async (payload,hasFile) => { payload.employee_id = id; await hrmSend('documents',payload,true,'POST'); notice('سند ثبت شد.'); await hrmPersonnelPage() }
   });
   box.querySelectorAll('[data-doc-dl]').forEach(b => b.onclick = async () => {
     try { download(await api(hrmUrl('documents/' + b.dataset.docDl + '/download')),'document') } catch(err){ notice(err.message) }
   });
 };
 await renderList();
}

/* ---------------- cartable / requests ---------------- */
async function hrmRequestsPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const L = await hrmLookups();
 const data = await api(hrmUrl('requests'));
 let tab = data.can_approve && data.pending.length ? 'pending' : 'mine';
 const payloadFields = type => {
   switch(type){
     case 'leave': case 'mission': return [
       {name:'date',label:'از تاریخ (شمسی)',type:'date',required:true,default:currentJalaliDate()},
       {name:'end_date',label:'تا تاریخ (شمسی)',type:'date',default:currentJalaliDate()},
       {name:'leave_type_id',label:'نوع مرخصی',type:'select',options:()=>hrmOpts(L.leave_types,'id','name')},
       {name:'minutes',label:'مدت (دقیقه)',type:'number',min:0,default:0},
       {name:'note',label:'توضیح',type:'textarea',wide:true}];
     case 'loan': return [
       {name:'amount',label:'مبلغ (ریال)',type:'number',min:1,required:true},
       {name:'request_date',label:'تاریخ درخواست (شمسی)',type:'date',required:true,default:currentJalaliDate()},
       {name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.kind_loan,default:'internal'},
       {name:'installments',label:'تعداد اقساط',type:'number',min:0,default:0},
       {name:'note',label:'توضیح',type:'textarea',wide:true}];
     case 'welfare': return [
       {name:'service_id',label:'خدمت رفاهی',type:'select',options:()=>hrmOpts(L.welfare_services,'id','name'),required:true},
       {name:'date',label:'تاریخ (شمسی)',type:'date',required:true,default:currentJalaliDate()},
       {name:'quantity',label:'تعداد',type:'number',min:1,default:1},
       {name:'note',label:'توضیح',type:'textarea',wide:true}];
     case 'reservation': return [
       {name:'accommodation_id',label:'اقامتگاه',type:'select',options:()=>hrmOpts(L.accommodations,'id','name'),required:true},
       {name:'from_date',label:'از تاریخ (شمسی)',type:'date',required:true,default:currentJalaliDate()},
       {name:'to_date',label:'تا تاریخ (شمسی)',type:'date',required:true,default:currentJalaliDate()},
       {name:'guests',label:'تعداد نفرات',type:'number',min:1,default:1},
       {name:'note',label:'توضیح',type:'textarea',wide:true}];
     default: return [{name:'note',label:'شرح درخواست',type:'textarea',wide:true,required:true}];
   }
 };
 const draw = () => {
   const sets = {mine:data.mine, pending:data.pending, all:data.rows};
   const rows = sets[tab] || [];
   el.innerHTML = `<div class="toolbar"><div><h2>کارتابل شخصی و درخواست‌ها</h2><p class="help">گردش تأیید ساده براساس نوع درخواست و مسئول مربوط؛ ضوابط از بخش «ضوابط و تنظیمات» قابل تغییر‌اند.</p></div><div>${hrmPerm('requests.create') ? '<button id="hrm-request-add">＋ درخواست جدید</button>' : ''}</div></div>
     <div class="hrm-tabs">${[['mine','درخواست‌های من'],['pending','در انتظار تأیید'],['all','همه درخواست‌های مجاز']].map(([id,label]) => `<button class="${tab===id?'selected':''}" data-rtab="${id}">${esc(label)} (${(sets[id]||[]).length})</button>`).join('')}</div>
     <div id="hrm-request-table"></div>`;
   const box = $('#hrm-request-table');
   box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>پرسنل</th><th>نوع</th><th>عنوان</th><th>وضعیت</th><th>زمان ثبت</th><th>عملیات</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.employee_name)}</td><td>${esc(r.type_name)}</td><td>${esc(r.title)}</td><td><span class="hrm-badge ${r.status==='approved'?'ok':r.status==='rejected'?'bad':'warn'}">${esc(r.status)}</span></td><td>${esc(String(r.created_at).slice(0,10))}</td>
     <td class="hrm-table-actions">${r.status==='submitted' && data.can_approve ? `<button class="quiet" data-approve="${r.id}">تأیید</button><button class="quiet" data-reject="${r.id}">رد</button>` : ''}${r.status==='submitted' && hrmPerm('requests.create') ? `<button class="quiet" data-cancel="${r.id}">لغو</button>` : ''}<button class="quiet" data-detail="${r.id}">جزئیات</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">درخواستی در این بخش وجود ندارد.</div>';
   el.querySelectorAll('[data-rtab]').forEach(b => b.onclick = () => { tab = b.dataset.rtab; draw() });
   if($('#hrm-request-add')) $('#hrm-request-add').onclick = () => {
     const types = L.request_types.filter(t => t.active);
     const employeeId = user.hrm?.employee_id || (L.employees[0]?.id);
     hrmDialog({title:'درخواست جدید', fields:[
       {name:'employee_id',label:'پرسنل',type:'select',options:()=>hrmEmployees(L),required:true,default:employeeId},
       {name:'type_id',label:'نوع درخواست',type:'select',options:()=>hrmOpts(types,'id','name'),required:true},
       {name:'title',label:'عنوان درخواست'},
     ], onSubmit: async meta => {
       const type = types.find(t => String(t.id) === String(meta.type_id));
       if(!type) throw Error('نوع درخواست یافت نشد');
       hrmDialog({title:'جزئیات ' + type.name, fields:payloadFields(type.code), values:{},
         onSubmit: async payload => {
           await api(hrmUrl('requests'), {method:'POST', body:{employee_id:meta.employee_id, type_id:meta.type_id, title:meta.title || type.name, payload}});
           notice('درخواست ثبت شد.'); await hrmRequestsPage();
         }});
     }});
   };
   box.onclick = async e => {
     const approve = e.target.closest('[data-approve]'), reject = e.target.closest('[data-reject]'),
       cancel = e.target.closest('[data-cancel]'), detail = e.target.closest('[data-detail]');
     const id = approve?.dataset.approve || reject?.dataset.reject || cancel?.dataset.cancel || detail?.dataset.detail;
     if(!id) return;
     try {
       if(detail){
         const r = await api(hrmUrl('requests/' + id));
         hrmDialog({title:'جزئیات درخواست', fields:[
           {name:'info',label:'اطلاعات',type:'static',wide:true,default:`${r.employee_name} · ${r.type_name} · ${r.status}`},
           {name:'payload',label:'جزئیات ثبت‌شده',type:'static',wide:true,default:JSON.stringify(r.payload)},
           {name:'note',label:'توضیح تصمیم',type:'static',wide:true,default:r.decision_note || '—'},
         ], onSubmit: async () => {}});
         return;
       }
       if(approve || reject){
         const status = approve ? 'approved' : 'rejected';
         await api(hrmUrl('requests/' + id + '/decision'), {method:'POST', body:{status, note:''}});
         notice(approve ? 'درخواست تأیید و رکورد مربوط ثبت شد.' : 'درخواست رد شد.');
       }
       if(cancel){ await api(hrmUrl('requests/' + id + '/cancel'), {method:'POST', body:{}}); notice('درخواست لغو شد.') }
       await hrmRequestsPage();
     } catch(err){ notice(err.message) }
   };
 };
 await draw();
}

/* ---------------- reports ---------------- */
async function hrmReportsPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const list = await api(hrmUrl('reports'));
 let selected = (list.find(r => r.available) || list[0]).name, from = '', to = '';
 const draw = async () => {
   const definition = list.find(r => r.name === selected);
   el.innerHTML = `<div class="toolbar"><div><h2>گزارش‌ها و خروجی Excel</h2><p class="help">جست‌وجو، فیلتر تاریخ شمسی و خروجی فقط در محدوده مجوزهای شما.</p></div><div>${hrmPerm('reports.export') ? '<button class="quiet" id="hrm-report-export">خروجی Excel</button>' : ''}</div></div>
     <div class="hrm-filter"><label>گزارش<select id="hrm-report-name">${list.map(r => `<option value="${esc(r.name)}" ${r.name===selected?'selected':''} ${r.available?'':'disabled'}>${esc(r.label)}${r.available?'':' · نیازمند مجوز محرمانه'}</option>`).join('')}</select></label>
     <label>از تاریخ (شمسی)<input id="hrm-report-from" dir="ltr" placeholder="۱۴۰۰/۰۱/۰۱" value="${esc(from)}"></label>
     <label>تا تاریخ (شمسی)<input id="hrm-report-to" dir="ltr" placeholder="۱۴۰۵/۱۲/۲۹" value="${esc(to)}"></label>
     <label>جستجو<input id="hrm-report-search" placeholder="فیلتر در نتایج"></label></div>
     <div id="hrm-report-body"></div>`;
   $('#hrm-report-name').onchange = e => { selected = e.target.value; draw() };
   $('#hrm-report-from').onchange = e => { from = e.target.value; draw() };
   $('#hrm-report-to').onchange = e => { to = e.target.value; draw() };
   const query = new URLSearchParams(); if(from) query.set('from',from); if(to) query.set('to',to);
   const report = await api(hrmUrl('reports/' + selected + (query.toString() ? '?' + query.toString() : '')));
   const box = $('#hrm-report-body');
   if(!report.rows.length){ box.innerHTML = '<div class="empty">رکوردی با این فیلترها یافت نشد.</div>' }
   else {
     box.innerHTML = `<div class="table-wrap hrm-scroll"><table><thead><tr>${report.headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${report.rows.map(r => `<tr>${report.columns.map(c => `<td>${hrmCell(r,c,/date|at$/.test(c)?'date':'text')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
     $('#hrm-report-search').oninput = e => {
       box.querySelectorAll('tbody tr').forEach(row => row.hidden = !searchMatch(row.textContent, e.target.value));
     };
   }
   if($('#hrm-report-export')) $('#hrm-report-export').onclick = async () => {
     try { download(await api(hrmUrl('reports/' + selected + '.xlsx' + (query.toString() ? '?' + query.toString() : ''))), 'Nikan-HRM-' + selected + '.xlsx') }
     catch(err){ notice(err.message) }
   };
 };
 await draw();
}

/* ---------------- users / permissions ---------------- */
async function hrmUsersPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const catalog = await api('hrm/catalog');
 const rows = await api(hrmUrl('users'));
 const canEdit = hrmPerm('users.permissions');
 const confidentialFields = Object.entries(catalog.confidential).map(([key,label]) =>
   ({name:'grant_'+key,label:'مجوز محرمانه: '+label,type:'checkbox',wide:true}));
 const fields = [
   {name:'username',label:'نام کاربری',type:'static'},
   {name:'role',label:'نقش منابع انسانی',type:'select',options:[['','بدون نقش']].concat(Object.entries(catalog.roles).map(([k,v])=>[k,v]))},
   {name:'scope',label:'دامنه دسترسی',type:'select',options:[['','تعیین‌نشده']].concat(Object.entries(catalog.scopes).map(([k,v])=>[k,v]))},
   {name:'company_id',label:'شرکت',type:'select',options:()=>user.companies.map(c=>[c.id,c.name])},
   {name:'employee_id',label:'پرونده پرسنلی مرتبط',type:'select',options:L=>[[0,'—']].concat(hrmEmployees(L))},
   {name:'unit_id',label:'واحد تحت مسئولیت',type:'number',min:0},
   {name:'active',label:'حساب فعال',type:'checkbox',default:1},
   {name:'password',label:'رمز عبور جدید (حداقل ۱۲ نویسه)',type:'password'},
   ...confidentialFields,
 ];
 el.innerHTML = `<div class="toolbar"><div><h2>کاربران، نقش‌ها و مجوزها</h2><p class="help">دسترسی براساس نقش، شرکت و واحد در سمت سرور اعمال می‌شود. مجوز اسناد محرمانه جداگانه و صریح است؛ مدیر سیستم به‌تنهایی به آن‌ها دسترسی ندارد.</p></div><div>${canEdit ? '<button id="hrm-user-add">＋ کاربر جدید</button>' : ''}</div></div>
   <div class="hrm-note">نقش‌ها: ${Object.values(catalog.roles).join(' · ')}</div><div id="hrm-user-table" style="margin-top:18px"></div>`;
 const box = $('#hrm-user-table');
 box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>نام کاربری</th><th>نقش</th><th>دامنه</th><th>شرکت</th><th>مجوزهای محرمانه</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.username)}</td><td>${esc(catalog.roles[r.role]||'—')}</td><td>${esc(catalog.scopes[r.scope]||'—')}</td><td>${esc(user.companies.find(c=>c.id===r.company_id)?.name||'هلدینگ')}</td><td>${r.confidential.length?r.confidential.map(c=>`<span class="hrm-badge warn">${esc(c)}</span>`).join(' '):'—'}</td><td>${r.active?'فعال':'غیرفعال'}</td><td>${canEdit?`<button class="quiet" data-user="${r.id}">ویرایش مجوزها</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
 if($('#hrm-user-add')) $('#hrm-user-add').onclick = () => hrmDialog({title:'ایجاد کاربر', fields:[
     {name:'username',label:'نام کاربری (حداقل ۳ نویسه)',required:true},
     {name:'password',label:'رمز عبور (حداقل ۱۲ نویسه)',type:'password',required:true},
     {name:'role',label:'نقش',type:'select',options:Object.entries(catalog.roles),required:true},
     {name:'company_id',label:'شرکت',type:'select',options:()=>user.companies.map(c=>[c.id,c.name])},
   ], onSubmit: async payload => { await api(hrmUrl('users'),{method:'POST',body:payload}); notice('کاربر ایجاد شد.'); await hrmUsersPage() }});
 box.onclick = e => {
   const button = e.target.closest('[data-user]'); if(!button) return;
   const row = rows.find(r => String(r.id) === button.dataset.user);
   const values = {...row};
   row.confidential.forEach(key => values['grant_'+key] = 1);
   hrmDialog({title:'مجوزهای ' + row.username, fields, values, onSubmit: async payload => {
     const body = {role:payload.role, scope:payload.scope, company_id:payload.company_id,
       employee_id:payload.employee_id, unit_id:payload.unit_id, active:payload.active,
       grants:[], revokes:[]};
     if(payload.password) body.password = payload.password;
     Object.keys(catalog.confidential).forEach(key => {
       const code = 'confidential.' + key;
       if(payload['grant_'+key]) body.grants.push(code); else body.revokes.push(code);
     });
     await api(hrmUrl('users/' + row.id), {method:'PUT', body});
     notice('مجوزها ذخیره شد.'); await hrmUsersPage();
   }});
 };
}

/* ---------------- settings ---------------- */
async function hrmSettingsPage(){
 const el = $('#content');
 el.innerHTML = '<div class="empty" role="status">در حال بارگذاری…</div>';
 const rows = await api(hrmUrl('settings'));
 const canEdit = hrmPerm('settings.edit');
 el.innerHTML = `<div class="toolbar"><div><h2>ضوابط و تنظیمات منابع انسانی</h2><p class="help">قوانین مرخصی، تمدید قرارداد، وام، رفاهیات و رزرو به‌صورت داده ذخیره شده‌اند و بدون تغییر کد قابل تنظیم‌اند. مقادیر اولیه فقط پیش‌فرض‌اند و باید به تأیید سازمان برسند.</p></div></div>
   <div class="panel hrm-form">${rows.map(r => `<label>${esc(r.label)}<input data-setting="${esc(r.key)}" value="${esc(r.value)}" ${canEdit?'':'disabled'}></label>`).join('')}</div>
   ${canEdit ? '<div class="hrm-actions"><button id="hrm-settings-save">ذخیره ضوابط</button></div>' : ''}<div id="hrm-settings-state"></div>`;
 if($('#hrm-settings-save')) $('#hrm-settings-save').onclick = async () => {
   const state = $('#hrm-settings-state'); state.textContent = 'در حال ذخیره…';
   try {
     for(const input of el.querySelectorAll('[data-setting]')){
       await api(hrmUrl('settings'), {method:'PUT', body:{key:input.dataset.setting, value:input.value}});
     }
     state.textContent = 'همه ضوابط ذخیره شد.';
   } catch(err){ state.textContent = err.message }
 };
}

/* ---------------- page table (configs) ---------------- */
function hrmConfigs(){
 const L = hrmCache.lookups[String(cid)] || {};
 const employee = {name:'employee_id',label:'پرسنل',type:'select',required:true,options:()=>hrmEmployees(hrmCache.lookups[String(cid)]||{})};
 const date = (name,label,required=true) => ({name,label,type:'date',required});
 const money = (name,label) => ({name,label,type:'number',min:0});
 const note = {name:'note',label:'توضیح',type:'textarea',wide:true};
 return {
  hrm_org: {endpoint:'org-units', title:'واحدهای سازمانی', perm:'personnel.view', write:'personnel.edit',
    columns:[['name','نام'],['code','کد'],['parent_id','واحد بالادست'],['active','فعال','bool']],
    fields:[{name:'name',label:'نام واحد',required:true},{name:'code',label:'کد واحد'},
      {name:'parent_id',label:'واحد بالادست',type:'select',options:()=>[[0,'—']].concat(hrmOpts(hrmCache.lookups[String(cid)]?.org_units,'id','name'))},
      {name:'manager_id',label:'مدیر واحد',type:'select',options:()=>[[0,'—']].concat(hrmEmployees(hrmCache.lookups[String(cid)]||{}))},
      {name:'active',label:'فعال',type:'checkbox',default:1}],
    report:'personnel'},
  hrm_contracts: {endpoint:'contracts', title:'قراردادها', perm:'contract.view', write:'contract.edit', report:'contracts',
    columns:[['employee_name','پرسنل'],['number','شماره'],['type','نوع'],['start_date','شروع','date'],['end_date','پایان','date'],['status','وضعیت'],['renewal_count','تمدید'],['days_to_expiry','روز تا انقضا']],
    fields:[employee,{name:'number',label:'شماره قرارداد'},{name:'type',label:'نوع قرارداد',default:'permanent'},
      {name:'contract_type_id',label:'قالب قرارداد',type:'select',options:()=>[[0,'—']].concat(hrmOpts(hrmCache.lookups[String(cid)]?.contract_types,'id','name'))},
      date('start_date','تاریخ شروع'),date('end_date','تاریخ پایان'),
      {name:'status',label:'وضعیت',type:'select',options:HRM_CHOICES.status_contract,default:'active'},
      money('wage','مبلغ / حقوق'),note],
    filters:[{name:'status',label:'وضعیت',options:HRM_CHOICES.status_contract},{name:'employee_id',label:'پرسنل',options:()=>hrmEmployees(hrmCache.lookups[String(cid)]||{})}],
    rowActions:[{id:'renew',label:'تمدید',show:r=>r.status==='active'||r.status==='expired',run:async row => {
      const L2 = await hrmLookups();
      hrmDialog({title:'تمدید قرارداد ' + (row.number||row.id), fields:[date('end_date','پایان جدید'),{name:'number',label:'شماره تمدید'},note],
        onSubmit: async payload => { await api(hrmUrl('contracts/'+row.id+'/renew'),{method:'POST',body:payload}); notice('قرارداد تمدید شد.'); await hrmCrudPage(hrmConfigs().hrm_contracts) }});
    }}],
    extraButtons:[{id:'alerts',label:'هشدار انقضا',run:async () => {
      const alerts = await api(hrmUrl('contract-alerts'));
      hrmDialog({title:'قراردادهای نزدیک به انقضا',fields:[{name:'list',label:'فهرست',type:'static',wide:true,
        default:alerts.length?alerts.map(a=>`${a.employee_name} · ${a.end_date_fa||faDate(a.end_date)} · ${a.days_to_expiry} روز`).join('\n'):'موردی وجود ندارد.'}],onSubmit:async()=>{}});
    }}]},
  hrm_actions: {endpoint:'personnel-actions', title:'احکام، انتقال و تسویه‌حساب', perm:'contract.view', write:'contract.edit', deletable:true,
    columns:[['employee_name','پرسنل'],['kind','نوع'],['effective_date','تاریخ اجرا','date'],['from_value','از'],['to_value','به'],['decision_no','شماره حکم'],['settlement_amount','مبلغ تسویه']],
    fields:[employee,{name:'kind',label:'نوع اقدام',type:'select',options:HRM_CHOICES.kind_action,required:true},
      date('effective_date','تاریخ اجرا'),{name:'from_value',label:'مقدار پیشین'},{name:'to_value',label:'مقدار جدید'},
      {name:'decision_no',label:'شماره حکم'},money('settlement_amount','مبلغ تسویه'),date('settlement_date','تاریخ تسویه',false),
      {name:'reason',label:'دلیل',type:'textarea',wide:true}], report:'personnel'},
  hrm_documents: {endpoint:'documents', title:'اسناد استخدامی', perm:'documents.view', write:'documents.edit', editable:false,
    columns:[['title','عنوان'],['category','دسته'],['filename','فایل'],['entity_type','مرتبط با'],['created_at','زمان ثبت','date'],['confidential','محرمانه','bool']],
    fields:[{name:'title',label:'عنوان سند',required:true},{name:'category',label:'دسته‌بندی'},
      employee,
      {name:'entity_type',label:'نوع رکورد',type:'select',options:[['','—'],['contract','قرارداد'],['action','حکم'],['disciplinary','انضباطی'],['loan','وام']]},
      {name:'confidential',label:'محرمانه',type:'checkbox',default:1},
      {name:'file',label:'فایل',type:'file',required:true,wide:true}],
    extraButtons:[{id:'upload',label:'＋ بارگذاری سند',run:async (L2,draw) => {
      hrmDialog({title:'بارگذاری سند',fields:hrmConfigs().hrm_documents.fields,values:{confidential:1},
        onSubmit:async (payload)=>{ await hrmSend('documents',payload,true,'POST'); notice('سند ثبت شد.'); await draw() }});
    }}], deletable:false},
  hrm_attendance: {endpoint:'attendance', title:'حضور، غیاب، مرخصی و اضافه‌کاری', perm:'attendance.view', write:'attendance.edit', report:'attendance',
    columns:[['employee_name','پرسنل'],['unit_name','واحد'],['kind_label','نوع'],['date','از تاریخ','date'],['end_date','تا تاریخ','date'],['minutes','دقیقه'],['hours','ساعت'],['status','وضعیت'],['source','منبع']],
    fields:[employee,{name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.kind_attendance,required:true},
      date('date','تاریخ'),date('end_date','تا تاریخ',false),
      {name:'leave_type_id',label:'نوع مرخصی',type:'select',options:()=>[[0,'—']].concat(hrmOpts(hrmCache.lookups[String(cid)]?.leave_types,'id','name'))},
      {name:'minutes',label:'مدت (دقیقه)',type:'number',min:0,default:0},{name:'hours',label:'ساعت',type:'number',min:0,default:0},
      {name:'status',label:'وضعیت',type:'select',options:HRM_CHOICES.status_attendance,default:'recorded'},note],
    filters:[{name:'kind',label:'نوع',options:HRM_CHOICES.kind_attendance},{name:'status',label:'وضعیت',options:HRM_CHOICES.status_attendance}],
    rowActions:[{id:'approve',label:'تأیید',show:r=>r.status==='pending'&&hrmPerm('requests.approve'),run:async(row,L2,draw)=>{
      await api(hrmUrl('attendance/'+row.id+'/decision'),{method:'POST',body:{status:'approved'}}); notice('رکورد تأیید شد.'); await draw(); }}],
    extraButtons:[{id:'template',label:'قالب اکسل',run:async()=>{ try{ download(await api(hrmUrl('attendance/template')),'attendance-template.xlsx') }catch(err){notice(err.message)} }},
      {id:'import',label:'ورود از Excel',run:async(L2,draw)=>{
        hrmDialog({title:'ورود اطلاعات حضور و غیاب از Excel',fields:[
          {name:'info',label:'راهنما',type:'static',wide:true,default:'ستون‌ها: کد پرسنلی، تاریخ شمسی، تا تاریخ، نوع، مدت (دقیقه)، ساعت، توضیح. رکوردهای تکراری و کدهای نامعتبر رد می‌شوند.'},
          {name:'file',label:'فایل اکسل',type:'file',required:true,wide:true}],
          onSubmit:async payload=>{ const fd=new FormData(); fd.append('file',payload.file); fd.append('company_id',String(cid));
            const result=await api(hrmUrl('attendance/import'),{method:'POST',body:fd});
            notice(`${result.inserted} رکورد ثبت شد، ${result.skipped} ردیف بررسی نشد.`); await draw(); }});
      }}]},
  hrm_performance: {endpoint:'performance-periods', title:'دوره‌ها، شاخص‌ها و امتیازات', perm:'performance.view', write:'performance.edit',
    columns:[['title','دوره'],['start_date','شروع','date'],['end_date','پایان','date'],['status','وضعیت']],
    fields:[{name:'title',label:'عنوان دوره',required:true},date('start_date','شروع دوره'),date('end_date','پایان دوره'),
      {name:'status',label:'وضعیت',type:'select',options:HRM_CHOICES.period_status,default:'open'}],
    extraButtons:[{id:'indicators',label:'شاخص‌های عملکرد',run:async()=>hrmCrudPage(hrmConfigs().hrm_performance_indicators)},
      {id:'scores',label:'ثبت امتیازات',run:async()=>{
        const L2=await hrmLookups();
        hrmDialog({title:'ثبت امتیازات ارزیابی',fields:[
          {name:'employee_id',label:'پرسنل',type:'select',options:()=>hrmEmployees(L2),required:true},
          {name:'period_id',label:'دوره',type:'select',options:()=>hrmOpts(L2.performance_periods,'id','title'),required:true},
          ...L2.performance_indicators.map(i=>({name:'score_'+i.id,label:i.title+' (۰ تا ۱۰۰)',type:'number',min:0})),
        ],onSubmit:async payload=>{
          const scores=Object.entries(payload).filter(([k])=>k.startsWith('score_')).map(([k,v])=>({indicator_id:Number(k.slice(6)),score:v||0}));
          await api(hrmUrl('performance-scores'),{method:'POST',body:{employee_id:payload.employee_id,period_id:payload.period_id,scores}});
          notice('امتیازات ذخیره شد.');
        }});
      }},{id:'report',label:'گزارش ارزیابی',run:async()=>{
        const rows=await api(hrmUrl('performance-report'));
        hrmDialog({title:'نتایج ارزیابی عملکرد',fields:[{name:'list',label:'نتایج',type:'static',wide:true,
          default:rows.length?rows.map(r=>`${r.employee_name} · ${r.period_title} · ${r.weighted_average}`).join('\n'):'ارزیابی‌ای ثبت نشده است.'}],onSubmit:async()=>{}});
      }}]},
  hrm_performance_indicators: {endpoint:'performance-indicators',title:'شاخص‌های عملکرد',perm:'performance.view',write:'performance.edit',
    columns:[['title','شاخص'],['weight','وزن'],['target','هدف'],['active','فعال','bool']],
    fields:[{name:'title',label:'عنوان شاخص',required:true},{name:'weight',label:'وزن',type:'number',min:0,default:1},
      {name:'target',label:'هدف',type:'number',min:0,default:100},{name:'description',label:'شرح',type:'textarea',wide:true},
      {name:'active',label:'فعال',type:'checkbox',default:1}]},
  hrm_disciplinary: {endpoint:'disciplinary',title:'سوابق تشویقی و انضباطی',perm:'performance.view',write:'performance.edit',report:'disciplinary',
    columns:[['employee_name','پرسنل'],['kind_label','نوع'],['date','تاریخ','date'],['reason','دلیل'],['decision','تصمیم'],['decision_no','شماره']],
    fields:[employee,{name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.kind_discipline,required:true},
      date('date','تاریخ'),{name:'reason',label:'دلیل',type:'textarea',required:true,wide:true},
      {name:'decision',label:'تصمیم'},{name:'decision_no',label:'شماره تصمیم'},
      {name:'confidential',label:'محرمانه',type:'checkbox',default:1}]},
  hrm_loans: {endpoint:'loans',title:'وام‌ها و تسهیلات',perm:'benefits.view',write:'benefits.edit',report:'loans',
    columns:[['employee_name','پرسنل'],['kind','نوع'],['amount','مبلغ'],['request_date','درخواست','date'],['status','وضعیت'],['paid_amount','پرداخت‌شده'],['letter_no','معرفی‌نامه']],
    fields:[employee,{name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.kind_loan,default:'internal'},
      money('amount','مبلغ (ریال)'),date('request_date','تاریخ درخواست'),
      {name:'installments',label:'تعداد اقساط',type:'number',min:0,default:0},
      {name:'bank_name',label:'بانک'},{name:'letter_no',label:'شماره معرفی‌نامه'},
      {name:'letter_status',label:'وضعیت معرفی‌نامه'},
      {name:'status',label:'وضعیت',type:'select',options:HRM_CHOICES.status_loan,default:'submitted'},note],
    filters:[{name:'kind',label:'نوع',options:HRM_CHOICES.kind_loan},{name:'status',label:'وضعیت',options:HRM_CHOICES.status_loan}],
    rowActions:[{id:'payment',label:'ثبت قسط',show:()=>hrmPerm('benefits.edit'),run:async(row,L2,draw)=>{
      hrmDialog({title:'ثبت پرداخت قسط',fields:[date('date','تاریخ پرداخت'),money('amount','مبلغ'),
        {name:'status',label:'وضعیت',type:'select',options:[['paid','پرداخت‌شده'],['planned','برنامه‌ریزی‌شده'],['late','با تأخیر']]},note],
        onSubmit:async payload=>{ await api(hrmUrl('loan-payments'),{method:'POST',body:{...payload,loan_id:row.id}}); notice('قسط ثبت شد.'); await draw() }});
    }}], deletable:false},
  hrm_welfare: {endpoint:'welfare-usages',title:'خدمات رفاهی و مزایا',perm:'welfare.view',write:'welfare.edit',report:'welfare',
    columns:[['employee_name','پرسنل'],['service_name','خدمت'],['date','تاریخ','date'],['quantity','تعداد'],['value','ارزش'],['unit_name','واحد']],
    fields:[employee,{name:'service_id',label:'خدمت',type:'select',options:()=>hrmOpts(hrmCache.lookups[String(cid)]?.welfare_services,'id','name'),required:true},
      date('date','تاریخ'),{name:'quantity',label:'تعداد',type:'number',min:1,default:1},money('value','ارزش'),note],
    filters:[{name:'service_id',label:'خدمت',options:()=>hrmOpts(hrmCache.lookups[String(cid)]?.welfare_services,'id','name')}],
    extraButtons:[{id:'services',label:'تعریف خدمات و ضوابط',run:async()=>hrmCrudPage(hrmConfigs().hrm_welfare_services)},
      {id:'report',label:'گزارش توزیع',run:async()=>{
        const r=await api(hrmUrl('benefits-report'));
        hrmDialog({title:'توزیع خدمات رفاهی',fields:[{name:'list',label:'به تفکیک فرد',type:'static',wide:true,
          default:r.welfare.length?r.welfare.map(x=>`${x.name} · ${x.count} دفعه · ${x.quantity} قلم`).join('\n'):'استفاده‌ای ثبت نشده است.'}],onSubmit:async()=>{}});
      }}]},
  hrm_welfare_services: {endpoint:'welfare-services',title:'انواع خدمات رفاهی',perm:'welfare.view',write:'welfare.edit',
    columns:[['name','خدمت'],['kind','نوع'],['max_per_period','سقف دوره'],['period','دوره'],['active','فعال','bool']],
    fields:[{name:'name',label:'نام خدمت',required:true},{name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.service_kind},
      {name:'max_per_period',label:'سقف استفاده در دوره',type:'number',min:0,default:0},
      {name:'period',label:'دوره',type:'select',options:HRM_CHOICES.period,default:'month'},
      {name:'requires_approval',label:'نیاز به تأیید',type:'checkbox',default:1},
      {name:'scope',label:'دامنه تعریف',type:'select',options:HRM_CHOICES.scope_rule},
      {name:'description',label:'شرح ضوابط',type:'textarea',wide:true}]},
  hrm_reservations: {endpoint:'reservations',title:'رزرو مهمانسرا و ویلاها (از جمله محمودآباد)',perm:'welfare.view',write:'welfare.reserve',report:'reservations',
    columns:[['employee_name','پرسنل'],['accommodation_name','اقامتگاه'],['location','موقعیت'],['from_date','از','date'],['to_date','تا','date'],['nights','شب‌ها'],['guests','نفرات'],['status','وضعیت']],
    fields:[employee,{name:'accommodation_id',label:'اقامتگاه',type:'select',options:()=>hrmOpts(hrmCache.lookups[String(cid)]?.accommodations,'id','name'),required:true},
      date('from_date','از تاریخ'),date('to_date','تا تاریخ (آخرین شب)'),
      {name:'guests',label:'تعداد نفرات',type:'number',min:1,default:1},note],
    filters:[{name:'status',label:'وضعیت',options:HRM_CHOICES.status_reservation},
      {name:'accommodation_id',label:'اقامتگاه',options:()=>hrmOpts(hrmCache.lookups[String(cid)]?.accommodations,'id','name')}],
    extraButtons:[{id:'places',label:'مدیریت اقامتگاه‌ها',run:async()=>hrmCrudPage(hrmConfigs().hrm_accommodations)},
      {id:'availability',label:'بررسی تداخل',run:async()=>{
        hrmDialog({title:'بررسی آزاد‌بودن اقامتگاه',fields:[
          {name:'accommodation_id',label:'اقامتگاه',type:'select',options:()=>hrmOpts(hrmCache.lookups[String(cid)]?.accommodations,'id','name'),required:true},
          date('from','از تاریخ',true),date('to','تا تاریخ',true)],
          onSubmit:async payload=>{ const q=new URLSearchParams({accommodation_id:payload.accommodation_id,from:payload.from,to:payload.to});
            const r=await api(hrmUrl('reservations/availability?'+q.toString()));
            notice(r.available?'اقامتگاه در این بازه آزاد است.':'تداخل رزرو وجود دارد؛ بازه دیگری انتخاب کنید.'); }});
      }}],
    rowActions:[{id:'approve',label:'تأیید',show:r=>r.status==='submitted'&&hrmPerm('requests.approve'),run:async(row,L2,draw)=>{
      await api(hrmUrl('reservations/'+row.id+'/decision'),{method:'POST',body:{status:'approved'}}); notice('رزرو تأیید شد.'); await draw(); }}], deletable:false},
  hrm_accommodations: {endpoint:'accommodations',title:'اقامتگاه‌ها و ضوابط',perm:'welfare.view',write:'welfare.edit',
    columns:[['name','اقامتگاه'],['kind','نوع'],['location','موقعیت'],['capacity','ظرفیت'],['max_nights','حداکثر شب'],['rules','ضوابط']],
    fields:[{name:'name',label:'نام',required:true},{name:'kind',label:'نوع',type:'select',options:HRM_CHOICES.place_kind},
      {name:'location',label:'موقعیت (مثلاً محمودآباد)'},{name:'capacity',label:'ظرفیت (خانواده/اتاق)',type:'number',min:1,default:1},
      {name:'max_nights',label:'حداکثر شب‌ها',type:'number',min:1,default:3},
      {name:'rules',label:'ضوابط استفاده',type:'textarea',wide:true},
      {name:'scope',label:'دامنه تعریف',type:'select',options:HRM_CHOICES.scope_rule}]},
 };
}

/* ---------------- render dispatch ---------------- */
async function hrmRender(){
 if(!cid){
   $('#content').innerHTML = '<div class="empty">ابتدا یک شرکت ثبت کنید.</div>'; return;
 }
 try { await hrmLookups() } catch(err){ notice(err.message); return }
 switch(page){
   case 'hrm_dashboard': return hrmDashboardPage();
   case 'hrm_personnel': return hrmPersonnelPage();
   case 'hrm_requests': return hrmRequestsPage();
   case 'hrm_reports': return hrmReportsPage();
   case 'hrm_users': return hrmUsersPage();
   case 'hrm_settings': return hrmSettingsPage();
   default: {
     const config = hrmConfigs()[page];
     if(!config){ $('#content').innerHTML = '<div class="empty">این بخش تعریف نشده است.</div>'; return }
     return hrmCrudPage(config);
   }
 }
}
const hrmPriorRender = render;
render = async function(){
 if(!HRM_PAGES.has(page)) return hrmPriorRender();
 notice('');
 $('#page-title').textContent = (HRM_MENUS.find(m => m[0] === page) || [])[2] || 'منابع انسانی';
 $('#company-label').textContent = user.companies.find(c => c.id === Number(cid))?.name || '';
 document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.page === page));
 const required = HRM_PERMS[page], conf = HRM_CONF[page];
 if(required && !hrmPerm(required) || conf && !hrmConf(conf)){
   $('#content').innerHTML = '<div class="empty"><h2>دسترسی مجاز نیست</h2><p>برای این بخش مجوز لازم به حساب شما اختصاص داده نشده است.</p></div>'; return;
 }
 return hrmRender();
};
