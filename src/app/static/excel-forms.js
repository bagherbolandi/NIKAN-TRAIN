// Every workbook is generated and validated by the server for the selected company.
let excelPreferredKind=null;
const excelPageKinds={employees:'employees',requirements:'job_profiles',job_profiles:'job_profiles',assessments:'assessments',courses:'courses',records:'records',instructors:'instructors',needs:'needs',passports:'personal_documents',companies:'companies'};
function excelKindForPage(){return page==='bank'?(user.role==='admin'?'competency-bank':'competency_selection'):excelPageKinds[page]}
function openExcelForms(kind){if(workspaceSaving)return;excelPreferredKind=kind;return navigateTo('import')}
const excelRender=render;render=async function(){
 const ownerPage=page,ownerCid=Number(cid),account=user;
 if(page==='import'){workspaceHeading();return renderExcelForms($('#content'))}
 await excelRender();if(user!==account||page!==ownerPage||Number(cid)!==ownerCid)return;
 const kind=excelKindForPage();if(!kind||!cid||$('#open-excel-forms'))return;
 const bar=document.createElement('div');bar.className='toolbar';bar.innerHTML=`<button type="button" class="quiet" id="open-excel-forms">${['requirements','job_profiles'].includes(page)?'قالب شناسنامه کامل شغل':'قالب و ورود اکسل این بخش'}</button>${['requirements','job_profiles'].includes(page)?'<button type="button" class="quiet" id="open-excel-requirements">قالب ماتریس الزامات شغل</button>':''}<span class="help">دریافت فایل استاندارد، بررسی ردیف‌ها و ثبت گروهی اطلاعات</span>`;$('#content').prepend(bar);bar.querySelector('#open-excel-forms').onclick=()=>openExcelForms(kind);const requirements=bar.querySelector('#open-excel-requirements');if(requirements)requirements.onclick=()=>openExcelForms('requirements');
};
async function renderExcelForms(section){
 const account=user,ownerCid=Number(cid),version=navigationVersion,forms=await api('excel/forms?company_id='+ownerCid);
 if(user!==account||page!=='import'||Number(cid)!==ownerCid||version!==navigationVersion)return;
 if(!Array.isArray(forms)||!forms.length)throw Error('قالب اکسل قابل استفاده‌ای برای این شرکت یافت نشد.');
 let kind=forms.find(form=>form.kind===excelPreferredKind)?.kind||forms[0].kind,preview=null,previewFile=null,previewKind='',previewPolicy='',busy=false;
 const canWrite=user.role!=='viewer';
 section.onclick=null;section.innerHTML=`<div class="panel"><h2>قالب‌های استاندارد اکسل هلدینگ</h2><p class="help">۱. قالب بخش موردنظر را دریافت و در اکسل تکمیل کنید. ۲. فایل را بررسی کنید و نتیجه و خطاهای ردیف‌ها را ببینید. ۳. پس از بررسی موفق، ثبت نهایی را بزنید. قالب با شرکت انتخاب‌شده و فهرست‌های مرجع همان شرکت تهیه می‌شود و از لوگوی ثبت‌شده هلدینگ استفاده می‌کند.</p><p class="help">ورود فایل هیچ رکوردی را حذف نمی‌کند. بررسی فایل اطلاعات را ذخیره نمی‌کند؛ ثبت نهایی همه ردیف‌ها را دوباره کنترل و یکجا ذخیره می‌کند.</p></div><form id="excel-form" class="panel pro-form"><label>نوع فرم<select id="excel-kind" name="kind" required data-searchable="true">${forms.map(form=>`<option value="${esc(form.kind)}" ${form.kind===kind?'selected':''}>${esc(form.title)}</option>`).join('')}</select></label><div class="toolbar"><button type="button" id="excel-download">دریافت قالب استاندارد</button></div><p id="excel-description" class="help wide"></p>${canWrite?'<label>روش ورود اطلاعات<select id="excel-policy" name="policy"><option value="insert">فقط افزودن رکوردهای جدید</option><option value="upsert">افزودن و به‌روزرسانی رکوردهای موجود</option></select></label><label>فایل تکمیل‌شده XLSX<input id="excel-file" name="file" type="file" accept=".xlsx" required></label><div class="toolbar wide"><button type="submit" id="excel-preview">بررسی فایل</button><button type="button" id="excel-commit" disabled>ثبت نهایی اطلاعات</button></div>':'<p class="help wide">حساب مشاهده‌گر می‌تواند قالب‌ها را دریافت کند؛ ورود یا تغییر اطلاعات برای این نقش فعال نیست.</p>'}<p id="excel-error" class="error wide" role="alert"></p></form><div id="excel-preview-result" aria-live="polite"></div><details class="panel"><summary>ستون‌ها و راهنمای فرم انتخاب‌شده</summary><div id="excel-columns"></div></details><details class="panel" id="excel-catalog"><summary>همه قالب‌های قابل دسترس</summary><div class="category-cards">${forms.map(form=>`<div class="panel"><h3>${esc(form.title)}</h3><p class="help">${esc(form.description||'')}</p><button type="button" class="quiet" data-excel-template="${esc(form.kind)}">دریافت قالب</button></div>`).join('')}</div></details>${canWrite?'<details class="panel" id="excel-legacy"><summary>ورود فایل قدیمی مبنا — سازگاری نسخه‌های پیشین</summary><p class="help">این مسیر فقط برای فایل مبنای قدیمی چندبرگی سامانه است. برای فایل‌های جدید از قالب استاندارد و بررسی ردیف‌ها در بالای صفحه استفاده کنید.</p><div id="excel-legacy-content"></div></details>':''}`;
 const form=$('#excel-form'),kindSelect=$('#excel-kind'),fileInput=$('#excel-file'),policySelect=$('#excel-policy'),commit=$('#excel-commit'),error=$('#excel-error'),resultBox=$('#excel-preview-result');
 const isCurrent=()=>user===account&&page==='import'&&Number(cid)===ownerCid&&$('#excel-form')===form;
 const selected=()=>forms.find(item=>item.kind===kind);
 const eligible=()=>canWrite&&selected()?.can_import!==false;
 const canCommit=()=>eligible()&&preview?.ok&&preview?.can_commit&&!preview.errors?.length&&previewFile===fileInput?.files[0]&&previewKind===kind&&previewPolicy===policySelect?.value;
 function updateCommit(){if(commit)commit.disabled=busy||!canCommit()}
 function invalidate(){preview=null;previewFile=null;previewKind='';previewPolicy='';resultBox.replaceChildren();error.textContent='';updateCommit()}
 function describe(){
  const descriptor=selected();$('#excel-description').textContent=descriptor.description||'';
  const fields=descriptor.columns||[];
  $('#excel-columns').innerHTML=`<div class="table-wrap"><table><thead><tr><th>ستون</th><th>وضعیت</th><th>مقادیر و راهنما</th></tr></thead><tbody>${fields.map(field=>`<tr><td>${esc(field.label)}</td><td>${field.required?'الزامی':'اختیاری'}</td><td>${esc(field.choices?.map(choice=>choice.label+' ('+choice.value+')').join('، ')||(field.reference?'انتخاب از فهرست مرجع داخل فایل':field.type==='date'?'تاریخ شمسی با قالب 1405/03/25':field.type==='number'||field.type==='integer'?'عدد':'متن'))}</td></tr>`).join('')}</tbody></table></div>`;
  if(fileInput){fileInput.disabled=!eligible();$('#excel-preview').disabled=!eligible();policySelect.disabled=!eligible()}
  updateCommit();
 }
 function showResult(result){
  const problems=Array.isArray(result.errors)?result.errors:[],committed=result.mode==='commit'&&result.ok;
  resultBox.innerHTML=`<div class="panel"><h2>${committed?'ثبت نهایی انجام شد':result.ok?'بررسی موفق؛ آماده ثبت نهایی':'فایل نیاز به اصلاح دارد'}</h2><div class="metrics">${[['ردیف‌های بررسی‌شده',result.total],['افزوده',result.created],['به‌روزرسانی',result.updated],['بدون تغییر',result.unchanged]].map(([label,value])=>`<div class="card"><small>${label}</small><strong>${Number(value||0).toLocaleString('fa-IR')}</strong></div>`).join('')}</div><p class="help">${committed?'همه تغییرات یکجا ذخیره شدند. هیچ رکوردی حذف نشد.':result.ok?'این نتیجه فقط بررسی فایل است و هنوز تغییری ذخیره نشده است. برای ذخیره، ثبت نهایی را بزنید.':'هیچ ردیفی ذخیره نشده است. خطاها را در فایل اصلاح و فایل را دوباره انتخاب و بررسی کنید.'}</p>${problems.length?`<div class="table-wrap"><table><thead><tr><th>ردیف اکسل</th><th>خطا</th></tr></thead><tbody>${problems.slice(0,50).map(problem=>`<tr><td>${esc(problem.row??'—')}</td><td>${esc(problem.message||'ردیف نامعتبر')}</td></tr>`).join('')}</tbody></table></div>${problems.length>50?'<p class="help">۵۰ خطای نخست نمایش داده شده است؛ پس از اصلاح، فایل را دوباره بررسی کنید.</p>':''}`:''}</div>`;
 }
 async function downloadTemplate(targetKind){
  if(busy||workspaceSaving||!isCurrent())return;busy=true;error.textContent='';const unlock=workspaceWriteLock(form);
  try{const archive=targetKind===null,path=archive?'excel/templates.zip':'excel/templates/'+encodeURIComponent(targetKind),filename=archive?'Nikan-Excel-Forms-company-'+ownerCid+'.zip':'Nikan-'+targetKind+'-company-'+ownerCid+'.xlsx';const blob=await api(path+'?company_id='+ownerCid);if(isCurrent())download(blob,filename)}
  catch(err){if(isCurrent()&&err.name!=='AbortError')error.textContent=err.message}
  finally{busy=false;unlock();if(isCurrent()){describe();updateCommit()}}
 }
 async function importFile(mode){
  if(busy||workspaceSaving||!isCurrent()||!eligible())return;
  if(mode==='commit'&&!canCommit())return;
  const file=fileInput.files[0];if(!file){error.textContent='فایل تکمیل‌شده XLSX را انتخاب کنید.';return}
  if(mode==='preview')invalidate();
  const requestedKind=kind,requestedPolicy=policySelect.value,data=new FormData();data.append('file',file);data.append('mode',mode);data.append('policy',requestedPolicy);
  busy=true;error.textContent='';setFormBusy(form,true);const unlock=workspaceWriteLock(form);
  try{
   const result=await api('excel/import/'+encodeURIComponent(requestedKind)+'?company_id='+ownerCid,{method:'POST',body:data});if(!isCurrent())return;
   showResult(result);
   if(mode==='preview'){preview=result;previewFile=file;previewKind=requestedKind;previewPolicy=requestedPolicy}
   else{preview=null;previewFile=null;cache={};if(typeof referenceOptions!=='undefined')referenceOptions={};if(typeof referenceOptionsCid!=='undefined')referenceOptionsCid=null;if(requestedKind==='companies')setCompanyChoices(await api('companies'));notice('اطلاعات فایل با موفقیت ثبت شد؛ هیچ رکوردی حذف نشد.')}
  }catch(err){
   if(isCurrent()&&err.name!=='AbortError'){preview=null;previewFile=null;error.textContent=err.message;if(err.data?.errors?.length)showResult(err.data)}
  }finally{busy=false;unlock();setFormBusy(form,false);if(isCurrent()){describe();updateCommit()}}
 }
 kindSelect.onchange=e=>{kind=e.target.value;excelPreferredKind=kind;if(fileInput)fileInput.value='';invalidate();describe()};
 if(fileInput){fileInput.onchange=invalidate;policySelect.onchange=invalidate;form.onsubmit=e=>{e.preventDefault();return importFile('preview')};commit.onclick=()=>importFile('commit')}
 else form.onsubmit=e=>e.preventDefault();
 $('#excel-download').onclick=()=>downloadTemplate(kind);
 const bundle=document.createElement('button');bundle.type='button';bundle.id='excel-download-all';bundle.className='quiet';bundle.textContent='دریافت همه قالب‌ها (ZIP)';$('#excel-download').after(bundle);bundle.onclick=()=>downloadTemplate(null);
 $('#excel-catalog').onclick=e=>{const button=e.target.closest('[data-excel-template]');if(button)downloadTemplate(button.dataset.excelTemplate)};
 const legacy=$('#excel-legacy');if(legacy)legacy.ontoggle=()=>{if(legacy.open&&!legacy.dataset.loaded){legacy.dataset.loaded='1';renderLegacyImport($('#excel-legacy-content'))}};
 describe();
}
