import sys
from cdp import Browser
only = set(sys.argv[1:])
b = Browser()
def want(n): return not only or n in only
try:
    b.view(390, 844, True, 2, "dark")
    b.go("/"); b.js("localStorage.setItem('et-me','p1')")
    for scheme in ("dark", "light"):
        b.view(390, 844, True, 2, scheme)
        if want("tonight_" + scheme):
            b.go("/"); b.shot("tonight-" + scheme)
    b.view(390, 844, True, 2, "dark")
    if want("simple"):
        b.go("/"); b.click_text("Night screen"); __import__("time").sleep(1.5); b.shot("simple")
    for name, path in (("rhythm", "/rhythm"), ("sleep", "/sleep")):
        if want(name):
            b.go(path, 4); b.shot(name + "-dark")
    if want("settings"):
        b.view(390, 1500, True, 2, "dark")
        b.go("/settings", 4); b.shot("settings")
    b.view(1360, 900, False, 1, "dark")
    for name, path in (("studio-overview", "/studio"), ("studio-nights", "/studio/nights"), ("studio-devices", "/studio/devices"),
                       ("studio-autopilot", "/studio/autopilot"), ("studio-insights", "/studio/insights"),
                       ("studio-notifications", "/studio/notifications")):
        if want(name):
            b.go(path, 5); b.shot(name)
finally:
    b.close()
