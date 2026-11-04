"""Accounts: password login, cookie sessions, invite-based sign-up, admin/member roles."""
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import config

COOKIE = "gs_session"
SESSION_TTL = 30 * 86400
MAX_FAILS, LOCK_SECONDS = 5, 60
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

router = APIRouter(prefix="/api/auth")
conn = None
_fails: dict[str, tuple[int, float]] = {}
_DUMMY_SALT = os.urandom(16)


def init(c) -> None:
    global conn
    conn = c
    _ensure_setup_code()


def _setup_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "setup_code.txt")


def _ensure_setup_code() -> None:
    """With no users yet, the first sign-up needs a code from this file, so a stranger can't claim admin."""
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    path = _setup_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(secrets.token_urlsafe(9))
    print(f"[auth] No accounts yet. The first sign-up needs the setup code in {path}")


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _start_session(response: Response, request: Request, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (_tok_hash(token), user_id, now + SESSION_TTL))
    conn.commit()
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")


def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.player_id FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ?""", (_tok_hash(token), int(time.time()))).fetchone()
    return dict(row) if row else None


def _need_user(request: Request) -> dict:
    user = user_from_request(request)
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def _need_admin(request: Request) -> dict:
    user = _need_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return user


def _check_password(pw: str) -> None:
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")


def _throttle_key(request: Request, username: str) -> str:
    return f"{request.client.host if request.client else '?'}|{username.lower()}"


class Credentials(BaseModel):
    username: str
    password: str


class Register(Credentials):
    invite: str


class ProfileIn(BaseModel):
    player_id: int | None = None
    clear_player: bool = False
    current_password: str | None = None
    new_password: str | None = None


class RoleIn(BaseModel):
    role: str


@router.get("/status")
def status():
    return {"has_users": bool(conn.execute("SELECT 1 FROM users LIMIT 1").fetchone())}


@router.post("/register")
def register(body: Register, request: Request, response: Response):
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username: 3-32 letters, numbers, . _ -")
    _check_password(body.password)
    code = body.invite.strip()
    first = not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if first:
        try:
            expected = open(_setup_path()).read().strip()
        except OSError:
            expected = ""
        if not expected or not hmac.compare_digest(code, expected):
            raise HTTPException(403, "Wrong setup code (see data/setup_code.txt on the server)")
    elif not conn.execute("SELECT 1 FROM invites WHERE code = ? AND used_at IS NULL", (code,)).fetchone():
        raise HTTPException(403, "Invalid or already used invite code")
    salt = os.urandom(16)
    try:
        cur = conn.execute("INSERT INTO users(username, pw_hash, salt, role, created_at) VALUES (?,?,?,?,?)",
                           (username, _hash(body.password, salt), salt, "admin" if first else "member", int(time.time())))
    except Exception:
        raise HTTPException(409, "Username already taken")
    uid = cur.lastrowid
    if first:
        # Squads saved before accounts existed go to the first admin
        conn.execute("UPDATE squads SET owner_id = ? WHERE owner_id IS NULL", (uid,))
        try:
            os.remove(_setup_path())
        except OSError:
            pass
    else:
        conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?", (uid, int(time.time()), code))
    conn.commit()
    _start_session(response, request, uid)
    return {"id": uid, "username": username, "role": "admin" if first else "member", "player_id": None}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    key = _throttle_key(request, body.username)
    count, until = _fails.get(key, (0, 0.0))
    if count >= MAX_FAILS and time.time() < until:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    row = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    digest = _hash(body.password, row["salt"] if row else _DUMMY_SALT)
    if not row or not hmac.compare_digest(digest, row["pw_hash"]):
        if time.time() >= until:
            count = 0
        _fails[key] = (count + 1, time.time() + LOCK_SECONDS)
        raise HTTPException(401, "Wrong username or password")
    _fails.pop(key, None)
    _start_session(response, request, row["id"])
    return {"id": row["id"], "username": row["username"], "role": row["role"], "player_id": row["player_id"]}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_tok_hash(token),))
        conn.commit()
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return _need_user(request)


@router.patch("/me")
def update_me(body: ProfileIn, request: Request):
    user = _need_user(request)
    if body.clear_player or body.player_id is not None:
        pid = None if body.clear_player else body.player_id
        if pid is not None and not conn.execute("SELECT 1 FROM players WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Person not found")
        conn.execute("UPDATE users SET player_id = ? WHERE id = ?", (pid, user["id"]))
    if body.new_password:
        _check_password(body.new_password)
        row = conn.execute("SELECT salt, pw_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not body.current_password or not hmac.compare_digest(_hash(body.current_password, row["salt"]), row["pw_hash"]):
            raise HTTPException(403, "Current password is wrong")
        salt = os.urandom(16)
        conn.execute("UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?", (_hash(body.new_password, salt), salt, user["id"]))
        # Sign out other devices; keep this one
        keep = _tok_hash(request.cookies.get(COOKIE, ""))
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], keep))
    conn.commit()
    return user_from_request(request)


@router.get("/users")
def list_users(request: Request):
    _need_admin(request)
    return [dict(r) for r in conn.execute("SELECT id, username, role, player_id, created_at FROM users ORDER BY username")]


def _admin_count() -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]


@router.patch("/users/{user_id}")
def set_role(user_id: int, body: RoleIn, request: Request):
    _need_admin(request)
    if body.role not in ("admin", "member"):
        raise HTTPException(400, "Role must be admin or member")
    target = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404, "User not found")
    if target["role"] == "admin" and body.role != "admin" and _admin_count() <= 1:
        raise HTTPException(400, "There must be at least one admin")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
    conn.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request):
    me_ = _need_admin(request)
    if user_id == me_["id"]:
        raise HTTPException(400, "You can't delete your own account")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return {"ok": True}


@router.get("/invites")
def list_invites(request: Request):
    _need_admin(request)
    return [dict(r) for r in conn.execute(
        """SELECT i.code, i.created_at, i.used_at, u.username AS used_by FROM invites i
           LEFT JOIN users u ON u.id = i.used_by ORDER BY i.created_at DESC""")]


@router.post("/invites")
def create_invite(request: Request):
    user = _need_admin(request)
    code = secrets.token_urlsafe(9)
    conn.execute("INSERT INTO invites(code, created_by, created_at) VALUES (?,?,?)", (code, user["id"], int(time.time())))
    conn.commit()
    return {"code": code}


@router.delete("/invites/{code}")
def delete_invite(code: str, request: Request):
    _need_admin(request)
    conn.execute("DELETE FROM invites WHERE code = ?", (code,))
    conn.commit()
    return {"ok": True}
