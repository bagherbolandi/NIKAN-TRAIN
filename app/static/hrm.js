/* Management workspaces share employee identities with the existing training app. */
(() => {
 'use strict';
 const modules = [
  ['personnel','♙','پرونده کارکنان'],['contracts','▤','قراردادها و کارگزینی'],
  ['documents','▱','مدارک پرونده'],['attendance','◷','حضور، مرخصی و مأموریت'],
  ['performance','▥','ارزیابی عملکرد'],['productivity','◈','آکورد بهره‌وری'],['discipline','≡','تشویق و سوابق انضباطی'],
  ['loans','◇','وام‌ها و تسهیلات'],['welfare','▦','خدمات و سوابق رفاهی'],
  ['accommodation','⌂','اقامتگاه‌ها و رزروها'],['training','◎','سوابق آموزش کارکنان'],
  ['hse','✚','ایمنی و تجهیزات حفاظت فردی'],['health_environment','❋','بهداشت و محیط زیست'],
 ];
 const moduleLabels=Object.fromEntries(modules.map(([key,,title])=>[key,title]));
 Object.assign(moduleLabels,{'settings':'تنظیمات و دسترسی','reports':'گزارش‌ها','backup.full_export':'خروجی کامل پایگاه و کلید بازیابی','health':'جزئیات پزشکی محرمانه'});
 const roleLabels={system_admin:'ادمین اصلی هلدینگ',holding_manager:'مدیر هلدینگی',holding_hr:'منابع انسانی هلدینگ',company_hr:'منابع انسانی شرکت',unit_manager:'مدیر واحد',training_officer:'مسئول آموزش',hse_officer:'مسئول تخصصی ایمنی / بهداشت و محیط زیست',executive:'مدیریت ارشد',unassigned:'بدون نقش منابع انسانی'};
 const registeredPages=new Map();
 const hrmPages=[['hrm_dashboard','◈','نمای کلی منابع انسانی'],['hrm_person','♙','پرونده یکپارچه فرد'],['hrm_structure','▣','شرکت‌ها، سایت‌ها و نفرات'],...modules.map(([key,icon,title])=>['hrm_'+key,icon,title]),['hrm_reports','↧','گزارش‌های منابع انسانی'],['hrm_access','♧','کاربران و مجوزهای HRM']];
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
 let dashboardSection='',structureCompany=null,structureSite='',personWorkspace=null;
 let filterOwner=null,filterCompany=null,activeFilters={};
 const personModules=()=>Object.keys(context().permissions||{}).filter(module=>!['reports','settings','backup.full_export'].includes(module));
 const hasPeopleDirectory=()=>managesAccess()||personModules().some(module=>['read','create','edit'].some(operation=>can(module,operation)));
 const inPersonWorkspace=()=>page==='hrm_person'&&personWorkspace?.account===user&&personWorkspace.id;
 function resetFilters(){filterOwner=user;filterCompany=Number(cid);activeFilters={};}
 function ensureFilters(){if(filterOwner!==user||filterCompany!==Number(cid))resetFilters();}
 function scopedHrm(path,companyId=cid,includeFilters=true){
  const [base,query='']=path.split('?'),parameters=new URLSearchParams(query);
  ensureFilters();
  if(companyId&&!(includeFilters&&activeFilters.all_companies&&(base==='hrm/dashboard'||base.startsWith('hrm/reports/')||base==='hrm/workforce_report')))parameters.set('company_id',companyId);
  if(includeFilters)for(const key of ['site_id','department','employee_id','from_date','to_date'])if(activeFilters[key])parameters.set(key,activeFilters[key]);
  if(includeFilters&&inPersonWorkspace()){if(base==='hrm/'+personWorkspace.resource||base==='hrm/reports/'+personWorkspace.resource+'.xlsx')for(const [key,value] of Object.entries(personWorkspace.resourceFilters||{}))parameters.set(key,String(value));parameters.set('employee_id',String(personWorkspace.id));}
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
 let editorDirty=false,editorBusy=false,editorOwner=null,editorGeneration=0;
 function dateInput(value){
  if(!value)return '';
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return String(value);
  const parsed=new Date(value+'T12:00:00Z');
  if(Number.isNaN(parsed.getTime()))return String(value);
  const parts=new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Asia/Tehran'}).formatToParts(parsed);
  return ['year','month','day'].map(type=>parts.find(part=>part.type===type)?.value).join('/');
 }
 function fieldLabel(schema,key){return schema?.fields?.find(field=>field.key===key)?.label||labels[key]||({employee_name:'نام فرد',employee_code:'کد پرسنلی',company_name:'شرکت',unit_name:'واحد',site_id:'سایت محل خدمت',site_name:'سایت',unit_id:'واحد تعریف‌شده',location:'محل فعالیت',due_date:'موعد پیگیری',exam_date:'تاریخ معاینه',fitness:'تناسب شغلی',contract_count:'تعداد قرارداد',employee_count:'تعداد کارکنان',version:'بازنگری',paid_rials:'جمع بازپرداخت (ریال)',outstanding_rials:'مانده (ریال)',repayment_status:'وضعیت بازپرداخت',renewal_count:'تعداد تمدید',days_remaining:'روز تا پایان',score_percent:'امتیاز عملکرد (درصد)',period_title:'دوره ارزیابی',total_records:'تعداد ثبت',work_minutes:'حضور (دقیقه)',leave_minutes:'مرخصی (دقیقه)',mission_minutes:'مأموریت (دقیقه)',absence_minutes:'غیبت (دقیقه)',late_minutes:'تأخیر (دقیقه)',overtime_minutes:'اضافه‌کاری (دقیقه)',review_required:'نیازمند بازنگری الزامات',ppe_review_required:'نیازمند بازنگری PPE',ppe_name:'تجهیز حفاظت فردی',missing_quantity:'کسری تعداد',next_due_date:'موعد تعویض',overdue:'نیازمند تعویض',reason:'علت پیگیری',target_site_id:'سایت فعالیت موقت',activity_location:'محل فعالیت واقعی',exam_type:'نوع معاینه',next_review_date:'موعد معاینه بعدی',employees:'تعداد کارکنان',contracts:'قراردادها',ppe_missing:'کسری تجهیزات حفاظت فردی',medical_due:'معاینات سررسیدشده',training_hours:'آموزش (ساعت)',actual_person_hours:'نفرساعت آموزش واقعی',competency_title:'عنوان شایستگی',current_level:'سطح فعلی',required_level:'سطح لازم',assessment_date:'تاریخ ارزیابی',record_date:'تاریخ سابقه آموزشی',target_site_name:'سایت فعالیت موقت',completion_percent:'درصد تکمیل شاخص‌ها',participation_percent:'درصد مشارکت',accord_rials:'آکورد (ریال)',base_rials:'مبلغ مبنا (ریال)',budget_rials:'بودجه آکورد (ریال)',policy_title:'ضابطه آکورد',policy_status:'وضعیت ضابطه',calculated_at:'زمان محاسبه',method:'روش محاسبه',minimum_score:'حداقل امتیاز',scheduled_minutes:'دقایق مبنای دوره',actual_minutes:'دقایق مشارکت مؤثر'}[key])||key;}
 function choicesOf(field){
  const value=field.choices||field.options||[];
  return Array.isArray(value)?value.map(item=>Array.isArray(item)?item:[item.value??item.key??item,item.label??item.title??String(item)]):Object.entries(value);
 }
 function accordLocked(key,row){return ['accord_policies','accord_entries'].includes(key)&&Boolean(row?.locked||row?.policy_status==='finalized'||(key==='accord_policies'&&row?.status==='finalized'));}
 function cellValue(schema,key,value,row){
  const field=schema?.fields?.find(item=>item.key===key),choices=field?choicesOf(field):[];
  if((value===null||value===undefined)&&key==='current_level')return 'ارزیابی ثبت نشده';
  if(value===null||value===undefined||value==='')return '—';
  if(field?.type==='date'||/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return dateInput(value);
  const choice=choices.find(([code])=>String(code)===String(value));if(choice)return choice[1];
  if(/_rials$/.test(key))return accordMoney(row?.[key+'_exact']??value);
  if(key==='employee_id')return row.employee_name||row.name||value;
  if(key==='site_id')return row.site_name||context().sites?.find(site=>Number(site.id)===Number(value))?.name||value;
  if(key==='unit_id')return row.unit_name||value;
  if(key==='company_id')return row.company_name||user.companies?.find(company=>Number(company.id)===Number(value))?.name||value;
  if(['review_required','ppe_review_required'].includes(key))return value?'نیازمند بازنگری':'—';
  if(key==='calculated_at'&&value){const stamp=new Date(String(value).replace(' ','T')+(String(value).endsWith('Z')?'':'Z'));if(!Number.isNaN(stamp.getTime()))return new Intl.DateTimeFormat('fa-IR',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Tehran'}).format(stamp);}
  if(key==='status'||key==='policy_status')return ({missing:'ثبت نشده',due:'سررسیدشده',recorded:'ثبت‌شده',pending:'در انتظار بررسی',open:'باز',closed:'بسته',draft:'پیش‌نویس',finalized:'نهایی‌شده',ready:'قابل بررسی',incomplete:'اطلاعات ناقص'})[value]||String(value);
  if(typeof value==='boolean')return value?'بله':'خیر';
  if(typeof value==='object')return JSON.stringify(value);
  if(typeof value==='number')return value.toLocaleString('fa-IR',{maximumFractionDigits:4});
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
  $('#content')?.focus({preventScroll:true});
  notice('');$('#page-title').textContent=hrmPages.find(item=>item[0]===page)?.[2]||'منابع انسانی';
  $('#company-label').textContent=user.companies?.find(company=>Number(company.id)===Number(cid))?.name||'محدوده مجاز مدیریت';
  document.querySelectorAll('#nav button').forEach(button=>button.classList.toggle('active',button.dataset.page===page));
 }
 window.nikanHrmRegisterPage=function(entry){
  if(!entry||!/^hrm_[a-z_]+$/.test(entry.key)||typeof entry.render!=='function')throw Error('صفحه منابع انسانی معتبر نیست');
  registeredPages.set(entry.key,entry);const menu=[entry.key,entry.icon||'▤',entry.title||'منابع انسانی'];
  const existing=hrmPages.find(item=>item[0]===entry.key);if(existing)existing.splice(0,3,...menu);else hrmPages.push(menu);
  if(!menus.some(item=>item[0]===entry.key))menus.push(menu);companyIndependentPages.add(entry.key);if(user)drawNavigation();
 };
 const aliases={employees:'hrm_personnel',users:'hrm_access',dashboard:'hrm_dashboard',assessments:'hrm_training',records:'hrm_training',import:'hrm_excel'};
 function canonicalPage(value){
  if(value==='assessments')sessionStorage.setItem('nikan-hrm-resource-training','skill_matrix');
  if(value==='records')sessionStorage.setItem('nikan-hrm-resource-training','training_records');
  return aliases[value]||value;
 }
 const navigationBeforeIntegration=navigateTo;
 navigateTo=function(nextPage,nextCompany=cid){return navigationBeforeIntegration(canonicalPage(nextPage),nextCompany)};
 const priorNavigation=drawNavigation;
 drawNavigation=function(){
  if(page==='dashboard'&&(available()||context().configured&&!context().training_access))page='hrm_dashboard';
  priorNavigation();
  document.querySelectorAll('#nav [data-page]').forEach(button=>{if(['employees','users','dashboard','assessments','records','import'].includes(button.dataset.page))button.remove()});
  document.querySelectorAll('#nav .nav-group-title').forEach(title=>{if(title.textContent==='پرسنل و شایستگی')title.textContent='آموزش و شایستگی — ابزارهای تخصصی';if(title.textContent==='برنامه و اجرا')title.textContent='برنامه‌ریزی و اجرای آموزش';});
  if(context().configured&&!context().training_access){
   const safe=managesAccess()?new Set(['users','companies','branding','backup','audit']):new Set();
   $('#nav [data-page]')?.closest('nav');
   document.querySelectorAll('#nav [data-page]').forEach(button=>{if(!safe.has(button.dataset.page))button.remove()});
   document.querySelectorAll('#nav .nav-group').forEach(group=>{if(!group.querySelector('button'))group.remove()});
  }
  if(!managesAccess())document.querySelectorAll('#nav [data-page]').forEach(button=>{if(['users','companies','branding','backup','audit'].includes(button.dataset.page))button.remove()});
  document.querySelectorAll('#nav .nav-group').forEach(group=>{if(!group.querySelector('button'))group.remove()});
  if(available()){
   const items=[hrmPages[0],...(managesAccess()||can('personnel')?[hrmPages.find(item=>item[0]==='hrm_structure')]:[]),...modules.filter(([key])=>key==='personnel'?hasPeopleDirectory():moduleAvailable(key)).map(([key])=>hrmPages.find(item=>item[0]==='hrm_'+key))];
   for(const [key,entry] of registeredPages)if(!entry.visible||entry.visible())items.push(hrmPages.find(item=>item[0]===key));
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
  catalogCache=null;catalogOwner=null;filterOwner=null;activeFilters={};dashboardSection='';structureCompany=null;structureSite='';personWorkspace=null;archiveViews.clear();editorGeneration++;editorDirty=false;editorBusy=false;editorOwner=null;
  $('#hrm-editor')?.close();priorReset(message);
 };
 const priorRender=render;
 render=async function(){
  page=canonicalPage(page);
  if(!page.startsWith('hrm_'))return priorRender();
  pageHeading();const target=$('#content'),viewOwner=owner();target.onclick=null;
  target.innerHTML='<div class="empty" role="status">در حال دریافت اطلاعات منابع انسانی…</div>';
  const extension=registeredPages.get(page);if(extension){if(extension.visible&&!extension.visible()){target.innerHTML='<div class="empty">دسترسی این بخش برای حساب شما تعریف نشده است.</div>';return;}return extension.render(target,viewOwner);}
  if(page==='hrm_person')return renderPersonWorkspace(target,viewOwner);
  if(page==='hrm_personnel')return renderPeopleWorkspace(target,viewOwner);
  if(page==='hrm_training')return renderTrainingWorkspace(target,viewOwner);
  if(page==='hrm_access')return renderAccess(target,viewOwner);
  if(page==='hrm_dashboard')return renderDashboard(target,viewOwner);
  if(page==='hrm_reports')return renderReports(target,viewOwner);
  if(page==='hrm_structure')return renderStructure(target,viewOwner);
  if(page==='hrm_productivity')return renderProductivity(target,viewOwner);
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
   focusSelectedTab(tabs);
   const workspace=$('#hrm-resource-workspace');workspace.innerHTML='<div class="empty" role="status">در حال دریافت سوابق…</div>';
   try{await renderResource(workspace,key,resources[key],viewOwner,()=>current(viewOwner)&&token===generation)}
   catch(err){if(current(viewOwner)&&token===generation&&err.name!=='AbortError'){workspace.innerHTML='<div class="empty"><h2>دریافت سوابق انجام نشد</h2><p>'+esc(err.message)+'</p><button data-resource-retry>تلاش مجدد</button></div>';workspace.querySelector('[data-resource-retry]').onclick=()=>open(key)}}
  };
  tabs.onclick=event=>{const button=event.target.closest('[data-hrm-resource]');if(button)open(button.dataset.hrmResource)};
  await open(selected);
 };
 function focusSelectedTab(container){container?.querySelector('[aria-selected=true]')?.focus({preventScroll:true});}
 function personLink(person,module='',resource='',label='پرونده یکپارچه'){
  const id=person.employee_id??person.id;if(!id)return '';
  return '<button type="button" class="quiet" data-person-workspace="'+esc(id)+'" data-person-module="'+esc(module)+'" data-person-resource="'+esc(resource)+'">'+esc(label)+'</button>';
 }
 async function openPersonWorkspace(employeeId,options={}){
  if(!user||workspaceSaving||!Number.isSafeInteger(Number(employeeId))||Number(employeeId)<1)return;
  personWorkspace={account:user,id:Number(employeeId),module:options.module||'',resource:options.resource||'',origin:page==='hrm_person'?(personWorkspace?.origin||'hrm_personnel'):page,initialFilters:Object.fromEntries(Object.entries(options.filters||{}).filter(([key])=>['competency_code','policy_id','period_id'].includes(key))),requestedResource:options.resource||'',overview:null,resourceFilters:{}};
  return navigateTo('hrm_person');
 }
 window.nikanOpenPersonWorkspace=openPersonWorkspace;
 window.nikanHrmOpenResource=async function(resource,options={}){
  if(!user||workspaceSaving)return;
  if(options.employee_id)return openPersonWorkspace(Number(options.employee_id),{module:options.module||'',resource});
  const resources=await catalog(),module=resource==='skill_matrix'?'training':resources[resource]?.module;
  if(!module)return;
  if(options.site_id){ensureFilters();activeFilters.site_id=String(options.site_id);}
  sessionStorage.setItem('nikan-hrm-resource-'+module,resource);return navigateTo('hrm_'+module);
 };
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-person-workspace]');if(!button||button.disabled||workspaceSaving||!user)return;
  event.preventDefault();openPersonWorkspace(Number(button.dataset.personWorkspace),{module:button.dataset.personModule,resource:button.dataset.personResource});
 });
 async function renderPeopleWorkspace(target,viewOwner){
  if(!hasPeopleDirectory()){target.innerHTML='<div class="empty">دسترسی پرونده کارکنان برای این حساب تعریف نشده است.</div>';return;}
  const resources=await catalog();if(!current(viewOwner))return;
  const extra=Object.entries(resources).filter(([key,schema])=>key!=='people'&&schema.module==='personnel'&&['read','create','edit','delete','export'].some(operation=>schemaCan(schema,operation)));
  const tabs=[['people','فهرست کارکنان'],...extra.map(([key,schema])=>[key,schema.title])];
  target.innerHTML='<div class="panel hrm-intro"><p class="eyebrow">پرونده مرکزی کارکنان</p><h2>یک فرد، یک پرونده در تمام بخش‌ها</h2><p class="help">مشخصات پایه، آموزش، کارگزینی، حضور، عملکرد، آکورد و HSE به همان شناسه فرد متصل‌اند. از پرونده یکپارچه، سوابق مجاز را مشاهده و با همان فرم‌های بخش مربوط ثبت کنید.</p></div><div class="hrm-tabs" role="tablist" aria-label="پرونده کارکنان"></div><div id="hrm-people-workspace"></div>';
  const referenceModule=personModules().find(module=>can(module))||personModules().find(module=>can(module,'create'))||'personnel';
  await scopeFilters(target,viewOwner,referenceModule,{operation:can(referenceModule)?'read':'create'});if(!current(viewOwner))return;
  const stored=sessionStorage.getItem('nikan-hrm-resource-personnel');let selected=tabs.some(([key])=>key===stored)?stored:'people',generation=0;
  async function open(key){
   if(workspaceSaving||!current(viewOwner))return;selected=key;sessionStorage.setItem('nikan-hrm-resource-personnel',key);const token=++generation;
   target.querySelector('.hrm-tabs').innerHTML=tabs.map(([id,title])=>'<button type="button" role="tab" data-hrm-resource="'+id+'" aria-selected="'+(id===key)+'" class="'+(id===key?'active':'quiet')+'">'+esc(title)+'</button>').join('');
   focusSelectedTab(target.querySelector('.hrm-tabs'));
   const workspace=target.querySelector('#hrm-people-workspace'),valid=()=>current(viewOwner)&&token===generation;workspace.innerHTML='<div class="empty" role="status">در حال دریافت پرونده‌ها…</div>';
   try{if(key==='people')await renderPeopleDirectory(workspace,resources.people,viewOwner,valid);else await renderResource(workspace,key,resources[key],viewOwner,valid)}catch(err){if(valid()&&err.name!=='AbortError')workspace.innerHTML='<div class="empty"><h3>دریافت پرونده‌ها انجام نشد</h3><p>'+esc(err.message)+'</p></div>';}
  }
  target.querySelector('.hrm-tabs').onclick=event=>{const button=event.target.closest('[data-hrm-resource]');if(button)open(button.dataset.hrmResource)};await open(selected);
 }
 async function renderPeopleDirectory(workspace,schema,viewOwner,isCurrent){
  const response=await api(scopedHrm('hrm/people-directory'));if(!isCurrent())return;const rows=asRows(response.people||response);
  workspace.innerHTML='<div class="toolbar hrm-toolbar"><label>جستجوی کارکنان<input id="hrm-search" placeholder="نام، کد پرسنلی، سایت یا واحد" autocomplete="off"></label><div>'+(schema&&schemaCan(schema,'create')?'<button type="button" id="hrm-add">＋ ثبت فرد در پرونده مرکزی</button>':'')+(schema&&schemaCan(schema,'export')?'<button type="button" class="quiet" id="hrm-export">خروجی Excel اطلاعات پرسنلی</button>':'')+'</div></div><p class="help">فهرست شامل افراد جاری و افرادی است که سوابق مجاز در این محدوده دارند. اطلاعات محل خدمت فعلی فقط در صورت داشتن مجوز آن محدوده نمایش داده می‌شود.</p><p id="hrm-row-status" class="help" role="status"></p><div id="hrm-table"></div>';
  let query='',offset=0;const table=workspace.querySelector('#hrm-table');
  function draw(){
   const filtered=rows.filter(person=>searchMatch([person.name,person.code,person.company_name,person.site_name,person.department,person.job].filter(Boolean).join(' '),query));if(offset>=filtered.length)offset=Math.max(0,Math.floor((filtered.length-1)/25)*25);
   workspace.querySelector('#hrm-row-status').textContent=fa(filtered.length)+' پرونده مجاز';
   table.innerHTML=filtered.length?'<div class="table-wrap"><table><thead><tr><th>کد پرسنلی</th><th>نام و نام خانوادگی</th><th>شرکت / سایت جاری</th><th>واحد / سمت</th><th>مبنای دسترسی</th><th>پرونده</th></tr></thead><tbody>'+filtered.slice(offset,offset+25).map(person=>'<tr data-hrm-row="'+person.id+'"><td>'+esc(person.code)+'</td><td>'+esc(person.name)+'</td><td>'+esc(person.current_assignment_visible?[person.company_name,person.site_name].filter(Boolean).join(' / ')||'سایت ثبت نشده':'در این محدوده نمایش داده نمی‌شود')+'</td><td>'+esc(person.current_assignment_visible?[person.department,person.job].filter(Boolean).join(' / ')||'ثبت نشده':'—')+'</td><td>'+esc(person.current_assignment_visible?'انتساب فعلی مجاز':'سوابق مجاز این محدوده')+'</td><td class="hrm-row-actions">'+personLink(person)+(schema&&schemaCan(schema,'edit')&&schemaCan(schema,'read')&&person.current_assignment_visible?'<button type="button" class="quiet" data-directory-edit="'+person.id+'">ویرایش مشخصات</button>':'')+'</td></tr>').join('')+'</tbody></table></div>'+pager(offset,25,filtered.length):'<div class="empty"><h3>پرونده‌ای در این محدوده پیدا نشد</h3><p>فیلترها را بررسی کنید یا در صورت داشتن مجوز، فرد را ثبت کنید.</p></div>';
   bindPager(table,()=>{offset-=25;draw()},()=>{offset+=25;draw()});
  }
  workspace.querySelector('#hrm-search').oninput=event=>{query=event.target.value;offset=0;draw()};draw();
  workspace.querySelector('#hrm-add')?.addEventListener('click',()=>openEditor('people',schema,null,viewOwner,{isCurrent}));
  workspace.querySelector('#hrm-export')?.addEventListener('click',async()=>{try{download(await api(scopedHrm('hrm/reports/people.xlsx')),'Nikan-Personnel.xlsx')}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}});
  table.onclick=async event=>{const button=event.target.closest('[data-directory-edit]');if(!button||workspaceSaving||!isCurrent())return;try{const person=await api(scopedHrm('hrm/people/'+button.dataset.directoryEdit,cid,false));if(isCurrent())openEditor('people',schema,person,viewOwner,{isCurrent})}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
 }
 async function renderPersonWorkspace(target,viewOwner){
  if(!inPersonWorkspace()){target.innerHTML='<div class="empty"><h2>فردی انتخاب نشده است</h2><button data-person-directory>فهرست کارکنان</button></div>';target.querySelector('[data-person-directory]').onclick=()=>navigateTo('hrm_personnel');return;}
  const identity=personWorkspace.id,[overview,resources]=await Promise.all([api(scopedHrm('hrm/people/'+identity+'/overview')),catalog()]);if(!current(viewOwner)||!inPersonWorkspace()||personWorkspace.id!==identity)return;
  personWorkspace.overview=overview;const person=overview.person,sections=overview.sections||{},visible=Boolean(overview.current_assignment_visible),companyName=user.companies?.find(company=>Number(company.id)===Number(cid))?.name||'شرکت انتخاب‌شده';
  document.querySelector('#nav [data-page=hrm_personnel]')?.classList.add('active');
  const tabs=[['basic','مشخصات و وضعیت پرونده'],...Object.entries(sections).filter(([,section])=>['read','create','edit','delete','export'].some(operation=>section[operation])&&(section.resources?.some(key=>key!=='people'&&key!=='training_courses')||section.read&&section===sections.training)).map(([module,section])=>[module,moduleLabels[module]||module])];
  const requestedModule=personWorkspace.module||resources[personWorkspace.resource]?.module;let selected=tabs.some(([key])=>key===requestedModule)?requestedModule:'basic',generation=0;
  target.innerHTML='<div class="panel hrm-person-header"><div class="hrm-section-heading"><div><p class="eyebrow">'+esc(companyName)+' / پرونده کارکنان</p><h2>'+esc(person.name)+'</h2><p><b>'+esc(person.code)+'</b>'+(visible?' · '+esc([person.site_name,person.department,person.job].filter(Boolean).join(' · ')):'')+'</p><span class="pill">'+(visible?'انتساب فعلی در محدوده مجاز':'سوابق مجاز؛ محل خدمت فعلی خارج از این نما')+'</span></div><div class="hrm-action-group"><button type="button" class="quiet" id="hrm-person-directory">فهرست کارکنان</button>'+(personWorkspace.origin!=='hrm_personnel'&&personWorkspace.origin!=='hrm_person'?'<button type="button" class="quiet" id="hrm-person-return">بازگشت به بخش قبلی</button>':'')+'</div></div><p class="help">'+(visible?'همه بخش‌ها از همین شناسه فرد استفاده می‌کنند؛ اطلاعات هر حوزه با مجوز مستقل آن حوزه ثبت و نمایش داده می‌شود.':'اطلاعات فعلی خارج از محدوده نمایش داده نمی‌شود. این نما برای مشاهده سوابق مجاز است؛ ثبت، ویرایش و حذف از این نما غیرفعال‌اند.')+'</p></div><div class="panel hrm-person-context"><p class="help">محدوده: '+esc(companyName)+(activeFilters.site_id?' · سایت انتخاب‌شده':'')+(activeFilters.department?' · '+esc(activeFilters.department):'')+(activeFilters.from_date?' · از '+esc(activeFilters.from_date):'')+(activeFilters.to_date?' · تا '+esc(activeFilters.to_date):'')+'</p><small class="help">شمارنده‌های بخش‌ها، همه سوابق مجاز از تمام تاریخ‌ها را نشان می‌دهند. جدول هر بخش، فیلتر تاریخی فعال خودش را رعایت می‌کند.</small>'+((activeFilters.from_date||activeFilters.to_date)?'<button type="button" class="quiet" id="hrm-person-clear-dates">نمایش همه تاریخ‌ها</button>':'')+'</div><div class="hrm-tabs hrm-person-tabs" role="tablist" aria-label="بخش‌های پرونده یکپارچه"></div><div id="hrm-person-body"></div>';
  target.querySelector('#hrm-person-directory').onclick=()=>navigateTo('hrm_personnel');target.querySelector('#hrm-person-return')?.addEventListener('click',()=>navigateTo(personWorkspace.origin));
  target.querySelector('#hrm-person-clear-dates')?.addEventListener('click',()=>{delete activeFilters.from_date;delete activeFilters.to_date;renderCurrent()});
  async function open(module){
   if(workspaceSaving||!current(viewOwner))return;selected=module;personWorkspace.module=module;const token=++generation;
   target.querySelector('.hrm-person-tabs').innerHTML=tabs.map(([key,title])=>'<button type="button" role="tab" data-person-section="'+key+'" aria-selected="'+(selected===key)+'" class="'+(selected===key?'active':'quiet')+'">'+esc(title)+(key!=='basic'&&sections[key]?.read&&sections[key].count!==null?' <small>('+fa(sections[key].count)+')</small>':'')+'</button>').join('');
   focusSelectedTab(target.querySelector('.hrm-person-tabs'));
   const box=target.querySelector('#hrm-person-body'),valid=()=>current(viewOwner)&&token===generation&&personWorkspace?.id===identity;box.innerHTML='<div class="empty" role="status">در حال دریافت بخش پرونده…</div>';
   try{if(module==='basic')await renderPersonBasic(box,overview,resources.people,viewOwner,valid);else await renderPersonSection(box,module,sections[module],resources,viewOwner,valid)}catch(err){if(valid()&&err.name!=='AbortError')box.innerHTML='<div class="empty"><h3>دریافت این بخش انجام نشد</h3><p>'+esc(err.message)+'</p></div>';}
  }
  target.querySelector('.hrm-person-tabs').onclick=event=>{const button=event.target.closest('[data-person-section]');if(button)open(button.dataset.personSection)};await open(selected);
 }
 async function renderPersonBasic(box,overview,schema,viewOwner,isCurrent){
  const basic=overview.person,allowed=overview.current_assignment_visible&&overview.sections?.personnel?.read&&schema;
  const person=allowed?await api(scopedHrm('hrm/people/'+basic.id,cid,false)):basic;if(!isCurrent())return;
  const fields=allowed?schema.fields.filter(field=>!field.readonly):[{key:'code',label:'کد پرسنلی'},{key:'name',label:'نام و نام خانوادگی'},{key:'company_name',label:'شرکت جاری مجاز'},{key:'site_name',label:'سایت جاری مجاز'},{key:'department',label:'واحد'},{key:'job',label:'سمت'}];
  box.innerHTML='<div class="panel"><div class="hrm-section-heading"><h2>مشخصات ثبت‌شده فرد</h2>'+(allowed&&overview.sections.personnel.edit&&schemaCan(schema,'edit')?'<button type="button" class="quiet" id="hrm-person-edit">ویرایش اطلاعات پایه</button>':'')+'</div>'+(!allowed?'<p class="help">این حساب فقط اطلاعات هویتی و سازمانی حداقلی مجاز را می‌بیند؛ اطلاعات کامل استخدامی به مجوز پرونده پرسنلی نیاز دارد.</p>':'')+'<dl class="hrm-person-fields">'+fields.map(field=>'<div><dt>'+esc(field.label)+'</dt><dd>'+esc(cellValue(schema||{},field.key,person[field.key],person))+'</dd></div>').join('')+'</dl></div>';
  box.querySelector('#hrm-person-edit')?.addEventListener('click',()=>openEditor('people',schema,person,viewOwner,{isCurrent}));
 }
 async function renderPersonSection(box,module,section,resources,viewOwner,isCurrent){
  const personal=(section.resources||[]).filter(key=>key!=='people'&&key!=='training_courses'&&resources[key]);
  const tabs=personal.map(key=>[key,resources[key].title]);
  if(module==='training'&&section.read){if(personWorkspace.overview.current_assignment_visible)tabs.unshift(['skill_matrix','ماتریس شایستگی جاری']);tabs.push(['training_certificates','گواهینامه‌های آموزش']);}
  if(!tabs.length){box.innerHTML='<div class="empty">فرمی برای این حوزه و محدوده در دسترس نیست.</div>';return;}
  const remembered=personWorkspace.resource;let selected=tabs.some(([key])=>key===remembered)?remembered:tabs[0][0],generation=0;
  box.innerHTML='<div class="hrm-tabs hrm-person-resources" role="tablist" aria-label="سوابق '+esc(moduleLabels[module]||module)+'"></div><div id="hrm-person-resource"></div>';
  async function open(key){
   if(workspaceSaving||!isCurrent())return;selected=key;personWorkspace.resource=key;personWorkspace.resourceFilters={...(section.resource_filters?.[key]||{}),...(key===personWorkspace.requestedResource?personWorkspace.initialFilters:{})};const token=++generation;
   box.querySelector('.hrm-person-resources').innerHTML=tabs.map(([id,title])=>'<button type="button" role="tab" data-person-resource-tab="'+id+'" aria-selected="'+(id===key)+'" class="'+(id===key?'active':'quiet')+'">'+esc(title)+'</button>').join('');
   focusSelectedTab(box.querySelector('.hrm-person-resources'));
   const workspace=box.querySelector('#hrm-person-resource'),valid=()=>isCurrent()&&token===generation;workspace.innerHTML='<div class="empty" role="status">در حال دریافت سوابق فرد…</div>';
   try{
    if(key==='skill_matrix')return await renderSkillMatrix(workspace,viewOwner,valid,{personOnly:true});
    if(key==='training_certificates')return await renderPersonTrainingCertificates(workspace,viewOwner,valid);
    const schema={...resources[key]};for(const operation of ['read','create','edit','delete','export'])schema['can_'+operation]=Boolean(section[operation]&&schemaCan(resources[key],operation));
    if(!personWorkspace.overview.current_assignment_visible){schema.can_create=schema.can_edit=schema.can_delete=false;}
    await renderResource(workspace,key,schema,viewOwner,valid);
   }catch(err){if(valid()&&err.name!=='AbortError')workspace.innerHTML='<div class="empty"><h3>دریافت سوابق انجام نشد</h3><p>'+esc(err.message)+'</p></div>';}
  }
  box.querySelector('.hrm-person-resources').onclick=event=>{const button=event.target.closest('[data-person-resource-tab]');if(button)open(button.dataset.personResourceTab)};await open(selected);
 }
 async function renderPersonTrainingCertificates(workspace,viewOwner,isCurrent){
  const id=personWorkspace.id,response=await api(scopedHrm('hrm/people/'+id+'/training'));if(!isCurrent())return;const rows=asRows(response.certificates);
  workspace.innerHTML='<div class="panel"><h2>گواهینامه‌های مجاز آموزش</h2><p class="help">این مدارک از همان سوابق آموزش خوانده می‌شوند؛ دریافت فایل نیز مجوز مستقل همان حوزه را رعایت می‌کند.</p>'+simpleTable(rows,['title','issuer','issued','expires'])+(can('training','export')?rows.map(row=>'<button type="button" class="quiet" data-person-certificate="'+row.id+'">دریافت '+esc(row.title||'گواهینامه')+'</button>').join(' '):'')+'</div>';
  workspace.onclick=async event=>{const button=event.target.closest('[data-person-certificate]');if(!button||!isCurrent())return;try{const row=rows.find(item=>String(item.id)===button.dataset.personCertificate),blob=await api(scopedHrm('hrm/people/'+id+'/training-certificates/'+button.dataset.personCertificate+'/file')),extension=({'application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg'})[blob.type]||'';download(blob,row?.filename||row?.original_name||'Nikan-Training-Certificate-'+button.dataset.personCertificate+extension)}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
 }
 async function renderTrainingWorkspace(target,viewOwner){
  if(!moduleAvailable('training')){target.innerHTML='<div class="empty">دسترسی آموزش برای این حساب تعریف نشده است.</div>';return;}
  const resources=await catalog();if(!current(viewOwner))return;
  const tabs=[...(can('training')?[['skill_matrix','ماتریس شایستگی']]:[]),...Object.entries(resources).filter(([,schema])=>schema.module==='training').map(([key,schema])=>[key,schema.title])];
  if(!tabs.length){target.innerHTML='<div class="empty">فرم آموزشی مجازی در دسترس نیست.</div>';return;}
  const legacy=context().is_primary_admin||context().training_access&&(context().scopes||[]).some(scope=>Number(scope.company_id)===Number(cid)&&!scope.site_id&&!scope.department);
  const toolKeys=legacy?['bank','requirements','passports','courses','needs','instructors','certificates','report',...(context().is_primary_admin?['governance']:[])]:[];
  target.innerHTML='<div class="panel hrm-intro"><p class="eyebrow">آموزش و توسعه در سامانه یکپارچه</p><h2>شایستگی، برنامه و شواهد آموزش</h2><p class="help">سطح لازم شغل، وضعیت واقعی فرد، دوره‌ها و آموزش‌های الزامی ایمنی و بهداشت از منابع مشترک خوانده می‌شوند. نبود ارزیابی با سطح صفر ثبت‌شده تفاوت دارد.</p>'+(toolKeys.length?'<details class="hrm-advanced-training"><summary>ابزارهای تخصصی آموزش، شناسنامه و برنامه‌ریزی</summary><div class="hrm-action-group">'+toolKeys.filter(key=>menus.some(item=>item[0]===key)).map(key=>'<button type="button" class="quiet" data-training-tool="'+key+'">'+esc(menus.find(item=>item[0]===key)[2])+'</button>').join('')+'</div><p class="help">شناسنامه‌ها و سوابق قبلی با منشأ شرکت ثبت‌کننده حفظ می‌شوند؛ تغییر شرکتِ انتخاب‌شده یا محل خدمت، آن منشأ را بازنویسی نمی‌کند.</p></details>':'')+'</div><div class="hrm-tabs" role="tablist" aria-label="آموزش و توسعه"></div><div id="hrm-training-workspace"></div>';
  await scopeFilters(target,viewOwner,'training',{operation:can('training')?'read':'create'});if(!current(viewOwner))return;
  target.querySelectorAll('[data-training-tool]').forEach(button=>button.onclick=()=>navigateTo(button.dataset.trainingTool));
  const stored=sessionStorage.getItem('nikan-hrm-resource-training');let selected=tabs.some(([key])=>key===stored)?stored:tabs[0][0],generation=0;
  async function open(key){
   if(workspaceSaving||!current(viewOwner))return;selected=key;sessionStorage.setItem('nikan-hrm-resource-training',key);const token=++generation;
   target.querySelector('.hrm-tabs').innerHTML=tabs.map(([id,title])=>'<button type="button" role="tab" data-hrm-resource="'+id+'" aria-selected="'+(id===key)+'" class="'+(id===key?'active':'quiet')+'">'+esc(title)+'</button>').join('');
   focusSelectedTab(target.querySelector('.hrm-tabs'));
   for(const name of ['from_date','to_date']){const field=target.querySelector('#hrm-filters [name='+name+']');if(field)field.closest('label').hidden=key==='skill_matrix';}
   const workspace=target.querySelector('#hrm-training-workspace'),valid=()=>current(viewOwner)&&token===generation;workspace.innerHTML='<div class="empty" role="status">در حال دریافت آموزش…</div>';
   try{if(key==='skill_matrix')await renderSkillMatrix(workspace,viewOwner,valid);else await renderResource(workspace,key,resources[key],viewOwner,valid)}catch(err){if(valid()&&err.name!=='AbortError')workspace.innerHTML='<div class="empty"><h3>دریافت آموزش انجام نشد</h3><p>'+esc(err.message)+'</p></div>';}
  }
  target.querySelector('.hrm-tabs').onclick=event=>{const button=event.target.closest('[data-hrm-resource]');if(button)open(button.dataset.hrmResource)};await open(selected);
 }
 async function renderSkillMatrix(workspace,viewOwner,isCurrent,{personOnly=false}={}){
  if(!can('training')){workspace.innerHTML='<div class="empty">مشاهده ماتریس به مجوز آموزش نیاز دارد.</div>';return;}
  workspace.innerHTML='<div class="panel"><form id="hrm-matrix-filters" class="hrm-matrix-filter"><label>نمای ماتریس<select name="view"><option value="required">الزامات و شواهد ثبت‌شده</option><option value="all">تمام بانک شایستگی</option></select></label><label>گروه شایستگی<select name="category" data-searchable="true"><option value="">تمام گروه‌ها</option></select></label><label>جستجوی شایستگی<input name="q" placeholder="کد یا عنوان شایستگی" autocomplete="off"></label><label>تاریخ مبنا (شمسی)<input name="as_of" dir="ltr" inputmode="numeric" value="'+currentJalaliDate()+'"></label><button type="submit">نمایش ماتریس</button>'+(can('training','export')?'<button type="button" class="quiet" id="hrm-matrix-export">خروجی Excel ماتریس</button>':'')+'</form><p class="help">انتساب و سطح لازم مربوط به وضعیت جاری است؛ شواهد تا تاریخ مبنا بررسی می‌شوند. نبود ارزیابی با سطح صفر ثبت‌شده تفاوت دارد. وضعیت بحرانی هر شایستگی جدا دیده می‌شود و با میانگین پنهان نمی‌شود.</p><p id="hrm-matrix-error" class="error" role="alert"></p></div><div id="hrm-matrix-result"></div>';
  const form=workspace.querySelector('#hrm-matrix-filters'),result=workspace.querySelector('#hrm-matrix-result'),error=workspace.querySelector('#hrm-matrix-error');let generation=0,lastParameters='',report=null,offset=0;
  const levelText=(value,required=false)=>value===null||value===undefined?(required?'تعریف نشده':'ارزیابی ثبت نشده'):(report?.level_definitions?.find(item=>Number(item.level)===Number(value))?.symbol||String(value))+' · '+(report?.level_definitions?.find(item=>Number(item.level)===Number(value))?.label||'سطح ثبت‌شده');
  function draw(){
   if(!report||!isCurrent())return;const competencies=asRows(report.competencies),people=asRows(report.employees),rows=asRows(report.rows),cells=new Map(rows.map(row=>[row.employee_id+':'+row.competency_code,row])),metrics=report.metrics||{};
   if(offset>=people.length)offset=Math.max(0,Math.floor((people.length-1)/15)*15);
   result.innerHTML='<div class="metrics hrm-matrix-metrics">'+[['employees','افراد مجاز'],['required_cells','الزامات تعریف‌شده'],['unknown_requirements','ارزیابی ثبت‌نشده'],['competency_gaps','فاصله شایستگی'],['critical_gaps','فاصله بحرانی'],['critical_unknowns','الزام بحرانی بدون ارزیابی']].map(([key,title])=>'<div class="card"><small>'+title+'</small><strong data-matrix-metric="'+key+'">'+fa(metrics[key])+'</strong></div>').join('')+'</div><div class="panel"><div class="hrm-matrix-legend">'+asRows(report.level_definitions).map(item=>'<span title="'+esc(item.observable)+'"><b>'+esc(item.symbol)+'</b> '+esc(item.label)+'</span>').join('')+'<span><b>؟</b> ارزیابی ثبت نشده</span></div><p class="help">هر خانه: سطح فعلی / سطح لازم. برای مشاهده دلیل، منشأ الزام و مشخصات ارزیابی روی خانه کلیک کنید.</p><div id="hrm-matrix-grid"></div><div id="hrm-matrix-cell-detail" aria-live="polite"></div></div>'+(report.data_quality?.length?'<details class="panel"><summary>موارد نیازمند تکمیل اطلاعات ('+fa(report.data_quality.length)+')</summary><ul>'+report.data_quality.map(item=>'<li>'+esc(item.message||'اطلاعات نیازمند بررسی است')+'</li>').join('')+'</ul></details>':'');
   const grid=result.querySelector('#hrm-matrix-grid');
   grid.innerHTML=people.length&&competencies.length?'<div class="table-wrap hrm-matrix-scroll"><table class="hrm-skill-matrix"><thead><tr><th class="hrm-matrix-person-col">فرد / محل خدمت</th>'+competencies.map(skill=>'<th title="'+esc(skill.title)+'"><b>'+esc(skill.code)+'</b><small>'+esc(skill.title)+'</small>'+(Number(skill.importance)===3?'<span class="hrm-critical-label">اهمیت بحرانی</span>':'')+'</th>').join('')+'</tr></thead><tbody>'+people.slice(offset,offset+15).map(person=>'<tr data-matrix-employee="'+person.id+'"><th class="hrm-matrix-person-col">'+esc(person.name)+'<small>'+esc([person.code,person.department,person.job].filter(Boolean).join(' · '))+'</small>'+(!personOnly?personLink(person,'training','skill_matrix','پرونده فرد'):'')+'</th>'+competencies.map(skill=>{
    const cell=cells.get(person.id+':'+skill.code);if(!cell)return '<td class="hrm-matrix-empty">—</td>';
    const symbol=cell.current_level===null||cell.current_level===undefined?'؟':report.level_definitions.find(item=>Number(item.level)===Number(cell.current_level))?.symbol||String(cell.current_level),required=cell.required_level===null||cell.required_level===undefined?'—':report.level_definitions.find(item=>Number(item.level)===Number(cell.required_level))?.symbol||String(cell.required_level);
    return '<td class="hrm-matrix-state-'+esc(cell.status)+'"><button type="button" class="hrm-matrix-cell" data-matrix-cell="'+esc(person.id+':'+skill.code)+'" aria-label="'+esc(person.name+'، '+skill.title+'، '+(cell.status_label||report.status_labels?.[cell.status]||'نیازمند بررسی'))+'"><strong>'+esc(symbol)+' <span>/ '+esc(required)+'</span></strong><small>'+esc(cell.status_label||report.status_labels?.[cell.status]||'نیازمند بررسی')+'</small></button></td>';
   }).join('')+'</tr>').join('')+'</tbody></table></div>'+pager(offset,15,people.length):'<div class="empty"><h3>'+(!people.length?'فردی در این محدوده مجاز پیدا نشد':'الزام یا شاهدی برای این فیلتر ثبت نشده است')+'</h3><p>محدوده، شغل و الزامات را بررسی کنید؛ مقدار ساختگی جایگزین داده ثبت‌نشده نمی‌شود.</p></div>';
   bindPager(grid,()=>{offset-=15;draw()},()=>{offset+=15;draw()});
   grid.onclick=event=>{const button=event.target.closest('[data-matrix-cell]');if(!button)return;const cell=cells.get(button.dataset.matrixCell);if(!cell)return;
    const sourceLabels={job_profile:'الزام مطلوب شغل',personal_profile:'الزام اختصاصی فرد',configured_general_policy:'ضابطه آموزش شغل و محل',hse_policy:'الزام تخصصی HSE'};
    const sourceText=asRows(cell.requirement_sources).map(source=>[source.label||source.title||sourceLabels[source.kind||source.source]||'الزام ثبت‌شده',source.level===undefined?'':levelText(source.level,true)].filter(Boolean).join(' · '));
    const actor=typeof cell.recorded_by==='string'?cell.recorded_by:cell.recorded_by?.username;
    const details=[['نام فرد',cell.employee_name],['شایستگی',cell.competency_code+' · '+cell.competency_title],['سطح مطلوب شغل',levelText(cell.job_required_level,true)],['سطح اختصاصی فرد',levelText(cell.personal_required_level,true)],['سطح لازم نهایی',levelText(cell.required_level,true)],['وضعیت واقعی',levelText(cell.current_level)],['فاصله با سطح لازم',cell.gap===null||cell.gap===undefined?'نامشخص':fa(cell.gap)],['وضعیت',cell.status_label||report.status_labels?.[cell.status]],['تاریخ ارزیابی',cell.assessment_date?dateInput(cell.assessment_date):'ثبت نشده'],['ارزیاب',cell.assessor_name||'ثبت نشده'],['روش ارزیابی',cell.assessment_method||'ثبت نشده'],['مستند ارزیابی',typeof cell.assessment_evidence==='string'?cell.assessment_evidence:'ثبت نشده'],['ثبت‌کننده',actor||'ثبت نشده'],['آخرین حضور واقعی آموزش',cell.last_training_date?dateInput(cell.last_training_date):'شاهد حضور واقعی ثبت نشده'],['موعد تجدید',cell.renewal_due_date?dateInput(cell.renewal_due_date):cell.renewal_days?'به شاهد معتبر حضور واقعی نیاز دارد':'ضابطه تجدید ثبت نشده']];
    const detail=result.querySelector('#hrm-matrix-cell-detail');detail.innerHTML='<section class="hrm-matrix-detail"><h3>'+esc(cell.competency_title)+' — '+esc(cell.employee_name)+'</h3><dl class="hrm-person-fields">'+details.map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value||'ثبت نشده')+'</dd></div>').join('')+'</dl>'+(sourceText.length?'<h4>منشأ سطح لازم</h4><ul>'+sourceText.map(text=>'<li>'+esc(text)+'</li>').join('')+'</ul>':'')+(cell.requirement_variance?'<p class="hrm-accord-blockers">سطح اختصاصی فرد با الگوی مطلوب شغل تفاوت دارد؛ هر دو مبنا برای بازنگری محفوظ‌اند.</p>':'')+'<button type="button" class="quiet" id="hrm-matrix-assessments">سوابق و ثبت ارزیابی همین فرد</button></section>';
    detail.querySelector('#hrm-matrix-assessments').onclick=()=>openPersonWorkspace(cell.employee_id,{module:'training',resource:'training_assessments',filters:{competency_code:cell.competency_code}});
   };
  }
  async function load(){
   if(workspaceSaving||!isCurrent())return;const token=++generation;error.textContent='';lastParameters=new URLSearchParams([...new FormData(form)].filter(([,value])=>String(value).trim())).toString();result.innerHTML='<div class="empty" role="status">در حال دریافت ماتریس شایستگی…</div>';
   try{const next=await api(scopedHrm('hrm/skill-matrix?'+lastParameters));if(!isCurrent()||token!==generation)return;report=next;offset=0;const categories=[...new Set(asRows(next.competencies).map(item=>item.category).filter(Boolean))],select=form.elements.namedItem('category'),value=select.value;select.innerHTML='<option value="">تمام گروه‌ها</option>'+[...new Set([...(value?[value]:[]),...categories])].map(category=>'<option value="'+esc(category)+'" '+(category===value?'selected':'')+'>'+esc(category)+'</option>').join('');select.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));draw()}catch(err){if(isCurrent()&&token===generation&&err.name!=='AbortError'){result.replaceChildren();error.textContent=err.message;}}
  }
  form.onsubmit=event=>{event.preventDefault();load()};form.querySelector('#hrm-matrix-export')?.addEventListener('click',async()=>{try{download(await api(scopedHrm('hrm/skill-matrix.xlsx?'+lastParameters)),'Nikan-Skill-Matrix.xlsx')}catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}});await load();
 }
 async function renderStructure(target,viewOwner){
  if(!managesAccess()&&!can('personnel')){target.innerHTML='<div class="empty">مشاهده ساختار و نفرات برای این حساب مجاز نیست.</div>';return;}
  if(structureCompany!==Number(cid)){structureCompany=Number(cid);structureSite='';}
  const [data,resources]=await Promise.all([api(scopedHrm('hrm/site-roster',cid,false)),catalog()]);if(!current(viewOwner))return;
  const sites=asRows(data.sites),people=[...new Map([...asRows(data.people),...asRows(data.unassigned)].map(person=>[String(person.id??person.employee_id),person])).values()],temporary=asRows(data.temporary_assignments);
  if(structureSite&&structureSite!=='unassigned'&&!sites.some(site=>String(site.id)===structureSite))structureSite='';
  const companyName=user.companies?.find(company=>Number(company.id)===Number(cid))?.name||'شرکت انتخاب‌شده';
  const siteSchema=resources.sites,peopleSchema=resources.people||{},canCreateSite=managesAccess()&&siteSchema&&schemaCan(siteSchema,'create');
  target.innerHTML='<div class="panel hrm-intro"><div class="hrm-section-heading"><div><p class="eyebrow">ساختار سازمانی</p><h2>سایت‌ها و نفرات '+esc(companyName)+'</h2><p class="help">انتساب فعلی هر فرد و حضور موقت در سایت‌های دیگر جدا نمایش داده می‌شوند. شرکت استخدام‌کننده با مأموریت موقت تغییر نمی‌کند.</p></div><div class="hrm-action-group">'+(managesAccess()?'<button type="button" class="quiet" id="hrm-manage-companies">مدیریت شرکت‌ها</button>':'')+(canCreateSite?'<button type="button" id="hrm-site-add">＋ افزودن سایت</button>':'')+'</div></div></div><div class="metrics hrm-structure-metrics">'+[['sites','سایت مجاز',sites.length],['employees','نفر در انتساب فعلی',people.length],['temporary_assignments','فعالیت موقت',temporary.length],['unassigned','نفر بدون سایت',asRows(data.unassigned).length]].map(([key,title,value])=>'<div class="card"><small>'+title+'</small><strong data-structure-total="'+key+'">'+fa(value)+'</strong></div>').join('')+'</div><div class="panel"><h2>سایت‌های شرکت</h2><p class="help">افزودن، ویرایش و حذف ساختار فقط در اختیار ادمین اصلی است. سایت دارای پرسنل، سابقه یا محدوده دسترسی حذف نمی‌شود؛ برای توقف استفاده، آن را غیرفعال کنید.</p><div id="hrm-structure-error" class="error" role="alert"></div><div id="hrm-structure-sites"></div></div><div class="panel"><form id="hrm-roster-filters" class="hrm-roster-filter"><label>نمایش نفرات سایت<select id="hrm-roster-site" data-searchable="true"><option value="">تمام سایت‌های مجاز شرکت</option>'+sites.map(site=>'<option value="'+site.id+'" '+(structureSite===String(site.id)?'selected':'')+'>'+esc(site.name)+'</option>').join('')+'<option value="unassigned" '+(structureSite==='unassigned'?'selected':'')+'>بدون سایت ثبت‌شده</option></select></label><label>جستجو در نفرات<input id="hrm-roster-search" placeholder="نام، کد پرسنلی، واحد یا سمت" autocomplete="off"></label><div class="hrm-action-group">'+(schemaCan(peopleSchema,'create')?'<button type="button" id="hrm-roster-add">＋ ثبت فرد</button>':'')+'<button type="button" class="quiet" id="hrm-roster-edit">پرونده و انتساب افراد</button></div></form></div><div class="panel" id="hrm-roster-current"><h2>نفرات با انتساب فعلی</h2><p class="help">این فهرست، مبنای مشخص شدن پرسنل هر سایت است.</p><div data-roster-table></div></div><div class="panel" id="hrm-roster-temporary"><h2>فعالیت‌های موقت در سایت</h2><p class="help">فعالیت‌های جاری و مؤثر در تاریخ '+esc(dateInput(data.as_of))+'؛ افراد این جدول به شمارش انتساب اصلی سایت اضافه نمی‌شوند.</p><div data-roster-table></div></div>';
  const siteTable=target.querySelector('#hrm-structure-sites');
  siteTable.innerHTML=sites.length?'<div class="table-wrap"><table><thead><tr><th>سایت</th><th>نوع</th><th>وضعیت</th><th>نفرات سایت</th><th>فعالیت موقت</th><th>عملیات</th></tr></thead><tbody>'+sites.map(site=>'<tr data-structure-site="'+site.id+'"><td>'+esc(site.name)+'</td><td>'+esc(site.kind||'—')+'</td><td>'+esc(site.active?'فعال':'غیرفعال')+'</td><td>'+fa(site.employee_count)+'</td><td>'+fa(site.temporary_assignment_count)+'</td><td class="hrm-row-actions"><button type="button" class="quiet" data-structure-people="'+site.id+'">نمایش نفرات</button>'+(managesAccess()&&schemaCan(siteSchema,'edit')?'<button type="button" class="quiet" data-structure-edit="'+site.id+'">ویرایش سایت</button>':'')+(managesAccess()&&siteSchema?.deletable&&schemaCan(siteSchema,'delete')?'<button type="button" class="quiet" data-structure-delete="'+site.id+'">حذف سایت</button>':'')+'</td></tr>').join('')+'</tbody></table></div>':'<div class="empty"><h3>سایتی ثبت نشده است</h3><p>پرسنل بدون سایت مشخص در فهرست پایین قابل شناسایی‌اند.</p></div>';
  let search='',offset=0;
  function drawRoster(){
   const matches=people.filter(person=>(structureSite==='unassigned'?!person.site_id:!structureSite||String(person.site_id)===structureSite)&&searchMatch([person.name,person.code,person.site_name,person.department,person.job,person.location].join(' '),search));
   if(offset>=matches.length)offset=Math.max(0,Math.floor((matches.length-1)/25)*25);
   const table=target.querySelector('#hrm-roster-current [data-roster-table]');table.innerHTML='<p class="help">'+fa(matches.length)+' نفر</p>'+simpleTable(matches.slice(offset,offset+25),['code','name','site_name','department','job','location','status'],{...peopleSchema,person_links:true,person_module:'personnel'})+(matches.length?pager(offset,25,matches.length):'');
   bindPager(table,()=>{offset-=25;drawRoster()},()=>{offset+=25;drawRoster()});
   const visitors=temporary.filter(record=>structureSite!=='unassigned'&&(!structureSite||String(record.target_site_id)===structureSite)&&searchMatch([record.employee_name,record.employee_code,record.company_name,record.target_site_name,record.activity_location].join(' '),search));
   target.querySelector('#hrm-roster-temporary [data-roster-table]').innerHTML=simpleTable(visitors,['employee_code','employee_name','company_name','target_site_name','start_date','end_date','activity_location'],{person_links:true,person_module:'personnel',fields:[{key:'company_name',label:'شرکت استخدام‌کننده'},{key:'target_site_name',label:'سایت فعالیت موقت'},{key:'start_date',label:'از تاریخ',type:'date'},{key:'end_date',label:'تا تاریخ',type:'date'}]});
  }
  target.querySelector('#hrm-roster-site').onchange=event=>{structureSite=event.target.value;offset=0;drawRoster()};
  target.querySelector('#hrm-roster-search').oninput=event=>{search=event.target.value;offset=0;drawRoster()};
  target.querySelector('#hrm-roster-filters').onsubmit=event=>event.preventDefault();drawRoster();
  target.querySelector('#hrm-manage-companies')?.addEventListener('click',()=>navigateTo('companies'));
  target.querySelector('#hrm-site-add')?.addEventListener('click',()=>openEditor('sites',siteSchema,null,viewOwner));
  target.querySelector('#hrm-roster-add')?.addEventListener('click',()=>{ensureFilters();if(structureSite&&structureSite!=='unassigned')activeFilters.site_id=structureSite;else delete activeFilters.site_id;openEditor('people',peopleSchema,null,viewOwner)});
  target.querySelector('#hrm-roster-edit').onclick=()=>{ensureFilters();if(structureSite&&structureSite!=='unassigned')activeFilters.site_id=structureSite;else delete activeFilters.site_id;sessionStorage.setItem('nikan-hrm-resource-personnel','people');navigateTo('hrm_personnel')};
  siteTable.onclick=async event=>{
   if(workspaceSaving||!current(viewOwner))return;
   const show=event.target.closest('[data-structure-people]'),edit=event.target.closest('[data-structure-edit]'),remove=event.target.closest('[data-structure-delete]');
   const site=sites.find(row=>String(row.id)===String(show?.dataset.structurePeople||edit?.dataset.structureEdit||remove?.dataset.structureDelete));if(!site)return;
   if(show){structureSite=String(site.id);const select=target.querySelector('#hrm-roster-site');select.value=structureSite;select.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));offset=0;drawRoster();target.querySelector('#hrm-roster-current').scrollIntoView({behavior:'smooth',block:'start'});return;}
   if(edit)return openEditor('sites',siteSchema,site,viewOwner);
   if(!remove||!confirm('سایت «'+site.name+'» حذف شود؟ حذف فقط برای سایت بدون پرسنل، سابقه یا دسترسی متصل انجام می‌شود.'))return;
   const unlock=workspaceWriteLock(target),error=target.querySelector('#hrm-structure-error');error.textContent='';
   try{await api(scopedHrm('hrm/sites/'+site.id,cid,false),{method:'DELETE',body:{version:site.version}});if(current(viewOwner)){unlock();await renderCurrent();notice('سایت بدون وابستگی حذف شد.')}}catch(err){if(current(viewOwner)&&err.name!=='AbortError')error.textContent=err.message}finally{unlock()}
  };
 }
 async function renderProductivity(target,viewOwner){
  if(!moduleAvailable('productivity')){target.innerHTML='<div class="empty"><h2>دسترسی آکورد بهره‌وری برای این حساب تعریف نشده است</h2></div>';return;}
  const resources=await catalog();if(!current(viewOwner))return;
  const choices=Object.entries(resources).filter(([key,schema])=>schema.module==='productivity');
  const tabs=[...choices.map(([key,schema])=>[key,schema.title])];
  if(can('productivity'))tabs.splice(Math.min(2,tabs.length),0,['calculation','محاسبه و تأیید آکورد']);
  if(!tabs.length){target.innerHTML='<div class="empty">فرم مجازی برای این بخش در دسترس نیست.</div>';return;}
  const performanceActions=[['evaluation_periods','۱. دوره ارزیابی'],['performance_criteria','۲. شاخص‌ها و وزن‌ها'],['performance_scores','۳. امتیاز عملکرد افراد']].filter(([key])=>resources[key]);
  target.innerHTML='<div class="panel hrm-intro"><p class="eyebrow">آکورد بهره‌وری</p><h2>از شاخص عملکرد تا مبلغ قابل بررسی</h2><p class="help">امتیاز هر فرد از شاخص‌ها و وزن‌های دوره ارزیابی خوانده می‌شود. سپس ضابطه آکورد و نسبت مشارکت، مبلغ پیشنهادی را تعیین می‌کنند. اطلاعات ناقص، صفرِ عملکرد فرض نمی‌شود.</p><div class="hrm-workflow-steps">'+performanceActions.map(([key,title])=>'<button type="button" class="quiet" data-go-performance="'+key+'">'+title+'</button>').join('')+'<span>۴. ضابطه و اطلاعات افراد</span><span>۵. محاسبه و نهایی‌سازی</span></div><p class="help">پس از ایجاد ضابطه تازه، از «محاسبه و بررسی» می‌توانید ۸ شاخص پیشنهادی را به دوره خالی اضافه و سپس وزن‌ها و امتیاز افراد را تنظیم کنید. این بخش پرداخت خودکار حقوق انجام نمی‌دهد. آموزش، اطلاعات پزشکی یا حادثه به‌صورت خودکار به کسر امتیاز تبدیل نمی‌شوند.</p></div><div class="hrm-tabs" role="tablist" aria-label="مراحل آکورد بهره‌وری"></div><div id="hrm-productivity-workspace"></div>';
  const operation=can('productivity')?'read':can('productivity','create')?'create':can('productivity','edit')?'edit':'read';
  await scopeFilters(target,viewOwner,'productivity',{operation});if(!current(viewOwner))return;
  target.querySelectorAll('[data-go-performance]').forEach(button=>button.onclick=()=>{sessionStorage.setItem('nikan-hrm-resource-performance',button.dataset.goPerformance);navigateTo('hrm_performance')});
  const storage='nikan-hrm-resource-productivity',stored=sessionStorage.getItem(storage);let selected=tabs.some(([key])=>key===stored)?stored:tabs[0][0],generation=0;
  async function open(key){
   if(workspaceSaving||!current(viewOwner))return;
   selected=key;sessionStorage.setItem(storage,key);const token=++generation;
   target.querySelector('.hrm-tabs').innerHTML=tabs.map(([id,title])=>'<button type="button" role="tab" data-productivity-tab="'+id+'" aria-selected="'+(id===selected)+'" class="'+(id===selected?'active':'quiet')+'">'+esc(title)+'</button>').join('');
   focusSelectedTab(target.querySelector('.hrm-tabs'));
   const filters=target.querySelector('#hrm-filters');if(filters)filters.hidden=key==='calculation';
   const workspace=target.querySelector('#hrm-productivity-workspace'),valid=()=>current(viewOwner)&&token===generation;workspace.innerHTML='<div class="empty" role="status">در حال دریافت اطلاعات…</div>';
   try{if(key==='calculation')await renderAccordCalculation(workspace,viewOwner,valid);else await renderResource(workspace,key,resources[key],viewOwner,valid)}
   catch(err){if(valid()&&err.name!=='AbortError'){workspace.innerHTML='<div class="empty"><h3>دریافت این بخش انجام نشد</h3><p>'+esc(err.message)+'</p><button data-productivity-retry>تلاش مجدد</button></div>';workspace.querySelector('[data-productivity-retry]').onclick=()=>open(key)}}
  }
  target.querySelector('.hrm-tabs').onclick=event=>{const button=event.target.closest('[data-productivity-tab]');if(button)open(button.dataset.productivityTab)};
  await open(selected);
 }
 function accordPercent(value){return Number(value).toLocaleString('fa-IR',{maximumFractionDigits:4});}
 function accordMoney(value){
  if(value===null||value===undefined||value==='')return '—';
  try{return BigInt(String(value)).toLocaleString('fa-IR')}catch{return typeof value==='number'?value.toLocaleString('fa-IR'):String(value)}
 }
 async function renderAccordCalculation(workspace,viewOwner,isCurrent){
  const policies=asRows(await api(scopedHrm('hrm/accord_policies',cid,false)));if(!isCurrent())return;
  const storage='nikan-hrm-accord-policy-'+cid,remembered=sessionStorage.getItem(storage);let chosen=policies.some(policy=>String(policy.id)===remembered)?remembered:policies.length===1?String(policies[0].id):'',generation=0;
  workspace.innerHTML='<div class="panel"><form id="hrm-accord-selection" class="hrm-roster-filter"><label>ضابطه و دوره آکورد<select id="hrm-accord-policy" data-searchable="true"><option value="">ضابطه مورد نظر را انتخاب کنید</option>'+policies.map(policy=>'<option value="'+policy.id+'" '+(chosen===String(policy.id)?'selected':'')+'>'+esc(policy.title+(policy.period_title?' · '+policy.period_title:'')+(policy.status==='finalized'?' · نهایی‌شده':''))+'</option>').join('')+'</select></label><button type="submit" id="hrm-accord-review">محاسبه / تازه‌سازی پیش‌نمایش</button></form><p class="help">محاسبه، تمام افراد ثبت‌شده در همین ضابطه را مبنا قرار می‌دهد. جستجوی نمایشی مبلغ‌ها را تغییر نمی‌دهد. پیش‌نمایش کامل به مجوز مشاهده عملکرد و کل محدوده ضابطه نیاز دارد؛ نتایج نهایی در محدوده مجاز هر کاربر قابل مشاهده‌اند.</p><p id="hrm-accord-error" class="error" role="alert"></p></div><div id="hrm-accord-report"></div>';
  const error=workspace.querySelector('#hrm-accord-error'),reportBox=workspace.querySelector('#hrm-accord-report');
  async function load(){
   if(workspaceSaving||!isCurrent())return;chosen=workspace.querySelector('#hrm-accord-policy').value;sessionStorage.setItem(storage,chosen);const token=++generation;error.textContent='';
   if(!chosen){reportBox.innerHTML='<div class="empty"><h3>ضابطه‌ای انتخاب نشده است</h3><p>ابتدا دوره، شاخص‌ها و امتیازها را تکمیل، سپس ضابطه و افراد مشمول آکورد را ثبت کنید.</p></div>';return;}
   reportBox.innerHTML='<div class="empty" role="status">در حال محاسبه آکورد براساس اطلاعات ثبت‌شده…</div>';
   try{
    const report=await api(scopedHrm('hrm/accord_report?policy_id='+encodeURIComponent(chosen),cid,false));if(!isCurrent()||token!==generation)return;
    const policy=report.policy||policies.find(row=>String(row.id)===chosen)||{},rows=asRows(report.rows),metrics=report.metrics||{},finalized=report.status==='finalized'||policy.status==='finalized';
    const formula=typeof report.formula==='string'?report.formula:policy.method==='pool_budget'?'سهم هر فرد = بودجه کل × امتیاز موزون مشارکت فرد ÷ مجموع امتیازهای موزون مشارکت':'آکورد فرد = مبلغ مبنای فرد × امتیاز عملکرد ÷ ۱۰۰ × نسبت مشارکت';
    const canFinalize=!finalized&&can('productivity','edit')&&can('performance')&&report.can_finalize===true;
    const canSeed=!finalized&&can('productivity','edit')&&can('performance','create');
    reportBox.innerHTML='<div class="panel hrm-accord-summary"><div class="hrm-section-heading"><div><h2>'+esc(policy.title||'نتیجه آکورد')+'</h2><span class="pill">'+(finalized?'نهایی‌شده و محفوظ':'پیش‌نمایش؛ هنوز نهایی نشده')+'</span></div><div class="hrm-action-group">'+(canSeed?'<button type="button" class="quiet" id="hrm-accord-seed">افزودن ۸ شاخص پیشنهادی به دوره خالی</button>':'')+(can('productivity','export')?'<button type="button" class="quiet" id="hrm-accord-export" '+(finalized?'':'disabled title="پس از نهایی‌سازی، خروجی نتایج محفوظ قابل دریافت است"')+'>خروجی Excel نتایج نهایی</button>':'')+(!finalized&&can('productivity','edit')?'<button type="button" id="hrm-accord-finalize" '+(canFinalize?'':'disabled')+'>نهایی‌سازی آکورد</button>':'')+'</div></div><p class="hrm-accord-formula">'+esc(formula)+'</p><p class="help">نسبت مشارکت = دقایق مؤثر تأییدشده ÷ دقایق مبنای دوره. محاسبه ریالی است؛ نتیجه نهایی با جزئیات مبنا نگهداری می‌شود و با ویرایش بعدی امتیازها تغییر نمی‌کند.</p><div class="metrics hrm-accord-metrics">'+[['employees','افراد مشمول',metrics.employees??rows.length,false],['incomplete','اطلاعات ناقص',metrics.incomplete??rows.filter(row=>!row.is_complete).length,false],['total','جمع آکورد (ریال)',metrics.total_accord_rials_exact??metrics.total_accord_rials,true],['unallocated','بودجه تخصیص‌نیافته (ریال)',policy.method==='pool_budget'?(metrics.unallocated_budget_rials_exact??metrics.unallocated_budget_rials):'not_applicable',true]].map(([key,title,value,money])=>'<div class="card"><small>'+title+'</small><strong data-accord-metric="'+key+'">'+(value==='not_applicable'?'کاربرد ندارد':value===null||value===undefined?'قابل محاسبه نیست':money?accordMoney(value):fa(value))+'</strong></div>').join('')+'</div>'+(report.finalize_blockers?.length?'<div class="hrm-accord-blockers"><h3>موارد لازم پیش از نهایی‌سازی</h3><ul>'+report.finalize_blockers.map(item=>'<li>'+esc(typeof item==='string'?item:item.message||item.title||'اطلاعات دوره کامل نیست')+'</li>').join('')+'</ul></div>':'')+(report.policy_metrics&&Number(report.policy_metrics.employees)!==Number(metrics.employees)?'<p class="help">فهرست حاضر در محدوده مجوز شماست؛ ضابطه کامل شامل '+fa(report.policy_metrics.employees)+' فرد است.</p>':'')+'</div><div class="panel"><div class="toolbar"><label>جستجو در نتیجه محاسبه<input id="hrm-accord-search" placeholder="نام، کد، سایت یا واحد" autocomplete="off"></label></div><div id="hrm-accord-results-table"></div></div>';
    let search='',offset=0;
    const detailRows=row=>asRows(row.criteria||row.score_details||row.details?.criteria);
    function draw(){
     const visible=rows.filter(row=>searchMatch([row.employee_name||row.name,row.employee_code||row.code,row.site_name,row.department].join(' '),search));if(offset>=visible.length)offset=Math.max(0,Math.floor((visible.length-1)/25)*25);
     const table=reportBox.querySelector('#hrm-accord-results-table');
     table.innerHTML=visible.length?'<p class="help">'+fa(visible.length)+' فرد در نمایش؛ جستجو در مبلغ محاسبه‌شده تغییری ایجاد نمی‌کند.</p><div class="table-wrap"><table class="hrm-accord-table"><thead><tr><th>کد پرسنلی</th><th>نام فرد / سایت</th><th>امتیاز عملکرد</th><th>تکمیل شاخص‌ها</th><th>مشارکت</th><th>مبلغ مبنا (ریال)</th><th>آکورد (ریال)</th><th>وضعیت و جزئیات</th></tr></thead><tbody>'+visible.slice(offset,offset+25).map(row=>{
      const missing=Array.isArray(row.missing_criteria)?row.missing_criteria.map(item=>typeof item==='string'?item:item.title||item.name||'شاخص بدون امتیاز').join('، '):'';
      const details=detailRows(row),status=({incomplete:'اطلاعات ناقص',ready:'قابل بررسی',below_minimum:'کمتر از حداقل تعیین‌شده',zero_participation:'بدون مشارکت مؤثر',draft:'پیش‌نمایش',finalized:'نهایی‌شده'})[row.status]||(!row.is_complete?'اطلاعات ناقص':finalized?'نهایی‌شده':'قابل بررسی');
      return '<tr data-accord-person="'+esc(row.employee_id)+'"><td>'+esc(row.employee_code||row.code||'—')+'</td><td>'+esc(row.employee_name||row.name||'—')+'<small class="hrm-cell-meta">'+esc([row.site_name,row.department].filter(Boolean).join(' · '))+'</small></td><td>'+(row.score_percent===null||row.score_percent===undefined?'تکمیل نشده':accordPercent(row.score_percent)+'٪')+'</td><td>'+(row.completion_percent===null||row.completion_percent===undefined?(row.is_complete?'کامل':'ناقص'):accordPercent(row.completion_percent)+'٪')+'</td><td>'+(row.participation_percent===null||row.participation_percent===undefined?'ثبت نشده':accordPercent(row.participation_percent)+'٪')+'</td><td>'+accordMoney(row.base_rials_exact??row.base_rials)+'</td><td class="hrm-accord-amount">'+(row.accord_rials===null||row.accord_rials===undefined?'قابل محاسبه نیست':accordMoney(row.accord_rials_exact??row.accord_rials))+'</td><td>'+esc(status)+(missing?'<small class="hrm-cell-meta">تکمیل شود: '+esc(missing)+'</small>':'')+(details.length?'<details><summary>جزئیات شاخص‌ها</summary>'+simpleTable(details,['title','weight','max_score','score','contribution_percent'],{fields:[{key:'title',label:'شاخص'},{key:'weight',label:'وزن'},{key:'max_score',label:'حداکثر'},{key:'score',label:'امتیاز فرد'},{key:'contribution_percent',label:'سهم در امتیاز نهایی'}]})+'</details>':'')+'</td></tr>';
     }).join('')+'</tbody></table></div>'+pager(offset,25,visible.length):'<div class="empty"><h3>فردی برای نمایش وجود ندارد</h3><p>افراد مشمول را در بخش اطلاعات آکورد ثبت کنید یا عبارت جستجو را تغییر دهید.</p></div>';
     bindPager(table,()=>{offset-=25;draw()},()=>{offset+=25;draw()});
    }
    reportBox.querySelector('#hrm-accord-search').oninput=event=>{search=event.target.value;offset=0;draw()};draw();
    reportBox.querySelector('#hrm-accord-export')?.addEventListener('click',async()=>{try{download(await api(scopedHrm('hrm/reports/accord_results.xlsx?policy_id='+encodeURIComponent(chosen),cid,false)),'Nikan-Accord-'+chosen+'.xlsx')}catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}});
    async function mutate(action){
     if(workspaceSaving||!isCurrent())return;
     const question=action==='finalize'?'آکورد تمام افراد این ضابطه نهایی شود؟ مبالغ و مبنای امتیاز به‌صورت نسخه محفوظ ثبت می‌شوند و اطلاعات این ضابطه پس از نهایی‌سازی قابل ویرایش نخواهد بود.':'۸ شاخص پیشنهادی به دوره اضافه شود؟ این کار فقط برای دوره باز و بدون شاخص انجام می‌شود؛ سپس عنوان، وزن و حداکثر امتیاز را مطابق ضابطه سازمان تنظیم کنید.';
     if(!confirm(question))return;const unlock=workspaceWriteLock(workspace);error.textContent='';
     try{await api(scopedHrm('hrm/accord_policies/'+chosen+'/'+action,cid,false),{method:'POST',body:{version:policy.version,...(action==='finalize'?{calculation_token:report.calculation_token}:{})}});if(isCurrent()){unlock();await renderCurrent();notice(action==='finalize'?'آکورد نهایی شد؛ مبنای محاسبه و نتایج محفوظ هستند.':'شاخص‌های پیشنهادی ثبت شدند؛ وزن و امتیاز افراد را در ارزیابی عملکرد بررسی و تکمیل کنید.')}}catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}finally{unlock()}
    }
    reportBox.querySelector('#hrm-accord-finalize')?.addEventListener('click',()=>{if(canFinalize)mutate('finalize')});
    reportBox.querySelector('#hrm-accord-seed')?.addEventListener('click',()=>mutate('seed-criteria'));
   }catch(err){if(isCurrent()&&token===generation&&err.name!=='AbortError'){reportBox.replaceChildren();error.textContent=err.message;}}
  }
  workspace.querySelector('#hrm-accord-policy').onchange=load;workspace.querySelector('#hrm-accord-selection').onsubmit=event=>{event.preventDefault();load()};await load();
 }
 async function renderDashboard(target,viewOwner){
  if(!available()){target.innerHTML='<div class="empty"><h2>نقش منابع انسانی برای این حساب تعریف نشده است</h2></div>';return;}
  const sections=dashboardSections.filter(([key])=>key==='holding'?managesAccess():key==='personnel'?['personnel','contracts','attendance','performance','productivity','discipline','loans','welfare','accommodation'].some(module=>can(module)):can(key)||(key==='health_environment'&&can('health')));
  if(!sections.length){target.innerHTML='<div class="panel"><h2>فضای ثبت سوابق مجاز</h2><p class="help">مجوز مشاهده شاخص‌های این حساب فعال نیست؛ از بخش‌های مجاز برای ثبت سوابق استفاده کنید.</p></div><div class="category-cards hrm-module-cards">'+modules.filter(([key])=>moduleAvailable(key)).map(([key,icon,title])=>'<button class="panel hrm-module-card" data-hrm-open="hrm_'+key+'"><span>'+icon+'</span><strong>'+esc(title)+'</strong></button>').join('')+'</div>';target.querySelectorAll('[data-hrm-open]').forEach(button=>button.onclick=()=>navigateTo(button.dataset.hrmOpen));return;}
  if(!sections.some(([key])=>key===dashboardSection))dashboardSection=sections[0]?.[0]||'personnel';
  const section=dashboardSection;ensureFilters();if(section==='holding'&&activeFilters.all_companies===undefined)activeFilters.all_companies='1';
  const data=await api(scopedHrm('hrm/dashboard?section='+section));if(!current(viewOwner))return;
  const cards=Array.isArray(data.metrics)?data.metrics:Object.entries(data.metrics||{}).map(([key,value])=>({key,label:moduleLabels[key]||key,value}));
  const allowed=modules.filter(([key])=>moduleAvailable(key));
  target.innerHTML='<div class="hero hrm-hero"><div><p class="eyebrow">NIKAN HRM</p><h2>مدیریت یکپارچه سرمایه انسانی نیکان</h2><p>ثبت و پایش سوابق کارکنان توسط مسئولان مجاز هلدینگ، شرکت‌ها و سایت‌ها.</p></div><div class="orb">N</div></div><div class="hrm-tabs" role="tablist" aria-label="داشبوردهای تخصصی">'+sections.map(([key,title])=>'<button type="button" role="tab" data-hrm-dashboard="'+key+'" aria-selected="'+(key===section)+'" class="'+(key===section?'active':'quiet')+'">'+esc(title)+'</button>').join('')+'</div><div class="metrics hrm-metrics">'+(cards.length?cards.map(card=>'<div class="card" data-hrm-metric="'+esc(card.key)+'"><small>'+esc(card.label||card.key)+'</small><strong>'+(card.missing||card.value===null||card.value===undefined?'داده ثبت نشده':typeof card.value==='number'?fa(card.value):esc(card.value))+'</strong><p>'+esc(card.unit||'در محدوده دسترسی شما')+'</p></div>').join(''):'<p class="help">اطلاعات کافی برای محاسبه شاخص این محدوده ثبت نشده است.</p>')+'</div><div id="hrm-alerts"></div><div class="category-cards hrm-module-cards">'+allowed.map(([key,icon,title])=>'<button type="button" class="panel hrm-module-card" data-hrm-open="hrm_'+key+'"><span aria-hidden="true">'+icon+'</span><strong>'+esc(title)+'</strong><small>'+(['create','edit'].some(operation=>can(key,operation))?'ثبت و مشاهده سوابق مجاز':'مشاهده سوابق مجاز')+'</small></button>').join('')+'</div>';
  const filterModule=section==='holding'?'reports':section==='personnel'?['personnel','contracts','attendance','performance','productivity','discipline','loans','welfare','accommodation'].find(module=>can(module))||'personnel':section==='health_environment'&&!can(section)?'health':section;
  await scopeFilters(target,viewOwner,filterModule,{aggregate:true});if(!current(viewOwner))return;
  target.querySelectorAll('[data-hrm-dashboard]').forEach(button=>button.onclick=()=>{dashboardSection=button.dataset.hrmDashboard;renderCurrent()});
  target.querySelectorAll('[data-hrm-open]').forEach(button=>button.onclick=()=>navigateTo(button.dataset.hrmOpen));
  const alerts=target.querySelector('#hrm-alerts');
  for(const [key,title,columns] of [['contract_alerts','قراردادهای نزدیک به پایان',['employee_name','number','end_date','days_remaining']],['hse_alerts','پیگیری‌های ایمنی و تجهیزات حفاظت فردی',['employee_name','ppe_name','missing_quantity','next_due_date','overdue','review_required']],['medical_alerts','سررسید معاینات مجاز',['employee_name','exam_type','next_review_date','status']],['assignment_reviews','بازنگری الزامات پس از تغییر محل فعالیت',['employee_name','target_site_id','activity_location','reason','review_required']]])if(data[key]?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>'+title+'</h2>'+simpleTable(data[key],columns,{person_links:true,person_module:key==='contract_alerts'?'contracts':key==='medical_alerts'?'health':key==='assignment_reviews'?'personnel':'hse'})+'</div>');
  if(data.companies?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>مقایسه شرکت‌های مجاز</h2>'+simpleTable(data.companies,Object.keys(data.companies[0]).filter(key=>key!=='id'&&!key.endsWith('_id')),{fields:[{key:'name',label:'شرکت'}]})+'</div>');
  if(data.sites?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>مقایسه سایت‌های مجاز</h2>'+simpleTable(data.sites,Object.keys(data.sites[0]).filter(key=>key!=='id'&&!key.endsWith('_id')),{fields:[{key:'name',label:'سایت'},{key:'company_name',label:'شرکت'}]})+'</div>');
  if(data.data_quality?.length)alerts.insertAdjacentHTML('beforeend','<div class="panel"><h2>اطلاعات ناقص و کیفیت داده‌ها</h2><ul>'+data.data_quality.map(item=>'<li>'+esc(typeof item==='string'?item:item.message||item.label||item.title||JSON.stringify(item))+'</li>').join('')+'</ul></div>');
 }
 function simpleTable(rows,keys,schema={}){
  if(!rows?.length)return '<p class="help">رکوردی در این محدوده ثبت نشده است.</p>';
  return `<div class="table-wrap"><table><thead><tr>${keys.map(key=>`<th>${esc(fieldLabel(schema,key))}</th>`).join('')}${schema.person_links?'<th>پرونده</th>':''}</tr></thead><tbody>${rows.map(row=>`<tr>${keys.map(key=>`<td>${esc(cellValue(schema,key,row[key],row))}</td>`).join('')}${schema.person_links?'<td>'+personLink(row,schema.person_module||'',schema.person_resource||'')+'</td>':''}</tr>`).join('')}</tbody></table></div>`;
 }
 async function renderResource(workspace,key,schema,viewOwner,isCurrent){
  const readable=schemaCan(schema,'read'),creatable=schemaCan(schema,'create'),exportable=schemaCan(schema,'export');
  const includeArchived=archiveViews.has(key),historyEnabled=schema.deletable===true;
  const response=readable?await api(scopedHrm('hrm/'+key+(includeArchived?'?include_deleted=1':''))):[];if(!isCurrent())return;
  const rows=asRows(response).filter(row=>!inPersonWorkspace()||!row.employee_id||Number(row.employee_id)===Number(personWorkspace.id));
  const columns=(schema.columns||schema.fields?.map(field=>field.key)||[]).map(field=>typeof field==='string'?field:field.key);
  const editable=schema.editable!==false&&schemaCan(schema,'edit'),deletable=schema.deletable===true&&schemaCan(schema,'delete');
  workspace.innerHTML=`${schema.help?'<p class="help hrm-resource-help">'+esc(schema.help)+'</p>':''}<div class="toolbar hrm-toolbar"><label>جستجو در ${esc(schema.title)}<input id="hrm-search" autocomplete="off" placeholder="نام، کد یا متن مورد نظر"></label><div>${creatable&&schema.creatable!==false?'<button type="button" id="hrm-add">＋ ثبت '+esc(schema.singular||schema.title)+'</button>':''}${exportable?'<button type="button" class="quiet" id="hrm-export">خروجی Excel</button>':''}${key==='attendance'&&creatable?'<button type="button" class="quiet" id="hrm-attendance-import">ورود از Excel</button>':''}</div></div>${readable&&historyEnabled?'<label class="check hrm-archive-filter"><input type="checkbox" id="hrm-include-archived" '+(includeArchived?'checked':'')+'> نمایش سوابق بایگانی‌شده و ابطال‌شده</label>':''}<div id="hrm-row-status" class="help" role="status"></div><div id="hrm-table"></div><div id="hrm-related-report"></div>`;
  workspace.querySelector('#hrm-include-archived')?.addEventListener('change',event=>{if(event.target.checked)archiveViews.add(key);else archiveViews.delete(key);renderCurrent()});
  const table=workspace.querySelector('#hrm-table'),status=workspace.querySelector('#hrm-row-status');let query='',offset=0;
  const draw=()=>{
   const filtered=rows.filter(row=>Object.entries(row).some(([field,value])=>field!=='content'&&searchMatch(typeof value==='object'?JSON.stringify(value):cellValue(schema,field,value,row),query)));
   if(offset>=filtered.length)offset=Math.max(0,Math.floor((filtered.length-1)/25)*25);
   status.textContent=readable?fa(filtered.length)+' رکورد در محدوده مجاز':'این حساب مجوز مشاهده سوابق این بخش را ندارد.';
   const actions=editable||deletable||(key==='contracts'&&creatable)||key==='documents'||key==='people'||rows.some(row=>row.employee_id)||(key==='accord_policies'&&readable);
   table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr>${columns.map(field=>`<th>${esc(fieldLabel(schema,field))}</th>`).join('')}${historyEnabled?'<th>وضعیت سابقه</th>':''}${actions?'<th>عملیات</th>':''}</tr></thead><tbody>${filtered.slice(offset,offset+25).map(row=>`<tr data-hrm-row="${esc(row.id??row.employee_id)}">${columns.map(field=>`<td>${esc(cellValue(schema,field,row[field],row))}</td>`).join('')}${historyEnabled?'<td>'+esc(row.is_deleted?'بایگانی‌شده':row.status==='cancelled'||row.status==='voided'?'ابطال‌شده':'فعال')+'</td>':''}${actions?`<td class="hrm-row-actions">${schema.restorable&&editable&&row.is_deleted&&!accordLocked(key,row)?`<button type="button" class="quiet" data-hrm-restore="${esc(row.id)}">بازیابی سابقه</button>`:''}${editable&&!row.is_deleted&&!accordLocked(key,row)&&(key!=='documents'||can(row.permission_group,'edit'))?`<button type="button" class="quiet" data-hrm-edit="${esc(row.id??row.employee_id)}">ویرایش</button>`:''}${deletable&&!row.is_deleted&&!accordLocked(key,row)?`<button type="button" class="quiet" data-hrm-delete="${esc(row.id)}">حذف / ابطال</button>`:''}${key==='contracts'&&creatable&&!row.is_deleted?`<button type="button" class="quiet" data-hrm-renew="${esc(row.id)}">ثبت تمدید</button>`:''}${key==='personnel_actions'&&editable&&!row.applied?`<button type="button" class="quiet" data-hrm-apply="${esc(row.id)}">اجرای حکم</button>`:''}${key==='documents'?`<button type="button" class="quiet" data-hrm-file="${esc(row.id)}">دریافت فایل</button>`:''}${key==='accord_policies'&&readable?`<button type="button" class="quiet" data-hrm-accord-review="${esc(row.id)}">محاسبه و بررسی</button>`:''}${(key==='people'||row.employee_id)&&!inPersonWorkspace()?personLink(row,schema.module,key):''}</td>`:''}</tr>`).join('')}</tbody></table></div>${pager(offset,25,filtered.length)}`:'<div class="empty"><h2>رکوردی پیدا نشد</h2><p>'+esc(query?'عبارت دیگری جستجو کنید.':'برای این محدوده هنوز اطلاعاتی ثبت نشده است.')+'</p></div>';
   bindPager(table,()=>{offset-=25;draw()},()=>{offset+=25;draw()});
  };
  workspace.querySelector('#hrm-search').oninput=event=>{query=event.target.value;offset=0;draw()};draw();
  workspace.querySelector('#hrm-add')?.addEventListener('click',()=>openEditor(key,schema,null,viewOwner,{isCurrent}));
  if(exportable)workspace.querySelector('#hrm-export').onclick=async()=>{try{download(await api(scopedHrm('hrm/reports/'+key+'.xlsx'+(includeArchived?'?include_deleted=1':''))),'Nikan-HRM-'+key+'.xlsx')}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
  workspace.querySelector('#hrm-attendance-import')?.addEventListener('click',()=>attendanceImport(viewOwner));
  table.onclick=async event=>{
   if(workspaceSaving||!isCurrent())return;
   const edit=event.target.closest('[data-hrm-edit]'),renew=event.target.closest('[data-hrm-renew]'),file=event.target.closest('[data-hrm-file]'),apply=event.target.closest('[data-hrm-apply]'),remove=event.target.closest('[data-hrm-delete]'),restore=event.target.closest('[data-hrm-restore]'),training=event.target.closest('[data-hrm-training]'),accord=event.target.closest('[data-hrm-accord-review]');
   const id=edit?.dataset.hrmEdit||renew?.dataset.hrmRenew||file?.dataset.hrmFile||apply?.dataset.hrmApply||remove?.dataset.hrmDelete||restore?.dataset.hrmRestore||training?.dataset.hrmTraining||accord?.dataset.hrmAccordReview;
   const row=rows.find(item=>String(item.id??item.employee_id)===String(id));if(!row)return;
   try{
    if(accord){sessionStorage.setItem('nikan-hrm-accord-policy-'+cid,String(row.id));sessionStorage.setItem('nikan-hrm-resource-productivity','calculation');return renderCurrent();}
    if(edit)return openEditor(key,schema,row,viewOwner,{isCurrent});
    if(renew)return openEditor(key,schema,row,viewOwner,{renew:true,isCurrent});
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
  if(inPersonWorkspace()&&field.key==='employee_id')return [[personWorkspace.id,(personWorkspace.overview?.person?.name||'فرد انتخاب‌شده')+' · '+(personWorkspace.overview?.person?.code||'')]];
  if(field.type==='employee'||field.key==='employee_id'||field.key==='manager_id'){
   const rows=asRows(await api(scopedHrm('hrm/people?module='+encodeURIComponent(schema.module)+operationQuery,referenceCompany,false)));
   if(!current(viewOwner))throw staleRequest();const options=rows.map(row=>[row.id??row.employee_id,(row.name||row.employee_name)+' · '+row.code+(row.department?' · '+row.department:'')]);options.records=rows;return options;
  }
  if(field.type==='resource'||field.resource){
   let path='hrm/'+(field.resource||field.reference);
   const filters={...(field.filters||{})};if(inPersonWorkspace()&&['loans','contracts','documents','accord_entries','accord_results'].includes(field.resource||field.reference))filters.employee_id=String(personWorkspace.id);if(['sites','units'].includes(field.resource||field.reference))filters.module=schema.module;if(['sites','units'].includes(field.resource||field.reference)||(field.resource||field.reference||'').endsWith('_options')){filters.operation=operation;filters.module=schema.module;}if((field.resource||field.reference)==='documents'&&!filters.permission_group&&schema.module!=='documents')filters.permission_group=schema.module;
   const parameters=new URLSearchParams(filters);if(parameters.size)path+='?'+parameters.toString();
   let result;try{result=await api(scopedHrm(path,referenceCompany,false))}catch(err){
    if(err.status!==403)throw err;
    if((field.resource||field.reference)==='documents'){if(!field.required)return [];throw err;}
    try{const fallbackQuery=new URLSearchParams({...filters,module:schema.module,operation});result=await api(scopedHrm('hrm/options/'+encodeURIComponent(field.resource||field.reference)+'?'+fallbackQuery.toString(),referenceCompany,false))}catch(fallback){if(fallback.status===403&&!field.required)return [];throw fallback;}
   }const rows=asRows(result).filter(row=>!inPersonWorkspace()||(field.resource||field.reference)!=='accord_policies'||(Number(row.company_id)===Number(personWorkspace.overview?.person?.company_id)&&(!row.site_id||Number(row.site_id)===Number(personWorkspace.overview?.person?.site_id))));if(!current(viewOwner))throw staleRequest();
   const options=rows.map(row=>[row[field.value_key]??row.value??row.id??row.employee_id,[row[field.label_key]||row.label||row.title||row.name||row.number||row.employee_name||row.code||('#'+row.id),row.employee_name&&row.title?row.employee_name:'',(field.resource||field.reference)==='sites'?user.companies?.find(company=>Number(company.id)===Number(row.company_id))?.name:'',row.start_date?dateInput(row.start_date):'',row.amount_rials?fa(row.amount_rials)+' ریال':''].filter(Boolean).join(' · ')]);options.records=rows;return options;
  }
  return null;
 }
 async function openEditor(key,schema,row,viewOwner,options={}){
  if(workspaceSaving||!current(viewOwner)||ensureDialog().open||accordLocked(key,row)||!schemaCan(schema,options.renew||!row?'create':'edit'))return;
  const preparation=++editorGeneration,pinned=inPersonWorkspace()?{identity:personWorkspace,id:personWorkspace.id,module:personWorkspace.module,resource:personWorkspace.resource}:null;
  const contextValid=()=>preparation===editorGeneration&&current(viewOwner)&&(!options.isCurrent||options.isCurrent())&&(!pinned||(inPersonWorkspace()&&personWorkspace===pinned.identity&&personWorkspace.id===pinned.id&&personWorkspace.module===pinned.module&&personWorkspace.resource===pinned.resource));
  const isRenew=Boolean(options.renew),operation=isRenew||!row?'create':'edit',notesOnly=Boolean(row&&!isRenew&&((key==='contracts'&&row.status==='renewed')||(key==='personnel_actions'&&row.applied))),fields=(schema.fields||[]).filter(field=>!field.readonly&&!['id','version','created','updated'].includes(field.key)&&(!notesOnly||field.key==='notes')&&(!(key==='documents'&&row)||['title','kind'].includes(field.key))).map(field=>field.choices_by_operation?.[operation]?{...field,choices:field.choices_by_operation[operation]}:field);
  const dossierGroup=inPersonWorkspace()?personWorkspace.resourceFilters.permission_group:null;
  if(key==='documents'&&dossierGroup){const index=fields.findIndex(field=>field.key==='permission_group');if(index>=0)fields[index]={...fields[index],choices:[[dossierGroup,moduleLabels[dossierGroup]||dossierGroup]],choices_by_operation:undefined};}
  const referenceSchema=key==='documents'?{...schema,module:row?.permission_group||dossierGroup||choicesOf(fields.find(field=>field.key==='permission_group')||{})[0]?.[0]||schema.module}:schema,optionsByField={};
  try{
   await Promise.all(fields.map(async field=>{optionsByField[field.key]=await referenceOptions(field,referenceSchema,viewOwner,options.renew||!row?'create':'edit',key==='personnel_actions'&&row?.target_company_id&&['sites','units'].includes(field.resource||field.reference)?row.target_company_id:undefined)}));
   if(!contextValid())return;
   const values={...(row||{})};if(key==='accord_policies'&&!row)values.method='individual_base';if(key==='accord_entries'&&!row)values.policy_id=sessionStorage.getItem('nikan-hrm-accord-policy-'+cid)||'';if(key==='documents'&&!row)values.permission_group=choicesOf(fields.find(field=>field.key==='permission_group')||{})[0]?.[0]||schema.permission_groups?.[0]||'documents';if(!row){for(const field of fields){if(field.key==='company_id')values.company_id=cid;if(field.key==='site_id'&&activeFilters.site_id)values.site_id=activeFilters.site_id;if(field.key==='employee_id'&&activeFilters.employee_id)values.employee_id=activeFilters.employee_id;}}
   if(isRenew){for(const key of ['number','start_date','end_date','issued_date','signed_date','notes'])if(key in values)values[key]='';delete values.parent_contract_id;}
   for(const field of fields)if((/_rials$/.test(field.key)||field.type==='money')&&row?.[field.key+'_exact']!==undefined&&row?.[field.key+'_exact']!==null)values[field.key]=String(row[field.key+'_exact']);
   if(inPersonWorkspace()){
    if(fields.some(field=>field.key==='employee_id'))values.employee_id=personWorkspace.id;
    if(key==='documents'&&!row&&dossierGroup)values.permission_group=dossierGroup;
    for(const [name,value] of Object.entries(personWorkspace.initialFilters||{}))if(fields.some(field=>field.key===name))values[name]=value;
   }
   const markup=fields.map(field=>fieldHtml(field,values[field.key],optionsByField[field.key],Boolean((inPersonWorkspace()&&(field.key==='employee_id'||key==='documents'&&field.key==='permission_group'&&dossierGroup))||(row&&!isRenew&&(field.immutable||(key==='accord_policies'&&['company_id','site_id','period_id'].includes(field.key))||(key==='accord_entries'&&['policy_id','employee_id'].includes(field.key))||(['employee_id','loan_id'].includes(field.key)&&['loans','loan_payments','reservations','welfare_uses'].includes(key))))))).join('');
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
   function bindAccordPolicy(){
    if(key!=='accord_policies')return;
    const period=form.elements.namedItem('period_id'),site=form.elements.namedItem('site_id'),record=optionsByField.period_id?.records?.find(item=>String(item.id)===String(period?.value));
    if(site){site.dataset.readonly='true';if(record)site.value=record.site_id?String(record.site_id):'';site.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));}
    const budget=form.elements.namedItem('budget_rials'),label=budget?.closest('label'),pooled=form.elements.namedItem('method')?.value==='pool_budget';if(budget&&label){label.hidden=!pooled;budget.disabled=!pooled;budget.required=pooled;}
   }
   form.elements.namedItem('period_id')?.addEventListener('change',bindAccordPolicy);form.elements.namedItem('method')?.addEventListener('change',bindAccordPolicy);bindAccordPolicy();
   function restrictAccordInput(){
    if(key!=='accord_entries')return;
    const policySelect=form.elements.namedItem('policy_id'),policy=optionsByField.policy_id?.records?.find(item=>String(item.id)===String(policySelect?.value));
    const personSelect=form.elements.namedItem('employee_id'),personOptions=optionsByField.employee_id;
    if(personSelect&&personOptions?.records){const previous=personSelect.value,allowed=personOptions.records.filter(person=>!policy||((!policy.company_id||Number(person.company_id)===Number(policy.company_id))&&(!policy.site_id||Number(person.site_id)===Number(policy.site_id))));personSelect.innerHTML='<option value="">انتخاب کنید</option>'+allowed.map(person=>'<option value="'+(person.id??person.employee_id)+'" '+(String(person.id??person.employee_id)===previous?'selected':'')+'>'+esc((person.name||person.employee_name)+' · '+person.code+(person.site_name?' · '+person.site_name:''))+'</option>').join('');personSelect.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));}
    const base=form.elements.namedItem('base_rials'),label=base?.closest('label');if(base&&label){const pooled=policy?.method==='pool_budget';label.hidden=pooled;base.disabled=pooled;base.required=Boolean(policy&&!pooled);}
   }
   form.elements.namedItem('policy_id')?.addEventListener('change',restrictAccordInput);restrictAccordInput();
   const scheduled=form.elements.namedItem('scheduled_minutes'),actual=form.elements.namedItem('actual_minutes');if(key==='accord_entries'&&scheduled&&actual){const bounds=()=>{scheduled.step='1';actual.step='1';actual.max=scheduled.value||'';};scheduled.addEventListener('input',bounds);bounds();}
   let referenceGeneration=0,referenceBusy=false,lastReferenceCompany=Number(values.target_company_id||values.company_id||cid);
   (form.elements.namedItem('target_company_id')||form.elements.namedItem('company_id'))?.addEventListener('change',async event=>{
    const company=Number(event.target.value);if(!company||company===lastReferenceCompany)return;lastReferenceCompany=company;const generation=++referenceGeneration;referenceBusy=true;form.querySelector('#hrm-save').disabled=true;
    try{
     for(const field of fields.filter(item=>item.type==='site'||['sites','units'].includes(item.resource||item.reference)||(key==='accord_policies'&&(item.resource||item.reference)==='evaluation_periods'))){
      const choices=await referenceOptions(field,referenceSchema,viewOwner,options.renew||!row?'create':'edit',company);
      if(!current(formOwner)||generation!==referenceGeneration||!dialog.open)return;
      optionsByField[field.key]=choices;const select=form.elements.namedItem(field.key);
      if(select){select.innerHTML='<option value="">انتخاب نشده</option>'+(choices||[]).map(([id,title])=>'<option value="'+esc(id)+'">'+esc(title)+'</option>').join('');select.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));}
     }
     restrictUnits();bindAccordPolicy();
    }catch(err){if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message;}
    finally{if(generation===referenceGeneration){referenceBusy=false;if(dialog.open&&!editorBusy)form.querySelector('#hrm-save').disabled=false;}}
   });
   form.onsubmit=async event=>{
    event.preventDefault();if(editorBusy||referenceBusy||!current(formOwner)||!contextValid())return;
    const payload={};for(const field of fields){const input=form.elements.namedItem(field.key);if(!input||input.disabled)continue;let value=input.value;if(value===''&&!field.required&&!row)continue;if(/_rials$/.test(field.key)||field.type==='money')value=value===''?null:value;else if(['number','integer','boolean','checkbox'].includes(field.type))value=value===''?null:Number(value);else if(['employee','company'].includes(field.type)||/_id$/.test(field.key))value=value===''?null:Number(value);payload[field.key]=value;}
    if(inPersonWorkspace()&&fields.some(field=>field.key==='employee_id'))payload.employee_id=personWorkspace.id;
    if(key==='accord_policies'&&payload.method==='individual_base')payload.budget_rials=null;
    if(row?.version!==undefined)payload.version=row.version;
    if(isRenew){delete payload.employee_id;delete payload.company_id;delete payload.parent_contract_id;}
    editorBusy=true;error.textContent='';const unlock=workspaceWriteLock(dialog);
    try{
     const path=scopedHrm('hrm/'+key+(isRenew?'/'+row.id+'/renew':row?'/'+(row.id??row.employee_id):''),cid,false);
     let body=payload;
     if(upload){body=new FormData();for(const [name,value] of Object.entries(payload))if(value!==null&&value!==undefined)body.append(name,String(value));body.append('file',form.elements.namedItem('file').files[0]);}
     const saved=await api(path,{method:isRenew||!row?'POST':'PUT',body});
     if(key==='accord_policies'&&!row&&saved?.id){sessionStorage.setItem('nikan-hrm-accord-policy-'+cid,String(saved.id));sessionStorage.setItem('nikan-hrm-resource-productivity','calculation');}
     if(!current(formOwner))return;editorDirty=false;dialog.close();unlock();await renderCurrent();notice(isRenew?'تمدید قرارداد به‌عنوان نسخه جدید ثبت شد.':'اطلاعات ثبت شد؛ سابقه تغییر در سامانه نگهداری می‌شود.');
    }catch(err){if(current(formOwner)&&err.name!=='AbortError')error.textContent=err.message}
    finally{unlock();editorBusy=false;}
   };
  }catch(err){if(contextValid()&&err.name!=='AbortError')notice(err.message)}
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
