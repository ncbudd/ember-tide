package app.embertide.wear

import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.roundToInt

/**
 * The watch app's thinking, kept free of Android screens so it can be tested: what the snapshot says (one side),
 * the words for it, the power button's rule, temperature steps and limits, and the flash countdown.
 */
data class FlashInfo(val kind: String, val endS: Double, val easeEndS: Double)

data class Side(
    val id: String, val name: String, val zone: String, val color: String, val fahrenheit: Boolean,
    val state: String, val phaseName: String?, val targetC: Double?, val measuredC: Double?, val setpointC: Double?,
    val manual: Boolean, val manualUntilS: Double?, val flash: FlashInfo?, val nextName: String?, val nextAtS: Double?,
    val nextWhenSettled: Boolean, val firstPhaseC: Double?, val awayKnown: Boolean, val deltaC: Double?,
    val bedtimeS: Double? = null, val firstPhaseName: String? = null, val firstPhaseStartS: Double? = null,
    val inBed: Boolean? = null, val simpleRed: Boolean = false,
)

data class Snap(val nowS: Double, val tz: String, val minC: Double, val maxC: Double, val me: Side, val partner: Side?)

object WatchLogic {
    const val DEFAULT_MIN_C = 12.0
    const val DEFAULT_MAX_C = 35.0
    const val FALLBACK_START_C = 21.1

    // ---------- reading the snapshot ----------
    private fun JSONObject.dbl(k: String): Double? = if (isNull(k) || !has(k)) null else optDouble(k).takeIf { !it.isNaN() }
    private fun JSONObject.str(k: String): String? = if (isNull(k) || !has(k)) null else optString(k).takeIf { it.isNotEmpty() }

    fun side(p: JSONObject): Side {
        val n = p.getJSONObject("night")
        val bed = p.optJSONObject("bed")
        val ov = n.optJSONObject("override")
        val fl = n.optJSONObject("flash")
        val nx = p.optJSONObject("next")
        val phases = p.optJSONArray("phases")
        val zone = p.optString("zone")
        val sched = p.optJSONObject("schedule")
        val first = if (phases != null && phases.length() > 0) phases.getJSONObject(0) else null
        val color = p.optJSONObject("settings")?.optString("color").orEmpty().ifEmpty { if (zone == "left") "teal" else "orange" }
        return Side(
            id = p.optString("id"), name = p.optString("name"), zone = zone, color = color,
            fahrenheit = p.optJSONObject("settings")?.optString("units", "F") != "C",
            state = n.optString("state"), phaseName = n.str("phase_name"),
            targetC = n.dbl("target_c"), measuredC = bed?.dbl("measured_c"), setpointC = bed?.dbl("setpoint_c"),
            manual = ov?.optBoolean("manual") == true, manualUntilS = ov?.dbl("end_ts"),
            flash = fl?.let { FlashInfo(it.optString("kind"), it.optDouble("end"), it.optDouble("ease_end", it.optDouble("end"))) },
            nextName = nx?.str("name"), nextAtS = nx?.dbl("at"), nextWhenSettled = nx?.optBoolean("when_settled") == true,
            firstPhaseC = first?.dbl("temp_c"),
            awayKnown = n.opt("away") is JSONObject, deltaC = ov?.dbl("delta_c"),
            bedtimeS = sched?.dbl("bedtime"), firstPhaseName = first?.str("name"), firstPhaseStartS = first?.dbl("start"),
            inBed = p.optJSONObject("presence")?.let { if (it.isNull("present") || !it.has("present")) null else it.optBoolean("present") },
            simpleRed = p.optJSONObject("settings")?.optBoolean("simple_red") == true,
        )
    }

    /** The signed-in person's side and (if the server sent it) the other side. Never guesses what wasn't sent. */
    fun snap(json: JSONObject, personId: String): Snap? {
        val arr = json.getJSONArray("people")
        val all = (0 until arr.length()).map { arr.getJSONObject(it) }
        val me = all.firstOrNull { it.optString("id") == personId } ?: return null
        val other = all.firstOrNull { it.optString("id") != personId }
        val hh = json.optJSONObject("household")
        val min = hh?.dbl("user_min_c") ?: DEFAULT_MIN_C
        val max = hh?.dbl("user_max_c") ?: DEFAULT_MAX_C
        return Snap(json.optDouble("now"), json.optString("tz", "UTC"), min, max, side(me), other?.let { side(it) })
    }

