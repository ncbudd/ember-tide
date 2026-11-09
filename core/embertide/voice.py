"""How Ember & Tide says things: a few natural ways to say each message, in three tones.

Every message type has at least three variants per tone with exactly the same slots ({name}, {phase}, {minutes}...),
so the meaning and every number stay the same whichever variant is picked; only the wording changes.

- calm (the default): the plain, quiet style the app has always used. Its first variant is the original sentence.
- friendly: warmer and a little chattier.
- cheeky: playful and teasing, never rude, never sexual, never about weight or body shape, never at anyone's expense.
  Alerts (bed offline, a frozen sensor, overnight problems) are never cheeky: ALERTS always use the calm wording.

The pick is deterministic per person, message type and day, so a page shows the same sentence on every reload. With a
store, the last pick per person and type is remembered (kv `voice_last:<pid>`) and a new day never repeats it back to
back; without a store, consecutive days still never repeat (the variant steps through the list by a per-person stride).
"""

from __future__ import annotations

import hashlib
import string
from datetime import date
from typing import Any

TONES = ("calm", "friendly", "cheeky")

# a variant is a string, or a (title, body) pair for notifications and suggestions
Variant = str | tuple[str, str]

PHRASES: dict[str, dict[str, list[Variant]]] = {
    # ---------- notifications ----------
    "away_precool": {
        "calm": [("Pre-cool is due, but you're not home", "{name}'s side would start cooling now. What should it do?"),
                 ("You're not home, and pre-cool is due", "{name}'s side is ready to start cooling. What should it do?"),
                 ("Pre-cool time, and you're not home yet", "{name}'s side would begin cooling now. Pick what it should do.")],
        "friendly": [("Pre-cool's ready, but you're not home", "Should {name}'s side wait an hour, take the night off, or start anyway?"),
                     ("Heads up: you're not home yet", "{name}'s side would start pre-cooling now. What would you like it to do?"),
                     ("Your side's about to pre-cool, and you're not home", "Should {name}'s side wait, skip tonight or start anyway?")],
        "cheeky": [("The bed is ready. You're not home.", "{name}'s side is all set to cool down while you're out gallivanting. Wait, skip tonight, or start anyway?"),
                   ("Pre-cool o'clock, and you're not home", "{name}'s side is tapping its foot. What should it do?"),
                   ("Your side misses you (you're not home)", "{name}'s side would start cooling now. Hold off, skip tonight, or go ahead?")],
    },
    "empty_side": {
        "calm": [("Your side is pre-cooled but empty", "It's past the time it keeps pre-cooling. Getting in still starts your night."),
                 ("Your side is cool and waiting, but empty", "Pre-cooling has run its time. Getting in still starts your night."),
                 ("Pre-cooled, but nobody's in your side", "It's past the pre-cool time. Getting in still starts your night.")],
        "friendly": [("Your side's ready when you are", "It's pre-cooled and still empty. Getting in still starts your night."),
                     ("Still up? Your side is pre-cooled", "Nobody's in it yet. Getting in still starts your night, whenever you're ready."),
                     ("Your side is cool and empty", "Pre-cool time is over. Getting in still starts your night.")],
        "cheeky": [("Your side is cooled and a bit lonely", "Pre-cool time is over and it's still waiting. Getting in still starts your night."),
                   ("Your pillow is asking questions", "Your side is pre-cooled and empty. Getting in still starts your night."),
                   ("The bed is ready. Are you?", "Your side is pre-cooled and nobody's in it. Getting in still starts your night.")],
    },
    "left_on": {
        "calm": [("{name}'s side is on, but nobody's in it", "It was turned on by hand and has been empty for {minutes} min."),
                 ("Nobody's in {name}'s side, but it's on", "It was turned on by hand and has been empty {minutes} min now."),
                 ("{name}'s side is running with nobody in it", "It was switched on by hand and has been empty for {minutes} min.")],
        "friendly": [("{name}, your side is still on", "Nobody's been in it for {minutes} min since it was turned on by hand."),
                     ("Left your side on, {name}?", "It's been empty for {minutes} min since it was turned on by hand."),
                     ("{name}'s side is on and empty", "It was turned on by hand and nobody's been in it for {minutes} min.")],
        "cheeky": [("{name}'s side is on for nobody", "It was turned on by hand and has kept an empty spot comfy for {minutes} min."),
                   ("Did the nap end without telling the bed, {name}?", "Your side was turned on by hand and has been empty for {minutes} min."),
                   ("{name}'s side is on. The sleeper is not.", "Empty for {minutes} min since it was turned on by hand.")],
    },
    "morning": {
        "calm": [("Good morning, {name}", "{summary}."), ("Morning, {name}", "Last night: {summary}."),
                 ("Your night, {name}", "{summary}.")],
        "friendly": [("Morning, {name}!", "{summary}."), ("Hope you slept well, {name}", "Last night: {summary}."),
                     ("Rise and shine, {name}", "{summary}.")],
        "cheeky": [("Rise and shine, {name} (or at least rise)", "{summary}."), ("Good morning, {name}. The bed has notes.", "{summary}."),
                   ("Morning, {name}! The night report is in.", "{summary}.")],
    },
    "checkin": {     # the end of the morning summary: one action, tap to check in
        "calm": ["How did it feel? Tap to check in.", "How was the temperature? Tap to check in.", "Tap to say how the night felt."],
        "friendly": ["How did it feel? One tap helps it learn.", "Too warm, too cool or just right? Tap to say.",
                     "How was it? A quick tap helps tune tonight."],
        "cheeky": ["Goldilocks check: too hot, too cold or just right? Tap to say.", "Rate the night. The bed promises not to sulk.",
                   "How did it feel? Tap to check in, the bed is dying to know."],
    },
    "checkin_app": {  # the question above the check-in buttons on the Sleep screen
        "calm": ["How was the temperature?", "How did the temperature feel?", "Was the temperature right?"],
        "friendly": ["How was the temperature last night?", "Did the bed feel right last night?", "Too warm, too cool or just right?"],
        "cheeky": ["Goldilocks report: how was the temperature?", "Be honest: how was the temperature?",
                   "Did the bed get it right, or does it need a talking-to?"],
    },
    "pet_question": {
        "calm": [("Was {who} on the bed around {when}?", "Tap to answer in Ember & Tide. It helps tell your pet and you apart."),
                 ("Was that {who} on the bed around {when}?", "One tap to answer helps tell your pet and you apart."),
                 ("{who} on the bed around {when}?", "Tap to answer. It helps Ember & Tide tell your pet and you apart.")],
        "friendly": [("Was {who} on the bed around {when}?", "Quick one: tap to answer so it learns your pet from you."),
                     ("Did {who} come up on the bed around {when}?", "Tap to answer. It helps tell your pet and you apart."),
                     ("Was it {who} on the bed around {when}?", "One tap helps it tell your pet and you apart.")],
        "cheeky": [("Was {who} hogging your side again around {when}?", "Tap to answer. It helps tell your pet and you apart."),
                   ("{who} on the bed around {when}: guilty or innocent?", "One tap to answer helps tell your pet and you apart."),
                   ("Someone furry on the bed around {when}? Was it {who}?", "Tap to answer and it'll know your pet from you.")],
    },
    "flash_end": {
        "calm": ["Flash over, back to the rhythm", "The flash has ended; back to your rhythm", "Flash done. Back to the rhythm now."],
        "friendly": ["Flash done! Easing back to your rhythm", "All done. Easing back to your rhythm now.",
                     "That's the flash over. Back to your rhythm."],
        "cheeky": ["Crisis averted. Back to the rhythm.", "Flash over. Easing back like nothing happened.",
                   "Flash done. The bed is back to its usual business."],
    },
    # ---------- the night story ----------
    "story_pet": {
        "calm": ["Looked like {who} on your side, ignored", "{who} seemed to be on your side; left out of your night",
                 "Something like {who} lay on your side; ignored"],
        "friendly": ["Looked like {who} took your side for a while; ignored", "{who} seemed to visit your side; it didn't count",
                     "Probably {who} on your side, so it was left out"],
        "cheeky": ["{who} appears to have claimed your side; not counted", "Looked like {who} borrowed your side; we didn't count it",
                   "{who} on your side, apparently. Ignored, as is tradition."],
    },
    "story_pet_with": {
        "calm": ["Looked like {who} joined you", "{who} seemed to join you", "Looked like {who} kept you company"],
        "friendly": ["{who} seemed to snuggle in with you", "Looked like {who} joined you for a bit", "Company in bed: probably {who}"],
        "cheeky": ["{who} crashed your side, by the look of it", "Looked like {who} muscled in next to you",
                   "{who} joined you. Rent is overdue."],
    },
    "story_fitted": {
        "calm": ["Later than usual: the rhythm was fitted into the time before your warm wake",
                 "Later than usual: the rhythm was squeezed into the time before your warm wake",
                 "A late start, so the rhythm was fitted in before your warm wake"],
        "friendly": ["Late night! The rhythm fitted itself into the time before your warm wake",
                     "Later than usual, so the rhythm made the most of the time before your warm wake",
                     "A late one: the rhythm was fitted into the time before your warm wake"],
        "cheeky": ["Burning the midnight oil? The rhythm squeezed itself in before your warm wake",
                   "Fashionably late: the rhythm was fitted in before your warm wake",
                   "Late one, night owl. The rhythm was fitted into the time before your warm wake"],
    },
    # ---------- Autopilot suggestions ----------
    "manual_cooler": {
        "calm": [("Last night you made the {phase} stage {deg} cooler", "Changed from {source}. Make it part of your rhythm so the {phase} stage runs this way every night?"),
                 ("You made {phase} {deg} cooler last night", "That was from {source}. Add it to your rhythm so {phase} runs this way every night?"),
                 ("You turned the {phase} stage {deg} cooler last night", "You did it from {source}. Keep it in your rhythm so {phase} runs this way every night?")],
        "friendly": [("You liked {phase} {deg} cooler last night", "You changed it from {source}. Want {phase} that way every night?"),
                     ("Last night you nudged {phase} {deg} cooler", "From {source}. Keep {phase} that way every night?"),
                     ("{phase}, {deg} cooler: your call last night", "Set from {source}. Shall we make {phase} run that way every night?")],
        "cheeky": [("You made {phase} {deg} cooler last night. Bold move.", "Done from {source}. Make it official so {phase} runs this way every night?"),
                   ("Somebody made {phase} {deg} cooler last night (it was you)", "Changed from {source}. Want {phase} like that every night, no sneaking required?"),
                   ("{phase} went {deg} cooler last night on your orders", "Via {source}. Shall we write it into your rhythm and save the midnight fiddling with {phase}?")],
    },
    "manual_warmer": {
        "calm": [("Last night you made the {phase} stage {deg} warmer", "Changed from {source}. Make it part of your rhythm so the {phase} stage runs this way every night?"),
                 ("You made {phase} {deg} warmer last night", "That was from {source}. Add it to your rhythm so {phase} runs this way every night?"),
                 ("You turned the {phase} stage {deg} warmer last night", "You did it from {source}. Keep it in your rhythm so {phase} runs this way every night?")],
        "friendly": [("You liked {phase} {deg} warmer last night", "You changed it from {source}. Want {phase} that way every night?"),
                     ("Last night you nudged {phase} {deg} warmer", "From {source}. Keep {phase} that way every night?"),
                     ("{phase}, {deg} warmer: your call last night", "Set from {source}. Shall we make {phase} run that way every night?")],
        "cheeky": [("You made {phase} {deg} warmer last night. Cosy choice.", "Done from {source}. Make it official so {phase} runs this way every night?"),
                   ("Somebody made {phase} {deg} warmer last night (it was you)", "Changed from {source}. Want {phase} like that every night, no sneaking required?"),
                   ("{phase} went {deg} warmer last night on your orders", "Via {source}. Shall we write it into your rhythm and save the midnight fiddling with {phase}?")],
    },
    "pattern_cooler": {
        "calm": [("{phase} has felt warm on {n} of the last {look} nights", "Autopilot and your own taps point the same way. Make {phase} {step} cooler?"),
                 ("{phase} ran warm on {n} of the last {look} nights", "Autopilot and your taps agree. Make {phase} {step} cooler?"),
                 ("Warm during {phase} on {n} of the last {look} nights", "Your taps and Autopilot point the same way. Try {phase} {step} cooler?")],
        "friendly": [("{phase} has been warm for you: {n} of the last {look} nights", "Your taps and Autopilot agree. Shall we make {phase} {step} cooler?"),
                     ("A bit warm in {phase} lately ({n} of the last {look} nights)", "Want to try {phase} {step} cooler?"),
                     ("{phase} felt warm on {n} of the last {look} nights", "How about {phase} {step} cooler? Your taps and Autopilot both say so.")],
        "cheeky": [("{phase} ran warm on {n} of the last {look} nights", "Shall we make {phase} {step} cooler before you start sleeping on top of the covers like a starfish?"),
                   ("{phase} has been a bit toasty: {n} of the last {look} nights", "Your taps and Autopilot are in cahoots. Make {phase} {step} cooler?"),
                   ("Warm {phase} again ({n} of the last {look} nights)", "Make {phase} {step} cooler, and the duvet can stop fearing for its life?")],
    },
    "pattern_warmer": {
        "calm": [("{phase} has felt cool on {n} of the last {look} nights", "Autopilot and your own taps point the same way. Make {phase} {step} warmer?"),
                 ("{phase} ran cool on {n} of the last {look} nights", "Autopilot and your taps agree. Make {phase} {step} warmer?"),
                 ("Cool during {phase} on {n} of the last {look} nights", "Your taps and Autopilot point the same way. Try {phase} {step} warmer?")],
        "friendly": [("{phase} has been cool for you: {n} of the last {look} nights", "Your taps and Autopilot agree. Shall we make {phase} {step} warmer?"),
                     ("A bit chilly in {phase} lately ({n} of the last {look} nights)", "Want to try {phase} {step} warmer?"),
                     ("{phase} felt cool on {n} of the last {look} nights", "How about {phase} {step} warmer? Your taps and Autopilot both say so.")],
        "cheeky": [("{phase} ran cool on {n} of the last {look} nights", "Shall we make {phase} {step} warmer before you start wearing socks to bed?"),
                   ("{phase} has been a bit nippy: {n} of the last {look} nights", "Your taps and Autopilot are in cahoots. Make {phase} {step} warmer?"),
                   ("Chilly {phase} again ({n} of the last {look} nights)", "Make {phase} {step} warmer, before you start smuggling in a hot water bottle?")],
    },
    # ---------- alerts: plain in every tone ----------
    "problem_offline": {
        "calm": [("Can't reach the bed", "No word from the bed for 5 minutes ({detail}). It keeps its last temperature."),
                 ("The bed isn't answering", "Nothing from the bed for 5 minutes ({detail}). It keeps its last temperature."),
                 ("Lost contact with the bed", "The bed hasn't answered for 5 minutes ({detail}). It keeps its last temperature.")],
    },
    "problem_stale": {
        "calm": [("{side} bed sensor stopped updating", "Its readings have been frozen for 10 minutes. The rhythm keeps running on the clock."),
                 ("{side} bed sensor is stuck", "No new readings for 10 minutes. The rhythm keeps running on the clock."),
                 ("{side} bed sensor went quiet", "Its readings haven't changed for 10 minutes. The rhythm keeps running on the clock.")],
    },
    "digest": {      # problems held overnight, told once in the morning
        "calm": [("While you slept", "{lines}"), ("Overnight", "{lines}"), ("From last night", "{lines}")],
    },
    "overnight": {   # the same, as one line at the end of the morning summary
        "calm": ["Overnight: {lines}", "While you slept: {lines}", "Also last night: {lines}"],
    },
    "assistant_proposed": {   # Ask: the line above the change cards when the model said nothing itself
        "calm": ["Here's what I'd change. Nothing changes until you tap Apply.",
                 "This is what I'd change. Nothing happens until you tap Apply.",
                 "Here's my suggestion. Tap Apply if you'd like it."],
        "friendly": ["Here's what I'd try. Tap Apply if it sounds good.",
                     "How about this? Nothing changes until you tap Apply.",
                     "I'd try this. Have a look, and tap Apply if you like it."],
        "cheeky": ["Here's my brilliant plan. It only happens if you tap Apply.",
                   "I have ideas. Tap Apply to make them real.",
                   "Behold, a suggestion. Apply it, or don't; I'll cope."],
    },
}
ALERTS = frozenset({"problem_offline", "problem_stale", "digest", "overnight"})
for _k in ALERTS:                                   # alerts read the same in every tone
    for _t in TONES:
        PHRASES[_k].setdefault(_t, PHRASES[_k]["calm"])


