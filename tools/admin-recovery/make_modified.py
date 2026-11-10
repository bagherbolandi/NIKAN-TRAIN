# -*- coding: utf-8 -*-
"""Generate the modified installer payload files for admin login-password recovery.

Inputs are the pristine NSIS extraction trees in /tmp/nikan/ex_setup and
/tmp/nikan/ex_server. Outputs go to /tmp/nikan/mod/{setup,server}/.
The rebuild step later embeds these bytes into both installers and refreshes
build-manifest.json hashes.
"""
import os, re, hashlib, py_compile

OUT = '/tmp/nikan/mod'
SRC = {'setup': '/tmp/nikan/ex_setup', 'server': '/tmp/nikan/ex_server'}
NEW_TAG = 'v0.10.2'

# ---------------------------------------------------------------- app/__init__.py
py = open(SRC['setup'] + '/app/__init__.py', encoding='utf-8').read()
assert py.count('import os, sqlite3, secrets, time, json, io, csv') == 1
py = py.replace('import os, sqlite3, secrets, time, json, io, csv',
                'import os, sqlite3, secrets, time, json, io, csv, hashlib')

old_schema = "CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);\n'''"
assert py.count(old_schema) == 1
py = py.replace(old_schema,
 "CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);\n"
 "CREATE TABLE IF NOT EXISTS recovery_keys(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),key_hash TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);\n"
 "CREATE TABLE IF NOT EXISTS recovery_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);\n'''")

old_setup = "db().execute(\"INSERT INTO users(username,password,role) VALUES (?,?,'admin')\",(u,generate_password_hash(p)));db().commit();return jsonify(ok=True)"
assert py.count(old_setup) == 1
py = py.replace(old_setup,
 "db().execute(\"INSERT INTO users(username,password,role) VALUES (?,?,'admin')\",(u,generate_password_hash(p)))\n"
 "  uid=db().execute('SELECT last_insert_rowid()').fetchone()[0]\n"
 "  key=secrets.token_urlsafe(32)\n"
 "  db().execute('INSERT INTO recovery_keys(user_id,key_hash) VALUES (?,?)',(uid,hashlib.sha256(key.encode()).hexdigest()))\n"
 "  db().commit();return jsonify(ok=True,recovery_key=key)")

anchor = " @app.get('/api/me')"
assert py.count(anchor) == 1
py = py.replace(anchor, """ @app.post('/api/recover')
 def recover():
  d=json_object();name=d.get('username','');key=d.get('recovery_key','');password=d.get('password','')
  if not all(isinstance(x,str) for x in (name,key,password)):raise ValueError('اطلاعات بازیابی باید متنی باشند')
  name=name.strip()[:100]
  rk='recover:'+name
  a=db().execute('SELECT * FROM recovery_attempts WHERE key=?',(rk,)).fetchone()
  if a and a['until']>time.time() and a['count']>=5:return error('تلاش‌های زیاد؛ ۱۵ دقیقه بعد دوباره امتحان کنید',429)
  u=db().execute("SELECT * FROM users WHERE username=? AND role='admin' AND active=1",(name,)).fetchone()
  rec=db().execute('SELECT * FROM recovery_keys WHERE user_id=?',(u['id'],)).fetchone() if u else None
  if not (u and rec and len(password)>=12 and secrets.compare_digest(rec['key_hash'],hashlib.sha256(key.encode()).hexdigest())):
   count=(a['count'] if a and a['until']>time.time() else 0)+1
   db().execute('INSERT OR REPLACE INTO recovery_attempts VALUES (?,?,?)',(rk,count,time.time()+900));db().commit();return error('نام کاربری، کلید بازیابی یا رمز جدید نامعتبر است',401)
  db().execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(password),u['id']))
  db().execute('DELETE FROM recovery_keys WHERE user_id=?',(u['id'],))
  db().execute('DELETE FROM recovery_attempts WHERE key=?',(rk,))
  db().execute('DELETE FROM login_attempts WHERE username=?',(name,))
  audit('recover_admin',None,name);db().commit();return jsonify(ok=True)
 @app.post('/api/recovery-key')
 @authenticated
 def issue_recovery_key():
  if g.user['role']!='admin':raise PermissionError()
  key=secrets.token_urlsafe(32)
  db().execute('DELETE FROM recovery_keys WHERE user_id=?',(g.user['id'],))
  db().execute('INSERT INTO recovery_keys(user_id,key_hash) VALUES (?,?)',(g.user['id'],hashlib.sha256(key.encode()).hexdigest()))
  audit('recovery_key_issued');db().commit();return jsonify(recovery_key=key)
""" + anchor)

