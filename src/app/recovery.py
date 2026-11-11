"""One-use recovery secrets minted only with local database access."""
import hashlib,secrets,sqlite3,time
from flask import jsonify,request,session
from werkzeug.security import generate_password_hash
from .validation import json_object

def migrate(conn):
    if 'auth_version' not in {r[1] for r in conn.execute('PRAGMA table_info(users)')}:
        conn.execute('ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0')
    conn.executescript('''CREATE TABLE IF NOT EXISTS password_recovery(
        user_id INTEGER PRIMARY KEY REFERENCES users(id),digest TEXT NOT NULL,
        expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recovery_rate(address TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);''')
    conn.commit()

def issue(conn,username,invalidate_password=False):
    """Not exposed over HTTP; caller must have OS access to the database."""
    migrate(conn)
    token=secrets.token_urlsafe(24)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        user=conn.execute('SELECT id,company_id FROM users WHERE username=? AND active=1',(username,)).fetchone()
        if not user:raise ValueError('Active account not found')
        conn.execute('INSERT OR REPLACE INTO password_recovery(user_id,digest,expires,attempts) VALUES (?,?,?,0)',(user[0],hashlib.sha256(token.encode()).hexdigest(),time.time()+1800))
        if invalidate_password:
            conn.execute('UPDATE users SET password=?,auth_version=auth_version+1 WHERE id=?',(generate_password_hash(secrets.token_urlsafe(48)),user[0]))
        conn.execute('INSERT INTO audit(user_id,company_id,action,detail) VALUES (?,?,?,?)',(user[0],user[1],'local_recovery_issued','Password invalidated' if invalidate_password else 'One-use code issued'))
    return token

def register(app,db,error):
    with app.app_context():migrate(db())

    @app.post('/api/password-recovery')
    def recover():
        d=json_object()
        username=d.get('username','');code=d.get('code','');password=d.get('password','')
        if not all(isinstance(v,str) for v in (username,code,password)):raise ValueError('اطلاعات بازیابی نامعتبر است')
        if not 12<=len(password)<=256:raise ValueError('رمز جدید باید بین ۱۲ تا ۲۵۶ نویسه باشد')
        if len(username)>100 or len(code)>128:raise ValueError('اطلاعات بازیابی نامعتبر است')
        address=request.remote_addr or 'unknown';now=time.time()
        with db():
            db().execute('BEGIN IMMEDIATE')
            rate=db().execute('SELECT * FROM recovery_rate WHERE address=?',(address,)).fetchone()
            if rate and rate['until']>now and rate['count']>=10:return error('تلاش‌های زیاد؛ ۱۵ دقیقه بعد دوباره امتحان کنید',429)
            row=db().execute('''SELECT r.*,u.active,u.company_id FROM password_recovery r
                JOIN users u ON u.id=r.user_id WHERE u.username=?''',(username.strip(),)).fetchone()
            valid=row and row['active'] and row['expires']>now and row['attempts']<5 and secrets.compare_digest(row['digest'],hashlib.sha256(code.strip().encode()).hexdigest())
            if not valid:
                count=(rate['count'] if rate and rate['until']>now else 0)+1
                db().execute('INSERT OR REPLACE INTO recovery_rate VALUES (?,?,?)',(address,count,now+900))
                if row:db().execute('UPDATE password_recovery SET attempts=attempts+1 WHERE user_id=?',(row['user_id'],))
                return error('نام کاربری یا کد بازیابی نامعتبر، منقضی یا مصرف‌شده است',400)
            db().execute('UPDATE users SET password=?,auth_version=auth_version+1 WHERE id=?',(generate_password_hash(password),row['user_id']))
            db().execute('DELETE FROM password_recovery WHERE user_id=?',(row['user_id'],))
            db().execute('DELETE FROM login_attempts WHERE username=?',(username.strip(),))
            db().execute('DELETE FROM recovery_rate WHERE address=?',(address,))
            db().execute('INSERT INTO audit(user_id,company_id,action,detail) VALUES (?,?,?,?)',(row['user_id'],row['company_id'],'password_recovered','All previous sessions revoked'))
        session.clear()
        return jsonify(ok=True)