def slots_of(v: Variant) -> set[str]:
    parts = v if isinstance(v, tuple) else (v,)
    return {f for p in parts for _, f, _, _ in string.Formatter().parse(p) if f}


def tone_of(person: Any) -> str:
    t = getattr(getattr(person, "settings", None), "tone", "calm") if person is not None else "calm"
    return t if t in TONES else "calm"


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:16], 16)


def pick(kind: str, n: int, who: str, day: date, seq: int = 0, last: tuple[str, int] | None = None) -> int:
    """Which variant: deterministic per person, kind and day; consecutive days (and seq) never give the same index;
    and never the `last` index remembered from an earlier day."""
    if n <= 1:
        return 0
    h = _h(f"{who}|{kind}")
    step = 1 + (h >> 20) % (n - 1)                   # 1..n-1: never a multiple of n, so day d and d+1 always differ
    i = (h + (day.toordinal() + seq) * step) % n
    if last and last[0] != day.isoformat() and last[1] == i:
        i = (i + 1) % n
    return i


def _fill(v: Variant, slots: dict[str, Any]) -> Variant:
    def one(t: str) -> str:
        s = t.format(**slots)
        return s[:1].upper() + s[1:]
    return (one(v[0]), one(v[1])) if isinstance(v, tuple) else one(v)


