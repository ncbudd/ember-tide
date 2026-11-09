"""Lab: many nights in a row with sleepers who react to temperature. Does Autopilot behave like a sensible helper?

Each scenario runs the real controller and Autopilot over 4-10 simulated nights and checks the outcome a person would
judge: a comfortable sleeper hears nothing, a hot sleeper is offered cooler (never warmer), suggestions stay on the
phase that's actually wrong, nothing flip-flops, limits hold in Act mode, the learning week stays quiet, dismissals stick.
"""

from collections import Counter, defaultdict


from lab import Lab, Sleeper

START = "2026-11-02T20:00"          # a Monday; every night uses the weeknight rhythm except Fri/Sat


def morning_items(show_all=True):
    def f(lab, d):
        r = lab.suggest(lab.p1, show_all=show_all)
        return {"date": d.isoformat(), "items": [(i["kind"], i.get("phase"), i["direction"], i["id"]) for i in r["items"]],
                "learning": r.get("learning"), "waiting": r.get("waiting")}
    return f


def acted(lab, pid="p1"):
    return [x for x in lab.decisions(pid) if x["status"] in ("suggested", "applied")]


def skip_learning(lab, *people):
    for p in people or (lab.p1,):
        p.settings.autopilot.learn_skip = True


# ───────── the comfortable sleeper must hear nothing ─────────
async def test_comfortable_sleeper_stays_quiet_for_ten_nights(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(watch=True)}, room_c=21.5)
    await lab.nights(10, morning=morning_items(show_all=True))
    assert acted(lab) == []
    assert all(m["items"] == [] for m in lab.mornings), [m for m in lab.mornings if m["items"]]


async def test_restless_but_comfortable_sleeper_gets_no_temperature_advice(tmp_path):
    """Moves a lot every night whatever the temperature (that's just how they sleep). After the learning week the
    baseline should absorb it: no 'make it cooler' suggestions built on restlessness alone."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(restless=0.45, watch=True)})
    await lab.nights(9, morning=morning_items(show_all=True))
    a = acted(lab)
    assert a == [], Counter((x["direction"], x["phase"], tuple(r["signal"] for r in x["reasons"])) for x in a)
    assert all(m["items"] == [] for m in lab.mornings), [m["items"] for m in lab.mornings if m["items"]]


# ───────── hot and cold sleepers ─────────
async def test_hot_sleeper_is_offered_cooler_never_warmer(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -1.7}, watch=True, tap_prob=0.3)})
    skip_learning(lab)
    await lab.nights(5, morning=morning_items())
    dirs = Counter(x["direction"] for x in acted(lab))
    assert dirs["cooler"] >= 5 and dirs["warmer"] == 0, dirs
    pats = [i for m in lab.mornings for i in m["items"] if i[0] == "pattern"]
    assert pats and all(i[2] == "cooler" for i in pats), pats
    assert {"deep"} <= {i[1] for i in pats}


async def test_cold_sleeper_is_offered_warmer_never_cooler(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": +1.7}, watch=True, tap_prob=0.3)})
    lab.p1.persona = "ember"
    skip_learning(lab)
    await lab.nights(5, morning=morning_items())
    dirs = Counter(x["direction"] for x in acted(lab))
    assert dirs["warmer"] >= 5 and dirs["cooler"] == 0, dirs
    pats = [i for m in lab.mornings for i in m["items"] if i[0] == "pattern"]
    assert pats and all(i[2] == "warmer" for i in pats), pats


async def test_hot_sleeper_without_watch_or_taps_still_noticed(tmp_path):
    """Only the bed's own sensors: restless and a faster heart rate whenever it's too warm."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -2.2})})
    skip_learning(lab)
    await lab.nights(4)
    dirs = Counter(x["direction"] for x in acted(lab))
    assert dirs["warmer"] == 0, dirs


# ───────── the right phase, and no flip-flopping ─────────
async def test_deep_only_tapper_gets_a_deep_suggestion_only(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"deep": -1.5}, tap_prob=0.8, tap_phases={"deep"})})
    skip_learning(lab)
    await lab.nights(4, morning=morning_items())
    pats = {(i[1], i[2]) for m in lab.mornings for i in m["items"] if i[0] == "pattern"}
    assert ("deep", "cooler") in pats
    assert pats <= {("deep", "cooler")}, pats


