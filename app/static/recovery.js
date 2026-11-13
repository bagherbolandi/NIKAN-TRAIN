const recoveryToggle=document.createElement('button');recoveryToggle.type='button';recoveryToggle.className='quiet';recoveryToggle.id='recovery-toggle';recoveryToggle.textContent='رمز عبور را فراموش کرده‌ام';$('#auth').after(recoveryToggle);
const recoveryForm=document.createElement('form');recoveryForm.id='password-recovery';recoveryForm.hidden=true;
recoveryForm.innerHTML='<h2>بازیابی رمز ورود</h2><p class="help">از منوی Start ویندوز، میانبر «Nikan Password Recovery» را اجرا کنید تا کد یک‌بارمصرف بگیرید. در نسخه شبکه، کد را از مدیر سرور دریافت کنید. کد ۳۰ دقیقه اعتبار دارد.</p><label>نام کاربری<input name="username" required autocomplete="username"></label><label>کد بازیابی<input name="code" required autocomplete="off" dir="ltr"></label><label>رمز جدید (۱۲ تا ۲۵۶ نویسه)<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label><label>تکرار رمز جدید<input name="confirm" type="password" required autocomplete="new-password"></label><p class="error" id="recovery-error" role="alert"></p><button type="submit">ثبت رمز جدید</button>';
recoveryToggle.after(recoveryForm);
recoveryToggle.onclick=()=>{const opening=recoveryForm.hidden;recoveryForm.hidden=!opening;$('#auth').hidden=opening;recoveryToggle.textContent=opening?'بازگشت به ورود':'رمز عبور را فراموش کرده‌ام';$('#recovery-error').textContent='';if(opening)recoveryForm.elements.username.focus();else recoveryForm.reset()};
recoveryForm.onsubmit=async event=>{
 event.preventDefault();if(recoveryForm.dataset.busy)return;
 const data=Object.fromEntries(new FormData(recoveryForm));
 if(data.password!==data.confirm){$('#recovery-error').textContent='تکرار رمز با رمز جدید یکسان نیست';return}
 setFormBusy(recoveryForm,true);
 try{await api('password-recovery',{method:'POST',body:{username:data.username,code:data.code,password:data.password}});recoveryToggle.click();$('#auth [name=username]').value=data.username;$('#auth [name=password]').value='';$('#login-error').textContent='رمز جدید ثبت شد. اکنون وارد شوید.'}
 catch(err){$('#recovery-error').textContent=err.message}
 finally{setFormBusy(recoveryForm,false)}
};
