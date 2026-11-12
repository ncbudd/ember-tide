package app.embertide.wear

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchLogicTest {
    private val NOW = 1_800_000_000.0   // a fixed "server now"

    private fun side(
        state: String = "rhythm", phase: String? = "Deep", target: Double? = 27.2, manual: Boolean = false, f: Boolean = true,
        flash: FlashInfo? = null, next: Pair<String, Double>? = "Warm wake" to NOW + 3600, settle: Boolean = false,
        setpoint: Double? = 21.0, first: Double? = 22.0, name: String = "Sam", color: String = "teal", until: Double? = null,
    ) = Side("p1", name, "left", color, f, state, phase, target, 25.0, setpoint, manual, until, flash, next?.first, next?.second, settle, first, false, null)

    private fun snap(me: Side = side(), partner: Side? = null, min: Double = 12.0, max: Double = 35.0) =
        Snap(NOW, "UTC", min, max, me, partner)

    // ---------- reading the snapshot ----------
    private val json = """
      {"now": $NOW, "tz": "UTC", "household": {"user_min_c": 13.0, "user_max_c": 33.0},
       "people": [
        {"id":"p1","name":"Sam","zone":"left","settings":{"units":"C","color":"violet"},
         "night":{"state":"rhythm","phase_name":"Deep","target_c":27.2,"flash":null,"override":{"manual":false,"delta_c":0.5,"end_ts":null},"away":null},
         "bed":{"measured_c":26.0,"setpoint_c":27.2},"phases":[{"temp_c":22.5}],
         "next":{"name":"Warm wake","at":${NOW + 3600},"phase":"wake"}},
        {"id":"p2","name":"Alex","zone":"right","settings":{"units":"F"},
         "night":{"state":"skipped","phase_name":null,"target_c":null,"flash":null,"override":null},"bed":{},"phases":[],"next":null}
      ]}"""

    @Test fun readsMySideAndThePartner() {
        val s = WatchLogic.snap(JSONObject(json), "p1")!!
        assertEquals("Sam", s.me.name); assertEquals("violet", s.me.color); assertFalse(s.me.fahrenheit)
        assertEquals(13.0, s.minC, 0.0); assertEquals(33.0, s.maxC, 0.0)
        assertEquals(22.5, s.me.firstPhaseC!!, 0.0)
        assertEquals("Alex", s.partner!!.name)
        assertEquals("orange", s.partner!!.color)      // right side default
        assertTrue(s.partner!!.fahrenheit)
    }

    @Test fun noPartnerWhenServerSendsNone() {
        val one = JSONObject(json)
        one.getJSONArray("people").remove(1)
        assertNull(WatchLogic.snap(one, "p1")!!.partner)
    }

    @Test fun readsPresenceAndRedNightText() {
        val j = JSONObject(json)
        val p = j.getJSONArray("people").getJSONObject(0)
        p.put("presence", JSONObject("""{"present":true}""")); p.getJSONObject("settings").put("simple_red", true)
        val me = WatchLogic.snap(j, "p1")!!.me
        assertEquals(true, me.inBed); assertTrue(me.simpleRed)
        p.put("presence", JSONObject("""{"present":null}"""))
        assertNull(WatchLogic.snap(j, "p1")!!.me.inBed)
        assertNull(WatchLogic.snap(JSONObject(json), "p1")!!.me.inBed)          // not sent
        assertFalse(WatchLogic.snap(JSONObject(json), "p1")!!.me.simpleRed)
    }

    @Test fun layoutSwitchesInAtOnceAndOutAfterTheHold() {

        assertEquals(WatchLogic.Layout.InBed, WatchLogic.layoutFor(WatchLogic.Layout.Full, true, 0))
        assertEquals(WatchLogic.Layout.InBed, WatchLogic.layoutFor(WatchLogic.Layout.InBed, true, 0))
        assertEquals(WatchLogic.Layout.InBed, WatchLogic.layoutFor(WatchLogic.Layout.InBed, false, 0))                       // a blip
        assertEquals(WatchLogic.Layout.InBed, WatchLogic.layoutFor(WatchLogic.Layout.InBed, false, WatchLogic.OUT_HOLD_MS - 1))
        assertEquals(WatchLogic.Layout.Full, WatchLogic.layoutFor(WatchLogic.Layout.InBed, false, WatchLogic.OUT_HOLD_MS))
        assertEquals(WatchLogic.Layout.Full, WatchLogic.layoutFor(WatchLogic.Layout.Full, false, 0))
    }

    @Test fun bedButtonWordsAndConfirm() {
        assertEquals("Off", WatchLogic.bedPowerLabel(side())); assertTrue(WatchLogic.needsOffConfirm(side()))
        assertEquals("Off", WatchLogic.bedPowerLabel(side(manual = true))); assertTrue(WatchLogic.needsOffConfirm(side(manual = true)))
        val skipped = side(state = "skipped", target = null)
        assertEquals("Resume", WatchLogic.bedPowerLabel(skipped)); assertFalse(WatchLogic.needsOffConfirm(skipped))
        val idle = side(state = "day", target = null)
        assertEquals("On", WatchLogic.bedPowerLabel(idle)); assertFalse(WatchLogic.needsOffConfirm(idle))
    }

    @Test fun unknownPersonIsNull() = assertNull(WatchLogic.snap(JSONObject(json), "nobody"))

    // ---------- words ----------
    @Test fun degreesInBothUnits() {
        assertEquals("81°", WatchLogic.degrees(27.2, true))
        assertEquals("27.2°", WatchLogic.degrees(27.2, false))
    }

    @Test fun stateLines() {
        val sn = snap()
        assertEquals("Deep · 81°", WatchLogic.stateLine(sn.me, sn))
        assertEquals("Pre-conditioning", WatchLogic.stateLine(side(state = "waiting"), snap(side(state = "waiting"))))
        assertEquals("Off tonight", WatchLogic.stateLine(side(state = "skipped", target = null), sn))
        assertEquals("Not in bed yet", WatchLogic.stateLine(side(state = "day", target = null), sn))
        assertEquals("Paused", WatchLogic.stateLine(side(state = "paused", target = null), sn))
        assertEquals("Ready while you're up", WatchLogic.stateLine(side(state = "out_of_bed_hold"), sn))
        assertEquals("Following the rhythm", WatchLogic.stateLine(side(phase = null), sn))
    }

    @Test fun timedByHandSessionSaysUntil() {
        val m = side(manual = true, until = NOW + 1800)
        val sn = snap(m)
        assertTrue(WatchLogic.stateLine(m, sn).startsWith("On until "))
        assertEquals("On by hand · 81°", WatchLogic.stateLine(side(manual = true), snap(side(manual = true))))
    }

    @Test fun nextChangeLine() {
        val sn = snap()
        val t = WatchLogic.clock(NOW + 3600, "UTC")
        assertEquals("Warm wake at $t", WatchLogic.nextLine(sn.me, sn))
        assertEquals("Warm wake when you settle", WatchLogic.nextLine(side(settle = true), sn))
        assertNull(WatchLogic.nextLine(side(next = null), sn))
        assertNull(WatchLogic.nextLine(side(state = "skipped", target = null), sn))
    }

    @Test fun clockUsesTheServersZone() {
        assertEquals("12:00 AM", WatchLogic.clock(1_798_761_600.0, "UTC"))    // 2027-01-01 00:00 UTC
        assertEquals("7:00 PM", WatchLogic.clock(1_798_761_600.0, "America/New_York"))
    }

    @Test fun partnerLineIsJustWhatTheServerSent() {
        val p = side(name = "Alex", state = "skipped", target = null, color = "orange")
        assertEquals("Alex · Off tonight", WatchLogic.partnerLine(p, snap(partner = p)))
        val q = side(name = "Alex", phase = "Deep", target = 26.0)
        assertEquals("Alex · Deep · 79°", WatchLogic.partnerLine(q, snap(partner = q)))
    }

    // ---------- flash countdown ----------
    @Test fun flashCountdown() {
        val f = FlashInfo("hot", NOW + 600, NOW + 900)
        val s = side(flash = f)
        assertEquals(600_000L, WatchLogic.flashLeftMs(s, NOW))
        assertEquals(540_000L, WatchLogic.flashLeftMs(s, NOW, 60_000))     // a minute passed on the watch since the fetch
        assertEquals("Hot flash · 10 min", WatchLogic.flashLabel("hot", 600_000))
        assertEquals("Cold flash · 1 min", WatchLogic.flashLabel("cold", 5_000))     // never "0 min" while running
        assertEquals("Hot flash · 10 min", WatchLogic.stateLine(s, snap(s), 30_000))
        assertTrue(WatchLogic.flashing(s, NOW, 599_000))
        assertFalse(WatchLogic.flashing(s, NOW, 600_000))
        assertEquals(0L, WatchLogic.flashLeftMs(s, NOW + 700))
        assertEquals("Easing back", WatchLogic.stateLine(s, NOW + 700))
        assertEquals(0L, WatchLogic.flashLeftMs(side(), NOW))
    }

    // ---------- temperature ----------
    @Test fun steps() {
        assertEquals(5.0 / 9.0, WatchLogic.stepC(true), 1e-9)
        assertEquals(0.5, WatchLogic.stepC(false), 1e-9)
    }

    @Test fun adjustSendsADeltaWhileRunning() {
        val sn = snap()
        val a = WatchLogic.adjustPlan(sn.me, sn, 2) as WatchLogic.Adjust.Delta
        assertEquals(1.11, a.deltaC, 0.001)              // two degrees F
        val b = WatchLogic.adjustPlan(sn.me, sn, -1) as WatchLogic.Adjust.Delta
        assertEquals(-0.56, b.deltaC, 0.001)
        assertEquals("{\"delta_c\":-0.56,\"until\":\"next_phase\",\"source\":\"watch\"}", WatchLogic.adjustBody(b))
        assertNull(WatchLogic.adjustPlan(sn.me, sn, 0))
    }

    @Test fun adjustRespectsTheServersLimits() {
        val hot = side(target = 34.8)
        val sn = snap(hot)
        assertFalse(WatchLogic.canStep(hot, sn, 0, 1))
        assertTrue(WatchLogic.canStep(hot, sn, 0, -1))
        val d = WatchLogic.adjustPlan(hot, sn, 3) as WatchLogic.Adjust.Delta
        assertEquals(0.2, d.deltaC, 0.001)               // clamped to the 35 limit
        assertNull(WatchLogic.adjustPlan(side(target = 35.0), snap(side(target = 35.0)), 1))
        val cold = side(target = 12.2)
        assertFalse(WatchLogic.canStep(cold, snap(cold), -1, -1))    // a tapped step not yet sent counts
        assertEquals("95°", WatchLogic.bigTemp(hot, 5, sn))          // shown as 95 F (35 C), never past the limit
    }

    @Test fun adjustWhenOffTonightResumesViaDelta_andIdleTurnsOnByHand() {
        val skipped = side(state = "skipped", target = null)
        assertTrue(WatchLogic.adjustPlan(skipped, snap(skipped), 1) is WatchLogic.Adjust.Delta)
        val idle = side(state = "day", target = null, setpoint = 21.0)
        val a = WatchLogic.adjustPlan(idle, snap(idle), 1) as WatchLogic.Adjust.SetTo
        assertEquals(21.56, a.tempC, 0.001)
        val noSetpoint = side(state = "day", target = null, setpoint = null, first = 22.0)
        assertEquals(22.56, (WatchLogic.adjustPlan(noSetpoint, snap(noSetpoint), 1) as WatchLogic.Adjust.SetTo).tempC, 0.001)
        val nothing = side(state = "day", target = null, setpoint = null, first = null)
        assertEquals(21.66, (WatchLogic.adjustPlan(nothing, snap(nothing), 1) as WatchLogic.Adjust.SetTo).tempC, 0.001)
        assertEquals("Off", WatchLogic.bigTemp(idle, 1, snap(idle)))
    }

    // ---------- the times line on the main page ----------
    private fun at(h: Int, m: Int) = java.time.ZonedDateTime.of(2027, 1, 15, h, m, 0, 0, java.time.ZoneId.of("UTC")).toEpochSecond().toDouble()

    private fun night(now: Double, state: String, target: Double?, next: Pair<String, Double>?, settle: Boolean = false, first: Double? = at(22, 15), bed: Double = at(23, 0)) =
        Snap(now, "UTC", 12.0, 35.0, side(state = state, target = target, next = next, settle = settle)
            .copy(bedtimeS = bed, firstPhaseName = "Wind-down", firstPhaseStartS = first), null)

    @Test fun timesBeforeWindDown() {
        val sn = night(at(19, 0), "day", null, "Wind-down" to at(22, 15))
        assertEquals("Wind-down 10:15 PM · In bed 11:00 PM", WatchLogic.timesLine(sn.me, sn))
    }

    @Test fun timesDuringWindDownShowsTheNextChange() {
        val sn = night(at(22, 30), "rhythm", 21.0, "Fall asleep" to at(23, 0))
        assertEquals("Fall asleep at 11:00 PM", WatchLogic.timesLine(sn.me, sn))
    }

    @Test fun timesWhileWaitingForSleepKeepsTheSettleWording() {
        val sn = night(at(23, 30), "rhythm", 21.0, "Fall asleep" to at(23, 40), settle = true)
        assertEquals("Fall asleep when you settle", WatchLogic.timesLine(sn.me, sn))
    }

    @Test fun timesDuringTheNightAndAtWake() {
        val deep = night(at(2, 0), "rhythm", 27.0, "Warm wake" to at(7, 0), first = at(22, 15) - 86400, bed = at(23, 0) - 86400)
        assertEquals("Warm wake at 7:00 AM", WatchLogic.timesLine(deep.me, deep))
        val none = night(at(9, 0), "day", null, null, first = at(22, 15) - 86400, bed = at(23, 0) - 86400)
        assertNull(WatchLogic.timesLine(none.me, none))
    }

    @Test fun timesWhenOffTonightOrPaused() {
        val skipped = night(at(19, 0), "skipped", null, "Wind-down" to at(22, 15))
        assertNull(WatchLogic.timesLine(skipped.me, skipped))
        val paused = night(at(23, 30), "paused", null, "Deep" to at(1, 0))
        assertNull(WatchLogic.timesLine(paused.me, paused))
    }

    @Test fun timesIdleBeforeBedWithoutAWindDownAhead() {
        val sn = night(at(22, 40), "day", null, "Fall asleep" to at(23, 0), first = at(22, 15))
        assertEquals("In bed 11:00 PM", WatchLogic.timesLine(sn.me, sn))
    }

    // ---------- off sides: -/+ preset what "Turn on now" starts at ----------
    @Test fun presetWhenIdle() {
        val idle = side(state = "day", target = null, first = 22.0)
        val sn = snap(idle)
        assertTrue(WatchLogic.canPreset(idle))
        assertEquals(22.0 + 2 * 5.0 / 9.0, WatchLogic.startTemp(idle, sn, 2), 0.001)
        assertEquals("74°", WatchLogic.shownTemp(idle, 0, 2, sn))      // 22 C is 71.6 F; two steps up is 73.6
        assertTrue(WatchLogic.canPresetStep(idle, sn, 0, 1))
        assertFalse(WatchLogic.canPresetStep(side(state = "day", target = null, first = 35.0), snap(side(state = "day", target = null, first = 35.0)), 0, 1))
        assertFalse(WatchLogic.canStep(idle, sn, 0, 1))
    }

    @Test fun noPresetWhenOffTonightOrPaused() {
        val off = side(state = "skipped", target = null, first = 22.0)
        assertFalse(WatchLogic.canPreset(off))
        assertFalse(WatchLogic.canPresetStep(off, snap(off), 0, 1))
        assertEquals("72°", WatchLogic.shownTemp(off, 0, 3, snap(off)))   // the plan's temperature, unchanged
    }

    // ---------- the power button (the tile's rules) ----------
    @Test fun powerRules() {
        assertEquals(WatchLogic.Power.TurnOffManual, WatchLogic.powerAction(side(manual = true)))
        assertEquals(WatchLogic.Power.OffTonight, WatchLogic.powerAction(side()))
        assertEquals(WatchLogic.Power.OffTonight, WatchLogic.powerAction(side(state = "waiting")))
        assertEquals(WatchLogic.Power.Resume, WatchLogic.powerAction(side(state = "skipped", target = null)))
        assertEquals(WatchLogic.Power.Resume, WatchLogic.powerAction(side(state = "paused", target = null)))
        assertEquals(WatchLogic.Power.TurnOnNow, WatchLogic.powerAction(side(state = "day", target = null)))
        assertEquals(WatchLogic.Power.TurnOnNow, WatchLogic.powerAction(side(state = "done", target = null)))
        assertEquals("Off tonight", WatchLogic.powerLabel(side()))
        assertEquals("Resume", WatchLogic.powerLabel(side(state = "skipped", target = null)))
        assertEquals("Turn on now", WatchLogic.powerLabel(side(state = "day", target = null)))
        assertEquals("Turn off", WatchLogic.powerLabel(side(manual = true)))
        assertEquals(22.0, WatchLogic.turnOnTemp(side(first = 22.0)), 0.0)
        assertEquals(WatchLogic.FALLBACK_START_C, WatchLogic.turnOnTemp(side(first = null)), 0.0)
    }

    // ---------- freshness and colour ----------
    @Test fun staleness() {
        assertFalse(WatchLogic.isStale(60_000)); assertTrue(WatchLogic.isStale(120_000))
        assertEquals("Updated just now", WatchLogic.ageText(20_000))
        assertEquals("Updated 3 min ago", WatchLogic.ageText(200_000))
    }

    @Test fun personColoursFollowTheServerPalette() {
        assertEquals(0xFF5FB8B0, WatchLogic.colorArgb("teal"))
        assertEquals(0xFFEE8A55, WatchLogic.colorArgb("orange"))
        assertNotNull(WatchLogic.colorArgb("nonsense"))
    }
}
