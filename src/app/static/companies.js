// Company names and membership are managed globally by the holding administrator.
menus.push(['companies','▣','مدیریت شرکت‌ها']);
const companiesRender=render;render=async function(){
 if(page!=='companies')return companiesRender();
 workspaceHeading();if(user.role!=='admin'){notice('مدیریت شرکت‌ها فقط در اختیار ادمین هلدینگ است.');return}
 const account=user,version=navigationVersion,rows=await api('companies');
 if(user!==account||page!=='companies'||version!==navigationVersion)return;
 setCompanyChoices(rows);
 const section=$('#content');section.onclick=null;
 section.innerHTML='<div class="panel"><h2>شرکت‌های هلدینگ</h2><p class="help">شرکت جدید را ثبت کنید یا نام شرکت‌های موجود را اصلاح کنید. تغییر نام، شناسه شرکت و اطلاعات آن را حفظ می‌کند. حذف تنها برای شرکتی امکان‌پذیر است که پرسنل، کاربر یا اطلاعات آموزشی و شغلی متصل نداشته باشد.</p></div><div class="toolbar"><label>جستجوی شرکت<input id="company-search" placeholder="نام یا شناسه شرکت" autocomplete="off"></label><button type="button" id="company-add">＋ ثبت شرکت جدید</button></div><p id="company-error" class="error" role="alert"></p><div id="companies-table"></div>';
 const table=$('#companies-table'),error=$('#company-error'),search=$('#company-search'),isCurrent=()=>user===account&&page==='companies'&&$('#companies-table')===table;
 let companies=rows,query='',offset=0,deleting=false;
 function draw(){
  const filtered=companies.filter(company=>[company.id,company.name].some(value=>searchMatch(value,query)));if(offset>=filtered.length)offset=Math.max(0,Math.floor((filtered.length-1)/20)*20);
  table.innerHTML=filtered.length?`<div class="table-wrap"><table><thead><tr><th>شناسه شرکت</th><th>نام شرکت</th><th>عملیات</th></tr></thead><tbody>${filtered.slice(offset,offset+20).map(company=>`<tr data-company-row="${company.id}"><td>${esc(company.id)}</td><td>${esc(company.name)}</td><td><button type="button" class="quiet" data-company-edit="${company.id}">ویرایش نام</button> <button type="button" class="quiet" data-company-delete="${company.id}">حذف شرکت</button></td></tr>`).join('')}</tbody></table></div>${pager(offset,20,filtered.length)}`:`<div class="empty"><h2>${companies.length?'شرکتی با این جستجو پیدا نشد':'هنوز شرکتی ثبت نشده است'}</h2><p>${companies.length?'نام یا شناسه دیگری جستجو کنید.':'برای شروع کار با پرسنل و آموزش‌ها، نخستین شرکت را ثبت کنید.'}</p></div>`;
  bindPager(table,()=>{offset-=20;draw()},()=>{offset+=20;draw()});
 }
 function applyCompanies(next){companies=next.sort((a,b)=>Number(a.id)-Number(b.id));setCompanyChoices(companies);draw()}
 async function refreshCompanies(){const latest=await api('companies');if(isCurrent())applyCompanies(latest)}
 function editCompany(company=null){
  if(!isCurrent()||workspaceSaving||deleting)return;error.textContent='';
  workspaceModal(company?'ویرایش نام شرکت':'ثبت شرکت جدید',['name'],{name:company?.name||''},async data=>{
   if(!isCurrent())throw Error('صفحه شرکت‌ها تغییر کرده است؛ فرم را دوباره باز کنید.');
   const result=await api('companies'+(company?'/'+company.id:''),{method:company?'PUT':'POST',body:{name:data.name}});
   if(!isCurrent())return;
   const next=company?companies.map(row=>Number(row.id)===Number(company.id)?result:row):[...companies,result];
   query='';search.value='';offset=0;applyCompanies(next);
   return {refresh:refreshCompanies,message:company?'نام شرکت اصلاح شد؛ اطلاعات و شناسه آن حفظ شد.':'شرکت جدید ثبت شد و به فهرست شرکت‌های هلدینگ اضافه شد.'};
  });
  const input=$('#fields [name=name]');if(input){input.required=true;input.maxLength=200;input.autocomplete='organization';input.parentElement.firstChild.textContent='نام شرکت';input.focus()}
 }
 draw();search.oninput=e=>{query=e.target.value;offset=0;draw()};$('#company-add').onclick=()=>editCompany();
 table.onclick=async e=>{
  if(!isCurrent()||workspaceSaving||deleting)return;
  const edit=e.target.closest('[data-company-edit]'),remove=e.target.closest('[data-company-delete]'),company=companies.find(row=>Number(row.id)===Number(edit?.dataset.companyEdit||remove?.dataset.companyDelete));
  if(!company)return;if(edit){editCompany(company);return}
  if(!remove||!confirm('شرکت «'+company.name+'» حذف شود؟ حذف فقط برای شرکت بدون اطلاعات و کاربر متصل امکان‌پذیر است.'))return;
  deleting=true;error.textContent='';const unlock=workspaceWriteLock(section);
  try{await api('companies/'+company.id,{method:'DELETE'});if(isCurrent()){applyCompanies(companies.filter(row=>Number(row.id)!==Number(company.id)));notice('شرکت بدون اطلاعات متصل حذف شد.')}}
  catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}
  finally{deleting=false;unlock();if(isCurrent())updateCompanyAvailability()}
 };
};
