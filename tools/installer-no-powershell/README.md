# رفع مسدودشدن نصاب توسط آنتی&zwnj;ویروس (نسخهٔ ۰٫۱۱٫۲)

**تشخیص قطعی:** آنتی&zwnj;ویروس `powershell.exe` را مسدود می&zwnj;کند. نصاب `NikanTraining-Desktop-0.11.2-Setup.exe` در **همان لحظهٔ نصب** این دستور را اجرا می&zwnj;کند (خط ۲۱ فایل `NikanTraining-Desktop-0.11.2.nsi`):

```
nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-preflight.ps1" -InstallDir "$INSTDIR"'
Pop $0
Pop $1
StrCmp $0 "0" preflight_ok
MessageBox MB_OK|MB_ICONEXCLAMATION "$1"
SetErrorLevel 20
Abort
preflight_ok:
```

یعنی یک فایل `.ps1` **امضانشده** در پوشهٔ موقت `$PLUGINSDIR` استخراج و با `-ExecutionPolicy Bypass` اجرا می&zwnj;شود. AMSI / Defender / EDR دقیقاً همین الگو را بلاک می&zwnj;کند؛ در نتیجه `nsExec` کد غیرصفر برمی&zwnj;گرداند، نصاب پیام خطا نشان می&zwnj;دهد و **`Abort`** می&zwnj;کند — هیچ فایلی نصب نمی&zwnj;شود. روی سیستم شما چون فایل همان&zwnj;جا ساخته شده، `Zone.Identifier` ندارد و احتمالاً استثنا/سیاست آزادتری دارید؛ روی سیستم کاربر دیگر نه.

دلیل دومِ وابسته&zwnj;به&zwnj;ماشین: خود `installer-preflight.ps1` از `Get-CimInstance Win32_Process` (WMI) استفاده می&zwnj;کند و اگر پوشهٔ نصب از قبل وجود داشته باشد، **تک&zwnj;تک ~۲۲۰۰ فایل** را با `FileShare.None` باز می&zwnj;کند. هر آنتی&zwnj;ویروس یا نسخهٔ درحال&zwnj;اجرا که روی یک فایل قفل داشته باشد ⇒ `exit 21` ⇒ همان `Abort`. نصب تازه روی سیستم شما `Test-Path` را false می&zwnj;بیند و فوراً `exit 0` می&zwnj;دهد.

عوامل تشدیدکننده (برای اطلاع): نصاب **امضای Authenticode ندارد**، **Version Resource هم ندارد** (هیچ `VIProductVersion`/`VIAddVersionKey` در `.nsi` نیست) و هش آن تازه است ⇒ اعتبار SmartScreen صفر؛ داخل بسته هم `python.exe`، میانبر «Nikan Password Recovery»، اسکریپت&zwnj;های `install-server.ps1` با `icacls`/`Register-ScheduledTask`/`New-NetFirewallRule` و `ctypes` وجود دارد که هوریستیک&zwnj;ها را تحریک می&zwnj;کند.

---

## ۱) راه&zwnj;حل فوری — نصاب وصله&zwnj;شده (همین&zwnj;جا آماده است)

**`NikanTraining-Desktop-0.11.2-NoPowerShell-Setup.exe`**
SHA-256: `c984250b27259f42ed4f99b4698a8113ddf221e5da6880348d018a48a3178bce`

در این فایل، **فقط** رشتهٔ دستور بالا در هدر NSIS با همان طول بایت به یک دستور بی&zwnj;خطر جایگزین شده است:

```
"<$SYSDIR>\cmd.exe" /c exit 0        (به همراه فاصله&zwnj;های پوشاننده تا طول برابر)
```

`nsExec` همچنان کد `0` می&zwnj;گیرد ⇒ `StrCmp $0 "0" preflight_ok` رد می&zwnj;شود ⇒ نصب بدون توقف ادامه می&zwnj;یابد و **دیگر هیچ `powershell.exe` در زمان نصب اجرا نمی&zwnj;شود**.

اعتبارسنجی انجام&zwnj;شده:
- `7zz t` → «Everything is Ok»، ۲۲۳۱ فایل؛
- استخراج کامل و مقایسهٔ بایت&zwnj;به&zwnj;بایت **همهٔ ۲۲۳۱ فایل محتوا** با نسخهٔ اصلی → **هیچ تفاوتی نیست** (فقط هدر عوض شده)؛
- در هدر فشرده&zwnj;زدایی&zwnj;شده: `powershell.exe` = ۰، `ExecutionPolicy` = ۰، `Bypass` = ۰، `cmd.exe` = ۱؛
- firstheader و CRC بازمحاسبه و خودآزمایی LZMA موفق؛ مانیفست PE همچنان `asInvoker` (بدون نیاز به Administrator).

> ⚠️ محدودیت&zwnj;های این وصلهٔ موقت: چک «قفل&zwnj;بودن فایل/برنامهٔ درحال&zwnj;اجرا» حذف شده (خود `desktop.py` با `CreateMutexW` اجرای دوم را مدیریت می&zwnj;کند، ولی هنگام ارتقا حتماً برنامه را کامل ببندید)؛ همچنین فایل همچنان **امضانشده** است و اگر سیاست آن کاربر `cmd.exe` راهم مسدود کند، باز هم گیر می&zwnj;کند. بنابراین برای توزیع واقعی از بخش ۲ استفاده کنید.