def say(kind: str, slots: dict[str, Any] | None = None, *, person: Any = None, tone: str | None = None,
        day: date | None = None, seq: int = 0, store: Any = None) -> Variant:
    """One message, in the person's tone, filled in. `store` remembers the last pick (no back-to-back repeats)."""
    slots = slots or {}
    t = tone or tone_of(person)
    if kind in ALERTS or t not in PHRASES[kind]:
        t = "calm"
    vs = PHRASES[kind][t]
    who = getattr(person, "id", None) or "household"
    day = day or date.today()
    key, rec = f"voice_last:{who}", None
    mem: dict = {}
    if store is not None:
        mem = store.get(key, {}) or {}
        rec = mem.get(f"{kind}:{t}")
    if rec and rec[0] == day.isoformat() and len(rec) > 2 and rec[2] == seq and rec[1] < len(vs):
        i = rec[1]                                    # already picked today: the same again (no flicker)
    else:
        i = pick(kind, len(vs), who, day, seq, (rec[0], rec[1]) if rec else None)
        if store is not None:
            mem[f"{kind}:{t}"] = [day.isoformat(), i, seq]
            store.put(key, mem)
    return _fill(vs[i], slots)


def samples(tone: str, name: str = "Alex", day: date | None = None) -> list[str]:
    """A few example lines in one tone, for the tone picker's preview."""
    day = day or date.today()
    p = type("P", (), {"id": f"preview-{name}", "settings": type("S", (), {"tone": tone})()})()
    sug = say("pattern_cooler", {"phase": "Deep", "n": 3, "look": 7, "step": "1°"}, person=p, day=day)
    pet = say("pet_question", {"who": "Biscuit", "when": "2:10 AM"}, person=p, day=day)
    morn = say("morning", {"name": name, "summary": "7 h 50 min in bed · settled in 12 min"}, person=p, day=day)
    return [f"{sug[0]}. {sug[1]}", pet[0], f"{morn[0]}: {say('checkin', person=p, day=day)}",
            str(say("flash_end", person=p, day=day))]


