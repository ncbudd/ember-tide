"""Domain models. All temperatures in °C, durations in minutes, clock times as 'HH:MM' local."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Zone = Literal["left", "right"]
Persona = Literal["tide", "ember", "neutral"]
AnchorKind = Literal["bedtime", "in_bed", "clock", "wake"]
View = Literal["me", "both", "partner"]


class Anchor(BaseModel):
    kind: AnchorKind
    offset_min: int = Field(0, ge=-1440, le=1440)   # relative to the anchor (negative = before)
    time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")      # for kind == "clock"


class Phase(BaseModel):
    id: str
    name: str
    temp_c: float = Field(ge=10, le=45)
    start: Anchor
    ramp_min: int = Field(10, ge=0, le=180)           # linear transition from the previous phase's temperature
    autopilot: bool = True       # Autopilot may adjust inside this phase (if the policy allows)


class Schedule(BaseModel):
    bedtime: str = Field("22:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")   # predicted bedtime when nothing better is known
    wake: str = Field("06:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    window_start: str = Field("21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # earliest time getting in bed starts the night
    window_end: str = Field("01:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")    # keep pre-cooling an empty side until this time
    precool_lead_min: int = Field(45, ge=0, le=180)
    confirm_min: int = Field(3, ge=0, le=30)          # minutes on your side before the rhythm starts
    after_wake_min: int = Field(15, ge=0, le=180)     # side turns off this long after wake
    absent_hold_min: int = Field(30, ge=0, le=240)    # out of bed at night: hold, then turn off
    start_on_presence: bool = True


class Rhythm(BaseModel):
    id: str
    name: str
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6], max_length=7)  # Monday = 0
    schedule: Schedule = Field(default_factory=Schedule)
    phases: list[Phase] = Field(min_length=1, max_length=12)
    # "work": the nights before this person's work days, "off": the nights before their days off; the days then follow
    # PersonSettings.work_days. "" = exactly the days chosen here.
    nights: Literal["", "work", "off"] = ""

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(not 0 <= d <= 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    def model_post_init(self, _ctx) -> None:
        # rhythms saved before work days existed: the two default ones, still on their default nights, follow work days
        if not self.nights and "nights" not in self.model_fields_set:
            if self.id == "weeknights" and self.days == [0, 1, 2, 3, 6]:
                self.nights = "work"
            elif self.id == "weekends" and self.days == [4, 5]:
                self.nights = "off"


def work_nights(work_days: list[int]) -> list[int]:
    """Rhythm days (the evening a night starts on, 0 = Monday) for the nights before a work day."""
    return sorted({(d - 1) % 7 for d in work_days})


def off_nights(work_days: list[int]) -> list[int]:
    """The nights before a day off."""
    return sorted(set(range(7)) - set(work_nights(work_days)))


DEFAULT_RHYTHM_NAMES = {"work": ("Weeknights", "Work nights"), "off": ("Weekends", "Days off")}


def sync_rhythm_days(person: Person) -> bool:
    """Point the rhythms that follow work days at this person's nights. True if anything changed."""
    wd = person.settings.work_days
    changed = False
    for rh in person.rhythms:
        if not rh.nights:
            continue
        days = work_nights(wd) if rh.nights == "work" else off_nights(wd)
        if rh.days != days:
            rh.days, changed = days, True
        old, new = DEFAULT_RHYTHM_NAMES[rh.nights]
        if rh.name == old and wd != [0, 1, 2, 3, 4]:      # "Weeknights" would be wrong for someone who works weekends
            rh.name, changed = new, True
    return changed


class FlashSettings(BaseModel):
    target_c: float = Field(ge=10, le=45)
    relative: bool = False       # if true, target is delta_c from the current target
    delta_c: float = Field(5.5, ge=0, le=15)
    duration_min: int = Field(15, ge=1, le=120)
    ease_min: int = Field(5, ge=0, le=30)
    extend_min: int = Field(5, ge=1, le=30)
    max_total_min: int = Field(60, ge=5, le=240)