async def test_split_preferences_never_flip_flop(tmp_path):
    """Too warm in Deep, too cool in Late night. Each phase gets its own direction, every morning."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"deep": -1.5, "late": +1.5}, tap_prob=0.8, watch=True)})
    skip_learning(lab)
    await lab.nights(5, morning=morning_items())
    seen: dict[str, set] = defaultdict(set)
    for m in lab.mornings:
        for kind, ph, d, _ in m["items"]:
            if kind == "pattern":
                seen[ph].add(d)
    assert seen.get("deep") == {"cooler"} and seen.get("late") == {"warmer"}, dict(seen)
    live = defaultdict(set)
    for x in acted(lab):
        live[x["phase"]].add(x["direction"])
    assert "warmer" not in live.get("deep", set()) and "cooler" not in live.get("late", set()), dict(live)


# ───────── flashes, warm and humid rooms ─────────
async def test_hot_flashes_in_one_phase_lead_to_a_suggestion(tmp_path):
    """A hot flash in Late night every night (otherwise comfortable): after a few nights, Late night 1 °F cooler."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(flash_phase="late", flash_minute=30)})
    skip_learning(lab)
    await lab.nights(5, morning=morning_items())
    assert len(lab.events("hot_flash", "p1")) >= 5
    pats = {(i[1], i[2]) for m in lab.mornings for i in m["items"]}
    assert ("late", "cooler") in pats, pats


# fixed: a room outside the comfortable range counts however usual it has become
async def test_warm_summer_week_cools_and_names_the_room(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(watch=True)}, room_c=26.5)
    skip_learning(lab)
    await lab.nights(4, morning=morning_items())
    a = acted(lab)
    assert a and all(x["direction"] == "cooler" for x in a)
    assert any(r["signal"] == "room" for x in a for r in x["reasons"])


async def test_humid_but_mild_room_alone_does_nothing(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(watch=True)}, room_c=22.5, humidity=72)
    skip_learning(lab)
    await lab.nights(3)
    assert acted(lab) == []


async def test_room_that_heats_up_mid_night(tmp_path):
    """Heating comes on at 03:00 and the room climbs to 26 °C: Late night gets cooler suggestions, earlier phases none."""
    from datetime import datetime
    from lab import TZ

    def room(t):
        h = datetime.fromtimestamp(t, TZ).hour
        return 26.0 if 3 <= h < 9 else 21.0
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(watch=True)}, room_profile=room)
    skip_learning(lab)
    await lab.nights(3)
    a = acted(lab)
    assert a and {x["phase"] for x in a} <= {"late"} and all(x["direction"] == "cooler" for x in a), \
        Counter((x["phase"], x["direction"]) for x in a)


# ───────── Act mode, closed loop ─────────
async def test_act_mode_hot_sleeper_respects_every_limit(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -2.5}, watch=True, tap_prob=0.5)})
    pol = lab.p1.settings.autopilot
    pol.mode, pol.learn_skip = "act", True
    await lab.nights(4)
    by_night = defaultdict(list)
    from embertide.engine.rhythm import night_date
    from datetime import datetime
    from lab import TZ
    for x in lab.decisions("p1", "applied"):
        by_night[night_date(datetime.fromtimestamp(x["ts"], TZ))].append(x)
    assert by_night, "Act mode never acted for a sleeper 2.5 °C too warm"
    for d, xs in by_night.items():
        assert len(xs) <= pol.max_actions
        assert abs(sum(x["delta_c"] for x in xs)) <= pol.max_delta_c + 0.01
        assert all(x["phase"] in pol.phases for x in xs)
        ts_ = sorted(x["ts"] for x in xs)
        assert all(b - a >= pol.min_gap_min * 60 for a, b in zip(ts_, ts_[1:]))
        assert all(x["direction"] == "cooler" for x in xs)
    adj = [x for x in lab.events("adjust", "p1") if x.get("source") == "autopilot"]
    assert len(adj) == len(lab.decisions("p1", "applied"))


