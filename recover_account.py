"""Local Windows-owner / server-administrator recovery helper. No network issuance."""
import argparse,os,sqlite3
from pathlib import Path
from contextlib import closing
from app.recovery import issue

def main():
    parser=argparse.ArgumentParser(description='Generate a one-use password recovery code on the installation host.')
    parser.add_argument('--server',action='store_true')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--username')
    args=parser.parse_args()
    root=args.data_dir or (Path(os.environ.get('PROGRAMDATA' if args.server else 'LOCALAPPDATA',str(Path.home())))/('NikanTrainingServer' if args.server else 'NikanTraining'))
    dbpath=root/'nikan.sqlite3'
    if not dbpath.exists():raise SystemExit('No database exists here. Complete initial setup first.')
    with closing(sqlite3.connect(dbpath)) as conn:
        username=args.username
        if not username:
            print('Active accounts:')
            for row in conn.execute('SELECT username FROM users WHERE active=1'):print('  '+row[0])
            username=input('Account username: ').strip()
        token=issue(conn,username)
    print('\nUsername: '+username+'\nRecovery code (one use, expires in 30 minutes):\n'+token)
    print('\nOpen Nikan Training > Password recovery. Enter this code and your new password.')
    if not args.username:input('\nPress Enter to close...')

if __name__=='__main__':main()