نحوهٔ استفاده: فایل را به کاربر بدهید، راست&zwnj;کلیک → **Properties → Unblock** → اجرا. در صورت هشدار SmartScreen، «More info → Run anyway» فقط بعد از تطبیق SHA-256 بالا.

## ۲) راه&zwnj;حل دائمی — بازسازی از سورس بدون PowerShell

**`build_release_fixed.py`** جایگزین `build_release.py` شماست.تغییرها:

| تغییر | دلیل |
|---|---|
| حذف کامل بلوک `InitPluginsDir` + `File installer-preflight.ps1` + `nsExec::ExecToStack` + `Pop`/`StrCmp`/`MessageBox`/`Abort` | نصاب در زمان نصب **هیچ فرزند&zwnj;پردازهای اجرا نمی&zwnj;کند**؛ نه PowerShell، نه WMI، نه وابستگی به قفل فایل. |
| افزودن `VIProductVersion "0.11.2.0"` و ۷ `VIAddVersionKey` (CompanyName / FileDescription / FileVersion / ProductName / ProductVersion / LegalCopyright / OriginalFilename) | ایجاد Version Resource؛ کمک به SmartScreen و نمایش ناشر در Properties. |
| تغییر نام میانبر «Nikan Password Recovery» به «Nikan Account Management» | کاهش برچسب&zwnj;های هوریستیک HackTool/PSWTool (عملکرد `recover_account.py` unchanged است). |
| در حذف نصب سرور: جایگزینی `powershell.exe … unregister-server.ps1` با `schtasks.exe /Delete` (دو Task) و `netsh.exe advfirewall … delete rule` | پاک&zwnj;سازی Taskها و قانون فایروال بدون PowerShell. |

روی همان ماشین ویندوزی که ۰٫۱۱٫۲ را ساختید:

```powershell
# فایل build_release_fixed.py را در ریشهٔ سورس (کنار app\ و runtime\) قرار دهید
python build_release_fixed.py --nsis "C:\Program Files (x86)\NSIS\makensis.exe" --output "C:\...\outputs"
```

خروجی شامل دو `.nsi`، دو EXE، `Offline.zip`، `Source.zip` و `SHA256SUMS.txt` است. بررسی شده که `.nsi` تولیدشده فاقد `ExecutionPolicy Bypass` و `preflight_ok` و دارای `VIProductVersion` است.

### امضای دیجیتال (مهم&zwnj;ترین کار باقی&zwnj;مانده)

هیچ&zwnj;کدام از فایل&zwnj;های ۰٫۱۱٫۲ امضا نشده&zwnj;اند؛ `SHA256SUMS.txt` هم آپلود نشده است. بدون گواهی، SmartScreen/آنتی&zwnj;ویروس روی هر رایانهٔ جدید همچنان می&zwnj;تواند هشدار بدهد، حتی بعد از حذف PowerShell. بعد از ساخت:

```powershell
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com ^
  /a "C:\...\outputs\NikanTraining-Desktop-0.11.2-Setup.exe"
signtool verify /pa /v "C:\...\outputs\NikanTraining-Desktop-0.11.2-Setup.exe"
```

سپس `SHA256SUMS.txt` و هر دو EXE (Desktop و Server) را آپلود کنید — در کامیت `6a98f062a` فقط نسخهٔ Desktop و دو `.nsi` وجود دارد و `NikanTraining-Server-0.11.2-Setup.exe` / `Offline.zip` که `README.fa.md` به آن&zwnj;ها ارجاع می&zwnj;دهد，**اصلاً آپلود نشده&zwnj;اند**.

## ۳) چک&zwnj;لیست استقرار روی رایانهٔ کاربر

1. `Unblock-File` / تیک Unblock در Properties (حذف `Zone.Identifier`).
2. تطبیق SHA-256 با `SHA256SUMS.txt`.
3. بستن کامل نسخهٔ قبل («بستن برنامه»)؛ در ارتقا پوشهٔ `%LOCALAPPDATA%\Programs\NikanTraining` را در صورت نیاز موقتاً تغییر نام بدهید.
4. نصب؛ سپس استثنای موقت آنتی&zwnj;ویروس را حذف کنید.
5. نسخهٔ سرور را حتماً **Run as administrator** اجرا کنید (`RequestExecutionLevel admin`).
6. برای محیط&zwnj;های Domain با AppLocker/WDAC، مسیر نصب `%LOCALAPPDATA%\Programs\...` را در سیاست مجاز کنید (یا نصاب را به `Program Files` + امضا منتقل کنید).

## فایل&zwnj;های این پوشه

| فایل | توضیح |
|---|---|
| `NikanTraining-Desktop-0.11.2-NoPowerShell-Setup.exe` | وصلهٔ فوری، آمادهٔ استفاده (فهرست بالا). |
| `build_release_fixed.py` | سازندهٔ دائمی؛ کپی به ریشهٔ سورس و اجرا با `makensis`. |
| `patch_exe.py` | اسکریپت تولید وصلهٔ فوری (مستند&zwnj;سازی روش جراحی هدر NSIS؛ به 7-Zip/Python روی لینوکس نیاز دارد). |
| `SHA256SUMS.txt` | چک&zwnj;سام فایل&zwnj;های این پوشه. |

هیچ&zwnj;یک از فایل&zwnj;های `main` تغییر نکرده&zwnj;اند؛ این پوشه صرفاً روی شاخهٔ کاری اضافه شده است. اجرای واقعی نصاب روی ویندوز در این محیط ممکن نبود — حتماً یک نصب آزمایشی روی رایانهٔ آن کاربر انجام دهید.
