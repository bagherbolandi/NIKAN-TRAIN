menus.push(['governance','◎','میز آموزش هلدینگ']);
companyIndependentPages.add('governance');
Object.assign(labels,{objective:'هدف قابل اندازه‌گیری',method:'روش توسعه (آموزش، مربیگری و ...)',owner:'مسئول پیگیری',due_date:'مهلت شمسی',success_criterion:'معیار موفقیت و واحد سنجش',baseline:'مقدار مبنا',target:'مقدار هدف',actual:'نتیجه واقعی (اختیاری)',evidence:'شاهد ارزیابی',review_date:'تاریخ ارزیابی شمسی (اختیاری)'});
const governanceOpenForm=openForm;
openForm=function(row={}){
 if(page==='needs'&&row.id&&row.status!=='submitted'){notice('نیاز بررسی‌شده قفل است؛ بازگشایی توسط هلدینگ انجام می‌شود.');return}
 governanceOpenForm(row);
 if(page==='needs')for(const key of ['status','decision']){const input=$('#entry [name='+key+']');if(input){input.disabled=true;input.closest('label').hidden=true}}
};
const governanceRender=render;
render=async function(){
 if(page!=='governance')return governanceRender();
 workspaceHeading();if(user.role!=='admin')return;
 const section=$('#content'),version=navigationVersion;
 const year=Number(sessionStorage.getItem('nikan-plan-year'))||currentJalaliYear();
 const [needs,gaps]=await Promise.all([api('holding/needs?year='+year),api('holding/gaps')]);
 if(version!==navigationVersion)return;
 section.onclick=null;
 section.innerHTML=`<div class="panel"><h2>تصمیم‌گیری متمرکز آموزش</h2><p>نیازهای شرکت‌ها در این میز بررسی می‌شوند. تصویب نیاز به‌تنهایی به معنی برگزاری آموزش یا احراز شایستگی نیست. آموزش موردی داخلی از تقویم همان شرکت ثبت می‌شود.</p><label>سال شمسی <input id="holding-year" type="number" min="1300" max="1499" value="${year}"></label><button id="holding-refresh">نمایش سال</button></div><div class="toolbar"><label>جستجو <input id="holding-search" placeholder="شرکت، مهارت یا دلیل نیاز"></label><label>وضعیت <select id="holding-status"><option value="">همه</option value="submitted">در انتظار بررسی</option><option value="approved">مصوب</option><option value="rejected">ردشده</option></select></label></div><div id="holding-needs"></div><div class="panel"><h2>تجمیع زنده گپ مهارتی همه شرکت‌ها</h2><p>این بخش وضعیت فعلی است و به فیلتر سال وابسته نیست. اولویت بر اساس اهمیت شایستگی و مجموع فاصله مرتب می‌شود.</p><div class="table-wrap"><table><thead><tr><th>شایستگی</th><th>اهمیت</th><th>تعداد افراد</th><th>جمع فاصله</th><th>تفکیک شرکت</th></tr></thead><tbody>${gaps.map(r=>`<tr><td>${esc(r.code)} · ${esc(r.title)}</td><td>${r.importance}</td><td>${r.people}</td><td>${r.gap_total}</td><td>${Object.entries(r.companies).map(([k,v])=>esc(k)+': '+v).join(' / ')}</td></tr>`).join('')||'<tr><td colspan="5">گپ ثبت‌شده‌ای وجود ندارد</td></tr>'}</tbody></table></div></div>`;
 const table=$('#holding-needs');
 function draw(){
  const q=$('#holding-search').value,status=$('#holding-status').value;
  const rows=needs.filter(r=>(!status||r.status===status)&&searchMatch([r.company_name,r.skill_title,r.reason].join(' '),q));
  table.innerHTML=`<div class="table-wrap"><table><thead><tr><th>شرکت / مهارت</th><th>دلیل / اولویت</th><th>وضعیت و تصمیم</th><th>برنامه و پیگیری</th><th>عملیات</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.company_name)}<br>${esc(r.skill_code)} · ${esc(r.skill_title)}</td><td>${esc(r.reason)}<br>اولویت: ${r.priority}</td><td>${esc(display('status',r.status))}<br>${esc(r.decision)}</td><td>${esc(r.objective||'برنامه تعیین نشده')}<br>${esc(r.owner||'')} ${esc(r.due_date||'')}${r.actual!==null&&r.actual!==undefined?'<br>نتیجه: '+esc(r.actual)+' / هدف: '+esc(r.target):''}</td><td>${r.status==='submitted'?`<button data-decide="approved" data-id="${r.id}">تصویب</button> <button data-decide="rejected" data-id="${r.id}" class="quiet">رد</button>`:`<button data-decide="submitted" data-id="${r.id}" class="quiet">بازگشایی</button>`}<button data-plan="${r.id}" class="quiet">برنامه / سابقه تصمیم</button></td></tr>`).join('')||'<tr><td colspan="5">نیازی با این فیلتر وجود ندارد</td></tr>'}</tbody></table></div>`;
 }
 $('#holding-search').oninput=draw;$('#holding-status').onchange=draw;draw();
 $('#holding-refresh').onclick=()=>{const value=Number($('#holding-year').value);if(!Number.isInteger(value)||value<1300||value>1499){notice('سال معتبر وارد کنید');return}sessionStorage.setItem('nikan-plan-year',value);renderCurrent()};
 table.onclick=async event=>{
  const decision=event.target.closest('[data-decide]'),plan=event.target.closest('[data-plan]');
  if(!decision&&!plan||workspaceSaving)return;
  const row=needs.find(r=>r.id===Number(decision?.dataset.id||plan.dataset.plan));
  try{
   if(decision){const reason=prompt('دلیل تصمیم هلدینگ را وارد کنید:');if(!reason?.trim())return;const unlock=workspaceWriteLock(section);try{await api('holding/needs/'+row.id+'/decision',{method:'POST',body:{status:decision.dataset.decide,decision:reason,version:row.version}});await renderCurrent()}finally{unlock()}return}
   const path=workspaceScope('development-plans/'+row.id,row.company_id);const [result,holdingCourses,internalCourses]=await Promise.all([api(path),api('calendar?scope=holding'),api(workspaceScope('calendar?scope=internal',row.company_id))]);
   if(version!==navigationVersion)return;
   if(row.status!=='approved'){notice(result.history.map(h=>h.created+' — '+h.username+': '+h.decision).join(' | ')||'هنوز تصمیمی ثبت نشده است');return}
   const values=result.plan||{baseline:0,target:1,version:0};
   workspaceModal('برنامه توسعه: '+row.skill_title,['objective','method','owner','due_date','success_criterion','baseline','target','actual','evidence','review_date','course_id'],values,async data=>{
    await api(path,{method:'PUT',body:{...data,course_id:data.course_id?Number(data.course_id):null,version:values.version}});
    return {refresh:()=>renderCurrent(),message:'برنامه و شواهد ارزیابی ذخیره شد.'};
   });
   const courseInput=$('#entry [name=course_id]');const courseSelect=document.createElement('select');courseSelect.name='course_id';courseSelect.innerHTML='<option value="">بدون دوره / اقدام توسعه‌ای دیگر</option>'+[...holdingCourses,...internalCourses].filter(c=>c.status!=='cancelled').map(c=>`<option value="${c.id}" ${Number(values.course_id)===c.id?'selected':''}>${esc(c.title)} · ${esc(c.company_name)} · ${esc(c.date)}</option>`).join('');courseInput.replaceWith(courseSelect);
   const help=document.createElement('p');help.className='help';help.textContent='انتخاب دوره اختیاری است و باید با شایستگی نیاز مرتبط باشد. معیار سنجش را با واحد و جهت موفقیت تعریف کنید. سابقه: '+(result.history.map(h=>h.created+' '+h.username+': '+h.decision).join(' | ')||'ندارد');$('#fields').append(help);
  }catch(err){if(err.name!=='AbortError')notice(err.message)}
 };
};