    // ---------- words ----------
    fun degrees(c: Double, f: Boolean): String = if (f) "${(c * 9 / 5 + 32).roundToInt()}°" else String.format(Locale.US, "%.1f°", c)

    fun clock(tsS: Double, tz: String): String {
        val zone = runCatching { ZoneId.of(tz) }.getOrDefault(ZoneId.of("UTC"))
        return DateTimeFormatter.ofPattern("h:mm a", Locale.US).format(Instant.ofEpochSecond(tsS.toLong()).atZone(zone))
    }

    /** Whole minutes left on a flash, rounded up, on the server's clock (`sinceFetchMs` is time passed on the watch). */
    fun flashLeftMs(s: Side, nowS: Double, sinceFetchMs: Long = 0): Long {
        val f = s.flash ?: return 0
        if (nowS >= f.endS) return 0
        return ((f.endS - nowS) * 1000).toLong() - sinceFetchMs
    }

    fun flashMinutes(leftMs: Long): Int = ceil(leftMs / 60000.0).toInt().coerceAtLeast(1)

    fun flashing(s: Side, nowS: Double, sinceFetchMs: Long = 0) = flashLeftMs(s, nowS, sinceFetchMs) > 0

    fun flashLabel(kind: String, leftMs: Long): String = "${if (kind == "hot") "Hot" else "Cold"} flash · ${flashMinutes(leftMs)} min"

    fun isOn(s: Side) = s.targetC != null

    /** The line under the name: what the side is doing, in plain words. */
    fun stateLine(s: Side, nowS: Double, sinceFetchMs: Long = 0): String {
        val left = flashLeftMs(s, nowS, sinceFetchMs)
        if (left > 0) return flashLabel(s.flash!!.kind, left)
        val t = s.targetC
        if (t == null) return when (s.state) {
            "skipped" -> "Off tonight"
            "paused" -> "Paused"
            "away" -> "Not home yet"
            "done" -> "Done for the night"
            "missed" -> "Waiting for you in bed"
            "no_rhythm" -> "No rhythm set"
            "out_of_bed" -> "Out of bed · side off"
            "day" -> "Not in bed yet"
            else -> "Off"
        }
        if (s.flash != null && nowS < s.flash.easeEndS) return "Easing back"
        val temp = degrees(t, s.fahrenheit)
        return when {
            s.manual -> "On by hand · $temp"
            s.state == "waiting" -> "Pre-conditioning"
            s.state == "out_of_bed_hold" -> "Ready while you're up"
            !s.phaseName.isNullOrBlank() -> "${s.phaseName} · $temp"
            else -> "Following the rhythm"
        }
    }

    /** `stateLine` with the clock for a timed by-hand session ("On until 7:30 AM"). */
    fun stateLine(s: Side, snap: Snap, sinceFetchMs: Long = 0): String {
        if (s.targetC != null && s.manual && s.manualUntilS != null && !flashing(s, snap.nowS, sinceFetchMs))
            return "On until ${clock(s.manualUntilS, snap.tz)}"
        return stateLine(s, snap.nowS, sinceFetchMs)
    }

    /** "Warm wake at 9:00 AM", or null when nothing is coming up for this side. */
    fun nextLine(s: Side, snap: Snap): String? {
        if (s.state == "skipped" || s.state == "done" || s.state == "paused") return null
        val name = s.nextName ?: return null
        val at = s.nextAtS ?: return null
        return if (s.nextWhenSettled) "$name when you settle" else "$name at ${clock(at, snap.tz)}"
    }

