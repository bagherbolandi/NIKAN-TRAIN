import argparse, threading, webbrowser
from app import create_app
from waitress import serve
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
 app=create_app()
 if not args.no_browser:threading.Timer(1.5,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
 print(f'Nikan Training: http://127.0.0.1:{args.port} — Ctrl+C to stop')
 serve(app,host='127.0.0.1',port=args.port,threads=6)
