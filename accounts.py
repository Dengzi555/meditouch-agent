"""Local accounts and revocable browser sessions."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from http.cookies import CookieError, SimpleCookie

COOKIE_NAME = 'yidiantong_session'
SESSION_SECONDS = 7 * 24 * 60 * 60


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, digest BLOB NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS account_sessions (digest TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL)')


def password_digest(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def request_token(cookie_header):
    try:
        cookie = SimpleCookie(cookie_header or '')
        value = cookie[COOKIE_NAME].value
    except (CookieError, KeyError):
        return None
    return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else None


def session_cookie(token=None):
    age = SESSION_SECONDS if token else 0
    return f'{COOKIE_NAME}={token or ""}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}'


def current_account(db, token):
    if not token:
        return None
    row = db.execute(
        'SELECT accounts.id, accounts.username FROM accounts JOIN account_sessions '
        'ON accounts.id=account_sessions.account_id WHERE account_sessions.digest=? AND account_sessions.expires>?',
        (token_digest(token), time.time()),
    ).fetchone()
    return dict(id=row[0], username=row[1]) if row else None


def sign_in(db, username, password, *, register=False, previous_token=None):
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError('请输入用户名和密码')
    username = username.strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{3,24}', username):
        raise ValueError('用户名须为 3–24 位字母、数字或下划线')
    if not 8 <= len(password) <= 128:
        raise ValueError('密码须为 8–128 个字符')
    if register:
        account_id = secrets.token_hex(16)
        salt = secrets.token_bytes(16)
        try:
            db.execute('INSERT INTO accounts VALUES (?,?,?,?)',
                       (account_id, username, salt, password_digest(password, salt)))
        except sqlite3.IntegrityError:
            raise ValueError('用户名已被使用') from None
    else:
        row = db.execute('SELECT id,salt,digest FROM accounts WHERE username=?', (username,)).fetchone()
        digest = password_digest(password, row[1] if row else bytes(16))
        if row is None or not hmac.compare_digest(digest, row[2]):
            raise ValueError('用户名或密码错误')
        account_id = row[0]
    revoke(db, previous_token)
    db.execute('DELETE FROM account_sessions WHERE expires<=?', (time.time(),))
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO account_sessions VALUES (?,?,?)',
               (token_digest(token), account_id, time.time() + SESSION_SECONDS))
    return dict(id=account_id, username=username), token


def revoke(db, token):
    if token:
        db.execute('DELETE FROM account_sessions WHERE digest=?', (token_digest(token),))
