"""Close only the isolated HRM test installation in this Windows session."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stop_desktop import stop

if __name__ == '__main__':
    stop('NikanHRM-Test')