# ---------------------------------------------------------------- index.html
html = open(SRC['setup'] + '/app/static/index.html', encoding='utf-8').read()
m = re.search(r'<form id="auth">.*?</form><p id="login-error" class="error"></p><small>.*?</small>', html, re.S)
assert m, 'login form anchor not found'
new_login = (
 '<form id="auth"><h2 id="auth-title">ورود به سامانه</h2>'
 '<label>نام کاربری<input name="username" required minlength="3" autocomplete="username"></label>'
 '<label>رمز عبور<input name="password" type="password" required autocomplete="current-password"></label>'
 '<button>ورود به فضای کاری</button></form>'
 '<form id="recover" hidden><h2>بازیابی رمز عبور مدیر</h2>'
 '<p class="help">کلید بازیابی دریافت\u200cشده هنگام راه\u200cاندازی یا از بخش مدیریت کاربران را وارد کنید. هر کلید تنها یک\u200cبار قابل استفاده است.</p>'
 '<label>نام کاربری مدیر<input name="username" required minlength="3" autocomplete="username"></label>'
 '<label>کلید بازیابی<input name="recovery_key" required autocomplete="off"></label>'
 '<label>رمز عبور جدید<input name="password" type="password" required minlength="12" autocomplete="new-password"></label>'
 '<label>تکرار رمز عبور جدید<input name="password_confirm" type="password" required minlength="12" autocomplete="new-password"></label>'
 '<button>بازنشانی رمز عبور</button></form>'
 '<p id="login-error" class="error"></p><p id="login-notice" class="help"></p>'
 '<button type="button" id="show-recover" class="quiet">بازیابی رمز عبور مدیر</button> '
 '<button type="button" id="show-login" class="quiet" hidden>بازگشت به ورود</button>'
 '<small>اطلاعات هر شرکت در محدوده دسترسی همان شرکت نگهداری می\u200cشود.</small>'
)
html = html[:m.start()] + new_login + html[m.end():]

# ---------------------------------------------------------------- app.js
js = open(SRC['setup'] + '/app/static/app.js', encoding='utf-8').read()
old_reset = "$('#entry').reset();$('#auth').reset();$('#content').replaceChildren();"
assert js.count(old_reset) == 1
js = js.replace(old_reset,
 "$('#entry').reset();$('#auth').reset();$('#recover').reset();$('#recover').hidden=true;$('#auth').hidden=false;$('#show-recover').hidden=false;$('#show-login').hidden=true;$('#login-notice').textContent='';$('#content').replaceChildren();")

old_show = " $('#login').hidden=false;$('#shell').hidden=true;\n"
assert js.count(old_show) == 1
js = js.replace(old_show,
 " $('#login').hidden=false;$('#shell').hidden=true;$('#recover').hidden=true;$('#auth').hidden=false;$('#show-recover').hidden=false;$('#show-login').hidden=true;$('#login-notice').textContent='';\n")

old_status = ";$('#auth [name=password]').minLength=setup?12:1}"
assert js.count(old_status) == 1
js = js.replace(old_status, ";$('#auth [name=password]').minLength=setup?12:1;$('#show-recover').hidden=setup}")

old_auth = " try{const data=Object.fromEntries(new FormData(form));if(setup){await api('setup',{method:'POST',body:data});setup=false}await api('login',{method:'POST',body:data});$('#login-error').textContent='';await load()}"
assert js.count(old_auth) == 1
js = js.replace(old_auth,
 " try{const data=Object.fromEntries(new FormData(form));let recoveryNotice='';if(setup){const created=await api('setup',{method:'POST',body:data});setup=false;if(created.recovery_key){downloadRecoveryKey(created.recovery_key);recoveryNotice='فایل کلید بازیابی مدیر دانلود شد؛ آن را در محل امن و جدا از رایانه نگهداری کنید.'}}await api('login',{method:'POST',body:data});$('#login-error').textContent='';await load();if(recoveryNotice)notice(recoveryNotice)}")