async def test_act_mode_backs_off_when_you_push_back(tmp_path):
    """Autopilot cools; the person immediately makes it warmer again. No further cooling for respect_manual_min."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -2.5}, watch=True, tap_prob=0.5)})
    pol = lab.p1.settings.autopilot
    pol.mode, pol.learn_skip, pol.respect_manual_min = "act", True, 60
    applied_seen = []

    async def pushback():
        for _ in range(400):
            await lab.step(1, 120)
            ap = lab.decisions("p1", "applied")
            if ap and not applied_seen:
                applied_seen.append(lab.clock.now_ts())
                lab.c.engine_for("p1").adjust(lab.clock.now_ts(), delta_c=0.56, until="next_phase", source="app")
                break
    lab.clock.jump_to(lab.clock.now_ts() + 3600)
    await pushback()
    assert applied_seen
    t0 = applied_seen[0]
    await lab.until(t0 + 3600, dt=120)
    later = [x for x in lab.decisions("p1", "applied") if t0 < x["ts"] < t0 + 3600]
    assert later == []


# ───────── learning week, cadence, dismissals ─────────
async def test_learning_week_is_silent_then_suggests(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -1.7}, watch=True, tap_prob=0.4)})
    assert lab.p1.settings.autopilot.learn_nights == 7
    await lab.nights(9, morning=morning_items(show_all=False))
    learning_nights = lab.mornings[:6]                   # the 7th night finishes the learning week
    assert all(m["items"] == [] for m in learning_nights), [m["items"] for m in learning_nights]
    assert all(not (m["learning"] or {}).get("done") for m in lab.mornings[:6])
    first_week = [x for x in lab.decisions("p1") if x["ts"] < lab.clock.now_ts() - 2 * 86400]
    assert not [x for x in first_week if x["status"] in ("suggested", "applied")], Counter(x["status"] for x in first_week)
    assert any(m["items"] for m in lab.mornings[7:]), [m for m in lab.mornings[7:]]


async def test_learning_week_in_act_mode_changes_nothing(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -2.5}, watch=True, tap_prob=0.5)})
    lab.p1.settings.autopilot.mode = "act"
    await lab.nights(3)
    assert lab.decisions("p1", "applied") == []
    assert not [x for x in lab.events("adjust", "p1") if x.get("source") == "autopilot"]


async def test_learning_nights_zero_means_no_learning(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -1.7}, tap_prob=0.5)})
    lab.p1.settings.autopilot.learn_nights = 0
    await lab.nights(2)
    assert acted(lab)


async def test_weekly_cadence_only_shows_on_the_chosen_morning(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"deep": -1.5}, tap_prob=0.8, tap_phases={"deep"})})
    pol = lab.p1.settings.autopilot
    pol.learn_skip, pol.suggest_cadence, pol.suggest_weekday = True, "weekly", 5      # Saturday mornings
    from datetime import date
    await lab.nights(7, morning=morning_items(show_all=False))
    for m in lab.mornings:
        wd = date.fromisoformat(m["date"]).weekday()
        # the morning after night `date` is date+1
        if (wd + 1) % 7 != 5:
            assert m["items"] == [], m
    assert any(m["items"] for m in lab.mornings if (date.fromisoformat(m["date"]).weekday() + 1) % 7 == 5)


async def test_dismissed_forever_pattern_stays_gone(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"deep": -1.5}, tap_prob=0.8, tap_phases={"deep"})})
    skip_learning(lab)
    await lab.nights(3)
    items = lab.suggest(lab.p1)["items"]
    pat = next(i for i in items if i["kind"] == "pattern")
    lab.store.put("ap_dismissed:p1", {pat["id"]: {"ts": lab.clock.now_ts(), "forever": True}})
    await lab.nights(2, morning=morning_items())
    back = [i for m in lab.mornings for i in m["items"] if i[0] == "pattern" and i[1] == pat["phase"] and i[2] == pat["direction"]]
    assert back == [], back


async def test_both_sleepers_at_once_stay_independent(tmp_path):
    """Hot sleeper on the left, cold on the right. Each only ever gets their own direction."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(pref_c={"*": -1.7}, tap_prob=0.4),
                                         "right": Sleeper(pref_c={"*": +1.7}, tap_prob=0.4)}, echo=False)
    lab.bed.script["right"] = ("22:29", "06:50", [])
    skip_learning(lab, lab.p1, lab.p2)
    await lab.nights(3)
    l = Counter(x["direction"] for x in acted(lab, "p1"))
    r = Counter(x["direction"] for x in acted(lab, "p2"))
    assert l["cooler"] and not l["warmer"], l
    assert r["warmer"] and not r["cooler"], r


async def test_blocked_records_alone_never_become_a_suggestion(tmp_path):
    """Three nights where Autopilot noticed 'too warm' in Deep but every time was blocked (e.g. you'd just changed it
    yourself). Nothing was ever suggested live; the morning shouldn't build a pattern on it either."""
    from lab import ts
    lab = Lab(tmp_path, "2026-11-06T09:00", sleepers={"left": Sleeper()})
    skip_learning(lab)
    for d in ("2026-11-03T01:00", "2026-11-04T01:00", "2026-11-05T01:00"):
        lab.store.event(ts(d), "autopilot", "p1", "autopilot", phase="deep", direction="cooler", status="blocked",
                        delta_c=-0.56, score=1.2, threshold=1.0, reasons=[], blocked_by=["you changed it yourself"])
    assert [i for i in lab.suggest(lab.p1)["items"] if i["kind"] == "pattern"] == []
