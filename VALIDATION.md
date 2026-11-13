# اعتبارسنجی Nikan HRM ۰٫۱۳

گزارش جاری در [validation-0.13.md](docs/validation-0.13.md) و فایل‌های JSON/XML همراه آن ثبت می‌شود. گزارش‌های نسخه‌های قبلی صرفاً سابقه همان انتشارها هستند.

## اجرای مجدد

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q --junitxml=docs/pytest-0.13-final.xml
.venv/bin/python tests/browser_hrm_smoke.py
.venv/bin/python tests/browser_org_accord_smoke.py
.venv/bin/python packaging/validate_hrm_payload.py --install . --report /tmp/hrm-source-result.json
```

مرورگر آزمون از `/usr/bin/chromium` استفاده می‌کند. داده‌ها، کاربران، مدارک و کلیدهای آزمون مصنوعی و موقت‌اند. آزمون مهاجرت، داده را با سورس تاریخی واقعی ۰٫۱۰ و ۰٫۱۱٫۲ تولید و مقادیر اصلی را پس از ارتقا، شروع مجدد و بازیابی مقایسه می‌کند.

## مرز بررسی ویندوز

ساخت EXE با NSIS، استخراج بسته و اجرای برنامه استخراج‌شده در Linux با اجرای نصب روی Windows یکسان نیست. تنها نتیجهٔ مشاهده‌شدهٔ workflow همین سورس با `native_attempted=true`، `native_passed=true` و نتیجهٔ موفق هر دو نصاب، تأیید بومی Windows محسوب می‌شود.

[workflow ویندوز](.github/workflows/windows-validation.yml) آزمون‌های سورس، نصب/ارتقا/خروج/حذف نسخه آزمایشی، سرویس مرکزی HTTPS، اتصال همزمان، کنترل HRM و بازیابی رمزگذاری‌شده را اجرا می‌کند. گزارش به commit و شماره اجرای GitHub متصل است و در artifact و شاخه جدید و تغییرناپذیر `validation/windows-<source_sha>-<run_id>` ذخیره می‌شود. نبود گزارش به معنی موفقیت نیست.
