"""Separate local test installation; never selects the old desktop's database."""
import os,sys,logging
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def main():
    # Importing this launcher (including test discovery) must never change the
    # profile of another desktop installation in the current process.
    os.environ['NIKAN_DESKTOP_PROFILE'] = 'NikanHRM-Test'
    from desktop import main as start_desktop
    start_desktop()

if __name__ == '__main__':
    try:main()
    except Exception:
        logging.exception('HRM test startup failed')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'اجرای نسخه آزمایشی ناموفق بود؛ فایل desktop.log در پوشه NikanHRM-Test را بررسی کنید.','نیکان HRM',0x10)
        else:raise
