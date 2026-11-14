const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let user,cid,page='dashboard',setup=false,cache={},editId=null,userReset=null;
let courseCalendarScope='holding',courseCalendarYear=currentJalaliYear();
function currentJalaliYear(){return Number(new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn',{year:'numeric',timeZone:'Asia/Tehran'}).format(new Date()).match(/\d+/)[0])}
function currentJalaliDate(){const parts=new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Asia/Tehran'}).formatToParts(new Date());return ['year','month','day'].map(type=>parts.find(part=>part.type===type).value).join('/')}
const labels={code:'کد',name:'نام و نام خانوادگی',department:'واحد سازمانی',job:'سمت شغلی',title:'عنوان',category:'گروه مهارت',importance:'اهمیت (۱ تا ۳)',skill_id:'مهارت',level:'سطح شایستگی (۰ تا ۴)',employee_id:'پرسنل',course_id:'دوره آموزشی',date:'تاریخ شمسی (۱۴۰۵/۰۳/۲۵)',instructor:'مدرس',hours:'مدت (ساعت)',cost:'هزینه (میلیون ریال)',status:'وضعیت',score:'نمره آزمون (۰ تا ۱۰۰)',reaction:'رضایت (۱ تا ۵)',behavior:'نظر مدیر (۱ تا ۵)',result:'تحقق هدف (درصد)',username:'نام کاربری',password:'رمز عبور (حداقل ۱۲ نویسه)',role:'نقش',company_id:'شرکت',required:'سطح لازم',current:'سطح فعلی',gap:'فاصله',created:'زمان ثبت',action:'عملیات',detail:'شناسه رکورد',active:'فعال',calendar_scope:'نمایش در تقویم'};
const defs={employees:['code','name','department','job'],skills:['code','title','category','importance'],requirements:['job','skill_id','level'],assessments:['employee_id','skill_id','level'],courses:['skill_id','title','date','instructor','hours','cost','status','calendar_scope'],records:['employee_id','course_id','level','score','reaction','behavior','result'],users:['username','password','role','company_id']};
const menus=[['dashboard','◈','نمای کلی آموزش'],['employees','♙','پرسنل و ساختار سازمانی'],['skills','▦','کاتالوگ مهارت‌ها'],['requirements','▤','شناسنامه شغلی'],['assessments','▥','ماتریس شایستگی'],['gaps','◎','نیازسنجی و گپ‌آنالیز'],['courses','▣','برنامه و تقویم آموزش'],['records','✓','سوابق و اثربخشی'],['import','↥','قالب‌ها و ورود اکسل'],['audit','≡','گزارش رویدادها'],['users','♧','مدیریت کاربران'],['backup','◷','پشتیبان‌گیری']];
let navigationVersion=0,readController=new AbortController(),entryContext=null;
const staleRequest=()=>new DOMException('', 'AbortError');
function nextNavigation(){navigationVersion++;readController.abort();readController=new AbortController();return navigationVersion}
function resetSession(message=''){
 nextNavigation();user=null;cid=null;page='dashboard';setup=false;cache={};editId=null;userReset=null;courseCalendarScope='holding';courseCalendarYear=currentJalaliYear();
 if(typeof workspaceDialog!=='undefined')workspaceDialog=null;if(typeof workspaceDialogOwner!=='undefined')workspaceDialogOwner=null;if(typeof workspaceDialogDirty!=='undefined')workspaceDialogDirty=false;if(typeof pendingWorkspace!=='undefined')pendingWorkspace=0;
 if(typeof referenceOptions!=='undefined')referenceOptions={};if(typeof referenceOptionsCid!=='undefined')referenceOptionsCid=null;if(typeof backupPoll!=='undefined')clearTimeout(backupPoll);if(typeof brandingAssets!=='undefined')brandingAssets=[];entryContext=null;
 $('#desktop-exit')?.remove();$('#company-logo')?.remove();document.querySelectorAll('.brand-mark').forEach(mark=>mark.textContent='N');document.querySelector('link[rel=icon]')?.remove();
 document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());$('#entry').reset();$('#auth').reset();$('#content').replaceChildren();$('#nav').replaceChildren();$('#company').replaceChildren();
 $('#notice').textContent='';$('#company-label').textContent='';$('#page-title').textContent='';$('#shell').hidden=true;$('#login').hidden=false;$('#login-error').textContent=message;$('#auth-title').textContent='ورود به سامانه';$('#auth button').textContent='ورود به فضای کاری';$('#auth [name=password]').minLength=1;$('#user-label').textContent='';
}
async function api(path,options={}){
 const version=navigationVersion,account=user,isRead=!options.method||options.method==='GET';
 options.headers={...(options.headers||{}),'X-CSRF-Token':user?.csrf||''};
 if(isRead&&!options.signal)options.signal=readController.signal;
 if(options.body&&!(options.body instanceof FormData)){options.headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}
 let response;
 try{response=await fetch('/api/'+path,options)}catch(err){if(err.name==='AbortError')throw err;throw Error('ارتباط با سامانه برقرار نشد. اتصال را بررسی و دوباره تلاش کنید.')}
 if(account!==user||(isRead&&version!==navigationVersion))throw staleRequest();
 if(!response.ok){
  const result=await response.json().catch(()=>({})),err=Error(result.error||'دریافت پاسخ سامانه انجام نشد. دوباره تلاش کنید.');err.status=response.status;err.data=result;err.requestId=result.request_id||response.headers.get('X-Request-ID');
  if(response.status>=500&&err.requestId)err.message+=' · کد پیگیری: '+err.requestId;
  if(account!==user||(isRead&&version!==navigationVersion))throw staleRequest();
  if(response.status===401&&user===account&&user)resetSession('نشست شما پایان یافته است. دوباره وارد شوید.');
  throw err;
 }
 let result;
 try{result=response.headers.get('content-type')?.includes('json')?await response.json():await response.blob()}catch(err){if(err.name==='AbortError')throw err;throw Error('پاسخ سامانه قابل خواندن نیست. دوباره تلاش کنید.')}
 if(account!==user||(isRead&&version!==navigationVersion))throw staleRequest();return result;
}
const scoped=p=>p+(p.includes('?')?'&':'?')+'company_id='+cid;
function notice(t){$('#notice').textContent=t}
const companyIndependentPages=new Set(['companies','users','backup','branding']);
function updateCompanyAvailability(){
 const missing=!user?.companies.length;$('#company').disabled=missing;if(missing)$('#company-label').textContent='هنوز شرکتی ثبت نشده است';
 document.querySelectorAll('#nav [data-page]').forEach(button=>button.disabled=missing&&!companyIndependentPages.has(button.dataset.page));
}
function setCompanyChoices(companies,preferredId=cid){
 if(!user)return;const previous=cid;user.companies=companies.map(company=>({id:Number(company.id),name:company.name}));
 cid=user.companies.find(company=>company.id===Number(preferredId))?.id??user.companies[0]?.id??null;
 if(cid!==previous){cache={};editId=null;userReset=null;entryContext=null;if(typeof referenceOptions!=='undefined')referenceOptions={};if(typeof referenceOptionsCid!=='undefined')referenceOptionsCid=null}
 $('#company').innerHTML=user.companies.length?user.companies.map(company=>`<option value="${company.id}">${esc(company.name)}</option>`).join(''):'<option value="">ابتدا یک شرکت ثبت کنید</option>';
 $('#company').value=cid===null?'':String(cid);$('#company-label').textContent=user.companies.find(company=>company.id===cid)?.name||'هنوز شرکتی ثبت نشده است';
 updateCompanyAvailability();if(typeof applyBranding==='function')applyBranding();
}
function navigationFailure(err,version=navigationVersion){
 if(err.name==='AbortError'||version!==navigationVersion||!user)return;
 notice(err.message);$('#content').innerHTML='<div class="empty"><h2>بارگذاری این بخش انجام نشد</h2><p>حساب شما همچنان متصل است؛ می‌توانید دوباره تلاش کنید یا بخش دیگری را باز کنید.</p><button id="retry-page">تلاش مجدد</button></div>';
 $('#retry-page').onclick=()=>renderCurrent();
}
async function renderCurrent(){
 if(!user)return false;if(!user.companies.length&&!companyIndependentPages.has(page))page='companies';const version=nextNavigation();
 try{await render();if(version===navigationVersion&&user)updateCompanyAvailability();return version===navigationVersion&&!!user}catch(err){navigationFailure(err,version);if(version===navigationVersion&&user)updateCompanyAvailability();return false}
}
async function navigateTo(nextPage,nextCompany=cid){
 if(!user||$('#entry').dataset.busy||(typeof workspaceSaving!=='undefined'&&workspaceSaving))return;const nextId=nextCompany===null||nextCompany===undefined?null:Number(nextCompany),changedCompany=nextId!==cid;page=nextPage;cid=nextId;if(changedCompany){cache={};editId=null;userReset=null;if(user.companies.some(company=>Number(company.id)===nextId))setCompanyChoices(user.companies,nextId)}
 return renderCurrent();
}
function drawNavigation(){
 const allowed=menus.filter(item=>user.role==='admin'||!['users','backup','branding','companies','governance'].includes(item[0]));
 const groups=[['پرسنل و شایستگی',['employees','passports','bank','skills','requirements','job_profiles','assessments','gaps']],['برنامه و اجرا',['governance','courses','needs','instructors','records','certificates','report']],['مدیریت سامانه',['companies','import','audit','users','backup','branding']]];
 const button=item=>`<button data-page="${item[0]}"><span aria-hidden="true">${item[1]}</span>${esc(item[2])}</button>`;
 $('#nav').innerHTML=allowed.filter(item=>item[0]==='dashboard').map(button).join('')+groups.map(([name,keys])=>{const items=allowed.filter(item=>keys.includes(item[0]));return items.length?`<section class="nav-group"><h2 class="nav-group-title">${name}</h2>${items.map(button).join('')}</section>`:''}).join('');
 $('#nav').onclick=e=>{const button=e.target.closest('[data-page]');if(button)navigateTo(button.dataset.page)};
}
async function showLogin(){
 $('#login').hidden=false;$('#shell').hidden=true;
 try{setup=(await api('status')).setup_required;$('#auth-title').textContent=setup?'ساخت حساب مدیر هلدینگ':'ورود به سامانه';$('#auth button').textContent=setup?'راه‌اندازی امن سامانه':'ورود به فضای کاری';$('#auth [name=password]').minLength=setup?12:1}
 catch(err){if(err.name!=='AbortError')$('#login-error').textContent=err.message}
}
async function load(){
 let account;
 try{account=await api('me')}catch(err){
  if(err.name==='AbortError')return;
  if(err.status===401){if(user)resetSession();await showLogin();return}
  if(user){navigationFailure(err);return}
  $('#login-error').textContent=err.message;let retry=$('#retry-login-state');if(!retry){retry=document.createElement('button');retry.id='retry-login-state';retry.type='button';retry.className='quiet';retry.textContent='تلاش مجدد برای اتصال';$('#login-error').after(retry)}retry.onclick=()=>load();return;
 }
 user=account;if(account.deployment_mode==='test'){$('.aside-bottom').textContent='نسخه آزمایشی · داده‌های مستقل';$('footer').textContent='نسخه آزمایشی منابع انسانی نیکان · داده‌های این نسخه مستقل از سامانه عملیاتی هستند'}if(account.deployment_mode==='server'){$('.aside-bottom').textContent='سرور مرکزی · دیتابیس هلدینگ';$('footer').textContent='سامانه یکپارچه منابع انسانی نیکان · نسخه شبکه · اطلاعات شرکت‌ها با دسترسی تفکیک‌شده'}page='dashboard';courseCalendarScope=user.role==='admin'?'holding':'internal';courseCalendarYear=currentJalaliYear();cache={};editId=null;userReset=null;
 $('#retry-login-state')?.remove();$('#login').hidden=true;$('#shell').hidden=false;$('#login-error').textContent='';$('#auth [name=password]').value='';cid=user.companies[0]?.id;
 setCompanyChoices(user.companies,cid);
 menus.find(item=>item[0]==='courses')[2]=user.role==='admin'?'تقویم تجمیعی و برنامه‌ریزی هلدینگ':'آموزش‌های داخلی شرکت';
 if(user.desktop&&!$('#desktop-exit')){const close=document.createElement('button');close.id='desktop-exit';close.className='quiet';close.textContent='بستن برنامه';close.onclick=async()=>{try{await api('desktop-exit',{method:'POST'});document.body.textContent='برنامه نیکان بسته شد. می‌توانید پنجره را ببندید.'}catch(err){notice(err.message)}};$('.header-tools').append(close)}
 $('#user-label').textContent=user.username+' · '+({admin:'مدیر هلدینگ',manager:'مسئول آموزش',viewer:'مشاهده‌گر'}[user.role]);drawNavigation();updateCompanyAvailability();await renderCurrent();
}
const formBusyStates=new WeakMap();
function setFormBusy(form,busy){
 if(busy){form.dataset.busy='1';const controls=[...form.querySelectorAll('button')];formBusyStates.set(form,controls.map(button=>[button,button.disabled]));controls.forEach(button=>button.disabled=true);form.setAttribute('aria-busy','true')}
 else{delete form.dataset.busy;form.removeAttribute('aria-busy');for(const [button,disabled] of formBusyStates.get(form)||[])if(button.isConnected)button.disabled=disabled;formBusyStates.delete(form)}
}
$('#auth').onsubmit=async e=>{
 e.preventDefault();const form=e.target;if(form.dataset.busy)return;setFormBusy(form,true);
 try{const data=Object.fromEntries(new FormData(form));if(setup){await api('setup',{method:'POST',body:data});setup=false}await api('login',{method:'POST',body:data});$('#login-error').textContent='';await load()}
 catch(err){if(err.name!=='AbortError')$('#login-error').textContent=err.message}finally{setFormBusy(form,false)}
};
$('#logout').onclick=async()=>{const button=$('#logout');if(button.disabled)return;button.disabled=true;try{await api('logout',{method:'POST'});resetSession();await showLogin()}catch(err){if(err.status!==401&&err.name!=='AbortError')notice(err.message)}finally{button.disabled=false}};
$('#company').onchange=e=>navigateTo(page,Number(e.target.value));$('#close-dialog').onclick=()=>{if(!$('#entry').dataset.busy)$('#dialog').close()};$('#dialog').addEventListener('cancel',e=>{if($('#entry').dataset.busy)e.preventDefault()});
async function preload(){
 const ownerCid=Number(cid),version=navigationVersion,items=await Promise.all(['employees','skills','courses'].map(table=>api('data/'+table+'?company_id='+ownerCid)));
 if(!user||Number(cid)!==ownerCid||version!==navigationVersion)throw staleRequest();
 ['employees','skills','courses'].forEach((table,index)=>cache[table]=items[index]);
}
function display(k,v){if(k==='calendar_scope')return v==='internal'?'داخلی شرکت':'تجمیعی هلدینگ';if(k==='skill_id')return cache.skills?.find(r=>r.id===v)?.title||v;if(k==='employee_id')return cache.employees?.find(r=>r.id===v)?.name||v;if(k==='course_id')return cache.courses?.find(r=>r.id===v)?.title||v;if(k==='company_id')return user.companies.find(r=>r.id===v)?.name||v;if(k==='status')return {planned:'برنامه‌ریزی‌شده',approved:'مصوب',completed:'برگزارشده',cancelled:'لغوشده'}[v]||v;if(k==='role')return {manager:'مسئول آموزش',viewer:'مشاهده‌گر',admin:'مدیر هلدینگ'}[v];return v??'—'}
async function render(){notice('');$('#company-label').textContent=user.companies.find(c=>c.id===Number(cid))?.name;$('#page-title').textContent=menus.find(m=>m[0]===page)?.[2];document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));if(!['users','backup','import'].includes(page))await preload();const el=$('#content');
 if(page==='dashboard'){const d=await api(scoped('dashboard'));el.innerHTML=`<div class="metrics">${[['پرسنل ثبت‌شده',d.employees,'سرمایه انسانی شرکت'],['مهارت‌های سازمانی',d.skills,'کاتالوگ شایستگی‌ها'],['گپ آموزشی باز',d.gaps,'مبنای نیازسنجی و برنامه‌ریزی'],['دوره‌های آموزشی',d.courses,'برنامه‌ریزی و اجرا']].map((x,i)=>`<div class="card"><small>${x[0]}</small><strong class="${i===2?'accent':''}">${Number(x[1]).toLocaleString('fa-IR')}</strong><p>${x[2]}</p></div>`).join('')}</div><div class="hero"><div><h2>از شایستگی امروز تا توانمندی فردا</h2><p>چرخه آموزش را از تعریف شغل و سنجش مهارت تا اجرای دوره و ارزیابی اثربخشی دنبال کنید.</p><button id="go-gaps">مشاهده نیازهای آموزشی ←</button></div><div class="orb">N</div></div><div class="grid"><div class="panel"><h2>نمای شرکت‌های تحت دسترسی</h2>${d.companies.map(c=>`<div class="company-row"><span>${esc(c.name)}</span><b>${Number(c.employees).toLocaleString('fa-IR')} نفر</b></div>`).join('')}</div><div class="panel"><h2>وضعیت چرخه آموزش</h2><div class="company-row"><span>سوابق فردی دوره‌ها</span><b>${d.records}</b></div><div class="company-row"><span>بودجه دوره‌های فعال</span><b>${Number(d.budget).toLocaleString('fa-IR')} م.ریال</b></div><p class="help">برای آغاز، کاتالوگ و شناسنامه‌ها را از اکسل وارد کنید، شرکت پرسنل را مشخص کنید و سپس دوره‌های رفع گپ را برنامه‌ریزی کنید.</p><div class="pill">تفکیک دسترسی ${user.companies.length.toLocaleString('fa-IR')} شرکت فعال است</div></div></div>`;$('#go-gaps').onclick=()=>navigateTo('gaps');return}
 if(page==='import')return renderExcelForms(el);
 if(page==='backup'){el.innerHTML='<div class="panel import-box"><h2>نسخه پشتیبان پایگاه داده</h2><p class="help">فایل پشتیبان شامل اطلاعات تمام شرکت‌ها و کاربران است. آن را در محل امن و با دسترسی محدود نگهداری کنید. برای بازیابی، سامانه را ببندید و مطابق راهنمای نصب اقدام کنید.</p><button id="download">دریافت نسخه پشتیبان</button></div>';$('#download').onclick=async()=>{try{download(await api('backup',{method:'POST'}),'nikan-backup.sqlite3')}catch(e){notice(e.message)}};return}
 if(page==='courses')return renderCourseCalendar(el);
 let rows=await api(page==='users'?'users':scoped(page==='gaps'?'gaps':page==='audit'?'audit':'data/'+page));const fields=page==='gaps'?['code','name','department','job','title','required','current','gap','importance']:page==='audit'?['created','username','action','detail']:page==='users'?['username','role','company_id','active']:defs[page];el.innerHTML=`<div class="toolbar"><input id="search" placeholder="جستجو در اطلاعات…" aria-label="جستجو"><div>${page==='gaps'?'<button id="csv">خروجی نیازسنجی CSV</button>':''}${defs[page]&&user.role!=='viewer'&&(page!=='users'||user.companies.length)?'<button id="add">＋ ثبت اطلاعات جدید</button>':''}</div></div><div id="table"></div>`;
 let tableOffset=0,tableQuery='';function draw(q=tableQuery){tableQuery=q;const filtered=rows.filter(r=>fields.some(k=>searchMatch(display(k,r[k]),q)));$('#table').innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr>${fields.map(k=>`<th>${labels[k]||k}</th>`).join('')}${defs[page]&&page!=='users'&&user.role!=='viewer'?'<th>عملیات</th>':''}</tr></thead><tbody>${filtered.slice(tableOffset,tableOffset+25).map(r=>`<tr>${fields.map(k=>`<td>${esc(display(k,r[k]))}</td>`).join('')}${defs[page]&&page!=='users'&&user.role!=='viewer'?`<td><button class="quiet" data-edit="${r.id}">ویرایش</button><button class="quiet" data-delete="${r.id}">حذف</button></td>`:''}</tr>`).join('')}</tbody></table></div>${pager(tableOffset,25,filtered.length)}`:'<div class="empty"><h2>اطلاعاتی برای نمایش وجود ندارد</h2><p>اطلاعات این شرکت را ثبت کنید یا از فایل اکسل وارد کنید.</p></div>';bindPager($('#table'),()=>{tableOffset-=25;draw()},()=>{tableOffset+=25;draw()})}
 draw();if(page==='users'){const box=document.createElement('div');box.className='panel';box.innerHTML='<h2>کنترل حساب‌ها</h2>'+rows.map(r=>`<div class="company-row"><span>${esc(r.username)}</span><div><button class="quiet" data-toggle="${r.id}" data-value="${r.active?0:1}">${r.active?'غیرفعال‌سازی':'فعال‌سازی'}</button> <button class="quiet" data-password="${r.id}">تغییر رمز</button></div></div>`).join('');$('#table').after(box);box.onclick=async e=>{if(workspaceSaving)return;const t=e.target.closest('[data-toggle]'),p=e.target.closest('[data-password]');try{if(t){const unlock=workspaceWriteLock(el);try{await api('users/'+t.dataset.toggle,{method:'PUT',body:{active:Number(t.dataset.value)}});await workspaceRefresh(()=>render(),'وضعیت حساب به‌روزرسانی شد.')}finally{unlock()}}if(p){entryContext={page,cid:Number(cid),account:user};userReset=p.dataset.password;$('#form-title').textContent='تغییر رمز عبور';$('#form-error').textContent='';$('#fields').innerHTML='<label>رمز عبور جدید<input name="password" type="password" minlength="12" required autocomplete="new-password"></label>';$('#dialog').showModal()}}catch(err){notice(err.message)}}}$('#search').oninput=e=>{tableOffset=0;draw(e.target.value)};if($('#add'))$('#add').onclick=()=>openForm();if($('#csv'))$('#csv').onclick=async()=>download(await api(scoped('export')),'training-needs.csv');$('#table').onclick=async e=>{if(workspaceSaving)return;const edit=e.target.closest('[data-edit]'),del=e.target.closest('[data-delete]');if(edit)openForm(rows.find(r=>r.id===Number(edit.dataset.edit)));if(del&&confirm('این رکورد حذف شود؟')){const unlock=workspaceWriteLock(el);try{await api(scoped('data/'+page+'/'+del.dataset.delete),{method:'DELETE'});await workspaceRefresh(()=>render(),'رکورد حذف شد.')}catch(err){notice(err.message)}finally{unlock()}}};
}
async function renderCourseCalendar(el){
 const isHoldingAdmin=user.role==='admin';if(!isHoldingAdmin)courseCalendarScope='internal';
 const ownerCid=Number(cid),companyName=user.companies.find(c=>c.id===ownerCid)?.name||'',ownCourses=cache.courses.filter(row=>Number(row.company_id)===ownerCid);
 const fields=defs.courses.filter(field=>isHoldingAdmin||field!=='calendar_scope'),months=['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
 let events=[],calendarQuery='',tableQuery='',tableOffset=0,requestVersion=0,calendarError='',loading=false;
 el.onclick=null;
 el.innerHTML=`<div class="panel"><h2 id="calendar-title"></h2><p id="calendar-context" class="help"></p><div class="toolbar">${isHoldingAdmin?'<label>نوع تقویم<select id="calendar-scope"><option value="holding">تقویم تجمیعی هلدینگ</option><option value="internal">تقویم داخلی شرکت انتخاب‌شده</option></select></label>':''}<label>سال شمسی<select id="calendar-year"></select></label><label>جستجوی تقویم<input id="calendar-search" placeholder="عنوان دوره، شرکت، شایستگی یا مدرس"></label></div><p id="calendar-state" class="help" role="status"></p></div><div id="calendar-grid" class="month-grid"></div><div class="section-title"><div><h2 id="course-management-title"></h2><p class="help">ثبت، ویرایش و حذف فقط برای دوره‌های شرکت انتخاب‌شده انجام می‌شود. سال و نوع تقویم انتخاب‌شده در این فهرست هم اعمال می‌شود.</p></div></div><div class="toolbar"><input id="search" placeholder="جستجو در دوره‌های شرکت…" aria-label="جستجوی دوره‌های شرکت"><div>${user.role!=='viewer'?'<button id="add">＋ ثبت دوره برای این شرکت</button>':''}</div></div><div id="table"></div>`;
 const calendarGrid=$('#calendar-grid'),calendarState=$('#calendar-state');
 const retryCalendar=document.createElement('button');retryCalendar.id='retry-calendar';retryCalendar.type='button';retryCalendar.className='quiet';retryCalendar.textContent='تلاش مجدد برای دریافت تقویم';retryCalendar.hidden=true;calendarState.after(retryCalendar);
 const isCurrent=()=>page==='courses'&&Number(cid)===ownerCid&&$('#calendar-grid')===calendarGrid;
 const dateParts=r=>String(r.date||'').split('/').map(Number);
 const matchesPeriod=r=>(r.calendar_scope||'holding')===courseCalendarScope&&dateParts(r)[0]===courseCalendarYear;
 function setYears(){
  const current=currentJalaliYear(),years=new Set([current-1,current,current+1,courseCalendarYear,...[...events,...ownCourses].map(r=>dateParts(r)[0]).filter(Number.isFinite)]);
  $('#calendar-year').innerHTML=[...years].sort((a,b)=>b-a).map(y=>`<option value="${y}" ${y===courseCalendarYear?'selected':''}>${y.toLocaleString('fa-IR',{useGrouping:false})}</option>`).join('');
 }
 function drawCalendar(){
  const visible=events.filter(r=>dateParts(r)[0]===courseCalendarYear&&[r.title,r.company_name,r.skill_title,r.instructor,r.date,display('status',r.status)].some(v=>searchMatch(v,calendarQuery))).sort((a,b)=>String(a.date).localeCompare(String(b.date))||Number(a.id)-Number(b.id));
  calendarGrid.innerHTML=months.map((month,i)=>{
   const items=visible.filter(r=>dateParts(r)[1]===i+1);
   return `<section class="month-card"><b>${month}</b>${items.length?items.map(r=>`<small class="calendar-event" data-course="${esc(r.id)}"><strong>${esc(r.company_name)}</strong><br>${esc(r.title)}<br>${esc(r.date)} · ${esc(display('status',r.status))}${r.instructor?`<br>مدرس: ${esc(r.instructor)}`:''}${user.role==='admin'&&courseCalendarScope==='holding'?`<br><button type="button" class="quiet" data-plan-course="${esc(r.id)}">برنامه‌ریزی افراد</button> <button type="button" class="quiet" data-attendance-course="${esc(r.id)}">حضور و تسهیم هزینه</button>`:''}</small>`).join(''):'<small>دوره‌ای ثبت نشده</small>'}</section>`;
  }).join('');
  retryCalendar.hidden=!calendarError;calendarState.className=calendarError?'error':'help';
  calendarState.textContent=calendarError?'دریافت تقویم انجام نشد: '+calendarError:loading?'در حال دریافت تقویم…':visible.length?`${visible.length.toLocaleString('fa-IR')} دوره در سال ${courseCalendarYear.toLocaleString('fa-IR',{useGrouping:false})}`:calendarQuery?'دوره‌ای با این جستجو در سال انتخاب‌شده پیدا نشد.':'در سال انتخاب‌شده دوره‌ای در این تقویم ثبت نشده است.';
 }
 function drawOwned(){
  const filtered=ownCourses.filter(r=>matchesPeriod(r)&&fields.some(k=>searchMatch(display(k,r[k]),tableQuery)));
  const scopeName=courseCalendarScope==='holding'?'تجمیعی':'داخلی';
  $('#course-management-title').textContent=`مدیریت دوره‌های ${scopeName} شرکت ${companyName}`;
  const table=$('#table');
  table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr>${fields.map(k=>`<th>${labels[k]}</th>`).join('')}${user.role!=='viewer'?'<th>عملیات</th>':''}</tr></thead><tbody>${filtered.slice(tableOffset,tableOffset+25).map(r=>`<tr>${fields.map(k=>`<td>${esc(display(k,r[k]))}</td>`).join('')}${user.role!=='viewer'?`<td><button class="quiet" data-edit="${esc(r.id)}">ویرایش</button><button class="quiet" data-delete="${esc(r.id)}">حذف</button></td>`:''}</tr>`).join('')}</tbody></table></div>${pager(tableOffset,25,filtered.length)}`:'<div class="empty"><h2>دوره‌ای از این شرکت برای نمایش وجود ندارد</h2><p>سال، نوع تقویم و جستجو را بررسی کنید یا دوره‌ای برای این شرکت ثبت کنید.</p></div>';
  bindPager(table,()=>{tableOffset-=25;drawOwned()},()=>{tableOffset+=25;drawOwned()});
 }
 function describeScope(){
  const holding=courseCalendarScope==='holding';
  if($('#calendar-scope'))$('#calendar-scope').value=courseCalendarScope;
  $('#calendar-title').textContent=holding?'تقویم آموزشی تجمیعی هلدینگ':`تقویم آموزشی داخلی شرکت ${companyName}`;
  $('#calendar-context').textContent=holding?'این تقویم دوره‌های مشترک همه شرکت‌های هلدینگ را نشان می‌دهد و با انتخاب شرکت در بالای صفحه محدود نمی‌شود. دوره‌های داخلی شرکت‌ها در تقویم داخلی همان شرکت دیده می‌شوند.':`دوره‌های خاص و داخلی شرکت ${companyName} در این تقویم نمایش داده می‌شوند.`;
 }
 async function loadCalendar(){
  const version=++requestVersion,scope=courseCalendarScope;
  describeScope();events=[];calendarError='';loading=true;setYears();drawCalendar();drawOwned();
  try{
   const result=await api('calendar?scope='+scope+(scope==='internal'?'&company_id='+ownerCid:''));
   if(!isCurrent()||version!==requestVersion)return;
   events=result;loading=false;setYears();drawCalendar();
  }catch(err){
   if(err.name==='AbortError'||!isCurrent()||version!==requestVersion||!user)return;
   calendarError=err.message;loading=false;drawCalendar();
  }
 }
 retryCalendar.onclick=()=>loadCalendar();
 if($('#calendar-scope'))$('#calendar-scope').onchange=async e=>{courseCalendarScope=e.target.value;tableOffset=0;await loadCalendar()};
 $('#calendar-year').onchange=e=>{courseCalendarYear=Number(e.target.value);tableOffset=0;drawCalendar();drawOwned()};
 $('#calendar-search').oninput=e=>{calendarQuery=e.target.value;drawCalendar()};
 calendarGrid.onclick=e=>{if(user.role!=='admin'||!isCurrent())return;const plan=e.target.closest('[data-plan-course]'),attendance=e.target.closest('[data-attendance-course]');if(plan)openCourseParticipantPlanner(Number(plan.dataset.planCourse));if(attendance)openCourseAttendance(Number(attendance.dataset.attendanceCourse))};
 $('#search').oninput=e=>{tableQuery=e.target.value;tableOffset=0;drawOwned()};
 if($('#add'))$('#add').onclick=()=>{if(isCurrent())openForm({calendar_scope:courseCalendarScope})};
 $('#table').onclick=async e=>{
  if(user.role==='viewer'||!isCurrent()||workspaceSaving)return;
  const edit=e.target.closest('[data-edit]'),del=e.target.closest('[data-delete]');
  if(edit){const row=ownCourses.find(r=>r.id===Number(edit.dataset.edit));if(row)openForm(row)}
  if(del&&ownCourses.some(r=>r.id===Number(del.dataset.delete))&&confirm('این دوره حذف شود؟')){
   const unlock=workspaceWriteLock(el);try{await api('data/courses/'+encodeURIComponent(del.dataset.delete)+'?company_id='+ownerCid,{method:'DELETE'});if(isCurrent())await workspaceRefresh(()=>render(),'دوره حذف شد.')}catch(err){notice(err.message)}finally{unlock()}
  }
 };
 await loadCalendar();
}
async function openCourseParticipantPlanner(courseId){
 if(user.role!=='admin'||workspaceSaving||$('#course-participant-dialog'))return;
 const dialog=document.createElement('dialog');dialog.id='course-participant-dialog';dialog.setAttribute('aria-labelledby','participant-title');
 dialog.innerHTML='<form id="course-participant-form"><div class="dialog-head"><h2 id="participant-title">برنامه‌ریزی افراد دوره مشترک</h2><button type="button" class="quiet" data-plan-close>✕</button></div><p id="participant-course" class="help"></p><p class="help">نام پرسنل همه شرکت‌های هلدینگ برای برنامه‌ریزی آموزش مشترک در دسترس ادمین است. انتخاب افراد در این بخش، برنامه حضور آنان در دوره را مشخص می‌کند؛ ثبت حضور و سوابق آموزشی جداگانه انجام می‌شود.</p><div id="participant-controls" hidden><div class="toolbar"><label>جستجوی نام، کد، شغل یا شرکت<input id="participant-search" placeholder="جستجوی پرسنل هلدینگ"></label><label>فیلتر شرکت<select id="participant-company"><option value="">همه شرکت‌ها</option></select></label></div><p id="participant-selected" class="help" role="status"></p><div id="participant-table"></div></div><p id="participant-error" class="error" role="alert"></p><p id="participant-loading" class="help">در حال دریافت فهرست پرسنل هلدینگ…</p><div class="toolbar"><button type="submit" id="save-course-participants" disabled>ذخیره برنامه‌ریزی افراد</button><button type="button" class="quiet" data-plan-close>انصراف</button></div></form>';
 document.body.append(dialog);dialog.showModal();let saving=false;
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});
 dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault()});
 dialog.querySelectorAll('[data-plan-close]').forEach(button=>button.onclick=()=>{if(!saving)dialog.close()});
 try{
  const [directory,roster]=await Promise.all([api('holding/personnel'),api('courses/'+courseId+'/participants')]);
  if(!dialog.isConnected)return;
  const people=[...new Map([...directory,...roster.participants].map(person=>[person.id,person])).values()],selected=new Set(roster.participants.map(person=>person.id));
  let query='',companyFilter='',offset=0;
  const table=dialog.querySelector('#participant-table'),save=dialog.querySelector('#save-course-participants'),error=dialog.querySelector('#participant-error');
  dialog.querySelector('#participant-course').textContent=roster.course.title+' · '+roster.course.date;
  dialog.querySelector('#participant-loading').hidden=true;dialog.querySelector('#participant-controls').hidden=false;save.disabled=false;
  const companies=[...new Map(people.map(person=>[person.company_id,person.company_name])).entries()];
  dialog.querySelector('#participant-company').innerHTML='<option value="">همه شرکت‌ها</option>'+companies.map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
  function draw(){
   const filtered=people.filter(person=>(!companyFilter||String(person.company_id)===companyFilter)&&[person.name,person.code,person.job,person.department,person.company_name].some(value=>searchMatch(value,query)));
   const selectedCompanies=new Set(people.filter(person=>selected.has(person.id)).map(person=>person.company_id));
   dialog.querySelector('#participant-selected').textContent=`${selected.size.toLocaleString('fa-IR')} نفر از ${selectedCompanies.size.toLocaleString('fa-IR')} شرکت برای این دوره انتخاب شده‌اند. انتخاب‌ها با تغییر جستجو و فیلتر حفظ می‌شوند.`;
   table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr><th>انتخاب</th><th>نام و نام خانوادگی</th><th>کد پرسنلی</th><th>شرکت</th><th>سمت شغلی</th><th>واحد</th></tr></thead><tbody>${filtered.slice(offset,offset+25).map(person=>`<tr><td><input type="checkbox" data-participant="${esc(person.id)}" aria-label="انتخاب ${esc(person.name)} از ${esc(person.company_name)}" ${selected.has(person.id)?'checked':''}></td><td>${esc(person.name)}</td><td>${esc(person.code)}</td><td>${esc(person.company_name)}</td><td>${esc(person.job)}</td><td>${esc(person.department)}</td></tr>`).join('')}</tbody></table></div>${pager(offset,25,filtered.length)}`:`<div class="empty">${people.length?'پرسنلی با این جستجو یا فیلتر پیدا نشد.':'هنوز پرسنلی برای شرکت‌های هلدینگ ثبت نشده است.'}</div>`;
   bindPager(table,()=>{offset-=25;draw()},()=>{offset+=25;draw()});
  }
  draw();
  dialog.querySelector('#participant-search').oninput=e=>{query=e.target.value;offset=0;draw()};
  dialog.querySelector('#participant-company').onchange=e=>{companyFilter=e.target.value;offset=0;draw()};
  table.onchange=e=>{if(e.target.matches('[data-participant]')){const id=Number(e.target.dataset.participant);if(e.target.checked)selected.add(id);else selected.delete(id);draw()}};
  dialog.querySelector('form').onsubmit=async e=>{
   e.preventDefault();if(saving)return;saving=true;const unlock=workspaceWriteLock(dialog);error.textContent='';
   try{await api('courses/'+courseId+'/participants',{method:'PUT',body:{employee_ids:[...selected]}});dialog.close();notice('برنامه‌ریزی افراد دوره مشترک ذخیره شد.')}
   catch(err){error.textContent=err.message}
   finally{saving=false;unlock()}
  };
 }catch(err){if(dialog.isConnected){dialog.querySelector('#participant-loading').hidden=true;dialog.querySelector('#participant-error').textContent=err.message}}
}
async function openCourseAttendance(courseId){
 if(user.role!=='admin'||workspaceSaving||$('#course-attendance-dialog'))return;
 const account=user,dialog=document.createElement('dialog');dialog.id='course-attendance-dialog';dialog.setAttribute('aria-labelledby','attendance-title');
 dialog.innerHTML='<form id="course-attendance-form"><div class="dialog-head"><h2 id="attendance-title">حضور واقعی و تسهیم هزینه دوره مشترک</h2><button type="button" class="quiet" data-attendance-close>✕</button></div><p id="attendance-course" class="help"></p><p class="help">ابتدا افراد را در «برنامه‌ریزی افراد» به دوره اضافه کنید؛ سپس فقط کسانی را که واقعاً در دوره حاضر بوده‌اند علامت بزنید. سهم هر شرکت از هزینه کل دوره، به نسبت تعداد افراد حاضر همان شرکت محاسبه می‌شود.</p><p id="attendance-cancelled" class="help" hidden>این دوره لغوشده است؛ افزودن حضور جدید ممکن نیست. حضور ثبت‌شده قبلی و هزینه آن محفوظ است و می‌توان حضور اشتباه را حذف کرد.</p><p id="attendance-loading" class="help" role="status">در حال دریافت حضور و هزینه دوره…</p><div id="attendance-controls" hidden><div class="toolbar"><label>جستجوی نام، کد یا شرکت<input id="attendance-search" placeholder="جستجو در افراد برنامه‌ریزی‌شده"></label><label>فیلتر شرکت<select id="attendance-company"><option value="">همه شرکت‌ها</option></select></label></div><p id="attendance-selected" class="help" role="status"></p><div id="attendance-table"></div><div class="toolbar"><button type="submit" id="save-course-attendance" disabled>ذخیره حضور و محاسبه سهم شرکت‌ها</button><button type="button" class="quiet" data-attendance-close>بستن</button></div><p id="attendance-pending" class="help" role="status"></p><section id="course-cost-report" aria-labelledby="cost-allocation-title"><h3 id="cost-allocation-title">سهم هزینه شرکت‌ها بر اساس حضور ثبت‌شده</h3><div id="cost-allocation-summary"></div><div id="cost-allocation-table"></div><p class="help">مبلغ قطعی هر سهم به ریال نمایش داده می‌شود؛ اختلاف گردکردن تا یک ریال میان شرکت‌ها توزیع می‌شود تا جمع سهم‌ها دقیقاً برابر هزینه کل باشد.</p></section></div><p id="attendance-error" class="error" role="alert"></p><button type="button" id="retry-cost-allocation" class="quiet" hidden>دریافت مجدد گزارش هزینه</button></form>';
 document.body.append(dialog);dialog.showModal();let saving=false,selected=new Set(),saved=new Set(),people=[],query='',companyFilter='',offset=0;
 const isCurrent=()=>dialog.isConnected&&user===account&&page==='courses',error=dialog.querySelector('#attendance-error'),save=dialog.querySelector('#save-course-attendance'),report=dialog.querySelector('#course-cost-report'),retry=dialog.querySelector('#retry-cost-allocation');
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault()});
 dialog.querySelectorAll('[data-attendance-close]').forEach(button=>button.onclick=()=>{if(!saving)dialog.close()});
 const dirty=()=>selected.size!==saved.size||[...selected].some(id=>!saved.has(id));
 function drawPending(){
  save.disabled=saving||!dirty();
  dialog.querySelector('#attendance-pending').textContent=dirty()?'تغییرات حضور هنوز ذخیره نشده است؛ گزارش زیر سهم شرکت‌ها را بر اساس آخرین حضور ذخیره‌شده نشان می‌دهد.':'گزارش هزینه بر اساس آخرین حضور ذخیره‌شده است.';
  report.classList.toggle('cost-report-pending',dirty());
 }
 function drawPeople(){
  const filtered=people.filter(person=>(!companyFilter||String(person.company_id)===companyFilter)&&[person.name,person.code,person.job,person.department,person.company_name].some(value=>searchMatch(value,query)));
  dialog.querySelector('#attendance-selected').textContent=`${selected.size.toLocaleString('fa-IR')} نفر حاضر از ${people.length.toLocaleString('fa-IR')} نفر برنامه‌ریزی‌شده؛ انتخاب‌ها با تغییر جستجو و فیلتر حفظ می‌شوند.`;
  const table=dialog.querySelector('#attendance-table'),cancelled=dialog.querySelector('#attendance-cancelled').hidden===false;
  table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr><th>حاضر در دوره</th><th>نام و نام خانوادگی</th><th>کد پرسنلی</th><th>شرکت</th><th>سمت شغلی</th></tr></thead><tbody>${filtered.slice(offset,offset+25).map(person=>`<tr><td><input type="checkbox" data-attendee="${esc(person.id)}" aria-label="حضور ${esc(person.name)} از ${esc(person.company_name)}" ${selected.has(person.id)?'checked':''} ${cancelled&&!person.attended?'disabled':''}></td><td>${esc(person.name)}</td><td>${esc(person.code)}</td><td>${esc(person.company_name)}</td><td>${esc(person.job)}</td></tr>`).join('')}</tbody></table></div>${pager(offset,25,filtered.length)}`:`<div class="empty">${people.length?'فردی با این جستجو یا فیلتر پیدا نشد.':'هنوز فردی برای این دوره برنامه‌ریزی نشده است. ابتدا افراد را در «برنامه‌ریزی افراد» انتخاب کنید.'}</div>`;
  bindPager(table,()=>{offset-=25;drawPeople()},()=>{offset+=25;drawPeople()});drawPending();
 }
 function drawAllocation(data){
  const count=value=>Number(value).toLocaleString('fa-IR'),money=value=>Number(value).toLocaleString('fa-IR',{maximumFractionDigits:0}),million=value=>Number(value).toLocaleString('fa-IR',{maximumFractionDigits:6});
  dialog.querySelector('#cost-allocation-summary').innerHTML=`<div class="cost-allocation-totals"><p>هزینه کل دوره<br><strong>${esc(money(data.total_cost_rials))} ریال</strong><small>${esc(million(data.total_cost_million_rials))} میلیون ریال</small></p><p>حضور ثبت‌شده<br><strong>${esc(count(data.attendee_count))} نفر</strong><small>از ${esc(count(data.planned_count))} نفر برنامه‌ریزی‌شده</small></p><p>هزینه تخصیص‌نیافته<br><strong>${esc(money(data.unallocated_cost_rials))} ریال</strong></p></div>${data.attendee_count===0?'<p class="cost-unallocated" role="status">هنوز حضور واقعی ثبت نشده است؛ سهم شرکت‌ها صفر است و کل هزینه تخصیص‌نیافته باقی می‌ماند.</p>':''}`;
  dialog.querySelector('#cost-allocation-table').innerHTML=data.companies.length?`<div class="table-wrap"><table><thead><tr><th>شرکت</th><th>برنامه‌ریزی‌شده</th><th>حاضر</th><th>سهم هزینه (ریال)</th><th>سهم هزینه (میلیون ریال)</th></tr></thead><tbody>${data.companies.map(company=>`<tr data-allocation-company="${esc(company.company_id)}"><td>${esc(company.company_name)}</td><td>${esc(count(company.planned_count))}</td><td data-actual-count>${esc(count(company.attendee_count))}</td><td data-cost-rials>${esc(money(company.cost_rials))}</td><td>${esc(million(company.cost_million_rials))}</td></tr>`).join('')}</tbody><tfoot><tr><th>جمع سهم شرکت‌ها</th><td>${esc(count(data.planned_count))}</td><td>${esc(count(data.attendee_count))}</td><td data-allocated-total>${esc(money(data.allocated_cost_rials))}</td><td></td></tr></tfoot></table></div>`:'<p class="help">پس از برنامه‌ریزی افراد، شرکت‌های مربوط در این گزارش نمایش داده می‌شوند.</p>';
  report.hidden=false;retry.hidden=true;
 }
 async function loadAllocation(){
  try{const data=await api('courses/'+courseId+'/cost-allocation');if(isCurrent()){drawAllocation(data);error.textContent=''}}
  catch(err){if(isCurrent()&&err.name!=='AbortError'){report.hidden=true;retry.hidden=false;error.textContent='دریافت گزارش هزینه انجام نشد. '+err.message}}
 }
 retry.onclick=()=>loadAllocation();
 try{
  const [roster,allocation]=await Promise.all([api('courses/'+courseId+'/attendance'),api('courses/'+courseId+'/cost-allocation')]);
  if(!isCurrent())return;people=roster.participants;selected=new Set(people.filter(person=>person.attended).map(person=>person.id));saved=new Set(selected);
  dialog.querySelector('#attendance-course').textContent=roster.course.title+' · '+roster.course.date+' · '+display('status',roster.course.status);
  dialog.querySelector('#attendance-cancelled').hidden=roster.course.status!=='cancelled';
  const companies=[...new Map(people.map(person=>[person.company_id,person.company_name])).entries()];
  dialog.querySelector('#attendance-company').innerHTML='<option value="">همه شرکت‌ها</option>'+companies.map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
  dialog.querySelector('#attendance-loading').hidden=true;dialog.querySelector('#attendance-controls').hidden=false;drawPeople();drawAllocation(allocation);
  dialog.querySelector('#attendance-search').oninput=e=>{query=e.target.value;offset=0;drawPeople()};
  dialog.querySelector('#attendance-company').onchange=e=>{companyFilter=e.target.value;offset=0;drawPeople()};
  dialog.querySelector('#attendance-table').onchange=e=>{if(e.target.matches('[data-attendee]')){const id=Number(e.target.dataset.attendee);if(e.target.checked)selected.add(id);else selected.delete(id);drawPeople()}};
  dialog.querySelector('form').onsubmit=async e=>{
   e.preventDefault();if(saving||!isCurrent()||!dirty())return;saving=true;const unlock=workspaceWriteLock(dialog);error.textContent='';
   try{
    const result=await api('courses/'+courseId+'/attendance',{method:'PUT',body:{employee_ids:[...selected]}});
    if(!isCurrent())return;people=result.participants;selected=new Set(people.filter(person=>person.attended).map(person=>person.id));saved=new Set(selected);
    notice('حضور واقعی ذخیره شد؛ سهم هزینه شرکت‌ها به‌روزرسانی می‌شود.');await loadAllocation();
   }catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}
   finally{saving=false;unlock();if(isCurrent())drawPeople()}
  };
 }catch(err){if(isCurrent()&&err.name!=='AbortError'){dialog.querySelector('#attendance-loading').hidden=true;error.textContent=err.message}}
}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function openForm(row={}){if(!user||user.role==='viewer'||$('#entry').dataset.busy)return;entryContext={page,cid:Number(cid),account:user};userReset=null;editId=row.id||null;$('#form-title').textContent=(editId?'ویرایش ':'ثبت ')+menus.find(m=>m[0]===page)[2];$('#form-error').textContent='';$('#fields').innerHTML=defs[page].map(k=>{let choices=null;if(k==='skill_id')choices=cache.skills.map(r=>[r.id,r.title]);if(k==='employee_id')choices=cache.employees.map(r=>[r.id,r.name+' · '+r.code]);if(k==='course_id')choices=cache.courses.map(r=>[r.id,r.title]);if(k==='company_id')choices=user.companies.map(r=>[r.id,r.name]);if(k==='role')choices=[['manager','مسئول آموزش'],['viewer','مشاهده‌گر']];if(k==='calendar_scope')choices=user.role==='admin'?[['holding','تجمیعی هلدینگ'],['internal','داخلی شرکت (موارد خاص)']]:[['internal','داخلی شرکت']];if(k==='status')choices=[['planned','برنامه‌ریزی‌شده'],['approved','مصوب'],['completed','برگزارشده'],['cancelled','لغوشده']];if(k==='level')choices=[[0,'۰ · ارزیابی‌نشده'],[1,'I · آشنایی'],[2,'L · یادگیری'],[3,'U · کاربرد'],[4,'O · تسلط']];let type=k==='password'?'password':['importance','hours','cost','score','reaction','behavior','result'].includes(k)?'number':'text';const optional=['department','job','category','instructor','score','reaction','behavior','result'];let min={importance:1,hours:0.01,cost:0,score:0,reaction:1,behavior:1,result:0}[k],max={importance:3,score:100,reaction:5,behavior:5,result:100}[k];return `<label>${labels[k]}${choices?`<select name="${k}" required ${k==='calendar_scope'&&user.role!=='admin'?'disabled':''}>${choices.map(c=>`<option value="${esc(c[0])}" ${String(row[k])===String(c[0])?'selected':''}>${esc(c[1])}</option>`).join('')}</select>`:`<input name="${k}" type="${type}" value="${esc(row[k]??(k==='importance'?1:k==='cost'?0:k==='year'?currentJalaliYear():k==='date'&&page==='courses'?currentJalaliDate():''))}" ${optional.includes(k)?'':'required'} ${k==='password'?'minlength="12"':''} ${type==='number'?'step="any"':''} ${min!==undefined?`min="${min}"`:''} ${max!==undefined?`max="${max}"`:''}>`}</label>`}).join('');$('#dialog').showModal()}
$('#entry').onsubmit=async e=>{
 e.preventDefault();const form=e.target;if(form.dataset.busy)return;
 const owner=entryContext;if(!owner||owner.account!==user||owner.page!==page||owner.cid!==Number(cid)){$('#form-error').textContent='فضای کاری تغییر کرده است؛ فرم را دوباره باز کنید.';return}
 const recordId=editId,passwordUserId=userReset,d=Object.fromEntries(new FormData(form));
 if(owner.page==='courses'&&user.role!=='admin')d.calendar_scope='internal';
 for(const k of Object.keys(d)){if(d[k]===''){if(['score','reaction','behavior','result'].includes(k))d[k]=null}else if(['company_id','employee_id','skill_id','course_id','level','importance','hours','cost','score','reaction','behavior','result'].includes(k))d[k]=Number(d[k])}
 setFormBusy(form,true);const isCurrent=()=>owner.account===user&&owner.page===page&&owner.cid===Number(cid);
 try{
  const path=passwordUserId?'users/'+passwordUserId:owner.page==='users'?'users':'data/'+owner.page+(recordId?'/'+recordId:'')+'?company_id='+owner.cid;
  await api(path,{method:passwordUserId||recordId?'PUT':'POST',body:passwordUserId?{password:d.password}:d});
  if(!isCurrent())return;userReset=null;$('#dialog').close();
  if(passwordUserId){notice('رمز عبور تغییر کرد');return}
  const rendered=await renderCurrent();if(isCurrent())notice(rendered?'اطلاعات با موفقیت ذخیره شد':'اطلاعات ذخیره شد؛ دریافت دوباره این بخش ناموفق بود. از تلاش مجدد استفاده کنید.');
 }catch(err){if(isCurrent()&&err.name!=='AbortError')$('#form-error').textContent=err.message}
 finally{setFormBusy(form,false)}
};

