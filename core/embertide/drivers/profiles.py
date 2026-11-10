"""Hardware profiles: the bits of wording that belong to one kind of bed, so nothing maker-specific lives in the
generic code or the apps.

A driver offers `profile = ...` (see `Profile`). The care list (`care.py`) takes its default tasks from it, and the
apps take the hub's light-colour guide from it. A task here is a plain dict:

  id, title, why, steps (short ordered sentences), supplies (optional note), interval_days (None = no schedule),
  trigger ("interval", or "hub_water": due whenever the hub says its water needs a look),
  source ("maker" = the maker's own instructions, "default" = our suggestion), url (where it comes from),
  note (what the maker says about the interval, shown next to it), reminder (the notification title)
"""

from __future__ import annotations

from typing import TypedDict


class Profile(TypedDict, total=False):
    name: str
    tasks: list[dict]
    light_guide: dict | None


# ---------- a generic bed (the simulator, and any driver that doesn't say) ----------
GENERIC: Profile = {
    "name": "generic",
    "tasks": [
        {"id": "water", "title": "Add water", "trigger": "hub_water", "interval_days": None, "source": "default",
         "why": "The hub tells you when its water is low. Low water means the topper cools and heats less well.",
         "steps": ["Switch to the hub's fill mode (Studio → Bed hub → Add water).",
                   "Add water as the maker describes, slowly.",
                   "Stop when it's full, then check the status here."],
         "supplies": "The kind of water the maker asks for."},
        {"id": "hub_check", "title": "Check the hub, hoses and vents", "interval_days": 90, "source": "default",
         "reminder": "Time for a quick check of the hub",
         "why": "A few minutes keeps the vents clear and spots a loose hose or a drip early.",
         "steps": ["Wipe the outside of the hub with a clean, damp cloth and dust its vents.",
                   "Check there is some space around the hub, and it isn't boxed in.",
                   "Check the hoses are pushed in firmly and aren't kinked.",
                   "Look for water on the floor around the hub. If you find any, switch it off and contact the maker."],
         "note": "No schedule comes from the maker; every 3 months is a suggestion."},
        {"id": "water_care", "title": "Treat or refresh the water", "interval_days": 180, "source": "default",
         "reminder": "Time to treat or refresh the hub's water",
         "why": "Water that sits for months can grow a film. Treating or refreshing it keeps the lines clean.",
         "steps": ["Follow the maker's instructions for treating or changing the water.",
                   "Tap Done here so the next reminder counts from today."],
         "note": "Suggested every 6 months. Change it to whatever the maker says for your bed."},
    ],
    "light_guide": None,
}

# ---------- Orion (care text from the maker's help centre; the light guide from the maker's own app) ----------
_ORION_FILL = "https://help.orionsleep.com/en/articles/13232796-filling-and-priming-your-orion-system"
_ORION_NOISE = "https://help.orionsleep.com/en/articles/16418436-noise-and-vibration"

ORION: Profile = {
    "name": "orion",
    "tasks": [
        {"id": "tablets", "title": "Purification tablets", "interval_days": 180, "source": "maker", "url": _ORION_FILL,
         "reminder": "Time to add purification tablets",
         "why": "Keeps the water in the hub clean. The maker calls them cleaning tablets.",
         "steps": ["Have 2 purification (cleaning) tablets ready.",
                   "Take the lid off the top of the hub.",
                   "Add the 2 tablets to the water in the top reservoir.",
                   "Put the lid back on. The water doesn't need draining or changing.",
                   "Tap Done here so the next reminder counts from today."],
         "supplies": "2 tablets each time. Also add 2 after any drain and refill, but not at every top-up.",
         "note": "The maker says every 6 to 8 months. This starts at 6; make it longer if you like."},
        {"id": "water", "title": "Add water", "trigger": "hub_water", "interval_days": None, "source": "maker", "url": _ORION_FILL,
         "why": "The hub pulses a gentle white light and the app tells you when the water is low. Low water cools less well.",
         "steps": ["Use distilled water only. Tap or filtered water leaves mineral deposits.",
                   "Take the lid off the top of the hub.",
                   "Start fill mode first (Studio → Bed hub → Add water, or the maker's app). Don't add water before it asks.",
                   "Pour slowly into the top reservoir, only up to the bottom of the funnel opening. Don't overfill.",
                   "You may be asked to add water more than once. Keep going until the hub says it's finished (priming takes 5 to 10 minutes and is louder than usual).",
                   "If it says it needs water but the reservoir has some, tap the side of the hub firmly a few times to release trapped air."],
         "supplies": "Distilled water and a pitcher. Add the purification tablets only if it's time (see that task).",
         "note": "Topped up only when the hub asks. The maker gives no regular water change."},
        {"id": "hub_check", "title": "Check the hub, hose and vents", "interval_days": 90, "source": "default", "url": _ORION_NOISE,
         "reminder": "Time for a quick check of the hub and hose",
         "why": "The maker gives these checks but no schedule. A few minutes keeps it quiet and cooling well.",
         "steps": ["Wipe the outside of the hub with a clean, damp cloth and dust its vents.",
                   "Check there are 3 to 5 inches between the hub and the wall, and that it isn't inside furniture or under the bed.",
                   "Check the hose is pushed in at both ends until it clicks twice, with some slack so it isn't pressed against the frame.",
                   "Look for water on the floor around the hub and under the bed. If you find any, unplug the hub and contact the maker."],
         "note": "Suggested every 3 months; the maker gives no schedule for these checks."},
    ],
    "light_guide": {
        "title": "What the hub's light colours mean",
        "source": "From the maker's own guide",
        "items": [["Blue", "Pairing mode."],
                  ["Orange", "Powering on. Wait for it to change."],
                  ["Red", "Updating. Leave it alone and don't unplug it."],
                  ["Purple", "An error. Unplug it and plug it back in."],
                  ["White", "Online and connected."],
                  ["Gently pulsing white", "The water is low. Top up when prompted."],
                  ["No light", "Check the light brightness setting. If that isn't it, unplug the hub and plug it back in."]],
        "note": "The hub's light colour and brightness can also be changed from this page, so a colour you chose yourself is not a status.",
    },
}
