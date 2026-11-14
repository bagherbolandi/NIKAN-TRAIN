/* One scoped hub for the standard HRM and retained training workbooks. */
(() => {
 'use strict';
 const labels={personnel:'اداری و پرسنلی',contracts:'قرارداد و کارگزینی',documents:'مدارک',attendance:'حضور و غیاب',performance:'ارزیابی عملکرد',productivity:'آکورد بهره‌وری',discipline:'تشویق و انضباط',loans:'وام و تسهیلات',welfare:'رفاهیات',accommodation:'اقامتگاه',training:'آموزش',hse:'ایمنی',health_environment:'بهداشت و محیط زیست',health:'سلامت شغلی محرمانه'};
 const h=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const fa=value=>Number(value||0).toLocaleString('fa-IR');
 const visible=()=>Boolean(user&&(user.role==='admin'||user.hrm?.training_access||user.hrm?.modules?.length));
 async function render(target,owner){
  const company=Number(cid),current=()=>user===owner.account&&Number(cid)===company&&page===owner.page&&navigationVersion===owner.version&&target.isConnected;
  const data=await api('hrm/excel/forms?company_id='+company);if(!current())return;
  const forms=[...(data.forms||[]).map(form=>({...form,source:'hrm',key:'hrm:'+form.resource})),...(data.legacy_forms||[]).map(form=>({...form,source:'legacy',resource:form.kind,module:'training',key:'legacy:'+form.kind,can_create:Boolean(form.can_import),can_edit:Boolean(form.can_import),can_download:true,importable:true,help:form.description}))];
  target.innerHTML='<div class="panel"><p class="eyebrow">فرم‌های استاندارد نیکان</p><h2>قالب‌ها و ورود اطلاعات از اکسل</h2><p>قالب مربوط به شرکت و سایت مجاز را دریافت کنید، در سطح شرکت تکمیل کنید و سپس همین‌جا بررسی و ثبت کنید. همه قالب‌ها راهنما، مرجع‌های مجاز و برگه نمونه جدا دارند.</p><p class="help">بررسی اولیه چیزی ثبت نمی‌کند. ثبت نهایی، فایل را دوباره بررسی می‌کند و در صورت خطا هیچ ردیفی اعمال نمی‌شود. کد پرسنلی، شناسه‌ها و مبالغ بزرگ را متن نگه دارید.</p></div><div class="panel"><div class="hrm-filter-grid"><label>جستجوی فرم<input id="hrm-excel-search" placeholder="نام فرم یا بخش" autocomplete="off"></label><label>بخش سازمانی<select id="hrm-excel-module"><option value="">همه بخش‌های مجاز</option>'+[...new Set(forms.map(form=>form.module))].map(module=>'<option value="'+h(module)+'">'+h(labels[module]||module)+'</option>').join('')+'</select></label><label>انتخاب فرم<select id="hrm-excel-kind" data-searchable="true"></select></label></div><p id="hrm-excel-count" class="help"></p></div><div id="hrm-excel-workspace"></div>';
  let selected=sessionStorage.getItem('nikan-excel-form-'+user.username+'-'+company)||'',generation=0,busy=false;
  if(selected.startsWith('legacy:')&&data.canonical_replacements?.[selected.slice(7)])selected='hrm:'+data.canonical_replacements[selected.slice(7)];
  const kind=target.querySelector('#hrm-excel-kind'),moduleFilter=target.querySelector('#hrm-excel-module'),search=target.querySelector('#hrm-excel-search'),workspace=target.querySelector('#hrm-excel-workspace');
  function list(){
   const query=search.value.trim().toLocaleLowerCase();
   const choices=forms.filter(form=>(!moduleFilter.value||form.module===moduleFilter.value)&&[form.title,labels[form.module],form.resource].some(value=>String(value||'').toLocaleLowerCase().includes(query)));
   if(!choices.some(form=>form.key===selected))selected=choices[0]?.key||'';
   kind.innerHTML=choices.map(form=>'<option value="'+h(form.key)+'" '+(form.key===selected?'selected':'')+'>'+h(form.title+' — '+(form.source==='legacy'?'آموزش حفظ‌شده':labels[form.module]||form.module))+'</option>').join('');
   kind.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));target.querySelector('#hrm-excel-count').textContent=fa(choices.length)+' فرم یا گزارش مجاز';open();
  }
  async function download(path,filename){
   const response=await fetch('/api/'+path,{credentials:'same-origin'});
   if(!response.ok){const value=await response.json().catch(()=>({}));throw Error(value.error||'دریافت فایل انجام نشد');}
   const blob=await response.blob();if(!current())return;const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  async function open(){
   const turn=++generation,form=forms.find(form=>form.key===selected);if(!current())return;
   if(!form){workspace.innerHTML='<div class="empty">فرمی در محدوده این جستجو یا مجوز وجود ندارد.</div>';return;}
   sessionStorage.setItem('nikan-excel-form-'+user.username+'-'+company,selected);
   const operations=[];if(form.can_create)operations.push(['insert','ثبت جدید؛ شناسه و نسخه خالی']);if(form.can_edit)operations.push(['update',form.source==='hrm'?'اصلاح صریح با شناسه و نسخه جاری':'به‌روزرسانی با کلیدهای قالب آموزش']);
   const canImport=form.importable&&operations.length>0;
   workspace.innerHTML='<div class="panel"><h2>'+h(form.title)+'</h2><p>'+h(form.help||'')+'</p><p class="help">'+(form.source==='legacy'?'این فرم از گردش آموزش قبلی استفاده می‌کند و مجوزهای همان بخش را رعایت می‌کند.':'در فرم اصلاح، خانه خالی مقدار قبلی را حفظ می‌کند؛ «[پاک شود]» مقدار اختیاری را خالی می‌کند. اصلاح هیچ رکوردی را حذف یا با نام فرد دیگری ادغام نمی‌کند.')+'</p><div class="toolbar"><label id="hrm-excel-site-label">محدوده سایت<select id="hrm-excel-site" data-searchable="true"><option value="">همه سایت‌های مجاز / سطح شرکت</option></select></label>'+(form.can_download?'<button type="button" id="hrm-excel-template">دریافت قالب خالی استاندارد</button>':'')+(form.can_download_existing?'<button type="button" class="quiet" id="hrm-excel-existing">قالب اصلاح همراه سوابق و نسخه جاری</button>':'')+(form.read_only&&form.can_export?'<button type="button" id="hrm-excel-report">دریافت خروجی گزارش</button>':'')+'</div><p id="hrm-excel-error" class="error" role="alert"></p></div>'+(canImport?'<div class="panel"><h3>بررسی و انتقال فایل تکمیل‌شده</h3><form id="hrm-excel-upload"><div class="hrm-filter-grid"><label>نوع عملیات<select id="hrm-excel-policy">'+operations.map(([value,title])=>'<option value="'+value+'">'+h(title)+'</option>').join('')+'</select></label><label>فایل XLSX<input type="file" id="hrm-excel-file" accept=".xlsx" required></label></div><div class="toolbar"><button type="button" id="hrm-excel-preview">۱. بررسی بدون ثبت</button><button type="button" id="hrm-excel-commit" disabled>۲. ثبت همه ردیف‌های معتبر</button></div></form><div id="hrm-excel-result" aria-live="polite"></div></div>':'<div class="panel"><p class="help">'+h(form.binary_attachment?'بارگذاری فایل واقعی و انتخاب سطح محرمانگی از فرم امن مدارک انجام می‌شود.':form.read_only?'این بخش از داده‌های ثبت‌شده محاسبه می‌شود و فرم ورود مستقل ندارد.':'مجوز ثبت یا اصلاح این فرم به حساب شما واگذار نشده است.')+'</p></div>');
   const site=workspace.querySelector('#hrm-excel-site'),error=workspace.querySelector('#hrm-excel-error'),policy=workspace.querySelector('#hrm-excel-policy'),file=workspace.querySelector('#hrm-excel-file'),resultBox=workspace.querySelector('#hrm-excel-result'),commit=workspace.querySelector('#hrm-excel-commit');
   const live=()=>current()&&turn===generation;
   if(form.source==='legacy')workspace.querySelector('#hrm-excel-site-label').hidden=true;
   else{
    const operation=form.can_create?'create':form.can_edit?'edit':'export';
    try{const sites=await api('hrm/sites?company_id='+company+'&module='+encodeURIComponent(form.module)+'&operation='+operation);if(!live())return;site.innerHTML='<option value="">همه سایت‌های مجاز / سطح شرکت</option>'+(Array.isArray(sites)?sites:[]).map(row=>'<option value="'+row.id+'">'+h(row.name)+'</option>').join('');site.dispatchEvent(new CustomEvent('nikan-options-changed',{bubbles:true}));}catch(err){if(!live())return;}
   }
   function query(){return 'company_id='+company+(form.source==='hrm'&&site.value?'&site_id='+encodeURIComponent(site.value):'');}
   async function fetchFile(existing=false,report=false){
    if(busy||!live())return;error.textContent='';try{const path=report?'hrm/reports/'+form.resource+'.xlsx?'+query():form.source==='legacy'?'excel/templates/'+encodeURIComponent(form.kind)+'?'+query():'hrm/excel/templates/'+form.resource+'?'+query()+(existing?'&include_existing=1':'');await download(path,'Nikan-'+form.resource+(existing?'-edit':'')+'-company-'+company+'.xlsx');}catch(err){if(live())error.textContent=err.message;}
   }
   workspace.querySelector('#hrm-excel-template')?.addEventListener('click',()=>fetchFile());workspace.querySelector('#hrm-excel-existing')?.addEventListener('click',()=>{if(policy)policy.value='update';fetchFile(true)});workspace.querySelector('#hrm-excel-report')?.addEventListener('click',()=>fetchFile(false,true));
   if(!canImport)return;
   let checkedFile=null,checkedPolicy=null,checkedSite=null;
   const invalidate=()=>{checkedFile=null;checkedPolicy=null;checkedSite=null;commit.disabled=true;if(resultBox)resultBox.innerHTML='';};
   file.onchange=invalidate;policy.onchange=invalidate;site.onchange=invalidate;workspace.querySelector('#hrm-excel-upload').onsubmit=event=>event.preventDefault();
   function drawResult(result){
    resultBox.innerHTML='<h3>'+h(result.applied?'نتیجه ثبت':'نتیجه بررسی')+'</h3><p>'+h(result.message||'')+'</p><p>'+fa(result.total)+' ردیف؛ '+(result.applied?fa(result.created)+' ثبت و '+fa(result.updated)+' اصلاح انجام شد.':fa(result.planned_created??result.created)+' ثبت و '+fa(result.planned_updated??result.updated)+' اصلاح قابل بررسی است؛ هنوز چیزی ثبت نشده است.')+'</p>'+(result.errors?.length?'<div class="table-wrap"><table><thead><tr><th>ردیف Excel</th><th>خطا</th></tr></thead><tbody>'+result.errors.map(item=>'<tr><td>'+fa(item.row)+'</td><td>'+h(item.message||item.error)+'</td></tr>').join('')+'</tbody></table></div>':'')+(result.applied&&form.source==='hrm'?'<button type="button" class="quiet" id="hrm-excel-open-resource">مشاهده سوابق همین بخش</button>':'');
    resultBox.querySelector('#hrm-excel-open-resource')?.addEventListener('click',()=>window.nikanHrmOpenResource?.(form.resource,{company_id:company}));
   }
   async function submit(mode){
    if(busy||!live())return;const chosen=file.files[0];if(!chosen){error.textContent='فایل تکمیل‌شده XLSX را انتخاب کنید.';return;}
    if(mode==='commit'&&(checkedFile!==chosen||checkedPolicy!==policy.value||checkedSite!==site.value)){error.textContent='فایل و نوع عملیات فعلی را ابتدا بررسی کنید.';commit.disabled=true;return;}
    if(mode==='commit'&&!confirm('همه ردیف‌های معتبر این فایل در شرکت انتخاب‌شده ثبت یا اصلاح شوند؟'))return;
    busy=true;error.textContent='';const unlock=typeof workspaceWriteLock==='function'?workspaceWriteLock(target):()=>{};
    try{
     const body=new FormData();body.append('file',chosen);body.append('mode',mode);body.append('policy',form.source==='legacy'&&policy.value==='update'?'upsert':policy.value);
     const path=form.source==='legacy'?'excel/import/'+encodeURIComponent(form.kind):'hrm/excel/import/'+form.resource;
     let result;try{result=await api(path+'?'+query(),{method:'POST',body})}catch(err){if(Array.isArray(err.data?.errors))result=err.data;else throw err}if(!live())return;
     drawResult(result);checkedFile=result.ok&&mode==='preview'?chosen:null;checkedPolicy=policy.value;checkedSite=site.value;commit.disabled=!(result.ok&&mode==='preview');
    }catch(err){if(live()){error.textContent=err.message;invalidate();}}
    finally{busy=false;unlock();if(live())commit.disabled=!checkedFile;}
   }
   workspace.querySelector('#hrm-excel-preview').onclick=()=>submit('preview');commit.onclick=()=>submit('commit');
  }
  kind.onchange=()=>{if(busy)return;selected=kind.value;open()};moduleFilter.onchange=list;search.oninput=list;list();
 }
 if(typeof window.nikanHrmRegisterPage==='function')window.nikanHrmRegisterPage({key:'hrm_excel',title:'قالب‌ها و ورود اکسل',icon:'⇧',visible,render});
})();