insert_anchor = "$('#logout').onclick="
assert js.count(insert_anchor) == 1
js = js.replace(insert_anchor, """function downloadRecoveryKey(key){const text='سامانه آموزش نیکان — کلید بازیابی رمز عبور مدیر\\n\\n'+key+'\\n\\nاین کلید فقط یک\\u200cبار قابل استفاده است؛ آن را در محل امن و جدا از رایانه نگهداری کنید.\\n';download(new Blob([text],{type:'text/plain;charset=utf-8'}),'Nikan-Admin-Recovery-Key.txt')}
$('#show-recover').onclick=()=>{$('#auth').hidden=true;$('#recover').hidden=false;$('#show-recover').hidden=true;$('#show-login').hidden=false;$('#login-error').textContent='';$('#login-notice').textContent=''};
$('#show-login').onclick=()=>{$('#recover').hidden=true;$('#auth').hidden=false;$('#show-recover').hidden=false;$('#show-login').hidden=true;$('#recover').reset();$('#login-error').textContent='';$('#login-notice').textContent=''};
$('#recover').onsubmit=async e=>{
 e.preventDefault();const form=e.target;if(form.dataset.busy)return;
 const data=Object.fromEntries(new FormData(form));
 if(data.password!==data.password_confirm){$('#login-error').textContent='تکرار رمز عبور با آن همخوانی ندارد';return}
 setFormBusy(form,true);
 try{await api('recover',{method:'POST',body:{username:data.username,recovery_key:data.recovery_key,password:data.password}});const uname=data.username;form.reset();$('#recover').hidden=true;$('#auth').hidden=false;$('#show-recover').hidden=false;$('#show-login').hidden=true;$('#login-error').textContent='';$('#login-notice').textContent='رمز عبور مدیر بازنشانی شد؛ اکنون با رمز جدید وارد شوید.';$('#auth [name=username]').value=uname}
 catch(err){if(err.name!=='AbortError')$('#login-error').textContent=err.message}finally{setFormBusy(form,false)}
};
""" + insert_anchor)

old_box = "box.innerHTML='<h2>کنترل حساب‌ها</h2>'+rows.map("
assert js.count(old_box) == 1
js = js.replace(old_box,
 "box.innerHTML='<h2>کنترل حساب‌ها</h2><div class=\"company-row\"><span>کلید بازیابی رمز عبور مدیر</span><button class=\"quiet\" id=\"issue-recovery\">دریافت کلید جدید</button></div>'+rows.map(")

old_click = "const t=e.target.closest('[data-toggle]'),p=e.target.closest('[data-password]');try{if(t){"
assert js.count(old_click) == 1
js = js.replace(old_click,
 "const t=e.target.closest('[data-toggle]'),p=e.target.closest('[data-password]'),rk=e.target.closest('#issue-recovery');if(rk){try{const out=await api('recovery-key',{method:'POST'});downloadRecoveryKey(out.recovery_key);notice('کلید بازیابی جدید دانلود شد؛ کلید قبلی باطل شد.')}catch(err){notice(err.message)}return}try{if(t){")

# ---------------------------------------------------------------- docs
section = """

## بازیابی رمز عبور مدیر

اگر رمز ورود حساب «مدیر هلدینگ» فراموش شود، بدون نیاز به ورود و از همان صفحهٔ ورود می\u200cتوان آن را با «کلید بازیابی» بازنشانی کرد:

1. هنگام نخستین راه\u200cاندازی، سامانه یک کلید تصادفی ۲۵۶ بیتی می\u200cسازد و آن را به\u200cصورت فایل `Nikan-Admin-Recovery-Key.txt` دانلود می\u200cکند. کلید را در خزانهٔ امن و جدا از رایانه نگهداری کنید.
2. مدیر واردشده می\u200cتواند از «مدیریت کاربران ← دریافت کلید جدید» کلید تازه بسازد؛ صدور کلید جدید، کلید قبلی را باطل می\u200cکند.
3. در صفحهٔ ورود «بازیابی رمز عبور مدیر» را بزنید و نام کاربری مدیر، کلید بازیابی و رمز جدید (حداقل ۱۲ نویسه) را وارد کنید؛ سپس با رمز جدید وارد شوید.

هر کلید فقط یک\u200cبار معتبر است، فقط برای حساب\u200cهای نقش «مدیر هلدینگ» کار می\u200cکند و صرفاً به\u200cصورت هش SHA-256 در پایگاه ذخیره می\u200cشود (متن کلید روی دیسک نگهداری نمی\u200cشود). پس از ۵ تلاش ناموفق، ۱۵ دقیقه مسدود می\u200cشود و رویدادها در گزارش ممیزی ثبت می\u200cگردند. این کلید با «کلید بازیابی پشتیبان» (`recovery.key`) متفاوت است: اولی رمز ورود را بازنشانی می\u200cکند، دومی فایل\u200cهای پشتیبان رمزگذاری\u200cشده را باز می\u200cکند.

### بازنشانی آفلاین توسط IT (بدون کلید)

اگر کلید گم شده و هیچ مدیر دیگری وارد سامانه نشده است، IT با دسترسی محلی به رایانه و پس از بستن کامل برنامه می\u200cتواند مستقیماً هش رمز مدیر را در پایگاه بازنویسی کند. نمونه برای نصب مستقل (PowerShell، مسیرها را در صورت نصب سفارشی تنظیم کنید):

```powershell
$env:NIKAN_NEW_PASSWORD = 'یک-رمز-جدید-حداقل-دوازده-نویسه'
@'
import os, sqlite3
from werkzeug.security import generate_password_hash
p = os.path.join(os.environ['LOCALAPPDATA'], 'NikanTraining', 'nikan.sqlite3')
new = os.environ['NIKAN_NEW_PASSWORD']
assert len(new) >= 12, 'رمز خیلی کوتاه است'
c = sqlite3.connect(p)
c.execute("UPDATE users SET password=? WHERE role='admin' AND active=1", (generate_password_hash(new),))
c.commit()
print('رمز مدیر بازنشانی شد')
'@ | & "$env:LOCALAPPDATA\\Programs\\NikanTraining\\runtime\\python.exe" -
Remove-Item Env:\\NIKAN_NEW_PASSWORD
```

پیش از اجرا از پایگاه پشتیبان بگیرید. این روش نیازی به کلید بازیابی ندارد، ولی به دسترسی محلی به فایل `nikan.sqlite3` وابسته است؛ بنابراین کلید بازیابی همچنان مسیر پیشنهادی و کم\u200cریسک\u200cتر است.
"""

