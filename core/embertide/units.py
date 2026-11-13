"""Temperatures are stored in °C everywhere in the core. Conversion happens only at the edges."""


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------- how a person wants to see numbers and times (settings: units, clock, language) ----------
def fmt_temp(c: float, units: str = "C") -> str:
    """A temperature (given in °C) in the person's unit: "72°F" or "22°C" / "21.5°C" (halves, no false precision)."""
    if units == "F":
        return f"{round(c_to_f(c))}°F"
    h = round(c * 2) / 2
    return f"{h:.0f}°C" if h == int(h) else f"{h:.1f}°C"


def fmt_delta(c: float, units: str = "C") -> str:
    """A temperature DIFFERENCE (given in °C), signed: "+2°F" / "-0.5°C"."""
    if units == "F":
        return f"{c * 9 / 5:+.0f}°F"
    return f"{c:+.1f}°C"


def fmt_gap(c: float, units: str = "C") -> str:
    """An unsigned difference: "3°F" / "1.7°C"."""
    return f"{abs(c) * 9 / 5:.0f}°F" if units == "F" else f"{abs(c):.1f}°C"


def clock_style(settings) -> str:
    """"12" or "24" for one person: their clock setting, or (auto) 24 h with °C and 12 h with °F."""
    ck = getattr(settings, "clock", "auto")
    if ck in ("12", "24"):
        return ck
    return "12" if getattr(settings, "units", "C") == "F" else "24"


def fmt_clock(when, style: str = "24", tz=None) -> str:
    """A time of day from a datetime (or a unix timestamp with tz): "11:42 PM" or "23:42"."""
    from datetime import datetime as _dt
    dt = _dt.fromtimestamp(when, tz) if isinstance(when, (int, float)) else when
    if style == "12":
        return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    return f"{dt.hour:02d}:{dt.minute:02d}"


def fmt_hour(h: int, style: str = "24") -> str:
    """A whole hour: "11 PM" or "23:00"."""
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}" if style == "12" else f"{h:02d}:00"


# (code, name in English). The model is told the NAME; "auto" means "the language the person writes in".
LANGUAGES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
             "pt-BR": "Brazilian Portuguese", "nl": "Dutch", "sv": "Swedish", "da": "Danish", "nb": "Norwegian",
             "fi": "Finnish", "pl": "Polish", "cs": "Czech", "tr": "Turkish", "ja": "Japanese", "ko": "Korean",
             "zh": "Chinese", "zh-TW": "Traditional Chinese", "hi": "Hindi", "ar": "Arabic", "he": "Hebrew"}


def language_name(code: str | None) -> str | None:
    """English name of a BCP-47 tag ("pt-BR" -> Brazilian Portuguese); None for "auto"/empty; unknown tags pass through."""
    if not code or code == "auto":
        return None
    if code in LANGUAGES:
        return LANGUAGES[code]
    base = code.split("-")[0]
    return LANGUAGES.get(base, code)


def language_line(code: str | None, can_follow_question: bool = False) -> str:
    """The sentence for the prompts. auto: follow the person's own language when they ask, otherwise English."""
    n = language_name(code)
    if n:
        return f"Write in {n}."
    return ("Write in the language the person asks in." if can_follow_question else "Write in English.")


# ---------- hemisphere / season from the household time zone ----------
_SOUTH_PREFIXES = ("Australia/", "Pacific/Auckland", "Pacific/Chatham", "Pacific/Fiji", "Pacific/Tongatapu", "Pacific/Apia",
                   "Pacific/Norfolk", "Pacific/Easter", "Pacific/Rarotonga", "Pacific/Tahiti", "Pacific/Noumea",
                   "America/Argentina/", "America/Buenos_Aires", "America/Santiago", "America/Punta_Arenas",
                   "America/Sao_Paulo", "America/Bahia", "America/Campo_Grande", "America/Cuiaba", "America/Curitiba",
                   "America/Montevideo", "America/Asuncion", "America/Lima", "America/La_Paz", "America/Porto_Velho",
                   "America/Rio_Branco", "America/Araguaina", "America/Maceio", "America/Recife", "America/Salvador",
                   "America/Sao_Paulo", "Brazil/", "Chile/", "Africa/Johannesburg", "Africa/Maputo", "Africa/Harare",
                   "Africa/Windhoek", "Africa/Maseru", "Africa/Mbabane", "Africa/Lusaka", "Africa/Gaborone",
                   "Africa/Luanda", "Indian/Mauritius", "Indian/Reunion", "Indian/Antananarivo", "Antarctica/")
# zones within about 12 degrees of the equator: seasons are not a thing, so no season word
_TROPICAL_PREFIXES = ("Asia/Singapore", "Asia/Jakarta", "Asia/Kuala_Lumpur", "Asia/Kuching", "Asia/Manila", "Asia/Bangkok",
                      "Asia/Ho_Chi_Minh", "Asia/Colombo", "Asia/Phnom_Penh", "Asia/Vientiane", "Asia/Pontianak",
                      "Asia/Makassar", "Asia/Jayapura", "Asia/Brunei", "Asia/Dili", "Asia/Yangon", "Africa/Nairobi",
                      "Africa/Lagos", "Africa/Accra", "Africa/Kampala", "Africa/Dar_es_Salaam", "Africa/Addis_Ababa",
                      "Africa/Kinshasa", "Africa/Libreville", "Africa/Abidjan", "Africa/Douala", "Africa/Mogadishu",
                      "Africa/Djibouti", "Africa/Kigali", "Africa/Bujumbura", "Africa/Brazzaville", "Africa/Bangui",
                      "Africa/Monrovia", "Africa/Freetown", "Africa/Conakry", "Africa/Lome", "Africa/Porto-Novo",
                      "America/Bogota", "America/Guayaquil", "America/Panama", "America/Caracas", "America/Manaus",
                      "America/Belem", "America/Fortaleza", "America/Cayenne", "America/Paramaribo", "America/Guyana",
                      "America/Costa_Rica", "America/Managua", "America/Port_of_Spain", "Indian/Maldives",
                      "Pacific/Port_Moresby", "Pacific/Tarawa", "Pacific/Majuro", "Pacific/Palau", "Pacific/Guam",
                      "Pacific/Galapagos", "Pacific/Nauru", "Pacific/Funafuti", "Pacific/Honolulu")


def hemisphere_of(tz_name: str | None, override: str = "auto") -> str:
    """"north", "south" or "none" (tropics: no season). override: the household's own setting beats the guess."""
    if override in ("north", "south", "none"):
        return override
    z = tz_name or ""
    if z.startswith(_SOUTH_PREFIXES):
        return "south"
    if z.startswith(_TROPICAL_PREFIXES):
        return "none"
    return "north"


def season_words(d, tz_name: str | None = None, override: str = "auto") -> str:
    """"fall (October)" for the review's context. Tropics (or hemisphere "none"): just the month, no season."""
    names = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "fall", "fall", "fall", "winter"]
    month = d.strftime("%B")
    h = hemisphere_of(tz_name, override)
    if h == "none":
        return month
    s = names[(d.month - 1 + (6 if h == "south" else 0)) % 12]
    return f"{s} ({month})"