class AutopilotPolicy(BaseModel):
    mode: Literal["off", "suggest", "act"] = "suggest"
    max_delta_c: float = 1.7     # about 3 °F
    floor_c: float = 14.5        # 58 °F
    ceiling_c: float = 26.5      # 80 °F
    max_actions: int = 4
    min_gap_min: int = 20
    phases: list[str] = Field(default_factory=lambda: ["fall_asleep", "deep", "late"])
    quiet_before_wake_min: int = Field(45, ge=0, le=180)
    signals: list[str] = Field(default_factory=lambda: ["feedback", "skin_temp", "movement", "hr", "flashes", "pad", "room"])
    sensitivity: Literal["low", "medium", "high"] = "medium"
    respect_manual_min: int = Field(30, ge=0, le=240)       # your own change wins for this long
    ai_review: bool = False                                  # ask the AI model to review each night in the morning
    suggest_manual: bool = True                              # morning: "add last night's own change to the rhythm?"
    suggest_cadence: Literal["morning", "weekly", "monthly", "off"] = "morning"   # how often suggestions are shown
    suggest_weekday: int = Field(6, ge=0, le=6)              # weekly/monthly: which morning (0 = Monday … 6 = Sunday);
                                                             # monthly = the first one of the month
    learn_nights: int = Field(7, ge=0, le=30)                # calibration: nights of bed data before it suggests anything
    learn_skip: bool = False                                 # "start now" without finishing the learning week
    learn_since: float | None = None                         # recalibrated at: only nights after this teach the normal range
    trust: dict[str, float] = Field(default_factory=lambda: {"bed": 1.0, "wearable": 1.0, "room": 1.0})
    # "Which to trust more" when the bed and a watch or ring disagree: in-bed times, sleep stages, heart rate, the
    # learned normal range and the live "is it me" vote (most wearables measure sleep better than a bed strip)
    trust_more: Literal["wearable", "balanced", "bed"] = "wearable"

    @field_validator("trust")
    @classmethod
    def _trust(cls, v: dict[str, float]) -> dict[str, float]:
        """How much each data source counts (0 = ignore … 2 = double). Unknown keys dropped, missing ones = 1."""
        out = {"bed": 1.0, "wearable": 1.0, "room": 1.0}
        import math
        for k in out:
            if k in v:
                try:
                    x = float(v[k])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(x):
                    out[k] = round(max(0.0, min(2.0, x)), 2)
        return out

    @field_validator("signals")
    @classmethod
    def _migrate_signals(cls, v: list[str]) -> list[str]:
        """Settings saved before Nov 2026 used other names and lacked the newer signals: bring them up to date."""
        legacy = {"bed_hr": "hr", "room_temp": "room"}
        if any(x in legacy for x in v):
            v = [legacy.get(x, x) for x in v] + ["feedback", "skin_temp", "flashes", "pad"]
        out: list[str] = []
        for x in v:
            if x not in out:
                out.append(x)
        return out


class NotifyPrefs(BaseModel):
    away_precool: bool = True     # pre-cool is due and you're not home
    empty_side: bool = True       # pre-cooled but nobody got in
    left_on: bool = True          # turned on by hand, then nobody in it for a while
    not_started: bool = True      # lying in bed a long while but the bed isn't sure it's you: offer to start the side
    morning: bool = True          # a short morning summary
    problems: bool = True         # bed or sensor trouble (household)
    hub: bool = True            # the bed's hub needs a look: a fault, or the water (owner)
    maintenance: bool = True    # a care job is due: purification tablets, a check (owners and sleepers, each their own switch; daytime only, at most one a day)
    pet_question: bool = True     # "was the pet on the bed?" (daytime only; the question also waits in the app)
    quiet_hours: bool = True      # hold anything non-urgent while you're in bed (bedtime to wake); it comes with the morning summary


# Person colours: key -> (dark theme, light theme). Light variants keep >= 4.5:1 on the darkest light card.
PALETTE: dict[str, tuple[str, str]] = {
    "teal": ("#5fb8b0", "#176963"), "orange": ("#ee8a55", "#9c4517"), "blue": ("#79aee8", "#1e5794"),
    "violet": ("#ae9af0", "#5a4398"), "rose": ("#ec8fae", "#9a2c52"), "green": ("#93c47e", "#386629"),
    "gold": ("#e0b45c", "#76540c"),
}
ColorKey = Literal["", "teal", "orange", "blue", "violet", "rose", "green", "gold"]


def person_color(p: Person) -> str:
    """The person's colour key; "" means the side default (left teal, right orange)."""
    return p.settings.color or ("teal" if p.zone == "left" else "orange")


