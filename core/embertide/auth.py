"""Sign-in and access.

- Accounts: username + password (scrypt). The first account is created on first visit and becomes the owner.
- Each account can be tied to one person in the household (whose side is "mine").
- Roles: owner (everything), sleeper (their own side fully; the partner's side only as far as the partner shares;
  no bed console, simulator or device setup), viewer (read-only).
- Sessions: random token in an HttpOnly cookie, stored hashed, 90 days sliding. Signed out from Settings.
- Invites: the owner makes a one-time link for a person; whoever opens it picks a username and password.
- API tokens: for Home Assistant, dials, scripts and the MCP server. Sent as `Authorization: Bearer et_…`.
- Changes made with a cookie must carry the `X-Embertide: 1` header (the web app always sends it), which a page
  on another site can't add, so a malicious page can't drive the bed through a signed-in browser.

ET_AUTH=off turns all of this off (everyone is the owner). Only for development or a fully trusted network.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie

from .store import Store

COOKIE = "et_session"
SESSION_DAYS = 90
INVITE_DAYS = 7
ROLES = ("owner", "sleeper", "viewer")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw TEXT NOT NULL,
  role TEXT NOT NULL, person TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL NOT NULL, seen REAL NOT NULL,
  expires REAL NOT NULL, agent TEXT, ip TEXT
);
CREATE TABLE IF NOT EXISTS invites (
  hash TEXT PRIMARY KEY, person TEXT, role TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  created_by TEXT, used_by TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, person TEXT,
  created REAL NOT NULL, last_used REAL, created_by TEXT
);
"""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    d = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${d.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, d = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(d)))
        return hmac.compare_digest(got.hex(), d)
    except Exception:  # noqa: BLE001
        return False


def password_problem(pw: str) -> str | None:
    if len(pw) < 8:
        return "Use at least 8 characters."
    if len(pw) > 200:
        return "That's too long."
    if pw.lower() in ("password", "12345678", "embertide", "password1", "qwertyui"):
        return "That one is too easy to guess."
    return None


@dataclass
class Principal:
    kind: str                 # "user" | "token" | "open" (auth off)
    id: str
    name: str
    role: str
    person: str | None
    session: str | None = None   # session hash, for "this device"

    def public(self) -> dict:
        return {"kind": self.kind, "id": self.id, "name": self.name, "role": self.role, "person": self.person}


OPEN = Principal("open", "open", "Everyone", "owner", None)


