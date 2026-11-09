"""Temperatures are stored in °C everywhere in the core. Conversion happens only at the edges."""


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