    /**
     * The times line on the main page. Before bedtime: when the wind-down starts and the usual time in bed
     * ("Wind-down 10:15 PM · In bed 11:00 PM"); waiting for a sign of sleep: "... when you settle"; otherwise the next change.
     * Nothing when the side is off tonight, paused or done.
     */
    fun timesLine(s: Side, snap: Snap): String? {
        if (s.state == "skipped" || s.state == "done" || s.state == "paused") return null
        if (s.nextWhenSettled) return nextLine(s, snap)
        val bed = s.bedtimeS
        val start = s.firstPhaseStartS
        if (bed != null && snap.nowS < bed) {
            val inBed = "In bed ${clock(bed, snap.tz)}"
            if (start != null && snap.nowS < start && start < bed) {
                val name = s.firstPhaseName?.takeIf { it.isNotBlank() } ?: "Wind-down"
                return "$name ${clock(start, snap.tz)} · $inBed"
            }
            if (s.targetC == null) return inBed
        }
        return nextLine(s, snap)
    }

    fun partnerLine(p: Side, snap: Snap): String {
        val base = stateLine(p, snap)
        val t = p.targetC?.let { degrees(it, p.fahrenheit) }
        return if (t == null || base.contains(t)) "${p.name} · $base" else "${p.name} · $base · $t"
    }

    fun bigTemp(s: Side, pendingSteps: Int, snap: Snap): String =
        s.targetC?.let { degrees(clampC(it + pendingSteps * stepC(s.fahrenheit), snap), s.fahrenheit) } ?: "Off"

    /** Idle (not running, not switched off for tonight): -/+ choose what "Turn on now" will start at; nothing is sent. */
    fun canPreset(s: Side) = s.targetC == null && s.state != "skipped" && s.state != "paused"

    /** The temperature "Turn on now" starts at after `steps` presses of -/+ (first phase, else a mild default; within the limits). */
    fun startTemp(s: Side, snap: Snap, steps: Int): Double = clampC(turnOnTemp(s) + steps * stepC(s.fahrenheit), snap)

    /** What the main page shows as the number: the running target, or (dimmed, labelled "Off") what it would run at. */
    fun shownTemp(s: Side, pendingSteps: Int, presetSteps: Int, snap: Snap): String =
        if (s.targetC != null) bigTemp(s, pendingSteps, snap) else degrees(startTemp(s, snap, if (canPreset(s)) presetSteps else 0), s.fahrenheit)

    // ---------- temperature ----------
    fun stepC(fahrenheit: Boolean): Double = if (fahrenheit) 5.0 / 9.0 else 0.5

    fun clampC(c: Double, snap: Snap) = c.coerceIn(snap.minC, snap.maxC)

    /** Can another step this way go? (Off sides can always be turned on with a step.) */
    fun canStep(s: Side, snap: Snap, pendingSteps: Int, dir: Int): Boolean {
        val t = s.targetC ?: return false                // off: handled by canPresetStep
        val next = t + (pendingSteps + dir) * stepC(s.fahrenheit)
        return next >= snap.minC - 0.01 && next <= snap.maxC + 0.01
    }

    fun canPresetStep(s: Side, snap: Snap, presetSteps: Int, dir: Int): Boolean {
        if (!canPreset(s)) return false
        val next = turnOnTemp(s) + (presetSteps + dir) * stepC(s.fahrenheit)
        return next >= snap.minC - 0.01 && next <= snap.maxC + 0.01
    }

    /** What to send for `steps` steps (positive = warmer): the same "adjust" the phone and tile use. */
    sealed interface Adjust {
        data class Delta(val deltaC: Double) : Adjust
        data class SetTo(val tempC: Double) : Adjust
    }

    fun adjustPlan(s: Side, snap: Snap, steps: Int): Adjust? {
        if (steps == 0) return null
        val step = steps * stepC(s.fahrenheit)
        val t = s.targetC
        if (t != null) {
            val d = clampC(t + step, snap) - t
            return if (abs(d) < 0.01) null else Adjust.Delta(round2(d))
        }
        // paused / off tonight: the server resumes the night, then applies the change to the plan
        if (s.state == "skipped" || s.state == "paused") return Adjust.Delta(round2(step))
        // idle: turn it on by hand around the bed's current setting (or the first phase's temperature)
        val base = s.setpointC ?: s.firstPhaseC ?: FALLBACK_START_C
        return Adjust.SetTo(round2(clampC(base + step, snap)))
    }