class PersonSettings(BaseModel):
    units: Literal["F", "C"] = "F"
    weight_units: Literal["auto", "lb", "kg"] = "auto"   # pets, bed scales; "auto" = lb with °F, kg with °C
    color: ColorKey = ""               # "" = side default
    app_mode: Literal["full", "simple"] = "full"   # simple: just temperature, flashes and an easy schedule (details in Studio)
    welcomed: bool = False                         # has seen the first-time welcome (app style choice + short tour)
    default_view: View = "me"
    simple_default: bool = False         # open on the Simple screen (no chart)
    simple_at_night: bool = True         # switch to Simple once you're in bed (stays until you leave it)
    night_colors: bool = False           # legacy, unused
    simple_red: bool = False             # deep-red text on the black Simple screen
    appearance: Literal["system", "dark", "light", "schedule"] = "system"
    light_from: str = "07:00"            # schedule: light theme from…
    dark_from: str = "20:00"             # …and dark theme from
    hot_flash: FlashSettings = Field(default_factory=lambda: FlashSettings(target_c=12.8))   # 55 °F
    cold_flash: FlashSettings = Field(default_factory=lambda: FlashSettings(target_c=30.0))  # 86 °F
    autopilot: AutopilotPolicy = Field(default_factory=AutopilotPolicy)
    partner_can_flash: bool = True
    partner_can_adjust: bool = False
    notify: NotifyPrefs = Field(default_factory=NotifyPrefs)
    # "Let the assistant make small changes for me": tonight warmer/cooler within ±1.5 °C, flashes (assistant.py)
    assistant_small_changes: bool = False
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)   # 0 = Monday; days this person works
    wearable: str = Field("", max_length=20, pattern=r"^[a-z_]*$")   # first-time setup's pick ("oura", "apple", "none"...)
    setup_steps: list[str] = Field(default_factory=list, max_length=30)   # first-time setup steps already done (skipped next time)
    tone: Literal["calm", "friendly", "cheeky"] = "calm"   # how notifications and suggestions are worded (voice.py); alerts stay plain
    wake_with_alarm: bool = True         # the warm wake lines up with the phone's next alarm (shared from the phone), earlier or a bit later
    pet_questions: bool = True           # ask "was the pet on the bed?" after something lay on your side that didn't look like you
    # in / out of bed tuning (None = recommended): minutes lying down before the night starts, and minutes out of
    # bed before it counts as getting up (shorter trips, like the bathroom, are left out of the story)
    in_bed_after_min: int | None = Field(None, ge=0, le=30)
    up_after_min: int | None = Field(None, ge=1, le=30)

    @field_validator("work_days")
    @classmethod
    def _work_days(cls, v: list[int]) -> list[int]:
        if any(not isinstance(d, int) or not 0 <= d <= 6 for d in v):
            raise ValueError("work days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class Person(BaseModel):
    id: str
    name: str
    persona: Persona = "neutral"
    zone: Zone
    settings: PersonSettings = Field(default_factory=PersonSettings)
    rhythms: list[Rhythm] = Field(default_factory=list)


class Pet(BaseModel):
    """A pet that may get on the bed. Used only to recognise readings that are the pet, not a person."""
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:8], max_length=16)
    name: str = Field(min_length=1, max_length=40)
    kind: Literal["dog", "cat", "other"] = "dog"
    weight_kg: float = Field(ge=0.5, le=90)
    size: Literal["small", "medium", "large"] | None = None     # None = from weight
    sleeps_on_bed: Literal["yes", "sometimes", "no"] = "sometimes"
    side: Literal["left", "right", "both", "either"] = "either"

    def model_post_init(self, _ctx) -> None:
        if self.size is None:
            self.size = "small" if self.weight_kg < 10 else "medium" if self.weight_kg < 25 else "large"

    @property
    def fast_heart(self) -> bool:
        """Cats and small dogs have a resting heart rate well above a sleeping person's."""
        return self.kind == "cat" or (self.kind != "cat" and self.weight_kg < 12)


class GuestSide(BaseModel):
    """A side of the bed lent to a guest: plain on/off and a temperature. No rhythm, no Autopilot, nothing recorded."""
    since: float
    on: bool = True
    temp_c: float = Field(21.0, ge=10.0, le=45.0)
    morning_off_min: int = Field(40, ge=30, le=45)     # morning: off once the side has been empty this long
    ends: float | None = None                          # leave guest mode automatically at this time (None = until stopped)
    dial: bool = True                                  # the bedside dial shows its guest screen (False: keeps its usual look)


class Household(BaseModel):
    people: list[Person]
    room_c_default: float = 21.5
    bed_min_c: float = 10.0
    bed_max_c: float = 45.0
    user_min_c: float = 12.0     # everyday clamp; extremes need explicit override
    user_max_c: float = 35.0
    guest: dict[Literal["left", "right"], GuestSide] = Field(default_factory=dict)
    pets: list[Pet] = Field(default_factory=list, max_length=12)
    pets_ignore: bool = True              # ignore bed readings that look like a pet (only matters when pets are set)