class Auth:
    def __init__(self, store: Store, enabled: bool = True) -> None:
        self.store = store
        self.enabled = enabled
        with store._lock, store._db:
            store._db.executescript(SCHEMA)
            try:
                store._db.execute("ALTER TABLE invites ADD COLUMN user_id TEXT")     # set for password-reset links
            except Exception:  # noqa: BLE001
                pass
        self._fails: dict[str, list[float]] = {}

    # ---------- helpers ----------
    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.store._lock:
            return self.store._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self.store._lock, self.store._db:
            return self.store._db.execute(sql, args).rowcount

    def setup_needed(self) -> bool:
        return self.enabled and not self._q("SELECT 1 FROM users LIMIT 1")

    # ---------- rate limiting ----------
    def throttled(self, key: str, now: float, limit: int, window: float) -> bool:
        xs = [t for t in self._fails.get(key, []) if now - t < window]
        self._fails[key] = xs
        return len(xs) >= limit

    def failed(self, key: str, now: float) -> None:
        if len(self._fails) > 5000:                  # names / addresses never tried again would otherwise pile up forever
            self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < 3600}
        self._fails.setdefault(key, []).append(now)

    # ---------- users ----------
    def create_user(self, username: str, password: str, role: str, person: str | None) -> dict:
        if not USERNAME_RE.match(username):
            raise ValueError("Usernames are 2–32 letters, numbers, dots, dashes or underscores.")
        if role not in ROLES:
            raise ValueError("unknown role")
        if (p := password_problem(password)):
            raise ValueError(p)
        if self._q("SELECT 1 FROM users WHERE username=?", (username,)):
            raise ValueError("That username is taken.")
        uid = "u_" + secrets.token_hex(6)
        self._x("INSERT INTO users(id,username,pw,role,person,created) VALUES(?,?,?,?,?,?)",
                (uid, username, hash_password(password), role, person, time.time()))
        return self.user(uid)

    def user(self, uid: str) -> dict | None:
        r = self._q("SELECT id,username,role,person,created FROM users WHERE id=?", (uid,))
        return dict(zip(("id", "username", "role", "person", "created"), r[0])) if r else None

    def users(self) -> list[dict]:
        return [dict(zip(("id", "username", "role", "person", "created"), r))
                for r in self._q("SELECT id,username,role,person,created FROM users ORDER BY created")]

    def update_user(self, uid: str, role: str | None = None, person: str | None | type(...) = ...) -> None:
        if role is not None:
            if role not in ROLES:
                raise ValueError("unknown role")
            if role != "owner" and self._is_last_owner(uid):
                raise ValueError("There has to be at least one owner.")
            self._x("UPDATE users SET role=? WHERE id=?", (role, uid))
        if person is not ...:
            self._x("UPDATE users SET person=? WHERE id=?", (person, uid))

    def _is_last_owner(self, uid: str) -> bool:
        owners = [r[0] for r in self._q("SELECT id FROM users WHERE role='owner'")]
        return owners == [uid]

    def delete_user(self, uid: str) -> None:
        if self._is_last_owner(uid):
            raise ValueError("There has to be at least one owner.")
        self._x("DELETE FROM sessions WHERE user_id=?", (uid,))
        self._x("DELETE FROM users WHERE id=?", (uid,))

    def set_password(self, uid: str, old: str | None, new: str) -> None:
        r = self._q("SELECT pw FROM users WHERE id=?", (uid,))
        if not r:
            raise ValueError("unknown user")
        if old is not None and not check_password(old, r[0][0]):
            raise ValueError("Your current password isn't right.")
        if (p := password_problem(new)):
            raise ValueError(p)
        self._x("UPDATE users SET pw=? WHERE id=?", (hash_password(new), uid))

    def verify(self, username: str, password: str) -> dict | None:
        r = self._q("SELECT id,pw FROM users WHERE username=?", (username,))
        if not r:
            check_password(password, hash_password("timing-equaliser"))   # same work whether or not the name exists
            return None
        return self.user(r[0][0]) if check_password(password, r[0][1]) else None

    # ---------- sessions ----------
    def new_session(self, uid: str, agent: str | None, ip: str | None) -> str:
        tok = secrets.token_urlsafe(32)
        now = time.time()
        self._x("INSERT INTO sessions(hash,user_id,created,seen,expires,agent,ip) VALUES(?,?,?,?,?,?,?)",
                (_h(tok), uid, now, now, now + SESSION_DAYS * 86400, (agent or "")[:200], ip))
        return tok

    def session_principal(self, tok: str) -> Principal | None:
        h = _h(tok)
        r = self._q("SELECT s.user_id,s.expires,s.seen,u.username,u.role,u.person FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=?", (h,))
        if not r:
            return None
        uid, expires, seen, username, role, person = r[0]
        now = time.time()
        if now > expires:
            self._x("DELETE FROM sessions WHERE hash=?", (h,))
            return None
        if now - seen > 3600:          # slide the expiry at most once an hour
            self._x("UPDATE sessions SET seen=?, expires=? WHERE hash=?", (now, now + SESSION_DAYS * 86400, h))
        return Principal("user", uid, username, role, person, session=h)

    def sessions(self, uid: str) -> list[dict]:
        return [dict(zip(("id", "created", "seen", "agent", "ip"), (r[0][:12], *r[1:])))
                for r in self._q("SELECT hash,created,seen,agent,ip FROM sessions WHERE user_id=? ORDER BY seen DESC", (uid,))]

    def end_session(self, h: str) -> None:
        self._x("DELETE FROM sessions WHERE hash=?", (h,))

    def end_session_by_prefix(self, uid: str, prefix: str) -> int:
        return self._x("DELETE FROM sessions WHERE user_id=? AND substr(hash,1,12)=?", (uid, prefix))

    def end_other_sessions(self, uid: str, keep: str | None) -> int:
        return self._x("DELETE FROM sessions WHERE user_id=? AND hash<>?", (uid, keep or ""))

    # ---------- invites ----------
    def new_invite(self, person: str | None, role: str, by: str) -> str:
        if role not in ROLES:
            raise ValueError("unknown role")
        code = secrets.token_urlsafe(18)
        now = time.time()
        self._x("INSERT INTO invites(hash,person,role,created,expires,created_by) VALUES(?,?,?,?,?,?)",
                (_h(code), person, role, now, now + INVITE_DAYS * 86400, by))
        return code

    def invite(self, code: str) -> dict | None:
        r = self._q("SELECT person,role,expires,used_by,user_id FROM invites WHERE hash=?", (_h(code),))
        if not r or r[0][3] or time.time() > r[0][2]:
            return None
        out = {"person": r[0][0], "role": r[0][1], "expires": r[0][2], "reset_for": None}
        if r[0][4]:
            u = self.user(r[0][4])
            if not u:
                return None
            out["reset_for"] = u["username"]
        return out

    def new_reset(self, uid: str, by: str) -> str:
        """A one-time link (24 h) that sets a new password for an existing account."""
        u = self.user(uid)
        if not u:
            raise ValueError("unknown user")
        code = secrets.token_urlsafe(18)
        now = time.time()
        self._x("DELETE FROM invites WHERE user_id=? AND used_by IS NULL", (uid,))           # only the newest link works
        self._x("INSERT INTO invites(hash,person,role,created,expires,created_by,user_id) VALUES(?,?,?,?,?,?,?)",
                (_h(code), u["person"], u["role"], now, now + 86400, by, uid))
        return code

    def invites(self) -> list[dict]:
        now = time.time()
        return [dict(zip(("id", "person", "role", "created", "expires"), (r[0][:12], *r[1:])))
                for r in self._q("SELECT hash,person,role,created,expires FROM invites WHERE used_by IS NULL AND user_id IS NULL AND expires>? ORDER BY created DESC", (now,))]

    def cancel_invite(self, prefix: str) -> int:
        return self._x("DELETE FROM invites WHERE substr(hash,1,12)=? AND used_by IS NULL", (prefix,))

    def accept_invite(self, code: str, username: str, password: str) -> dict:
        inv = self.invite(code)
        if not inv:
            raise ValueError("This invite link has expired or was already used. Ask for a new one.")
        if inv["reset_for"]:
            uid = self._q("SELECT user_id FROM invites WHERE hash=?", (_h(code),))[0][0]
            self.set_password(uid, None, password)
            self._x("DELETE FROM sessions WHERE user_id=?", (uid,))            # anyone holding the old password is out
            self._x("UPDATE invites SET used_by=? WHERE hash=?", (uid, _h(code)))
            return self.user(uid)
        u = self.create_user(username, password, inv["role"], inv["person"])
        self._x("UPDATE invites SET used_by=? WHERE hash=?", (u["id"], _h(code)))
        return u

    # ---------- API tokens ----------
    def new_token(self, name: str, role: str, person: str | None, by: str) -> tuple[str, dict]:
        if role not in ROLES:
            raise ValueError("unknown role")
        name = name.strip()[:60] or "Token"
        tok = "et_" + secrets.token_urlsafe(30)
        tid = "t_" + secrets.token_hex(5)
        self._x("INSERT INTO tokens(id,hash,name,role,person,created,created_by) VALUES(?,?,?,?,?,?,?)",
                (tid, _h(tok), name, role, person, time.time(), by))
        return tok, next(t for t in self.tokens() if t["id"] == tid)

    def tokens(self) -> list[dict]:
        return [dict(zip(("id", "name", "role", "person", "created", "last_used"), r))
                for r in self._q("SELECT id,name,role,person,created,last_used FROM tokens ORDER BY created")]

    def revoke_token(self, tid: str) -> int:
        return self._x("DELETE FROM tokens WHERE id=?", (tid,))

    def token_principal(self, tok: str) -> Principal | None:
        r = self._q("SELECT id,name,role,person,last_used FROM tokens WHERE hash=?", (_h(tok),))
        if not r:
            return None
        tid, name, role, person, last = r[0]
        now = time.time()
        if not last or now - last > 300:
            self._x("UPDATE tokens SET last_used=? WHERE id=?", (now, tid))
        return Principal("token", tid, name, role, person)

    # ---------- request → principal ----------
    def principal(self, headers: dict[str, str]) -> Principal | None:
        if not self.enabled:
            return OPEN
        a = headers.get("authorization", "")
        if a.lower().startswith("bearer "):
            tok = a[7:].strip()
            if tok.startswith("et_"):
                return self.token_principal(tok)
            return self.session_principal(tok)          # an app session (same as the cookie, carried by the app)
        c = headers.get("cookie")
        if c:
            jar = SimpleCookie()
            try:
                jar.load(c)
            except Exception:  # noqa: BLE001
                return None
            if COOKIE in jar:
                return self.session_principal(jar[COOKIE].value)
        return None