    fun adjustBody(a: Adjust): String = when (a) {
        is Adjust.Delta -> "{\"delta_c\":${a.deltaC},\"until\":\"next_phase\",\"source\":\"watch\"}"
        is Adjust.SetTo -> "{\"temp_c\":${a.tempC},\"until\":\"next_phase\",\"source\":\"watch\"}"
    }

    private fun round2(v: Double) = (v * 100).roundToInt() / 100.0

    // ---------- the power button (same rules as the tile) ----------
    enum class Power { TurnOffManual, OffTonight, Resume, TurnOnNow }

    /** by hand -> off; running -> off tonight; off tonight / paused -> resume (then on by hand if still idle); else on now. */
    fun powerAction(s: Side): Power = when {
        s.manual -> Power.TurnOffManual
        s.targetC != null && s.state != "skipped" && s.state != "paused" -> Power.OffTonight
        s.state == "skipped" || s.state == "paused" -> Power.Resume
        else -> Power.TurnOnNow
    }

    fun powerLabel(s: Side): String = when (powerAction(s)) {
        Power.TurnOffManual -> "Turn off"
        Power.OffTonight -> "Off tonight"
        Power.Resume -> "Resume"
        Power.TurnOnNow -> "Turn on now"
    }

    fun powerDoneText(p: Power): String = when (p) {
        Power.TurnOffManual -> "Turned off"
        Power.OffTonight -> "Off for tonight"
        Power.Resume -> "Back on"
        Power.TurnOnNow -> "On"
    }

    /** The temperature "turn on now" starts at before any -/+: the first phase, else a mild default. */
    fun turnOnTemp(s: Side): Double = s.firstPhaseC ?: FALLBACK_START_C

    // ---------- the first page's two layouts ----------
    enum class Layout { InBed, Full }

    /** How long "not in bed" must hold before the full layout comes back (a bit under one 30 s refresh), so a blip never flickers. */
    const val OUT_HOLD_MS = 25_000L

    /**
     * Which layout page 1 shows. In bed (the server's presence is true) switches at once; leaving bed only switches back
     * after `absentMs` (time since the signal first said "not in bed") reaches the hold. Unknown presence counts as not in bed.
     */
    fun layoutFor(previous: Layout, inBed: Boolean, absentMs: Long): Layout = when {
        inBed -> Layout.InBed
        previous == Layout.InBed && absentMs < OUT_HOLD_MS -> Layout.InBed
        else -> Layout.Full
    }

    /** The on/off button's words in the in-bed layout: just "On" / "Off" (or "Resume" after being switched off for tonight). */
    fun bedPowerLabel(s: Side): String = when (powerAction(s)) {
        Power.TurnOffManual, Power.OffTonight -> "Off"
        Power.Resume -> "Resume"
        Power.TurnOnNow -> "On"
    }

    /** Switching off (either kind) asks for a second tap in the in-bed layout. */
    fun needsOffConfirm(s: Side): Boolean = powerAction(s).let { it == Power.TurnOffManual || it == Power.OffTonight }

    // ---------- freshness ----------
    const val STALE_AFTER_MS = 90_000L

    fun isStale(ageMs: Long) = ageMs > STALE_AFTER_MS

    fun ageText(ageMs: Long): String {
        val m = (ageMs / 60000).toInt()
        return if (m < 1) "Updated just now" else if (m == 1) "Updated 1 min ago" else "Updated $m min ago"
    }

    // ---------- person colours (the server's palette, dark theme) ----------
    fun colorArgb(key: String): Long = when (key) {
        "teal" -> 0xFF5FB8B0
        "orange" -> 0xFFEE8A55
        "blue" -> 0xFF79AEE8
        "violet" -> 0xFFAE9AF0
        "rose" -> 0xFFEC8FAE
        "green" -> 0xFF93C47E
        "gold" -> 0xFFE0B45C
        else -> 0xFF5FB8B0
    }
}