network_section = """

## بازیابی رمز عبور مدیر (نسخهٔ شبکه)

بازیابی رمز ورود مدیر از همان صفحهٔ ورود و با کلید بازیابی انجام می\u200cشود؛ نیازی به توقف سرور، ابزار آفلاین یا فایل پشتیبان نیست. **کلید بازیابی رمز ورود را جدا از فایل\u200cهای `.nikbak` و کلید `keys\\recovery.key` نگهداری کنید**: کلید نخست فقط رمز ورود مدیر را بازنشانی می\u200cکند و کلید دوم فقط پشتیبان\u200cهای رمزگذاری\u200cشده را باز می\u200cکند. صدور کلید تازه و بازنشانی رمز فقط برای نقش «مدیر هلدینگ» است، هر کلید یک\u200cبار مصرف می\u200cشود و رویدادها در گزارش ممیزی ثبت می\u200cگردند. محدودیت ۵ تلاش ناموفق در ۱۵ دقیقه نیز روی درخواست\u200cهای بازیابی اعمال می\u200cشود.

اگر کلید گم شده و هیچ مدیری وارد نشده است، IT پس از **Stop server** و تهیهٔ پشتیبان می\u200cتواند هش رمز مدیر را مستقیماً در `%ProgramData%\\NikanTrainingServer\\nikan.sqlite3` بازنویسی کند (همان الگوی بالا، با مفسر `C:\\Program Files\\NikanTrainingServer\\runtime\\python.exe`). سپس Start server را اجرا کنید. این کار جایگزین نگهداری امن کلید بازیابی نیست.
"""

for kind, base in SRC.items():
    rd = open(base + '/README.md', encoding='utf-8').read()
    rd = rd.replace('v0.10.0', NEW_TAG)
    rd = rd.rstrip('\n') + '\n' + section.lstrip('\n')
    d = f'{OUT}/{kind}'
    os.makedirs(d + '/app/static', exist_ok=True)
    open(d + '/README.md', 'w', encoding='utf-8').write(rd)
    open(d + '/app/__init__.py', 'w', encoding='utf-8').write(py)
    open(d + '/app/static/index.html', 'w', encoding='utf-8').write(html)
    open(d + '/app/static/app.js', 'w', encoding='utf-8').write(js)
    if kind == 'server':
        nr = open(base + '/NETWORK-README.md', encoding='utf-8').read()
        nr = nr.replace('v0.10.0', NEW_TAG)
        nr = nr.rstrip('\n') + '\n' + network_section.lstrip('\n')
        open(d + '/NETWORK-README.md', 'w', encoding='utf-8').write(nr)

py_compile.compile(f'{OUT}/setup/app/__init__.py', doraise=True)
print('Python syntax OK')
for kind in SRC:
    for rel in ['README.md', 'app/__init__.py', 'app/static/index.html', 'app/static/app.js'] + (['NETWORK-README.md'] if kind == 'server' else []):
        p = f'{OUT}/{kind}/{rel}'
        print(f'{kind:6s} {rel:28s} {os.path.getsize(p):7d} bytes  sha256={hashlib.sha256(open(p,"rb").read()).hexdigest()[:16]}')
