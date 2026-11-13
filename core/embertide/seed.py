"""Default household for a fresh install (and the simulator). Names are placeholders the user changes in setup."""

from __future__ import annotations

from .models import Anchor, Household, Person, Phase, Rhythm, Schedule, clamp_phase_temp
from .units import f_to_c


def _phases(wind, fall, deep, late, wake) -> list[Phase]:
    return [
        Phase(id="wind_down", name="Wind-down", temp_c=round(f_to_c(wind), 2), start=Anchor(kind="bedtime", offset_min=-45), ramp_min=15, autopilot=False),
        Phase(id="fall_asleep", name="Fall asleep", temp_c=round(f_to_c(fall), 2), start=Anchor(kind="in_bed"), ramp_min=10),
        Phase(id="deep", name="Deep", temp_c=round(f_to_c(deep), 2), start=Anchor(kind="in_bed", offset_min=40), ramp_min=20),
        Phase(id="late", name="Late night", temp_c=round(f_to_c(late), 2), start=Anchor(kind="clock", time="03:00"), ramp_min=30),
        Phase(id="wake", name="Warm wake", temp_c=round(f_to_c(wake), 2), start=Anchor(kind="wake", offset_min=-30), ramp_min=30, autopilot=False),
    ]


PERSONA_TEMPS_F = {
    "tide": (68, 66, 63, 65, 74),     # runs hot
    "ember": (78, 75, 72, 72, 80),    # runs cold
    "neutral": (72, 70, 68, 69, 76),
}


def default_rhythms(persona: str) -> list[Rhythm]:
    t = PERSONA_TEMPS_F.get(persona, PERSONA_TEMPS_F["neutral"])
    return [
        Rhythm(id="weeknights", name="Weeknights", days=[0, 1, 2, 3, 6], nights="work", schedule=Schedule(), phases=_phases(*t)),
        Rhythm(id="weekends", name="Weekends", days=[4, 5], nights="off", schedule=Schedule(bedtime="23:00", wake="08:00", window_end="02:30"), phases=_phases(*t)),
    ]


def apply_persona_temps(person, household: Household | None = None) -> None:
    """Set each default phase to the persona's starting temperature (first-time setup: "I run cold / hot")."""
    t = dict(zip(("wind_down", "fall_asleep", "deep", "late", "wake"), PERSONA_TEMPS_F.get(person.persona, PERSONA_TEMPS_F["neutral"])))
    for rh in person.rhythms:
        for ph in rh.phases:
            if ph.id in t:
                ph.temp_c = clamp_phase_temp(f_to_c(t[ph.id]), household)


def default_household() -> Household:
    return Household(people=[
        Person(id="p1", name="Left", persona="tide", zone="left", rhythms=default_rhythms("tide")),
        Person(id="p2", name="Right", persona="ember", zone="right", rhythms=default_rhythms("ember")),
    ])
