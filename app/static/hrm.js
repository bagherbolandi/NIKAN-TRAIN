/* Management workspaces share employee identities with the existing training app. */
(() => {
 'use strict';
 const modules = [
  ['personnel','♙','پرونده و ساختار پرسنلی'],['contracts','▤','قراردادها و کارگزینی'],
  ['documents','▱','مدارک پرونده'],['attendance','◷','حضور، مرخصی و مأموریت'],
  ['performance','▥','ارزیابی عملکرد'],['discipline','≡','تشویق و سوابق انضباطی'],
  ['loans','◇','وام‌ها و تسهیلات'],['welfare','▦','خدمات و سوابق رفاهی'],
  ['accommodation','⌂','اقامتگاه‌ها و رزروها'],['training','◎','سوابق آموزش کارکنان'],
  ['hse','✚','ایمنی و تجهیزات حفاظت فردی'],['health_environment','❋','بهداشت و محیط زیست'],
 ];
 const moduleLabels=Object.fromEntries(modules.map(([key,,title])=>[key,title]));
 Object.assign(moduleLabels,{'settings':'تنظیمات و دسترسی','reports':'گزارش‌ها','backup.full_export':'خروجی کامل پایگاه و کلید بازیابی','health':'جزئیات پزشکی محرمانه'});
 const roleLabels={system_admin:'ادمین اصلی هلدینگ',holding_manager:'مدیر هلدینگی',holding_hr:'منابع انسانی هلدینگ',company_hr:'منابع انسانی شرکت',unit_manager:'مدیر واحد',training_officer:'مسئول آموزش',hse_officer:'مسئول تخصصی ایمنی / بهداشت و محیط زیست',executive:'مدیریت ارشد',unassigned:'بدون نقش منابع انسانی'};
 const hrmPages=[['hrm_dashboard','◈','نمای کلی منابع انسانی'],...modules.map(([key,icon,title])=>['hrm_'+key,icon,title]),['hrm_reports','↧','گزارش‌های منابع انسانی'],['hrm_access','♧','کاربران و مجوزهای HRM']];
 for(const entry of hrmPages){if(!menus.some(item=>item[0]===entry[0]))menus.push(entry);companyIndependentPages.add(entry[0]);}
 const context=()=>user?.hrm||{};
 const can=(module,operation='read')=>{
  const flags=context().permissions?.[module]||{};
  if(operation===true)return Boolean(flags.create??flags.write)||Boolean(flags.edit??flags.write);
  if(operation===false)operation='read';
  return Boolean(flags[operation]??(['create','edit'].includes(operation)?flags.write:false));
 };
 const moduleAvailable=module=>['read','create','edit','delete','export'].some(operation=>can(module,operation))||(module==='health_environment'&&moduleAvailable('health'));
 const schemaCan=(schema,operation)=>schema['can_'+operation]===undefined?can(schema.module,operation):Boolean(schema['can_'+operation]);
 const dashboardSections=[['holding','تجمیعی هلدینگ'],['personnel','امور اداری و پرسنلی'],['training','آموزش و توسعه'],['hse','ایمنی'],['health_environment','بهداشت و محیط زیست']];
 let dashboardSection='';
 let filterOwner=null,filterCompany=null,activeFilters={};
 function resetFilters(){filterOwner=user;filterCompany=Number(cid);activeFilters={};}
 function ensureFilters(){if(filterOwner!==user||filterCompany!==Number(cid))resetFilters();}
 function scopedHrm(path,companyId=cid,includeFilters=true){
  const [base,query='']=path.split('?'),parameters=new URLSearchParams(query);
  ensureFilters();
  if(companyId&&!(includeFilters&&activeFilters.all_companies&&(base==='hrm/dashboard'||base.startsWith('hrm/reports/')||base==='hrm/workforce_report')))parameters.set('company_id',companyId);
  if(includeFilters)for(const key of ['site_id','department','employee_id','from_date','to_date'])if(activeFilters[key])parameters.set(key,activeFilters[key]);
  return base+(parameters.size?'?'+parameters.toString():'');
 }
 async function scopeFilters(target,viewOwner,module,{aggregate=false,operation='read'}={}){
  ensureFilters();let sites=context().sites||[],people=[];
  const lookups=await Promise.allSettled([api(scopedHrm('hrm/sites?module='+encodeURIComponent(module)+'&operation='+encodeURIComponent(operation),cid,false)),module==='reports'?Promise.resolve([]):api(scopedHrm('hrm/people?module='+encodeURIComponent(module)+'&operation='+encodeURIComponent(operation),cid,false))]);
  if(!current(viewOwner))return;
  if(lookups[0].status==='fulfilled')sites=asRows(lookups[0].value);
  if(lookups[1].status==='fulfilled')people=asRows(lookups[1].value);
  sites=sites.filter(site=>!site.company_id||Number(site.company_id)===Number(cid));
  const departments=[...new Set(people.map(person=>person.department).filter(Boolean))].sort();
  const form=document.createElement('form');form.className='hrm-filters panel';form.id='hrm-filters';
  form.innerHTML='<div class="hrm-filter-grid">'+(aggregate?'<label>سطح گزارش<select name="all_companies"><option value="">شرکت انتخاب‌شده</option><option value="1" '+(activeFilters.all_companies?'selected':'')+'>تمام شرکت‌های مجاز</option></select></label>':'')+'<label>سایت<select name="site_id"><option value="">تمام سایت‌های مجاز</option>'+sites.map(site=>'<option value="'+site.id+'" '+(String(activeFilters.site_id||'')===String(site.id)?'selected':'')+'>'+esc(site.name||site.title)+'</option>').join('')+'</select></label><label>واحد سازمانی<input name="department" list="hrm-departments" value="'+esc(activeFilters.department||'')+'" placeholder="همه واحدهای مجاز"><datalist id="hrm-departments">'+departments.map(value=>'<option value="'+esc(value)+'">').join('')+'</datalist></label><label>فرد<select name="employee_id" data-searchable="true"><option value="">همه افراد مجاز</option>'+people.map(person=>'<option value="'+(person.id??person.employee_id)+'" '+(String(activeFilters.employee_id||'')===String(person.id??person.employee_id)?'selected':'')+'>'+esc((person.name||person.employee_name)+' · '+person.code)+'</option>').join('')+'</select></label><label>از تاریخ شمسی<input name="from_date" dir="ltr" inputmode="numeric" placeholder="1405/01/01" value="'+esc(activeFilters.from_date||'')+'"></label><label>تا تاریخ شمسی<input name="to_date" dir="ltr" inputmode="numeric" placeholder="1405/12/29" value="'+esc(activeFilters.to_date||'')+'"></label></div><div class="toolbar"><button type="submit">اعمال فیلتر</button><button type="button" class="quiet" id="hrm-clear-filters">پاک کردن فیلترها</button><small class="help">شرکت از بالای صفحه انتخاب می‌شود؛ همه نتایج در محدوده مجوز حساب شما هستند.</small></div>';
  target.prepend(form);
  form.onsubmit=event=>{event.preventDefault();activeFilters=Object.fromEntries(new FormData(form));renderCurrent()};
  form.querySelector('#hrm-clear-filters').onclick=()=>{resetFilters();renderCurrent()};
 }

 const managesAccess=()=>Boolean(context().can_manage_access);
 const available=()=>managesAccess()||modules.some(([key])=>moduleAvailable(key))||can('reports');
 const current=owner=>Boolean(user&&user===owner.account&&Number(cid)===owner.company&&page===owner.page&&navigationVersion===owner.version);
 const owner=()=>({account:user,company:Number(cid),page,version:navigationVersion});
 const fa=value=>Number(value||0).toLocaleString('fa-IR');
 const archiveViews=new Set();
 const asRows=value=>Array.isArray(value)?value:(value?.items||value?.rows||value?.data||[]);
 let catalogOwner=null,catalogCompany=null,catalogCache=null;
 let editorDirty=false,editorBusy=false,editorOwner=null;
 function dateInput(value){
  if(!value)return '';
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return String(value);
  const parsed=new Date(value+'T12:00:00Z');
  if(Number.isNaN(parsed.getTime()))return String(value);
  const parts=new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Asia/Tehran'}).formatToParts(parsed);
  return ['year','month','day'].map(type=>parts.find(part=>part.type===type)?.value).join('/');
 }
 function fieldLabel(schema,key){return schema?.fields?.find(field=>field.key===key)?.label||labels[key]||({employee_name:'نام فرد',employee_code:'کد پرسنلی',company_name:'شرکت',unit_name:'واحد',site_id:'سایت محل خدمت',site_name:'سایت',unit_id:'واحد تعریف‌شده',location:'محل فعالیت',due_date:'موعد پیگیری',exam_date:'تاریخ معاینه',fitness:'تناسب شغلی',contract_count:'تعداد قرارداد',employee_count:'تعداد کارکنان',version:'بازنگری',paid_rials:'جمع بازپرداخت (ریال)',outstanding_rials:'مانده (ریال)',repayment_status:'وضعیت بازپرداخت',renewal_count:'تعداد تمدید',days_remaining:'روز تا پایان',score_percent:'امتیاز عملکرد (درصد)',period_title:'دوره ارزیابی',total_records:'تعداد ثبت',work_minutes:'حضور (دقیقه)',leave_minutes:'مرخصی (دقیقه)',mission_minutes:'مأموریت (دقیقه)',absence_minutes:'غیبت (دقیقه)',late_minutes:'تأخیر (دقیقه)',overtime_minutes:'اضافه‌کاری (دقیقه)',review_required:'نیازمند بازنگری الزامات',ppe_review_required:'نیازمند بازنگری PPE',ppe_name:'تجهیز حفاظت فردی',missing_quantity:'کسری تعداد',next_due_date:'موعد تعویض',overdue:'نیازمند تعویض',reason:'علت پیگیری',target_site_id:'سایت فعالیت موقت',activity_location:'محل فعالیت واقعی',exam_type:'نوع معاینه',next_review_date:'موعد معاینه بعدی',employees:'تعداد کارکنان',contracts:'قراردادها',ppe_missing:'کسری تجهیزات حفاظت فردی',medical_due:'معاینات سررسیدشده',training_hours:'آموزش (ساعت)',actual_person_hours:'نفرساعت آموزش واقعی',competency_title:'عنوان شایستگی',current_level:'سطح فعلی',required_level:'سطح لازم',assessment_date:'تاریخ ارزیابی',record_date:'تاریخ سابقه آموزشی'}[key])||key;}
 function choicesOf(field){
  const value=field.choices||field.options||[];
  return Array.isArray(value)?value.map(item=>Array.isArray(item)?item:[item.value??item.key??item,item.label??item.title??String(item)]):Object.entries(value);
 }
 function cellValue(schema,key,value,row){
  const field=schema?.fields?.find(item=>item.key===key),choices=field?choicesOf(field):[];
  if(value===null||value===undefined||value==='')return '—';
  if(field?.type==='date'||/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return dateInput(value);
  const choice=choices.find(([code])=>String(code)===String(value));if(choice)return choice[1];
  if(key==='employee_id')return row.employee_name||row.name||value;
  if(key==='site_id')return row.site_name||context().sites?.find(site=>Number(site.id)===Number(value))?.name||value;
  if(key==='unit_id')return row.unit_name||value;
  if(key==='company_id')return row.company_name||user.companies?.find(company=>Number(company.id)===Number(value))?.name||value;
  if(['review_required','ppe_review_required'].includes(key))return value?'نیازمند بازنگری':'—';
  if(key==='status')return ({missing:'ثبت نشده',due:'سررسیدشده',recorded:'ثبت‌شده',pending:'در انتظار بررسی',open:'باز',closed:'بسته'})[value]||String(value);
  if(typeof value==='boolean')return value?'بله':'خیر';
  if(typeof value==='object')return JSON.stringify(value);
  if(typeof value==='number')return value.toLocaleString('fa-IR');
  return String(value);
 }
 async function catalog(force=false){
  if(!force&&catalogCache&&catalogOwner===user&&catalogCompany===Number(cid))return catalogCache;
  const account=user,company=Number(cid),value=await api(scopedHrm('hrm/catalog'));
  if(user!==account||Number(cid)!==company)throw staleRequest();
  const source=value.resources||value;
  catalogCache=Array.isArray(source)?Object.fromEntries(source.map(schema=>[schema.key||schema.resource,schema])):source;
  catalogOwner=account;catalogCompany=company;return catalogCache;
 }
 function pageHeading(){
  notice('');$('#page-title').textContent=hrmPages.find(item=>item[0]===page)?.[2]||'منابع انسانی';
  $('#company-label').textContent=user.companies?.find(company=>Number(company.id)===Number(cid))?.name||'محدوده مجاز مدیریت';
  document.querySelectorAll('#nav button').forEach(button=>button.classList.toggle('active',button.dataset.page===page));
 }
 const priorNavigation=drawNavigation;
 drawNavigation=function(){
  if(page==='dashboard'&&(available()||context().configured&&!context().training_access))page='hrm_dashboard';
  priorNavigation();
  if(context().configured&&!context().training_access){
   const safe=managesAccess()?new Set(['users','companies','branding','backup','audit']):new Set();
   $('#nav [data-page]')?.closest('nav');
   document.querySelectorAll('#nav [data-page]').forEach(button=>{if(!safe.has(button.dataset.page))button.remove()});
   document.querySelectorAll('#nav .nav-group').forEach(group=>{if(!group.querySelector('button'))group.remove()});
  }
  if(!managesAccess())document.querySelectorAll('#nav [data-page]').forEach(button=>{if(['users','companies','branding','backup','audit'].includes(button.dataset.page))button.remove()});
  document.querySelectorAll('#nav .nav-group').forEach(group=>{if(!group.querySelector('button'))group.remove()});
  if(available()){
   const items=[hrmPages[0],...modules.filter(([key])=>moduleAvailable(key)).map(([key])=>hrmPages.find(item=>item[0]==='hrm_'+key))];
   if(can('reports')||Object.values(context().permissions||{}).some(flags=>flags.export))items.push(hrmPages.find(item=>item[0]==='hrm_reports'));
   const section=document.createElement('section');section.className='nav-group hrm-nav';
   section.innerHTML='<h2 class="nav-group-title">مدیریت منابع انسانی</h2>'+items.map(([key,icon,title])=>`<button data-page="${key}"><span aria-hidden="true">${icon}</span>${esc(title)}</button>`).join('');
   $('#nav').append(section);
   if(managesAccess()){
    const admin=document.createElement('section');admin.className='nav-group hrm-nav';
    admin.innerHTML='<h2 class="nav-group-title">تنظیمات منابع انسانی</h2><button data-page="hrm_access"><span aria-hidden="true">♧</span>کاربران و مجوزهای HRM</button>';
    $('#nav').append(admin);
   }
   $('#user-label').textContent=user.username+' · '+(roleLabels[context().role]||roleLabels[user.role]||'کاربر سامانه');
  }
 };
 const priorReset=resetSession;
 resetSession=function(message=''){
  catalogCache=null;catalogOwner=null;filterOwner=null;activeFilters={};dashboardSection='';archiveViews.clear();editorDirty=false;editorBusy=false;editorOwner=null;
  $('#hrm-editor')?.close();priorReset(message);
 };
 const priorRender=render;
 render=async function(){
  if(!page.startsWith('hrm_'))return priorRender();
  pageHeading();const target=$('#content'),viewOwner=owner();target.onclick=null;
  target.innerHTML='<div class="empty" role="status">در حال دریافت اطلاعات منابع انسانی…</div>';
  if(page==='hrm_access')return renderAccess(target,viewOwner);
  if(page==='hrm_dashboard')return renderDashboard(target,viewOwner);
  if(page==='hrm_reports')return renderReports(target,viewOwner);
  const module=page.slice(4);
  if(!moduleAvailable(module)){target.innerHTML='<div class="empty"><h2>دسترسی این بخش برای حساب شما تعریف نشده است</h2></div>';return;}
  const resources=await catalog();if(!current(viewOwner))return;
  const matches=Object.entries(resources).filter(([key,schema])=>(schema.module===module||(module==='documents'&&key==='documents')||(module==='health_environment'&&schema.module==='health')));
  if(!matches.length&&module==='training')return renderTraining(target,viewOwner);
  if(!matches.length){target.innerHTML='<div class="empty"><h2>فرم مجازی برای این بخش در دسترس نیست</h2><p>مجوز بخش و محدوده شرکت خود را از مدیر سامانه پیگیری کنید.</p></div>';return;}
  target.innerHTML='<div class="hrm-intro panel"><h2>'+esc(moduleLabels[module]||'منابع انسانی')+'</h2><p class="help">سوابق در محدوده شرکت، سایت و واحد مجاز شما ثبت و نگهداری می‌شوند. تاریخ‌ها شمسی و مبالغ مالی بر حسب ریال هستند.</p></div><div class="hrm-tabs" role="tablist"></div><div id="hrm-resource-workspace"></div>';
  await scopeFilters(target,viewOwner,module==='health_environment'&&!can(module)?'health':module);if(!current(viewOwner))return;
  const tabs=target.querySelector('.hrm-tabs');
  const stored=sessionStorage.getItem('nikan-hrm-resource-'+module);
  let selected=matches.some(([key])=>key===stored)?stored:matches[0][0],generation=0;
  const open=async key=>{
   if(workspaceSaving||!current(viewOwner))return;
   selected=key;sessionStorage.setItem('nikan-hrm-resource-'+module,key);const token=++generation;
   tabs.innerHTML=matches.map(([resource,schema])=>`<button type="button" role="tab" aria-selected="${resource===selected}" data-hrm-resource="${esc(resource)}" class="${resource===selected?'active':'quiet'}">${esc(schema.title)}</button>`).join('');
   const workspace=$('#hrm-resource-workspace');workspace.innerHTML='<div class="empty" role="status">در حال دریافت سوابق…</div>';
   try{await renderResource(workspace,key,resources[key],viewOwner,()=>current(viewOwner)&&token===generation)}
   catch(err){if(current(viewOwner)&&token===generation&&err.name!=='AbortError'){workspace.innerHTML='<div class="empty"><h2>دریافت سوابق انجام نشد</h2><p>'+esc(err.message)+'</p><button data-resource-retry>تلاش مجدد</button></div>';workspace.querySelector('[data-resource-retry]').onclick=()=>open(key)}}
  };
  tabs.onclick=event=>{const button=event.target.closest('[data-hrm-resource]');if(button)open(button.dataset.hrmResource)};
  await open(selected);
 };
 async function renderDashboard(target,viewOwner){
  if(!available()){target.innerHTML='<div class="empty"><h2>نقش منابع انسانی برای این حساب تعریف نشده است</h2></div>';return;}
  const sections=dashboardSections.filter(([key])=>key==='holding'?managesAccess():key==='personnel'?['personnel','contracts','attendance','performance','discipline','loans','welfare','accommodation'].some(module=>can(module)):can(key)||(key==='health_environment'&&can('health')));
  if(!sections.length){target.innerHTML='<div class="panel"><h2>فضای ثبت سوابق مجاز</h2><p class="help">مجوز مشاهده شاخص‌های این حساب فعال نیست؛ از بخش‌های مجاز برای ثبت سوابق استفاده کنید.</p></div><div class="category-cards hrm-module-cards">'+modules.filter(([key])=>moduleAvailable(key)).map(([key,icon,title])=>'<button class="panel hrm-module-card" data-hrm-open="hrm_'+key+'"><span>'+icon+'</span><strong>'+esc(title)+'</strong></button>').join('')+'</div>';target.querySelectorAll('[data-hrm-open]').forEach(button=>button.onclick=()=>navigateTo(button.dataset.hrmOpen));return;}
  if(!sections.some(([key])=>key===dashboardSection))dashboardSection=sections[0]?.[0]||'personnel';
  const section=dashboardSection;ensureFilters();if(section==='holding'&&activeFilters.all_companies===undefined)activeFilters.all_companies='1';
  const data=await api(scopedHrm('hrm/dashboard?section='+section));if(!current(viewOwner))return;
  const cards=Array.isArray(data.metrics)?data.metrics:Object.entries(data.metrics||{}).map(([key,value])=>({key,label:moduleLabels[key]||key,value}));
  const allowed=modules.filter(([key])=>moduleAvailable(key));
  target.innerHTML='<div class="hero hrm-hero"><div><p class="eyebrow">NIKAN HRM</p><h2>مدیریت یکپارچه سرمایه انسانی نیکان</h2><p>ثبت و پایش سوابق کارکنان توسط مسئولان مجاز هلدینگ، شرکت‌ها و سایت‌ها.</p></div><div class="orb">N</div></div><div class="hrm-tabs" role="tablist" aria-label="داشبوردهای تخصصی">'+sections.map(([key,title])=>'<button type="button" role="tab" data-hrm-dashboard="'+key+'" aria-selected="'+(key===section)+'" class="'+(key===section?'active':'quiet')+'">'+esc(title)+'</button>').join('')+'</div><div class="metrics hrm-metrics">'+(cards.length?cards.map(card=>'<div class="card" data-hrm-metric="'+esc(card.key)+'"><small>'+esc(card.label||card.key)+'</small><strong>'+(card.missing||card.value===null||card.value===undefined?'داده ثبت نشده':typeof card.value==='number'?fa(card.value):esc(card.value))+'</strong><p>'+esc(card.unit||'در محدوده دسترسی شما')+'</p></div>').join(''):'<p class="help">اطلاعات کافی برای محاسبه شاخص این محدوده ثبت نشده است.</p>')+'</div><div id="hrm-alerts"></div><div class="category-cards hrm-module-cards">'+allowed.map(([key,icon,title])=>'<button type="button" class="panel hrm-module-card" data-hrm-open="hrm_'+key+'"><span aria-hidden="true">'+icon+'</span><strong>'+esc(title)+'</strong><small>'+(['create','edit'].some(operation=>can(key,operation))?'ثبت و مشاهده سوابق مجاز':'مشاهده سوابق مجاز')+'</small></button>').join('')+'</div>';
  const filterModule=section==='holding'?'reports':section==='personnel'?['personnel','contracts','attendance','performance','discipline','loans','welfare','accommodation'].find(module=>can(module))||'personnel':section==='health_environment'&&!can(section)?'health':section;
  await scopeFilters(target,viewOwner,filterModule,{aggregate:true});if(!current(viewOwner))return;
  target.querySelectorAll('[data-hrm-dashboard]').forEach(button=>button.onclick=()=>{dashboardSection=button.dataset.hrmDashboard;renderCurrent()});
  target.querySelectorAll('[data-hrm-open]').forEach(button=>button.onclick=()=>navigateTo(button.dataset.hrmOpen));
  const alerts=target.querySelector('#hrm-alerts');
  for(const [key,title,columns] of [['contract_alerts','قراردادهای نزدیک به پایان',['employee_name','number','end_date','days_remaining']],['hse_alerts','پیگیری‌های ایمنی و تجهیزات حفاظت فردی',['employee_name','ppe_name','missing_quantity','next_due_date','overdue','review_required']],['medical_alerts','سررسید معاینات مجاز',['employee_name','exam_type','next_review_date','status']],['assignment_reviews','بازنگری الزامات پس از تغییر محل فعالیت',['employee_name','target_site_id','activity_location','reason','review_required']]])if(data[key]?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>'+title+'</h2>'+simpleTable(data[key],columns)+'</div>');
  if(data.companies?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>مقایسه شرکت‌های مجاز</h2>'+simpleTable(data.companies,Object.keys(data.companies[0]).filter(key=>key!=='id'&&!key.endsWith('_id')),{fields:[{key:'name',label:'شرکت'}]})+'</div>');
  if(data.sites?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>مقایسه سایت‌های مجاز</h2>'+simpleTable(data.sites,Object.keys(data.sites[0]).filter(key=>key!=='id'&&!key.endsWith('_id')),{fields:[{key:'name',label:'سایت'},{key:'company_name',label:'شرکت'}]})+'</div>');
  if(data.data_quality?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>اطلاعات ناقص و کیفیت داده‌ها</h2><ul>'+data.data_quality.map(item=>'<li>'+esc(typeof item==='string'?item:item.message||item.label||item.title||JSON.stringify(item))+'</li>').join('')+'</ul></div>');
 }
 function simpleTable(rows,keys,schema={}){
  if(!rows?.length)return '<p class="help">رکوردی در این محدوده ثبت نشده است.</p>';
  return `<div class="table-wrap"><table><thead><tr>${keys.map(key=>`<th>${esc(fieldLabel(schema,key))}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${keys.map(key=>`<td>${esc(cellValue(schema,key,row[key],row))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 }
 async function renderResource(workspace,key,schema,viewOwner,isCurrent){
  const readable=schemaCan(schema,'read'),creatable=schemaCan(schema,'create'),exportable=schemaCan(schema,'export');
  const includeArchived=archiveViews.has(key),historyEnabled=schema.deletable===true;
  const response=readable?await api(scopedHrm('hrm/'+key+(includeArchived?'?include_deleted=1':''))):[];if(!isCurrent())return;
  const rows=asRows(response);
  const columns=(schema.columns||schema.fields?.map(field=>field.key)||[]).map(field=>typeof field==='string'?field:field.key);
  const editable=schema.editable!==false&&schemaCan(schema,'edit'),deletable=schema.deletable===true&&schemaCan(schema,'delete');
  workspace.innerHTML=`${schema.help?'<p class="help hrm-resource-help">'+esc(schema.help)+'</p>':''}<div class="toolbar hrm-toolbar"><label>جستجو در ${esc(schema.title)}<input id="hrm-search" autocomplete="off" placeholder="نام، کد یا متن مورد نظر"></label><div>${creatable&&schema.creatable!==false?'<button type="button" id="hrm-add">＋ ثبت '+esc(schema.singular||schema.title)+'</button>':''}${exportable?'<button type="button" class="quiet" id="hrm-export">خروجی Excel</button>':''}${key==='attendance'&&creatable?'<button type="button" class="quiet" id="hrm-attendance-import">ورود از Excel</button>':''}</div></div>${readable&&historyEnabled?'<label class="check hrm-archive-filter"><input type="checkbox" id="hrm-include-archived" '+(includeArchived?'checked':'')+'> نمایش سوابق بایگانی‌شده و ابطال‌شده</label>':''}<div id="hrm-row-status" class="help" role="status"></div><div id="hrm-table"></div><div id="hrm-related-report"></div>`;
  workspace.querySelector('#hrm-include-archived')?.addEventListener('change',event=>{if(event.target.checked)archiveViews.add(key);else archiveViews.delete(key);renderCurrent()});
  const table=workspace.querySelector('#hrm-table'),status=workspace.querySelector('#hrm-row-status');let query='',offset=0;
  const draw=()=>{
   const filtered=rows.filter(row=>Object.entries(row).some(([field,value])=>field!=='content'&&searchMatch(typeof value==='object'?JSON.stringify(value):cellValue(schema,field,value,row),query)));
   if(offset>=filtered.length)offset=Math.max(0,Math.floor((filtered.length-1)/25)*25);
   status.textContent=readable?fa(filtered.length)+' رکورد در محدوده مجاز':'این حساب مجوز مشاهده سوابق این بخش را ندارد.';
   const actions=editable||deletable||(key==='contracts'&&creatable)||key==='documents'||key==='people';
   table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr>${columns.map(field=>`<th>${esc(fieldLabel(schema,field))}</th>`).join('')}${historyEnabled?'<th>وضعیت سابقه</th>':''}${actions?'<th>عملیات</th>':''}</tr></thead><tbody>${filtered.slice(offset,offset+25).map(row=>`<tr data-hrm-row="${esc(row.id??row.employee_id)}">${columns.map(field=>`<td>${esc(cellValue(schema,field,row[field],row))}</td>`).join('')}${historyEnabled?'<td>'+esc(row.is_deleted?'بایگانی‌شده':row.status==='cancelled'||row.status==='voided'?'ابطال‌شده':'فعال')+'</td>':''}${actions?`<td class="hrm-row-actions">${schema.restorable&&editable&&row.is_deleted?`<button type="button" class="quiet" data-hrm-restore="${esc(row.id)}">بازیابی سابقه</button>`:''}${editable&&!row.is_deleted&&(key!=='documents'||can(row.permission_group,'edit'))?`<button type="button" class="quiet" data-hrm-edit="${esc(row.id??row.employee_id)}">ویرایش</button>`:''}${deletable&&!row.is_deleted?`<button type="button" class="quiet" data-hrm-delete="${esc(row.id)}">حذف / ابطال</button>`:''}${key==='contracts'&&creatable&&!row.is_deleted?`<button type="button" class="quiet" data-hrm-renew="${esc(row.id)}">ثبت تمدید</button>`:''}${key==='personnel_actions'&&editable&&!row.applied?`<button type="button" class="quiet" data-hrm-apply="${esc(row.id)}">اجرای حکم</button>`:''}${key==='documents'?`<button type="button" class="quiet" data-hrm-file="${esc(row.id)}">دریافت فایل</button>`:''}${key==='people'&&can('training')?`<button type="button" class="quiet" data-hrm-training="${esc(row.id??row.employee_id)}">پرونده آموزش</button>`:''}</td>`:''}</tr>`).join('')}</tbody></table></div>${pager(offset,25,filtered.length)}`:'<div class="empty"><h2>رکوردی پیدا نشد</h2><p>'+esc(query?'عبارت دیگری جستجو کنید.':'برای این محدوده هنوز اطلاعاتی ثبت نشده است.')+'</p></div>';
   bindPager(table,()=>{offset-=25;draw()},()=>{offset+=25;draw()});
  };
  workspace.querySelector('#hrm-search').oninput=event=>{query=event.target.value;offset=0;draw()};draw();
  workspace.querySelector('#hrm-add')?.addEventListener('click',()=>openEditor(key,schema,null,viewOwner));
  if(exportable)workspace.querySelector('#hrm-export').onclick=async()=>{try{download(await api(scopedHrm('hrm/reports/'+key+'.xlsx'+(includeArchived?'?include_deleted=1':''))),'Nikan-HRM-'+key+'.xlsx')}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
  workspace.querySelector('#hrm-attendance-import')?.addEventListener('click',()=>attendanceImport(viewOwner));
  table.onclick=async event=>{
   if(workspaceSaving||!isCurrent())return;
   const edit=event.target.closest('[data-hrm-edit]'),renew=event.target.closest('[data-hrm-renew]'),file=event.target.closest('[data-hrm-file]'),apply=event.target.closest('[data-hrm-apply]'),remove=event.target.closest('[data-hrm-delete]'),restore=event.target.closest('[data-hrm-restore]'),training=event.target.closest('[data-hrm-training]');
   const id=edit?.dataset.hrmEdit||renew?.dataset.hrmRenew||file?.dataset.hrmFile||apply?.dataset.hrmApply||remove?.dataset.hrmDelete||restore?.dataset.hrmRestore||training?.dataset.hrmTraining;
   const row=rows.find(item=>String(item.id??item.employee_id)===String(id));if(!row)return;
   try{
    if(edit)return openEditor(key,schema,row,viewOwner);
    if(renew)return openEditor(key,schema,row,viewOwner,{renew:true});
    if(restore){const unlock=workspaceWriteLock(workspace);try{await api(scopedHrm('hrm/'+key+'/'+id+'/restore',cid,false),{method:'POST',body:{version:row.version}});if(isCurrent()){unlock();await renderCurrent();notice('سابقه بازیابی شد؛ تاریخچه بایگانی در سامانه حفظ می‌شود.');}}finally{unlock();}return;}
    if(remove){if(!confirm('این سابقه حذف / ابطال شود؟ تاریخچه آن در سامانه حفظ می‌شود.'))return;const unlock=workspaceWriteLock(workspace);try{await api(scopedHrm('hrm/'+key+'/'+id,cid,false),{method:'DELETE',body:{version:row.version}});if(isCurrent()){unlock();await renderCurrent();notice('حذف / ابطال ثبت شد؛ تاریخچه در سامانه حفظ می‌شود.');}}finally{unlock();}return;}
    if(apply){if(!confirm('حکم در پرونده جاری فرد اجرا و در تاریخچه ثبت شود؟'))return;const unlock=workspaceWriteLock(workspace);try{await api(scopedHrm('hrm/personnel_actions/'+id+'/apply',cid,false),{method:'POST',body:{version:row.version}});if(isCurrent()){unlock();await renderCurrent();notice('حکم در پرونده جاری اجرا و در تاریخچه ثبت شد.');}}finally{unlock();}return;}
    if(file){const blob=await api(scopedHrm('hrm/documents/'+id+'/file'));download(blob,row.filename||row.original_name||'Nikan-HRM-document');return;}
    if(training)return showTraining(Number(id),viewOwner,row.employee_name||row.name);
   }catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}
  };
  if(readable&&['attendance','performance_scores'].includes(key)){
   try{const report=await api(scopedHrm('hrm/workforce_report'));if(isCurrent()){
    const values=key==='attendance'?report.attendance:report.performance;
    const fields=key==='attendance'?['name','department','work_minutes','leave_minutes','mission_minutes','absence_minutes','late_minutes','overtime_minutes']:['name','period_title','score_percent','criteria_count'];
    if(values?.length)workspace.querySelector('#hrm-related-report').innerHTML='<details class="panel"><summary>خلاصه '+esc(key==='attendance'?'حضور و غیاب':'روند عملکرد')+'</summary>'+simpleTable(values,fields)+'</details>';
   }}catch(err){if(isCurrent()&&err.name!=='AbortError')workspace.querySelector('#hrm-related-report').innerHTML='<p class="help">گزارش خلاصه اکنون در دسترس نیست؛ سوابق بالا قابل استفاده‌اند.</p>'}
  }
 }
 function ensureDialog(){
  let dialog=$('#hrm-editor');if(dialog)return dialog;
  dialog=document.createElement('dialog');dialog.id='hrm-editor';dialog.setAttribute('aria-labelledby','hrm-editor-title');document.body.append(dialog);
  dialog.addEventListener('cancel',event=>{if(!closeAllowed())event.preventDefault()});
  dialog.addEventListener('close',()=>{editorDirty=false;editorBusy=false;editorOwner=null});
  dialog.addEventListener('input',()=>editorDirty=true);dialog.addEventListener('change',()=>editorDirty=true);
  return dialog;
 }
 function closeAllowed(){return !editorBusy&&(!editorDirty||confirm('تغییرات این فرم ذخیره نشده است. فرم بسته شود؟'));}
 function closeDialog(){if(closeAllowed())ensureDialog().close();}
 function dialogShell(title,content,submit='ذخیره اطلاعات'){
  const dialog=ensureDialog();if(dialog.open||workspaceSaving)return null;
  editorDirty=false;editorBusy=false;editorOwner=owner();
  dialog.innerHTML='<form id="hrm-form"><div class="dialog-head"><h2 id="hrm-editor-title">'+esc(title)+'</h2><button type="button" class="quiet" data-hrm-close aria-label="بستن فرم">✕</button></div>'+content+'<p id="hrm-form-error" class="error" role="alert"></p><div class="toolbar"><button type="submit" id="hrm-save">'+esc(submit)+'</button><button type="button" class="quiet" data-hrm-close>انصراف</button></div></form>';
  dialog.querySelectorAll('[data-hrm-close]').forEach(button=>button.onclick=closeDialog);dialog.showModal();return dialog;
 }
 function fieldHtml(field,value,options=null,readonly=false){
  const key=field.key,label=field.label||key,type=field.type||'text',required=Boolean(field.required);
  let input='',attrs=`name="${esc(key)}" id="hrm-field-${esc(key)}" ${required?'required':''} ${field.max_length?`maxlength="${Number(field.max_length)}"`:''}`;
  if(type==='textarea'||field.multiline)input=`<textarea ${attrs} rows="3" ${readonly?'readonly':''}>${esc(value??field.default??'')}</textarea>`;
  else if(options||type==='select'||type==='boolean'||type==='checkbox'){
   const items=options||((type==='boolean'||type==='checkbox')?[[1,'بله'],[0,'خیر']]:choicesOf(field));
   input=`<select ${attrs} data-searchable="true" ${readonly?'data-readonly="true"':''}>${!required?'<option value="">انتخاب نشده</option>':'<option value="">انتخاب کنید</option>'}${items.map(([code,title])=>`<option value="${esc(code)}" ${String(value??field.default??'')===String(code)?'selected':''}>${esc(title)}</option>`).join('')}</select>`;
  }else if(type==='date')input=`<input ${attrs} type="text" inputmode="numeric" dir="ltr" placeholder="${currentJalaliDate()}" value="${esc(dateInput(value??field.default??''))}" ${readonly?'readonly':''}>`;
  else if(type==='number'||type==='integer'||type==='money')input=`<input ${attrs} type="number" dir="ltr" step="${esc(field.step??(type==='integer'||type==='money'?1:'any'))}" ${field.minimum!==undefined||field.min!==undefined?`min="${esc(field.minimum??field.min)}"`:''} ${field.maximum!==undefined||field.max!==undefined?`max="${esc(field.maximum??field.max)}"`:''} value="${esc(value??field.default??'')}" ${readonly?'readonly':''}>`;
  else input=`<input ${attrs} type="${['email','tel','password'].includes(type)?type:'text'}" value="${type==='password'?'':esc(value??field.default??'')}" ${readonly?'readonly':''} autocomplete="${type==='password'?'new-password':'off'}">`;
  const description=field.help||field.description||'';
  return `<label class="${type==='textarea'||field.multiline?'hrm-wide':''}" data-hrm-field="${esc(key)}">${esc(label)}${required?' <span aria-hidden="true">*</span>':''}${input}${description?'<small class="help">'+esc(description)+'</small>':''}</label>`;
 }
 async function referenceOptions(field,schema,viewOwner,operation='read',referenceCompany=undefined){
  if(referenceCompany===undefined)referenceCompany=field.key==='target_site_id'?null:cid;
  const operationQuery='&operation='+encodeURIComponent(operation);
  if(field.type==='site'){field={...field,type:'resource',resource:'sites'};}
  if(field.type==='company')return (context().companies||user.companies||[]).map(company=>[company.id,company.name]);
  if(field.type==='employee'||field.key==='employee_id'||field.key==='manager_id'){
   const rows=asRows(await api(scopedHrm('hrm/people?module='+encodeURIComponent(schema.module)+operationQuery,referenceCompany,false)));
   if(!current(viewOwner))throw staleRequest();const options=rows.map(row=>[row.id??row.employee_id,(row.name||row.employee_name)+' · '+row.code+(row.department?' · '+row.department:'')]);options.records=rows;return options;
  }
  if(field.type==='resource'||field.resource){
   let path='hrm/'+(field.resource||field.reference);
   const filters={...(field.filters||{})};if(['sites','units'].includes(field.resource||field.reference))filters.module=schema.module;if(['sites','units'].includes(field.resource||field.reference)||(field.resource||field.reference||'').endsWith('_options')){filters.operation=operation;filters.module=schema.module;}if((field.resource||field.reference)==='documents'&&!filters.permission_group&&schema.module!=='documents')filters.permission_group=schema.module;
   const parameters=new URLSearchParams(filters);if(parameters.size)path+='?'+parameters.toString();
   let result;try{result=await api(scopedHrm(path,referenceCompany,false))}catch(err){
    if(err.status!==403)throw err;
    if((field.resource||field.reference)==='documents'){if(!field.required)return [];throw err;}
    try{result=await api(scopedHrm('hrm/options/'+encodeURIComponent(field.resource||field.reference)+'?module='+encodeURIComponent(schema.module)+'&operation='+encodeURIComponent(operation),referenceCompany,false))}catch(fallback){if(fallback.status===403&&!field.required)return [];throw fallback;}
   }const rows=asRows(result);if(!current(viewOwner))throw staleRequest();
   const options=rows.map(row=>[row[field.value_key]??row.value??row.id??row.employee_id,[row[field.label_key]||row.label||row.title||row.name||row.number||row.employee_name||row.code||('#'+row.id),row.employee_name&&row.title?row.employee_name:'',(field.resource||field.reference)==='sites'?user.companies?.find(company=>Number(company.id)===Number(row.company_id))?.name:'',row.start_date?dateInput(row.start_date):'',row.amount_rials?fa(row.amount_rials)+' ریال':''].filter(Boolean).join(' · ')]);options.records=rows;return options;
  }
  return null;
 }
 async function openEditor(key,schema,row,viewOwner,options={}){
  if(workspaceSaving||!current(viewOwner)||ensureDialog().open||!schemaCan(schema,options.renew||!row?'create':'edit'))return;
  const isRenew=Boolean(options.renew),operation=isRenew||!row?'create':'edit',notesOnly=Boolean(row&&!isRenew&&((key==='contracts'&&row.status==='renewed')||(key==='personnel_actions'&&row.applied))),fields=(schema.fields||[]).filter(field=>!field.readonly&&!['id','version','created','updated'].includes(field.key)&&(!notesOnly||field.key==='notes')&&(!(key==='documents'&&row)||['title','kind'].includes(field.key))).map(field=>field.choices_by_operation?.[operation]?{...field,choices:field.choices_by_operation[operation]}:field);
  const referenceSchema=key==='documents'?{...schema,module:row?.permission_group||choicesOf(fields.find(field=>field.key==='permission_group')||{})[0]?.[0]||schema.module}:schema,optionsByField={};
  try{
   await Promise.all(fields.map(async field=>{optionsByField[field.key]=await referenceOptions(field,referenceSchema,viewOwner,options.renew||!row?'create':'edit',key==='personnel_actions'&&row?.target_company_id&&['sites','units'].includes(field.resource||field.reference)?row.target_company_id:undefined)}));
   if(!current(viewOwner))return;
   const values={...(row||{})};if(key==='documents'&&!row)values.permission_group=choicesOf(fields.find(field=>field.key==='permission_group')||{})[0]?.[0]||schema.permission_groups?.[0]||'documents';if(!row){for(const field of fields){if(field.key==='company_id')values.company_id=cid;if(field.key==='site_id'&&activeFilters.site_id)values.site_id=activeFilters.site_id;if(field.key==='employee_id'&&activeFilters.employee_id)values.employee_id=activeFilters.employee_id;}}
   if(isRenew){for(const key of ['number','start_date','end_date','issued_date','signed_date','notes'])if(key in values)values[key]='';delete values.parent_contract_id;}
   const markup=fields.map(field=>fieldHtml(field,values[field.key],optionsByField[field.key],Boolean(row&&!isRenew&&(field.immutable||(['employee_id','loan_id'].includes(field.key)&&['loans','loan_payments','reservations','welfare_uses'].includes(key)))))).join('');
   const upload=key==='documents'&&!row;
   const dialog=dialogShell((isRenew?'ثبت تمدید ':row?'ویرایش ':'ثبت ')+(schema.singular||schema.title),'<p class="help">'+esc(isRenew?'قرارداد قبلی حفظ می‌شود و تمدید به‌عنوان نسخه جدید ثبت خواهد شد.':'اطلاعات در محدوده مجاز شما ثبت می‌شود. تاریخ‌ها را شمسی وارد کنید؛ مبلغ‌ها بر حسب ریال هستند.')+'</p><div class="hrm-fields">'+markup+(upload?'<label class="hrm-wide">فایل مدرک (PDF، تصویر یا Word)<input type="file" name="file" required accept=".pdf,.png,.jpg,.jpeg,.docx"><small class="help">مدرک فقط از مسیر مجاز و به صورت فایل قابل دریافت است.</small></label>':'')+'</div>');
   if(!dialog)return;const form=dialog.querySelector('form'),formOwner=editorOwner,error=dialog.querySelector('#hrm-form-error');
   function toggleFields(){for(const field of fields){const rule=field.show_when;if(!rule)continue;const visible=Object.entries(rule).every(([name,allowed])=>[].concat(allowed).map(String).includes(String(form.elements.namedItem(name)?.value)));const label=form.querySelector('[data-hrm-field="'+CSS.escape(field.key)+'"]');label.hidden=!visible;label.querySelectorAll('input,select,textarea').forEach(input=>input.disabled=!visible);}}
   form.addEventListener('change',toggleFields);toggleFields();
   function restrictUnits(){
    const site=(form.elements.namedItem('target_site_id')||form.elements.namedItem('site_id'))?.value;
    for(const field of fields.filter(item=>(item.resource||item.reference)==='units')){
     const select=form.elements.namedItem(field.key),records=optionsByField[field.key]?.records;if(!select||!records)continue;
     const value=select.value,allowed=records.filter(record=>!site||!record.site_id||String(record.site_id)===String(site));
     select.innerHTML='<option value="">انتخاب نشده</option>'+allowed.map(record=>'<option value="'+record.id+'" '+(String(record.id)===value?'selected':'')+'>'+esc(record.name||record.title)+'</option>').join('');
     select.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));
    }
   }
   (form.elements.namedItem('target_site_id')||form.elements.namedItem('site_id'))?.addEventListener('change',restrictUnits);restrictUnits();
   let referenceGeneration=0,referenceBusy=false,lastReferenceCompany=Number(values.target_company_id||values.company_id||cid);
   (form.elements.namedItem('target_company_id')||form.elements.namedItem('company_id'))?.addEventListener('change',async event=>{
    const company=Number(event.target.value);if(!company||company===lastReferenceCompany)return;lastReferenceCompany=company;const generation=++referenceGeneration;referenceBusy=true;form.querySelector('#hrm-save').disabled=true;
    try{
     for(const field of fields.filter(item=>item.type==='site'||['sites','units'].includes(item.resource||item.reference))){
      const choices=await referenceOptions(field,referenceSchema,viewOwner,options.renew||!row?'create':'edit',company);
      if(!current(formOwner)||generation!==referenceGeneration||!dialog.open)return;
      optionsByField[field.key]=choices;const select=form.elements.namedItem(field.key);
      if(select){select.innerHTML='<option value="">انتخاب نشده</option>'+(choices||[]).map(([id,title])=>'<option value="'+esc(id)+'">'+esc(title)+'</option>').join('');select.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));}
     }
     restrictUnits();
    }catch(err){if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message;}
    finally{if(generation===referenceGeneration){referenceBusy=false;if(dialog.open&&!editorBusy)form.querySelector('#hrm-save').disabled=false;}}
   });
   form.onsubmit=async event=>{
    event.preventDefault();if(editorBusy||referenceBusy||!current(formOwner))return;
    const payload={};for(const field of fields){const input=form.elements.namedItem(field.key);if(!input||input.disabled)continue;let value=input.value;if(value===''&&!field.required&&!row)continue;if(['number','integer','money','boolean','checkbox'].includes(field.type))value=value===''?null:Number(value);else if(['employee','company'].includes(field.type)||/_id$/.test(field.key))value=value===''?null:Number(value);payload[field.key]=value;}
    if(row?.version!==undefined)payload.version=row.version;
    if(isRenew){delete payload.employee_id;delete payload.company_id;delete payload.parent_contract_id;}
    editorBusy=true;error.textContent='';const unlock=workspaceWriteLock(dialog);
    try{
     const path=scopedHrm('hrm/'+key+(isRenew?'/'+row.id+'/renew':row?'/'+(row.id??row.employee_id):''),cid,false);
     let body=payload;
     if(upload){body=new FormData();for(const [name,value] of Object.entries(payload))if(value!==null&&value!==undefined)body.append(name,String(value));body.append('file',form.elements.namedItem('file').files[0]);}
     await api(path,{method:isRenew||!row?'POST':'PUT',body});
     if(!current(formOwner))return;editorDirty=false;dialog.close();unlock();await renderCurrent();notice(isRenew?'تمدید قرارداد به‌عنوان نسخه جدید ثبت شد.':'اطلاعات ثبت شد؛ سابقه تغییر در سامانه نگهداری می‌شود.');
    }catch(err){if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message}
    finally{unlock();editorBusy=false;}
   };
  }catch(err){if(current(viewOwner)&&err.name!=='AbortError')notice(err.message)}
 }
 async function attendanceImport(viewOwner){
  if(workspaceSaving||!current(viewOwner))return;
  const dialog=dialogShell('ورود حضور و غیاب از Excel','<p class="help">قالب استاندارد همین شرکت را دریافت و تکمیل کنید. بررسی اولیه چیزی ثبت نمی‌کند؛ ثبت نهایی همه ردیف‌ها را دوباره بررسی می‌کند.</p><button type="button" id="hrm-attendance-template" class="quiet">دریافت قالب استاندارد حضور و غیاب</button><div class="hrm-fields"><label class="hrm-wide">فایل تکمیل‌شده<input type="file" id="hrm-import-file" accept=".xlsx" required></label><label>روش ورود<select id="hrm-import-policy"><option value="insert">فقط افزودن</option><option value="upsert">افزودن و به‌روزرسانی</option></select></label></div><div id="hrm-import-preview" aria-live="polite"></div><div class="toolbar"><button type="button" id="hrm-import-check">بررسی فایل</button></div>','ثبت اطلاعات فایل');
  if(!dialog)return;const form=dialog.querySelector('form'),save=dialog.querySelector('#hrm-save'),error=dialog.querySelector('#hrm-form-error'),result=dialog.querySelector('#hrm-import-preview'),formOwner=editorOwner;let approved=false;
  dialog.querySelector('#hrm-attendance-template').hidden=!can('attendance','export');dialog.querySelector('#hrm-import-policy option[value=upsert]').disabled=!can('attendance','edit');
  save.disabled=true;
  const reset=()=>{approved=false;save.disabled=true;result.replaceChildren()};
  dialog.querySelector('#hrm-import-file').onchange=reset;dialog.querySelector('#hrm-import-policy').onchange=reset;
  dialog.querySelector('#hrm-attendance-template').onclick=async()=>{try{download(await api(scopedHrm('hrm/attendance/template.xlsx')),'Nikan-HRM-Attendance.xlsx')}catch(err){if(current(formOwner))error.textContent=err.message}};
  async function run(mode){
   if(editorBusy||!current(formOwner))return;
   const file=dialog.querySelector('#hrm-import-file').files[0];if(!file){error.textContent='فایل Excel را انتخاب کنید.';return;}
   if(mode==='commit'&&!approved)return;
   const body=new FormData();body.append('file',file);body.append('mode',mode);body.append('policy',dialog.querySelector('#hrm-import-policy').value);
   editorBusy=true;error.textContent='';const unlock=workspaceWriteLock(dialog);
   try{
    const data=await api(scopedHrm('hrm/attendance/import'),{method:'POST',body});if(!current(formOwner))return;
    approved=mode==='preview'&&data.can_commit===true;
    result.innerHTML='<div class="panel"><h3>'+esc(mode==='commit'?'نتیجه ثبت':'نتیجه بررسی')+'</h3><p>'+fa(data.total)+' ردیف؛ '+fa(data.added)+' جدید، '+fa(data.updated)+' قابل تغییر، '+fa(data.unchanged)+' بدون تغییر</p>'+(data.errors?.length?'<ul>'+data.errors.map(item=>'<li>ردیف '+esc(item.row)+': '+esc(item.message)+'</li>').join('')+'</ul>':'<p>فایل در بررسی فعلی بدون خطاست.</p>')+'</div>';
    if(mode==='commit'&&data.applied){editorDirty=false;approved=false;dialog.addEventListener('close',()=>{if(current(formOwner))renderCurrent()},{once:true});notice('اطلاعات حضور و غیاب از فایل ثبت شد.');}
   }catch(err){approved=false;if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message}
   finally{unlock();editorBusy=false;if(current(formOwner))save.disabled=!approved;}
  }
  dialog.querySelector('#hrm-import-check').onclick=()=>run('preview');form.onsubmit=event=>{event.preventDefault();run('commit')};
 }
 async function renderTraining(target,viewOwner){
  if(!can('training')){target.innerHTML='<div class="empty">دسترسی سوابق آموزش تعریف نشده است.</div>';return;}
  const people=asRows(await api(scopedHrm('hrm/people?module=training')));if(!current(viewOwner))return;
  target.innerHTML='<div class="panel"><h2>آموزش و توسعه در پرونده کارکنان</h2><p class="help">سوابق از ماژول آموزش موجود و همان شناسه فرد خوانده می‌شوند؛ اطلاعات پرسنل دوباره ثبت نمی‌شود.</p></div><div class="toolbar"><label>انتخاب فرد<select id="hrm-training-person" data-searchable="true"><option value="">فرد مورد نظر را انتخاب کنید</option>'+people.map(person=>`<option value="${person.id??person.employee_id}">${esc(person.name||person.employee_name)} · ${esc(person.code)}</option>`).join('')+'</select></label></div><div id="hrm-training-detail"></div>';
  await scopeFilters(target,viewOwner,'training');if(!current(viewOwner))return;
  target.querySelector('#hrm-training-person').onchange=event=>{if(event.target.value)showTraining(Number(event.target.value),viewOwner,people.find(person=>Number(person.id??person.employee_id)===Number(event.target.value))?.name,false);else target.querySelector('#hrm-training-detail').replaceChildren()};
 }
 async function showTraining(employeeId,viewOwner,name='',modal=true){
  const data=await api(scopedHrm('hrm/people/'+employeeId+'/training'));if(!current(viewOwner))return;
  const content='<p class="help">'+esc(name||data.employee?.name||'پرونده آموزش')+' · اطلاعات مشترک با ماژول آموزش</p><h3>دوره‌ها و سوابق</h3>'+simpleTable(data.records||[],['title','date','level','score','reaction','behavior','result'])+'<h3>ارزیابی شایستگی‌ها</h3>'+simpleTable(data.assessments||[],['skill_title','level'])+'<h3>گواهینامه‌ها و مدارک آموزشی</h3>'+simpleTable(data.certificates||[],['title','issued','expires']);
  if(!modal){$('#hrm-training-detail').innerHTML=content;return;}
  const dialog=dialogShell('سوابق آموزش '+name,content,'بستن');if(dialog){dialog.querySelector('form').onsubmit=event=>{event.preventDefault();dialog.close()};dialog.querySelectorAll('[data-hrm-close]').forEach(button=>button.textContent='بستن');}
 }
 async function renderReports(target,viewOwner){
  const resources=await catalog();if(!current(viewOwner))return;
  const list=Object.entries(resources).filter(([,schema])=>schemaCan(schema,'export'));
  target.innerHTML='<div class="panel"><h2>خروجی اطلاعات مجاز منابع انسانی</h2><p class="help">خروجی فقط رکوردهای مجاز همین حساب و محدوده شرکت انتخاب‌شده را شامل می‌شود. گزارش اسناد محرمانه نیز به مجوز مستقل همان بخش نیاز دارد.</p><form id="hrm-report-form" class="hrm-fields"><label>بخش گزارش<select name="resource" required data-searchable="true">'+list.map(([key,schema])=>`<option value="${esc(key)}">${esc(schema.title)}</option>`).join('')+'</select></label><button type="submit">دریافت Excel گزارش</button></form><p id="hrm-report-error" class="error" role="alert"></p></div>';
  await scopeFilters(target,viewOwner,list[0]?.[1]?.module||'reports',{aggregate:true,operation:'export'});if(!current(viewOwner))return;
  target.querySelector('#hrm-report-form').onsubmit=async event=>{event.preventDefault();const form=event.target;if(form.dataset.busy)return;setFormBusy(form,true);try{const key=form.elements.namedItem('resource').value;download(await api(scopedHrm('hrm/reports/'+encodeURIComponent(key)+'.xlsx')),'Nikan-HRM-'+key+'.xlsx')}catch(err){if(current(viewOwner)&&err.name!=='AbortError')target.querySelector('#hrm-report-error').textContent=err.message}finally{setFormBusy(form,false)}};
 }
 async function renderAccess(target,viewOwner){
  if(!managesAccess()){target.innerHTML='<div class="empty"><h2>مدیریت مجوزها برای این حساب مجاز نیست</h2></div>';return;}
  const [rows,options]=await Promise.all([api('hrm/access/users'),api('hrm/access/options')]);if(!current(viewOwner))return;
  target.innerHTML='<div class="panel hrm-access-note"><h2>نقش، محدوده و مجوزهای منابع انسانی</h2><p>هر حساب برای مسئول مدیریت یک بخش تعریف می‌شود؛ پرسنل، موضوع پرونده‌ها هستند و حساب کارتابل شخصی ندارند. قرارداد، مدارک شخصی، انضباط، وام و سلامت به مجوز صریح نیاز دارند. تغییر مجوزها با نام کاربر ثبت می‌شود.</p></div><div class="toolbar"><label>جستجوی کاربر<input id="hrm-user-search" placeholder="نام کاربری یا نقش"></label><button type="button" id="hrm-user-add">＋ تعریف کاربر مسئول</button></div><div id="hrm-users"></div>';
  const list=asRows(rows),table=target.querySelector('#hrm-users');
  function draw(query=''){const matches=list.filter(row=>searchMatch(row.username+' '+(roleLabels[row.role]||row.role),query));table.innerHTML=matches.length?'<div class="table-wrap"><table><thead><tr><th>نام کاربری</th><th>نقش منابع انسانی</th><th>محدوده شرکت / واحد</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>'+matches.map(row=>`<tr data-hrm-user="${row.id}"><td>${esc(row.username)}</td><td>${esc(roleLabels[row.role]||row.role)}</td><td>${esc((row.scopes||[]).map(scope=>(options.companies.find(company=>Number(company.id)===Number(scope.company_id))?.name||scope.company_id)+(scope.site_id?' / '+(options.sites?.find(site=>Number(site.id)===Number(scope.site_id))?.name||scope.site_id):'')+(scope.department?' / '+scope.department:'')).join('، ')||(row.all_companies||row.role==='system_admin'?'تمام هلدینگ':'تعریف نشده'))}</td><td>${row.active?'فعال':'غیرفعال'}</td><td><button type="button" class="quiet" data-hrm-access-edit="${row.id}">ویرایش نقش و مجوز</button></td></tr>`).join('')+'</tbody></table></div>':'<div class="empty">کاربری با این جستجو پیدا نشد.</div>';}
  draw();target.querySelector('#hrm-user-search').oninput=event=>draw(event.target.value);
  target.querySelector('#hrm-user-add').onclick=()=>accessEditor(null,options,viewOwner);
  table.onclick=event=>{const button=event.target.closest('[data-hrm-access-edit]');if(button)accessEditor(list.find(row=>Number(row.id)===Number(button.dataset.hrmAccessEdit)),options,viewOwner)};
 }
 function accessEditor(row,options,viewOwner){
  if(workspaceSaving||!current(viewOwner))return;
  const roles=options.roles||[],permissionModules=(options.modules||[]).filter(module=>!['requests','settings','backup.full_export'].includes(module.key)),scopes=row?.scopes||[];
  const operations=[['read','مشاهده'],['create','ثبت'],['edit','ویرایش'],['delete','حذف / ابطال'],['export','خروجی گزارش']];
  const dialog=dialogShell(row?'ویرایش دسترسی '+row.username:'تعریف کاربر مسئول منابع انسانی','<div class="hrm-fields">'+fieldHtml({key:'username',label:'نام کاربری',required:true,max_length:100},row?.username||'',null,Boolean(row))+fieldHtml({key:'password',label:row?'رمز جدید (خالی یعنی بدون تغییر)':'رمز عبور (حداقل ۱۲ نویسه)',type:'password',required:!row},'')+fieldHtml({key:'role',label:'نقش منابع انسانی',required:true,type:'select',choices:roles.map(role=>[role.key,role.label])},row?.role==='unassigned'?'':row?.role||'company_hr')+fieldHtml({key:'active',label:'وضعیت حساب',type:'select',choices:[[1,'فعال'],[0,'غیرفعال']],required:true},row?Number(row.active):1)+'</div><fieldset class="hrm-scope-fieldset"><legend>محدوده مجاز شرکت، سایت و واحد</legend><label class="check"><input type="checkbox" name="all_companies" '+(row?.all_companies?'checked':'')+'> همه شرکت‌ها و سایت‌های هلدینگ</label><p class="help">برای هر محدوده یک سطر اضافه کنید. سایت و واحد خالی یعنی تمام سایت‌ها و واحدهای همان شرکت. برای دسترسی به چند سایت، چند سطر تعریف کنید.</p><div id="hrm-scopes"></div><button type="button" class="quiet" id="hrm-scope-add">＋ افزودن محدوده</button></fieldset><fieldset><legend>مجوز مستقل عملیات هر بخش</legend><p class="help">هر نوع عملیات مستقل است. جزئیات پزشکی مجوز محرمانه جداگانه دارد. ادمین اصلی هلدینگ به همه شرکت‌ها و بخش‌ها دسترسی کامل دارد.</p><div class="table-wrap"><table class="hrm-permission-table"><thead><tr><th>بخش</th>'+operations.map(([,title])=>'<th>'+title+'</th>').join('')+'</tr></thead><tbody>'+permissionModules.map(module=>'<tr data-permission="'+esc(module.key)+'"><td>'+esc(module.label||moduleLabels[module.key]||module.key)+(module.confidential?'<span class="hrm-confidential">محرمانه</span>':'')+'</td>'+operations.map(([operation,title])=>'<td><input type="checkbox" aria-label="'+title+' '+esc(module.label||module.key)+'" data-permission-operation="'+operation+'" data-permission-module="'+esc(module.key)+'" '+(row?.permissions?.[module.key]?.[operation]??(['create','edit'].includes(operation)&&row?.permissions?.[module.key]?.write)?'checked':'')+'></td>').join('')+'</tr>').join('')+'</tbody></table></div></fieldset><label class="check"><input type="checkbox" name="training_access" '+(row?.training_access?'checked':'')+'> دسترسی به محیط عملیاتی آموزش در محدوده مجاز</label><p class="help">محیط آموزش فقط با مجوزهای آموزش و محدوده شرکت، سایت و واحد تعیین‌شده فعال می‌شود.</p>');
  if(!dialog)return;const form=dialog.querySelector('form'),formOwner=editorOwner,error=dialog.querySelector('#hrm-form-error'),scopeList=form.querySelector('#hrm-scopes');
  function addScope(scope={}){
   const line=document.createElement('div');line.className='hrm-scope-row';
   line.innerHTML='<label>شرکت<select data-scope-company required><option value="">انتخاب شرکت</option>'+options.companies.map(company=>'<option value="'+company.id+'" '+(Number(scope.company_id)===Number(company.id)?'selected':'')+'>'+esc(company.name)+'</option>').join('')+'</select></label><label>سایت<select data-scope-site><option value="">همه سایت‌های شرکت</option></select></label><label>واحد سازمانی<input data-scope-department value="'+esc(scope.department||'')+'" placeholder="تمام واحدهای این محدوده"></label><button type="button" class="quiet" data-remove-scope aria-label="حذف این محدوده">حذف محدوده</button>';
   scopeList.append(line);
   const company=line.querySelector('[data-scope-company]'),site=line.querySelector('[data-scope-site]');
   function drawSites(selected=''){site.innerHTML='<option value="">همه سایت‌های شرکت</option>'+(options.sites||[]).filter(item=>Number(item.company_id)===Number(company.value)).map(item=>'<option value="'+item.id+'" '+(Number(selected)===Number(item.id)?'selected':'')+'>'+esc(item.name||item.title)+'</option>').join('');}
   drawSites(scope.site_id);company.onchange=()=>drawSites();line.querySelector('[data-remove-scope]').onclick=()=>{line.remove();editorDirty=true};
  }
  scopes.forEach(addScope);if(!scopes.length&&!row?.all_companies)addScope({company_id:cid});
  form.querySelector('#hrm-scope-add').onclick=()=>{addScope();editorDirty=true};
  const allCompanies=form.elements.namedItem('all_companies');
  function toggleScopes(){scopeList.querySelectorAll('input,select,button').forEach(input=>input.disabled=allCompanies.checked);form.querySelector('#hrm-scope-add').disabled=allCompanies.checked;}
  allCompanies.onchange=toggleScopes;toggleScopes();
  form.onsubmit=async event=>{
   event.preventDefault();if(editorBusy||!current(formOwner))return;
   const scoped=allCompanies.checked?[]:[...scopeList.querySelectorAll('.hrm-scope-row')].map(line=>({company_id:Number(line.querySelector('[data-scope-company]').value),site_id:line.querySelector('[data-scope-site]').value?Number(line.querySelector('[data-scope-site]').value):null,department:line.querySelector('[data-scope-department]').value.trim()}));
   const permissions={};for(const module of permissionModules){permissions[module.key]={};for(const [operation] of operations)permissions[module.key][operation]=[...form.querySelectorAll('[data-permission-operation]')].find(input=>input.dataset.permissionModule===module.key&&input.dataset.permissionOperation===operation).checked;}
   const payload={username:form.elements.namedItem('username').value,role:form.elements.namedItem('role').value,employee_id:null,scopes:scoped,permissions,all_companies:allCompanies.checked,training_access:form.elements.namedItem('training_access').checked,active:Number(form.elements.namedItem('active').value)};
   const password=form.elements.namedItem('password').value;if(password)payload.password=password;
   if(row?.version!==undefined)payload.version=row.version;if(row)delete payload.username;
   if(!row){delete payload.active;if(scoped.length)payload.company_id=scoped[0].company_id;}
   editorBusy=true;error.textContent='';const unlock=workspaceWriteLock(dialog);
   try{await api('hrm/access/users'+(row?'/'+row.id:''),{method:row?'PUT':'POST',body:payload});if(!current(formOwner))return;editorDirty=false;dialog.close();unlock();catalogCache=null;
    const refreshed=await api('me');if(user===formOwner.account){user.hrm=refreshed.hrm;if(refreshed.companies)setCompanyChoices(refreshed.companies,cid);drawNavigation();await renderCurrent();notice('نقش، محدوده و مجوزهای کاربر ذخیره شد.');}
   }catch(err){if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message}
   finally{unlock();editorBusy=false;}
  };
 }
 window.addEventListener('beforeunload',event=>{if(editorDirty||editorBusy){event.preventDefault();event.returnValue=''}});
})();