# ---------- what each role may do ----------
PUBLIC = {("GET", "/api/auth/state"), ("POST", "/api/auth/login"), ("POST", "/api/auth/setup"),
          ("POST", "/api/auth/logout"), ("GET", "/api/auth/invite"), ("POST", "/api/auth/accept"),
          ("POST", "/api/client-error"),
          ("GET", "/api/oura/callback")}        # Oura's OAuth redirect: the single-use state names the person        # app crash reports: small, rate-limited, must work before sign-in
OWNER_ONLY = [
    re.compile(r"^/api/console$"), re.compile(r"^/api/sim$"), re.compile(r"^/api/sources"),
    re.compile(r"^/api/calibration"), re.compile(r"^/api/auth/(users|invites|tokens)"), re.compile(r"^/api/notify/(providers|test|settings)"),
    re.compile(r"^/api/integrations"), re.compile(r"^/api/pets"), re.compile(r"^/api/ha(/|$)"), re.compile(r"^/api/client-errors$"),
    re.compile(r"^/api/guest/(left|right|both)/(start|stop)$"), re.compile(r"^/api/bed-orientation$"),
    re.compile(r"^/api/dials(/|$)"), re.compile(r"^/api/notify/log$"),   # dial setup asks HA with the owner's token; the log is everyone's messages
]
# no sign-in, the address itself is the key: OAuth redirects (single-use state) and owner-made webhooks (secret URL)
PUBLIC_PATTERNS = [("GET", re.compile(r"^/api/cloud/[a-z]{2,20}/callback$")), ("POST", re.compile(r"^/api/hooks/[A-Za-z0-9_]{2,20}/[A-Za-z0-9_\-]{16,64}$"))]
# a person's own data that the partner and view-only accounts don't read (the owner can, and each person their own):
# where they are, their phone and watch, every night's detail, Autopilot's view of them, their imports and questions
PRIVATE_READS = ("/phone-context", "/watch-hr", "/nights", "/night", "/sleeps", "/stages", "/compare", "/autopilot",
                 "/insights", "/imports", "/pet-question")