def night_words(s: dict, person: Any, d: date) -> dict:
    """Words for one night on the Sleep screen, in the person's tone (picked per night, so each night keeps its own
    wording): pet and late-start story lines, the check-in question, and the night in one line. Lines that must stay
    plain (the room-cold note) or discreet ("Active, not sleeping yet") are left exactly as the engine wrote them."""
    from .notify import summary_line
    for i, m in enumerate(s.get("moments") or []):
        if m.get("kind") == "pet":
            slots = {"who": m.get("pet") or "a pet"}
            m["text"] = say("story_pet_with" if m.get("with_person") else "story_pet", slots, person=person, day=d, seq=i)
        elif m.get("fitted"):
            m["text"] = say("story_fitted", person=person, day=d)
    s["voice"] = {"tone": tone_of(person), "checkin": say("checkin_app", person=person, day=d),
                  "story": summary_line(s) if s.get("in_bed_min") else None}
    return s


def suggestion_words(kind: str, direction: str, slots: dict[str, Any], person: Any, day: date, seq: int = 0) -> dict:
    """{"title", "detail"} for an Autopilot suggestion ("manual" or "pattern", "cooler" or "warmer")."""
    title, detail = say(f"{kind}_{direction}", slots, person=person, day=day, seq=seq)
    return {"title": title, "detail": detail}