async function renderLegacyImport(el){el.innerHTML=`<div class="panel import-box"><h2>انتقال اطلاعات از فایل مبنا</h2><p class="help">مهارت‌ها و سطوح لازم مشاغل به شرکت انتخاب‌شده وارد می‌شوند. ورود تکراری رکوردهای موجود را تغییر نمی‌دهد. پرسنل و ارزیابی‌ها تنها با تأیید شما منتقل می‌شوند؛ سوابق و برنامه‌های اکسل در این نسخه منتقل نمی‌شوند.</p><form id="import-form"><label>فایل اکسل<input name="file" type="file" accept=".xlsx" required></label><label class="check"><input type="checkbox" name="personnel" value="yes"> تأیید می‌کنم پرسنل این فایل متعلق به شرکت انتخاب‌شده است.</label><button ${user.role==='viewer'?'disabled':''}>ورود و اعتبارسنجی اطلاعات</button></form></div>`;$('#import-form').onsubmit=async e=>{e.preventDefault();const form=e.target;if(form.dataset.busy||workspaceSaving||user.role==='viewer')return;const ownerCid=cid,account=user,data=new FormData(form);setFormBusy(form,true);const unlock=workspaceWriteLock(form);notice('در حال بررسی و ورود اطلاعات…');try{const r=await api('import?company_id='+ownerCid,{method:'POST',body:data});if(user===account&&cid===ownerCid)notice(`ورود انجام شد: ${r.skills} مهارت، ${r.requirements} الزام شغلی، ${r.employees} پرسنل و ${r.assessments} ارزیابی جدید.`)}catch(err){if(user===account&&cid===ownerCid&&err.name!=='AbortError')notice(err.message)}finally{unlock();setFormBusy(form,false)}};}
