# NIKAN-TRAIN

## دانلود آخرین نسخهٔ نصبی

آخرین بسته‌های نصب ویندوز (انتشار [`v0.10.2`](https://github.com/bagherbolandi/NIKAN-TRAIN/releases/tag/v0.10.2)، شامل **بازیابی رمز ورود مدیر** و کارپوشهٔ نمونه با رمز محافظت به&zwnj;روزشده `QA383940`):

| فایل | لینک دانلود |
| --- | --- |
| نصاب مستقل ویندوز | [NikanTraining-Setup.exe](https://github.com/bagherbolandi/NIKAN-TRAIN/blob/v0.10.2/NikanTraining-Setup.exe?raw=true) |
| نصاب سرور شبکه | [NikanTraining-Network-Server-Setup.exe](https://github.com/bagherbolandi/NIKAN-TRAIN/blob/v0.10.2/NikanTraining-Network-Server-Setup.exe?raw=true) |
| چک&zwnj;سام SHA-256 | [SHA256SUMS.txt](https://github.com/bagherbolandi/NIKAN-TRAIN/blob/v0.10.2/SHA256SUMS.txt?raw=true) |

[صفحهٔ انتشار آخرین نسخه](https://github.com/bagherbolandi/NIKAN-TRAIN/releases/latest) · [همهٔ انتشارها](https://github.com/bagherbolandi/NIKAN-TRAIN/releases)

فایل&zwnj;ها مستقیماً از برچسب ثابت `v0.10.2` دریافت می&zwnj;شوند (لینک `?raw=true` مشابه شیوهٔ نسخه&zwnj;های قبل است). مخزن خصوصی است؛ برای دانلود باید با حساب GitHub دارای دسترسی به `bagherbolandi/NIKAN-TRAIN` وارد شوید و در صفحهٔ فایل، در صورت نیاز **Download raw file** را بزنید. به&zwnj;دلیل محدودیت بارگذاری دارایی&zwnj;های باینری در انتشار GitHub، از لینک `releases/latest/download/...` استفاده نکنید؛ آن صفحه دارای پیوست باینری نیست.

## بازیابی رمز عبور مدیر (جدید در ۰٫۱۰٫۲)

اگر رمز ورود حساب «مدیر هلدینگ» فراموش شود، دیگر نیازی به دستکاری پایگاه یا ابزار آفلاین نیست:

- هنگام نخستین راه&zwnj;اندازی، سامانه یک «کلید بازیابی» تصادفی می&zwnj;سازد و آن را به&zwnj;صورت فایل `Nikan-Admin-Recovery-Key.txt` دانلود می&zwnj;کند.
- مدیر واردشده می&zwnj;تواند از **مدیریت کاربران ← دریافت کلید جدید** کلید تازه بسازد (کلید قبلی باطل می&zwnj;شود).
- در صفحهٔ ورود، **بازیابی رمز عبور مدیر** را بزنید و نام کاربری، کلید و رمز جدید (حداقل ۱۲ نویسه) را وارد کنید.

هر کلید یک&zwnj;بار مصرف است، فقط برای نقش «مدیر هلدینگ» کار می&zwnj;کند، به&zwnj;صورت هش SHA-256 ذخیره می&zwnj;شود و پس از ۵ تلاش ناموفق ۱۵ دقیقه مسدود می&zwnj;گردد. رویدادها در گزارش ممیزی ثبت می&zwnj;شوند. این کلید با «کلید بازیابی پشتیبان» (`recovery.key` در نسخهٔ شبکه) متفاوت است. راهنمای کامل، از جمله روش آفلاین IT، در `README.md` و `NETWORK-README.md` داخل هر نصاب آمده است.

نسخه&zwnj;های پیشین: [`v0.10.1`](https://github.com/bagherbolandi/NIKAN-TRAIN/releases/tag/v0.10.1) فقط شامل به&zwnj;روزرسانی رمز محافظت کارپوشه بود.
