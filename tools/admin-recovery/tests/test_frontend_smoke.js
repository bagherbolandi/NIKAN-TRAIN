const fs=require('fs');
const {JSDOM}=require('/tmp/nikan/jsdomtest/node_modules/jsdom');
const html=fs.readFileSync('/tmp/nikan/mod/setup/app/static/index.html','utf8');
const js=fs.readFileSync('/tmp/nikan/mod/setup/app/static/app.js','utf8');
const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://localhost/'});
const w=dom.window;
w.AbortController=global.AbortController;
w.fetch=()=>Promise.reject(new Error('no network in smoke test'));
if(!w.URL.createObjectURL)w.URL.createObjectURL=()=>'blob:stub';
// jsdom lacks dialog.showModal; stub so any accidental call doesn't crash
w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')};
w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')};
try{
  w.eval(js);
  console.log('app.js evaluated OK');
  const ids=['auth','recover','show-recover','show-login','login-error','login-notice','logout'];
  for(const id of ids) console.log(id, w.document.getElementById(id)?'present':'MISSING');
  console.log('auth onsubmit',typeof w.document.getElementById('auth').onsubmit);
  console.log('recover onsubmit',typeof w.document.getElementById('recover').onsubmit);
  console.log('show-recover onclick',typeof w.document.getElementById('show-recover').onclick);
  console.log('show-login onclick',typeof w.document.getElementById('show-login').onclick);
  // simulate toggles
  w.document.getElementById('show-recover').onclick();
  console.log('after show-recover: auth.hidden=',w.document.getElementById('auth').hidden,'recover.hidden=',w.document.getElementById('recover').hidden);
  w.document.getElementById('show-login').onclick();
  console.log('after show-login: auth.hidden=',w.document.getElementById('auth').hidden,'recover.hidden=',w.document.getElementById('recover').hidden);
}catch(e){console.error('EVAL ERROR',e);process.exit(1)}