PERSON_PATH = re.compile(r"^/api/people/([^/]+)(/.*)?$")
FLASH = re.compile(r"^/flash(/extend|/stop)?$")
ADJUST = re.compile(r"^/(adjust(/clear)?|tonight)$")


def authorize(p: Principal, method: str, path: str, partner_shares) -> str | None:
    """None if allowed, else a short reason. `partner_shares(pid)` → (can_flash, can_adjust) for that person."""
    if p.role == "owner":
        return None
    read = method in ("GET", "HEAD", "OPTIONS")
    if any(rx.match(path) for rx in OWNER_ONLY):
        if read and path.startswith(("/api/sim", "/api/sources", "/api/calibration", "/api/pets", "/api/bed-orientation")):
            return None                                     # seeing device setup is fine; changing it is the owner's
        return "Only the owner can do that."
    if read:
        m = PERSON_PATH.match(path)
        if m and p.person != m.group(1) and (m.group(2) or "").startswith(PRIVATE_READS):
            return "That's their own sleep and phone data."      # health, location and habits stay with their person
        return None
    if path.startswith(("/api/auth/me", "/api/notify/me")):
        return None                                       # your own password, devices and notifications, any role
    if p.role == "viewer":
        return "This account can only look."
    if path == "/api/household/swap-sides":
        return None
    if re.match(r"^/api/guest/(left|right|both)/set$", path):
        return None                                       # anyone who can control the bed can work a guest's side
    if path.startswith(("/api/auth/me", "/api/notify/me", "/api/oura/me", "/api/cloud/me/")) or path == "/api/ai/ask":
        return None
    if path.startswith("/api/assistant/"):
        return None                                       # the assistant scopes itself; its changes go back through here

    m = PERSON_PATH.match(path)
    if m:
        pid, rest = m.group(1), m.group(2) or ""
        if p.person == pid:
            return None
        can_flash, can_adjust = partner_shares(pid)
        if FLASH.match(rest):
            return None if can_flash else "They haven't shared flashes on their side."
        if ADJUST.match(rest):
            return None if can_adjust else "They haven't shared control of their side."
        return "That's their setting to change."
    return "Only the owner can do that."
