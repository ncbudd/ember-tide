"""Small helpers for the second simulation pass (test_sim2_*.py), on top of simnight.py.

`catch(r)` records every message the notifier produces on every tick (kind, whom, the page a tap opens, the moment the
phone drops it) so a scenario can say exactly which notifications fired. Call it again after `r.boot()`."""

from __future__ import annotations

from dataclasses import dataclass

from simnight import hm, ts  # noqa: F401  (re-exported)


@dataclass
class Note:
    at: float
    kind: str
    person: str | None
    link: str
    expires: int | None
    title: str


def catch(r) -> list[Note]:
    got: list[Note] = []
    nt = r.c.notifier
    real = nt.observe

    def observe(now, snap):
        out = real(now, snap)
        for m in out:
            try:
                link = nt._link(m.person or "", m)
            except Exception:  # noqa: BLE001
                link = "?"
            got.append(Note(now, m.kind, m.person, link, nt._expires(m), m.title))
        return out
    nt.observe = observe
    return got


def kinds(got: list[Note], person: str | None = None) -> list[str]:
    return [n.kind for n in got if person is None or n.person == person]


def anchors(r, pid="p1"):
    return [(hm(e["at"]), e["why"]) for e in r.events("sleep_anchor", pid)]


def reading(plan, zone: str, a: str, b: str):
    """Awake and still in bed (reading): steady heart rate, a page turn now and then."""
    return plan.at(a, lambda r: r.bed.reading.update({zone: True})).at(b, lambda r: r.bed.reading.pop(zone, None))


def deep_from(r, p=None, a="2026-11-05T21:00", b="2026-11-06T02:00"):
    pid = (p or r.p1).id
    for t, row in r.rec:
        if ts(a) <= t < ts(b) and row[pid][2] == "deep":
            return hm(t)
    return None


def phase_at(r, when, p=None):
    return r.at(p or r.p1, when)[2]
