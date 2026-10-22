"""Opaque server-side sessions and PBKDF2 passwords, using the standard library."""
import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import HTTPException

COOKIE='bioflow_session'
USERNAME=re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}$')
ITERATIONS=600000


def hash_password(password):
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def verify_password(password,stored):
    try:
        algorithm,iterations,salt,digest=stored.split('$')
        if algorithm!='pbkdf2_sha256':
            return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(iterations)).hex()
        return hmac.compare_digest(actual,digest)
    except (ValueError,TypeError):
        return False


def create_user(store,username,password,role='user'):
    username=username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise HTTPException(422,'用户名须为 3–32 位字母、数字、点、下划线或短横线')
    if len(password)<8 or len(password)>128:
        raise HTTPException(422,'密码长度须为 8–128 个字符')
    import sqlite3
    try:
        with store.connect() as db:
            uid=uuid.uuid4().hex
            db.execute('INSERT INTO users(id,username,password_hash,role) VALUES(?,?,?,?)',(uid,username,hash_password(password),role))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409,'该用户名已注册') from exc
    return {'id':uid,'username':username,'role':role}


def session_user(store,token):
    if not token:
        return None
    hashed=hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        row=db.execute('SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(hashed,time.time())).fetchone()
        return dict(row) if row else None


def new_session(store,uid):
    token=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(24)
    with store.connect() as db:
        db.execute('DELETE FROM sessions WHERE expires_at<?',(time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,csrf,time.time()+12*3600))
    return token,csrf


def logout(store,token):
    with store.connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256((token or '').encode()).hexdigest(),))


def login(store,username,password,address):
    key=hashlib.sha256((address+'|'+username.strip().lower()).encode()).hexdigest()
    now=time.time()
    with store.connect() as db:
        attempt=db.execute('SELECT count,reset_at FROM login_attempts WHERE key=?',(key,)).fetchone()
        if attempt and attempt['reset_at']>now and attempt['count']>=8:
            raise HTTPException(429,'尝试次数较多，请稍后登录')
        user=db.execute('SELECT * FROM users WHERE username=?',(username.strip().lower(),)).fetchone()
    # Equal work for unknown accounts prevents a trivial timing-based user lookup.
    candidate=user['password_hash'] if user else f'pbkdf2_sha256${ITERATIONS}$'+('00'*16)+'$'+('00'*32)
    if not verify_password(password,candidate) or not user:
        with store.connect() as db:
            count=(attempt['count']+1) if attempt and attempt['reset_at']>now else 1
            db.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,now+900))
        raise HTTPException(401,'用户名或密码错误')
    with store.connect() as db:
        db.execute('DELETE FROM login_attempts WHERE key=?',(key,))
    return {k:user[k] for k in ('id','username','role')}
