menus.push(['branding','◈','هویت بصری و لوگوها']);let brandingAssets=[],brandingStamp=0;
function applyBranding(){
 const holding=brandingAssets.some(asset=>asset.key==='holding');
 for(const mark of document.querySelectorAll('.brand-mark'))mark.innerHTML=holding?`<img src="/api/branding/logo/holding?v=${brandingStamp}" alt="هلدینگ نیکان">`:'N';
 let icon=document.querySelector('link[rel=icon]');
 if(holding){if(!icon){icon=document.createElement('link');icon.rel='icon';document.head.append(icon)}icon.href='/api/branding/logo/holding?v='+brandingStamp}else icon?.remove();
 let image=$('#company-logo');
 if(!image&&user){image=document.createElement('img');image.id='company-logo';image.alt='لوگوی شرکت';$('.header-tools').append(image)}
 if(image){const key='company-'+cid,visible=!!user&&brandingAssets.some(asset=>asset.key===key);image.hidden=!visible;if(visible)image.src='/api/branding/logo/'+key+'?v='+brandingStamp;else image.removeAttribute('src')}
}
async function refreshBranding(){
 const account=user,ownerCid=Number(cid),version=navigationVersion,assets=await api('branding');
 if(user!==account||Number(cid)!==ownerCid||version!==navigationVersion)return;
 if(!Array.isArray(assets))throw Error('فهرست لوگوها قابل خواندن نیست.');
 brandingAssets=assets;brandingStamp=Date.now();applyBranding();
}
const brandingRender=render;render=async function(){
 applyBranding();
 if(page!=='branding'){
  await brandingRender();
  // Logos are supplemental: an unavailable logo endpoint must not replace a working page.
  try{await refreshBranding()}catch(err){if(err.name!=='AbortError')applyBranding()}
  return;
 }
 workspaceHeading();if(user.role!=='admin'){notice('مدیریت لوگوها فقط برای مدیر هلدینگ مجاز است');return}
 const account=user,ownerCid=Number(cid),version=navigationVersion;await refreshBranding();
 if(user!==account||page!=='branding'||Number(cid)!==ownerCid||version!==navigationVersion)return;
 const entities=[{key:'holding',name:'هلدینگ نیکان'},...user.companies.map(company=>({key:'company-'+company.id,name:company.name}))];
 $('#content').innerHTML=`<div class="panel"><h2>هویت بصری هلدینگ و شرکت‌ها</h2><p class="help">لوگوی اصلی هر سازمان را در جای درست ثبت کنید. تصویر اصلی بدون تغییر نگهداری می‌شود؛ برای لوگوی هلدینگ، نسخه مناسب آیکون برنامه نیز ساخته می‌شود. لوگوها همراه پایگاه داده پشتیبان‌گیری می‌شوند.</p><form id="branding-form" class="pro-form"><label>سازمان<select name="key" id="branding-key">${entities.map(entity=>`<option value="${entity.key}">${esc(entity.name)}</option>`).join('')}</select></label><label>لوگو (PNG یا JPEG)<input type="file" name="file" accept="image/png,image/jpeg" required id="branding-file"></label><div class="logo-preview"><img id="branding-preview" hidden alt="پیش‌نمایش لوگوی انتخابی"></div><button type="submit">ثبت لوگوی سازمان</button></form></div><div class="category-cards">${entities.map(entity=>`<div class="panel brand-card">${brandingAssets.some(asset=>asset.key===entity.key)?`<img src="/api/branding/logo/${entity.key}?v=${brandingStamp}" alt="${esc(entity.name)}">`:'<div class="empty-logo">لوگو ثبت نشده</div>'}<h3>${esc(entity.name)}</h3></div>`).join('')}</div>`;
 const form=$('#branding-form'),preview=$('#branding-preview'),isCurrent=()=>user===account&&page==='branding'&&Number(cid)===ownerCid&&$('#branding-form')===form;
 let image=null,fileVersion=0;
 $('#branding-file').onchange=e=>{
  const file=e.target.files[0],version=++fileVersion;image=null;preview.hidden=true;preview.removeAttribute('src');if(!file)return;
  const reader=new FileReader();
  reader.onload=()=>{if(!isCurrent()||version!==fileVersion)return;const candidate=new Image();candidate.onload=()=>{if(isCurrent()&&version===fileVersion){image=candidate;preview.src=reader.result;preview.hidden=false}};candidate.onerror=()=>{if(isCurrent()&&version===fileVersion)notice('تصویر قابل خواندن نیست؛ یک فایل PNG یا JPEG معتبر انتخاب کنید.')};candidate.src=reader.result};
  reader.onerror=()=>{if(isCurrent()&&version===fileVersion)notice('خواندن فایل انجام نشد؛ فایل را دوباره انتخاب کنید.')};reader.readAsDataURL(file);
 };
 form.onsubmit=async e=>{
  e.preventDefault();if(form.dataset.busy||!isCurrent())return;
  const data=new FormData(form);setFormBusy(form,true);const unlock=workspaceWriteLock(form);
  try{
   if(data.get('key')==='holding'){
    if(!image?.complete||!image.naturalWidth)throw Error('ابتدا بارگذاری پیش‌نمایش تصویر را کامل کنید');
    const canvas=document.createElement('canvas');canvas.width=canvas.height=256;const context=canvas.getContext('2d'),scale=Math.min(240/image.width,240/image.height),width=image.width*scale,height=image.height*scale;
    context.drawImage(image,(256-width)/2,(256-height)/2,width,height);const icon=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!icon)throw Error('ساخت آیکون انجام نشد؛ تصویر دیگری انتخاب کنید.');data.append('icon',icon,'holding-icon.png');
   }
   const result=await api('branding/'+data.get('key'),{method:'POST',body:data});if(!isCurrent())return;
   const message=result.icon_updated?'لوگو ثبت شد؛ هویت بصری سامانه به‌روزرسانی شد.':'لوگو ثبت شد؛ تغییر آیکون ویندوز نیازمند اجرای دوباره برنامه است.';
   const rendered=await renderCurrent();if(user===account&&page==='branding'&&Number(cid)===ownerCid)notice(rendered?message:message+' بارگذاری دوباره انجام نشد؛ تلاش مجدد را بزنید.');
  }catch(err){if(isCurrent()&&err.name!=='AbortError')notice(err.message)}finally{unlock();setFormBusy(form,false)}
 };
};
load().catch(error=>{$('#login-error').textContent=error.message});
