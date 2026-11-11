"""A rig for notification simulations: the real controller on a simulated bed with a fixed clock, a Notifier whose
network is a fake that records everything that would have been sent (nothing leaves the machine), phones with real
push keys (so the recorded messages are decrypted exactly as a phone would), and snapshot helpers to put a person in
a situation without running a whole night. Everything here is synthetic (Left/Right, 192.168.1.x, example hosts)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass, field

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from embertide import webpush
from embertide.notify import Notifier
from test_scenarios import Night, ts

PUSH_SERVER = "https://ntfy.example"
FCM_HOST = "fcm.googleapis.com"
WEB_HOST = "web.push.example.test"


def b64u(x: bytes) -> str:
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


@dataclass
class Phone:
    """A phone with its own push keys."""
    person: str
    kind: str                    # up | fcm | web
    label: str
    endpoint: str
    key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    auth: str = field(default_factory=lambda: b64u(os.urandom(16)))

    @property
    def p256dh(self) -> str:
        return b64u(self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


@dataclass
class Got:
    """One message that reached something."""
    via: str                     # up | fcm | web | ntfy | ha
    to: str                      # phone label, ntfy topic or HA service
    person: str | None           # whose phone/target it was (None = the household's)
    title: str
    body: str
    kind: str = ""
    actions: list[str] = field(default_factory=list)       # button labels
    acts: list[str] = field(default_factory=list)          # what each button does
    codes: list[str] = field(default_factory=list)         # single-use codes behind link buttons (ntfy / HA / web)
    raw: dict = field(default_factory=dict)


def service_account(path):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    (path / "fcm-service-account.json").write_text(json.dumps({
        "type": "service_account", "project_id": "ember-test", "private_key": pem,
        "client_email": "push@ember-test.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token"}))


class Rig:
    def __init__(self, tmp_path, start: str = "2026-11-05T12:00", *, ntfy: bool = False, ha: bool = False, fcm: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        if fcm:
            service_account(tmp_path)
        self.n = Night(tmp_path, start)
        self.c = self.n.c
        self.got: list[Got] = []
        self.behave: dict[str, object] = {}              # host or endpoint → status int, or an Exception to raise, or a callable
        self.requests: list[httpx.Request] = []
        self.phones: dict[str, Phone] = {}                # endpoint → phone
        self.targets: dict[str, str | None] = {}          # ntfy topic / HA service → person
        self.tmp = tmp_path
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        self.n.c.notifier = self.nt = Notifier(self.c, "https://bed.example", client=self.client)
        self.nt.fcm.data_dir = tmp_path
        self.c.store.put("notify_settings", {"push_server": PUSH_SERVER, "push_token": None, "public_url": "https://bed.example"})
        provs = []
        if ntfy:
            provs += [self._prov("n1", "ntfy", "p1", {"url": PUSH_SERVER, "topic": "left-bed", "token": None}),
                      self._prov("n2", "ntfy", "p2", {"url": PUSH_SERVER, "topic": "right-bed", "token": None}),
                      self._prov("n3", "ntfy", None, {"url": PUSH_SERVER, "topic": "house", "token": None})]
        if ha:
            provs += [self._prov("h1", "ha", "p1", {"url": "http://192.168.1.10:8123", "token": "ha_tok", "service": "notify.mobile_app_left"}),
                      self._prov("h2", "ha", "p2", {"url": "http://192.168.1.10:8123", "token": "ha_tok", "service": "notify.mobile_app_right"})]
        for p in provs:
            self.targets[p["settings"].get("topic") or p["settings"]["service"]] = p["person"]
        self.nt.save_providers(provs)

    @staticmethod
    def _prov(pid, kind, person, settings):
        return {"id": pid, "kind": kind, "name": f"{kind} {person or 'house'}", "person": person, "enabled": True, "settings": settings}

    # ---------- people and phones ----------
    @property
    def p1(self):
        return self.n.p1

    @property
    def p2(self):
        return self.n.p2

    def phone(self, person: str, kind: str = "fcm", label: str | None = None) -> Phone:
        label = label or f"{person}-{kind}-{len([p for p in self.phones.values() if p.person == person]) + 1}"
        ep = {"fcm": ("f" * 150 + label.replace("-", "x"))[:300], "up": f"{PUSH_SERVER}/up{label.replace('-', 'x')}12?up=1",
              "web": f"https://{WEB_HOST}/send/{label}"}[kind]
        ph = Phone(person, kind, label, ep)
        self.phones[ep] = ph
        self.nt.add_device(person, ep, ph.p256dh, ph.auth, label, kind=kind)
        return ph

    def name(self, person: str) -> str:
        return next(p.name for p in self.c.household.people if p.id == person)

    # ---------- the fake network ----------
    async def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url, host = str(req.url), req.url.host
        rule = self.behave.get(url.split("?")[0]) or self.behave.get(host)
        if callable(rule):
            rule = rule(req)
        if isinstance(rule, Exception):
            raise rule
        if rule == "hang":                                  # a provider that never answers
            await asyncio.sleep(120)
        if host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        if isinstance(rule, tuple):                          # (status, body text)
            return httpx.Response(rule[0], text=rule[1])
        status = rule if isinstance(rule, int) else 200
        if status >= 400:
            return httpx.Response(status, json={})
        if host == FCM_HOST:
            msg = json.loads(req.content)["message"]
            ph = self.phones[msg["token"]]
            self._record_push("fcm", ph, webpush_bytes=_unb64(msg["data"]["e"]))
        elif host == WEB_HOST:
            self._record_push("web", self.phones[url], webpush_bytes=req.content)
        elif url.startswith(PUSH_SERVER + "/up"):
            self._record_push("up", self.phones[url], webpush_bytes=req.content)
        elif host == "ntfy.example":
            b = json.loads(req.content)
            acts = b.get("actions") or []
            self.got.append(Got("ntfy", b["topic"], self.targets.get(b["topic"]), b["title"], b["message"], actions=[a["label"] for a in acts],
                                codes=[a["url"].rsplit("/", 1)[-1] for a in acts], raw=b))
        elif host == "192.168.1.10":
            b = json.loads(req.content)
            service = url.rsplit("/", 1)[-1]
            acts = b["data"].get("actions") or []
            codes = [(a["action"][3:] if a["action"].startswith("ET_") else a["uri"].split("#", 1)[1]) for a in acts]
            self.got.append(Got("ha", service, self.targets.get(f"notify.{service}"), b["title"], b["message"],
                                actions=[a["title"] for a in acts], codes=codes, raw=b))
        return httpx.Response(200, json={})

    def _record_push(self, via: str, ph: Phone, webpush_bytes: bytes) -> None:
        d = json.loads(webpush.decrypt(webpush_bytes, ph.key, ph.auth))
        acts = d.get("actions") or []
        codes = [a["url"].split("#", 1)[1] for a in acts if "#" in a.get("url", "")]
        self.got.append(Got(via, ph.label, ph.person, d["title"], d["body"], kind=d.get("kind", ""),
                            actions=[a.get("label") or a.get("title") for a in acts], acts=[a.get("act", "") for a in acts], codes=codes, raw=d))

    def restart(self) -> Notifier:
        """The server restarts: a new Notifier over the same database (memory gone, the store kept)."""
        old = self.nt
        old.stop()
        self.n.c.notifier = self.nt = Notifier(self.c, "https://bed.example", client=self.client)
        self.nt.fcm.data_dir = self.tmp
        self.nt.owner_persons, self.nt.care_persons = old.owner_persons, old.care_persons
        return self.nt

    # ---------- time and snapshots ----------
    def at(self, when: str) -> float:
        self.n.clock.jump_to(ts(when))
        return self.n.clock.now_ts()

    @property
    def now(self) -> float:
        return self.n.clock.now_ts()

    async def prime(self) -> dict:
        """One live tick, then a snapshot to put people into situations."""
        await self.n.step(1, dt=0)
        return self.snap()

    def snap(self) -> dict:
        return json.loads(json.dumps(self.c.snapshot(), default=str))

    def pv(self, snap: dict, person: str) -> dict:
        return next(p for p in snap["people"] if p["id"] == person)

    def put(self, snap: dict, person: str, **night) -> dict:
        """Set fields of a person's night (state, guest, away, override ...)."""
        pv = self.pv(snap, person)
        pv["night"].update(night)
        return pv

    def sch(self, snap: dict, person: str) -> dict:
        return self.pv(snap, person)["schedule"]

    async def observe(self, snap: dict | None = None, *, at: float | None = None) -> list:
        """Run the triggers at `at` (default: the clock), let the sends finish, and return the messages that came due."""
        if at is not None:
            self.n.clock.jump_to(at)
        out = self.nt.observe(self.now, snap if snap is not None else self.snap())
        await self.drain()
        return out

    async def drain(self) -> None:
        for _ in range(5):
            tasks = list(self.nt._tasks)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)

    def sent(self, kind: str | None = None, person: str | None = ..., via: str | None = None) -> list[Got]:
        """What reached something, optionally of one kind / for one person / over one channel. ntfy and Home Assistant
        carry no kind of their own, so it is read back from the notification log by title."""
        kinds = {e["title"]: e["about"] for e in self.events("notification")}
        out = self.got
        for g in out:
            g.kind = g.kind or kinds.get(g.title, "")
        if kind is not None:
            out = [g for g in out if g.kind == kind]
        if person is not ...:
            out = [g for g in out if g.person == person]
        if via:
            out = [g for g in out if g.via == via]
        return out

    def events(self, kind: str) -> list[dict]:
        return [e for e in self.c.store.events(limit=5000) if e["kind"] == kind]


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
