/* Role-filtered navigation and accessible RTL shell. No additional data source. */
(() => {
 'use strict';
 const sidebar=document.querySelector('#workspace-sidebar'),main=document.querySelector('#workspace-main'),nav=document.querySelector('#nav');
 if(!sidebar||!main||!nav)return;
 const toggle=document.querySelector('#workspace-menu-toggle'),close=document.querySelector('#workspace-menu-close'),backdrop=document.querySelector('#workspace-drawer-backdrop');
 const mobile=window.matchMedia('(max-width: 760px)');
 let lastSyncedPage='';
 const groups=[
  {key:'overview',title:'نمای مدیریتی',pages:['hrm_dashboard','hrm_reports','report']},
  {key:'people',title:'کارکنان و امور اداری',pages:['hrm_personnel','hrm_structure','hrm_contracts','hrm_documents','hrm_attendance','hrm_discipline']},
  {key:'development',title:'آموزش و توسعه',pages:['hrm_training','bank','skills','requirements','job_profiles','passports','gaps','needs','courses','records','certificates','instructors','governance']},
  {key:'performance',title:'عملکرد و بهره‌وری',pages:['hrm_performance','hrm_productivity']},
  {key:'hse',title:'ایمنی، بهداشت و محیط زیست',pages:['hrm_hse','hrm_health_environment']},
  {key:'benefits',title:'خدمات و تسهیلات',pages:['hrm_loans','hrm_welfare','hrm_accommodation']},
  {key:'management',title:'مدیریت سامانه',pages:['hrm_excel','import','companies','hrm_access','users','audit','backup','branding']},
 ];
 const iconPaths={
  overview:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  people:'M15.5 7a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0ZM5 21v-3a7 7 0 0 1 14 0v3M18 5a3 3 0 0 1 0 6M21 21v-3a5 5 0 0 0-2-4',
  development:'M12 5v16M12 5C9 3 5 3 2 4v15c4-1 7 0 10 2 3-2 6-3 10-2V4c-3-1-7-1-10 1Z',
  performance:'M4 20V13h4v7M10 20V8h4v12M16 20V3h4v17',
  hse:'m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4ZM8 12l3 3 5-6',
  benefits:'M3 8h18v12H3zM2 5h20v4H2zM12 5v15M12 5C5 6 5-1 9 2l3 3ZM12 5c7 1 7-6 3-3l-3 3Z',
  management:'M3 5h18M3 12h18M3 19h18M7 2v6M17 9v6M9 16v6',
  file:'M6 2h8l4 4v16H6zM14 2v5h5M9 11h6M9 15h6M9 19h4',
 };
 const storageKey=()=> 'nikan-workspace-groups-'+String(user?.username||'anonymous');
 const storedGroups=()=>{try{return JSON.parse(sessionStorage.getItem(storageKey())||'{}')}catch{return {}}};
 function rememberGroup(key,expanded){const value=storedGroups();value[key]=expanded;try{sessionStorage.setItem(storageKey(),JSON.stringify(value))}catch{}}
 function icon(key){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',iconPaths[key]||iconPaths.file);svg.append(path);return svg;}
 function activePage(){return typeof page!=='undefined'?page:'';}
 function regroup(){
  const buttons=[...nav.querySelectorAll('button[data-page]')],saved=storedGroups(),fragment=document.createDocumentFragment(),seen=new Set();
  for(const group of groups){
   const members=group.pages.map(key=>buttons.find(button=>button.dataset.page===key)).filter(Boolean);
   if(!members.length)continue;
   const section=document.createElement('section');section.className='nav-group workspace-nav-group';section.dataset.workspaceGroup=group.key;
   const heading=document.createElement('h2');heading.className='nav-group-title';
   const button=document.createElement('button');button.type='button';button.className='workspace-group-toggle';button.dataset.workspaceToggle=group.key;
   const body=document.createElement('div');body.className='workspace-group-items';body.id='workspace-nav-'+group.key;
   const expanded=saved[group.key]!==false||members.some(item=>item.dataset.page===activePage());button.setAttribute('aria-expanded',String(expanded));button.setAttribute('aria-controls',body.id);body.hidden=!expanded;
   const title=document.createElement('span');title.className='workspace-group-name';title.textContent=group.title;
   const chevron=document.createElement('span');chevron.className='workspace-group-chevron';chevron.setAttribute('aria-hidden','true');chevron.textContent='⌄';button.append(title,chevron);heading.append(button);section.append(heading,body);
   for(const member of members){seen.add(member);const mark=member.querySelector('span');if(mark){mark.replaceChildren(icon(group.key));mark.classList.add('workspace-nav-icon');}member.type='button';body.append(member);}
   fragment.append(section);
  }
  // Extensions remain reachable without granting or inventing a permission.
  const others=buttons.filter(button=>!seen.has(button));
  if(others.length){const section=document.createElement('section');section.className='nav-group workspace-nav-group';const title=document.createElement('h2');title.className='nav-group-title';title.textContent='سایر ابزارهای مجاز';section.append(title,...others);fragment.append(section);}
  nav.replaceChildren(fragment);sync();
 }
 function restoreDefaultLogo(){
  for(const mark of document.querySelectorAll('.brand-mark'))if(!mark.querySelector('img')){const image=document.createElement('img');image.src='/static/logos/nikanholding-logo.png';image.alt='هلدینگ نیکان';mark.replaceChildren(image);}
 }
 function sync(){
  const current=activePage(),canonical=current==='hrm_person'?'hrm_personnel':current;
  for(const button of nav.querySelectorAll('[data-page]')){const selected=button.dataset.page===canonical;button.classList.toggle('active',selected);if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
  if(canonical!==lastSyncedPage){
   const selected=nav.querySelector('[aria-current="page"]'),items=selected?.closest('.workspace-group-items');
   if(items){items.hidden=false;items.previousElementSibling?.querySelector('[aria-expanded]')?.setAttribute('aria-expanded','true');}
   lastSyncedPage=canonical;
  }
  const group=groups.find(item=>item.pages.includes(canonical));document.querySelector('#workspace-section-label').textContent=group?.title||'فضای کاری مدیریت';
  const title=document.querySelector('#page-title').textContent;document.title=(title?title+' | ':'')+'نیکان HRM';
  const date=document.querySelector('#workspace-today');date.textContent=new Intl.DateTimeFormat('fa-IR',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'Asia/Tehran'}).format(new Date());
  date.dateTime=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  restoreDefaultLogo();
 }
 function closeDrawer(restoreFocus=true){
  const wasOpen=document.body.classList.contains('workspace-drawer-open');document.body.classList.remove('workspace-drawer-open');toggle.setAttribute('aria-expanded','false');backdrop.hidden=true;main.inert=false;
  sidebar.inert=mobile.matches;sidebar.setAttribute('aria-hidden',String(mobile.matches));sidebar.removeAttribute('role');sidebar.removeAttribute('aria-modal');
  if(restoreFocus&&wasOpen&&mobile.matches&&!document.querySelector('#shell').hidden)toggle.focus({preventScroll:true});
 }
 function openDrawer(){
  if(!mobile.matches)return;
  document.body.classList.add('workspace-drawer-open');sidebar.inert=false;sidebar.removeAttribute('aria-hidden');sidebar.setAttribute('role','dialog');sidebar.setAttribute('aria-modal','true');main.inert=true;backdrop.hidden=false;toggle.setAttribute('aria-expanded','true');
  requestAnimationFrame(()=>{if(document.body.classList.contains('workspace-drawer-open'))close.focus({preventScroll:true});});
 }
 toggle.addEventListener('click',()=>document.body.classList.contains('workspace-drawer-open')?closeDrawer():openDrawer());close.addEventListener('click',()=>closeDrawer());backdrop.addEventListener('click',()=>closeDrawer());
 nav.addEventListener('click',event=>{
  const item=event.target.closest('[data-page]');if(item&&!item.disabled)closeDrawer(false);
  const toggleButton=event.target.closest('[data-workspace-toggle]');if(!toggleButton)return;
  const expanded=toggleButton.getAttribute('aria-expanded')!=='true';toggleButton.setAttribute('aria-expanded',String(expanded));document.getElementById(toggleButton.getAttribute('aria-controls')).hidden=!expanded;rememberGroup(toggleButton.dataset.workspaceToggle,expanded);
 },true);
 nav.addEventListener('keydown',event=>{
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)||!event.target.matches('button'))return;
  const items=[...nav.querySelectorAll('button')].filter(button=>!button.disabled&&button.getClientRects().length),index=items.indexOf(event.target);if(index<0)return;
  event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next]?.focus();
 });
 document.addEventListener('keydown',event=>{
  if(!document.body.classList.contains('workspace-drawer-open'))return;
  if(event.key==='Escape'){event.preventDefault();closeDrawer();return;}
  if(event.key==='Tab'){
   const items=[...sidebar.querySelectorAll('button,a,input,select,[tabindex]')].filter(item=>!item.disabled&&item.tabIndex>=0&&item.getClientRects().length);if(!items.length)return;
   const first=items[0],last=items[items.length-1];if(!sidebar.contains(document.activeElement)){event.preventDefault();first.focus();}else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
 });
 mobile.addEventListener('change',()=>closeDrawer(false));
 const previousNavigation=drawNavigation;drawNavigation=function(){const value=previousNavigation.apply(this,arguments);regroup();return value;};
 const previousRender=render;render=async function(){try{return await previousRender.apply(this,arguments);}finally{sync();}};
 if(typeof applyBranding==='function'){const priorBranding=applyBranding;applyBranding=function(){const value=priorBranding.apply(this,arguments);restoreDefaultLogo();return value;};}
 const previousReset=resetSession;resetSession=function(){closeDrawer(false);const value=previousReset.apply(this,arguments);restoreDefaultLogo();return value;};
 new MutationObserver(()=>{if(document.querySelector('#shell').hidden)closeDrawer(false);}).observe(document.querySelector('#shell'),{attributes:true,attributeFilter:['hidden']});
 closeDrawer(false);restoreDefaultLogo();
 if(nav.querySelector('[data-page]'))regroup();
})();
