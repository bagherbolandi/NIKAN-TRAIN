// Network deployments replace the legacy raw-database download with encrypted archives.
const networkRender=render;let backupPoll,backupViewSequence=0;
function networkDate(value){return value?new Date(value).toLocaleString('fa-IR',{timeZone:'Asia/Tehran'}):'هنوز انجام نشده'}
render=async function(){
 clearTimeout(backupPoll);const view=++backupViewSequence;
 if(!user?.network||page!=='backup'){
  await networkRender();
  if(user?.network&&view===backupViewSequence){$('.aside-bottom').textContent='نسخه شبکه · پایگاه داده مرکزی';$('footer').textContent='سامانه آموزش نیکان · داده مرکزی و دسترسی مستقل شرکت‌ها';const button=document.querySelector('[data-page=backup]');if(button)button.textContent='پشتیبان و بازیابی'}
  return;
 }
 workspaceHeading();if(user.role!=='admin')return;
 const account=user,ownerCid=Number(cid),settings=await api('network/settings');
 if(user!==account||page!=='backup'||Number(cid)!==ownerCid||view!==backupViewSequence)return;
 const cfg=settings.backup,section=$('#content');
 section.innerHTML=`<div class="hero"><div><h2>پشتیبان رمزگذاری‌شده و بازیابی آزمایش‌شده</h2><p>پس از هر پشتیبان، رمزگشایی و اجرای پایگاه بازیابی‌شده در محیط جداگانه آزمایش می‌شود. بازیابی نهایی با توقف سرور و ابزار بازیابی نصب‌شده انجام می‌شود.</p><p>آدرس شبکه: <b dir="ltr">${esc(settings.url)}</b></p><p>اعتبار گواهی HTTPS: ${networkDate(settings.certificate_expires)}</p></div></div><div class="panel"><h2>زمان‌بندی و نسخه دوم در شبکه</h2><form id="backup-settings" class="pro-form"><label>پشتیبان خودکار<select name="enabled"><option value="true" ${cfg.enabled?'selected':''}>فعال</option><option value="false" ${!cfg.enabled?'selected':''}>غیرفعال</option></select></label><label>ساعت روزانه به وقت تهران<input type="time" name="time" value="${esc(cfg.time)}" required></label><label>تعداد نسخه نگهداری (۲ تا ۳۶۵)<input type="number" name="retain" min="2" max="365" value="${cfg.retain}" required></label><label class="wide">مسیر پوشه دوم (محلی یا UNC شبکه)<input name="mirror" dir="ltr" value="${esc(cfg.mirror)}" placeholder="\\\\BACKUP-SERVER\\NikanBackups"></label><p class="help wide">حساب اجرای سرور باید دسترسی خواندن و نوشتن در پوشه شبکه داشته باشد. مسیر را روی سرور تعریف می‌کنید. پایگاه اصلی همواره روی دیسک محلی سرور می‌ماند.</p><button type="submit">ذخیره تنظیمات و بررسی دسترسی مسیر</button></form></div><div class="toolbar"><button id="backup-now">پشتیبان‌گیری اکنون</button><button class="quiet" id="recovery-key">دریافت کلید بازیابی</button><button class="quiet" id="server-certificate">دریافت گواهی سرور</button></div><div class="warning">کلید بازیابی را در محل امنی جدا از فایل‌های پشتیبان نگهداری کنید؛ بدون آن بازیابی ممکن نیست. کلید فقط در اختیار مدیر هلدینگ است.</div><div id="backup-state" role="status"></div><div id="backup-list"></div>`;
 const form=$('#backup-settings'),state=$('#backup-state'),list=$('#backup-list'),backupNow=$('#backup-now');
 const isCurrent=()=>user===account&&page==='backup'&&Number(cid)===ownerCid&&view===backupViewSequence&&$('#backup-settings')===form;
 let operationBusy=false,serverRunning=false,refreshSequence=0,pollError='';
 function applyRunning(){backupNow.disabled=operationBusy||serverRunning;list.querySelectorAll('[data-backup-test]').forEach(button=>button.disabled=operationBusy||serverRunning)}
 async function write(action,message,launchesWork=false){
  if(!isCurrent()||operationBusy||(launchesWork&&serverRunning))return;
  operationBusy=true;refreshSequence++;clearTimeout(backupPoll);const unlock=workspaceWriteLock(section);
  try{await action();if(isCurrent()){if(launchesWork)serverRunning=true;notice(message)}}
  catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}
  finally{unlock();operationBusy=false;if(isCurrent()){applyRunning();await refresh()}}
 }
 form.onsubmit=e=>{
  e.preventDefault();if(!isCurrent()||operationBusy)return;
  const data=Object.fromEntries(new FormData(form));data.enabled=data.enabled==='true';data.retain=Number(data.retain);
  return write(()=>api('network/settings',{method:'PUT',body:data}),'تنظیمات ذخیره و دسترسی مسیر تأیید شد.');
 };
 backupNow.onclick=()=>write(()=>api('network/backups',{method:'POST'}),'درخواست ثبت شد؛ نتیجه در وضعیت پشتیبان نمایش داده می‌شود.',true);
 $('#recovery-key').onclick=async()=>{if(!isCurrent()||operationBusy)return;try{const blob=await api('network/recovery-key');if(isCurrent())download(blob,'Nikan-Recovery.key')}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
 $('#server-certificate').onclick=async()=>{if(!isCurrent()||operationBusy)return;try{const blob=await api('network/certificate');if(isCurrent())download(blob,'Nikan-Server.pem')}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}};
 list.onclick=async e=>{
  if(!isCurrent()||operationBusy)return;const downloadButton=e.target.closest('[data-backup-download]'),test=e.target.closest('[data-backup-test]');
  if(test&&!test.disabled)return write(()=>api('network/verify',{method:'POST',body:{name:test.dataset.backupTest}}),'آزمون بازیابی در پایگاه جداگانه شروع شد.',true);
  if(downloadButton&&!downloadButton.disabled){try{const blob=await api('network/backups/'+encodeURIComponent(downloadButton.dataset.backupDownload));if(isCurrent())download(blob,downloadButton.dataset.backupDownload)}catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}}
 };
 async function refresh(){
  clearTimeout(backupPoll);if(!isCurrent()||operationBusy)return;const request=++refreshSequence;
  try{
   const result=await api('network/backups');if(!isCurrent()||operationBusy||request!==refreshSequence)return;
   if(pollError&&$('#notice').textContent===pollError)notice('');pollError='';
   const status=result.status;serverRunning=!!status.running;
   state.innerHTML=`<div class="panel"><h2>${serverRunning?'عملیات در حال اجراست…':'وضعیت پشتیبان'}</h2><p>آخرین پشتیبان سالم: ${networkDate(status.last_success)}</p><p>آخرین نسخه موفق در پوشه دوم: ${networkDate(status.mirror_success)}</p><p>آزمون آخرین پشتیبان: ${status.verification?.verified?'تأیید شده':'هنوز انجام نشده'}</p><p>آزمون انتخابی بازیابی: ${status.recovery_test?.verified?esc(status.recovery_test.file)+' · '+networkDate(status.recovery_test.checked_at):'هنوز انجام نشده'}</p>${status.error?`<p class="error">خطای پشتیبان یا بازیابی: ${esc(status.error)}</p>`:''}${status.mirror_error?`<p class="error">نسخه محلی سالم است؛ خطای نسخه دوم: ${esc(status.mirror_error)}</p>`:''}</div>`;
   list.innerHTML=result.files.length?`<div class="table-wrap"><table><thead><tr><th>فایل پشتیبان</th><th>حجم</th><th>عملیات</th></tr></thead><tbody>${result.files.map(file=>`<tr><td dir="ltr">${esc(file.name)}</td><td>${(file.bytes/1024/1024).toFixed(2)} MB</td><td><button class="quiet" data-backup-download="${esc(file.name)}">دریافت فایل</button> <button class="quiet" data-backup-test="${esc(file.name)}">آزمون بازیابی</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">پشتیبانی ثبت نشده است.</div>';applyRunning();
  }catch(err){if(isCurrent()&&request===refreshSequence&&err.name!=='AbortError'){pollError=err.message;notice(pollError)}}
  finally{if(isCurrent()&&!operationBusy&&request===refreshSequence)backupPoll=setTimeout(refresh,3000)}
 }
 await refresh();
};
