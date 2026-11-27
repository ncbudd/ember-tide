package app.embertide.wear

import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.wear.tiles.TileService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import kotlin.math.roundToInt

enum class Problem { NotSignedIn, SignedOut, NoSide, Unreachable }

/** What the watch app screen shows and does. All networking goes through WearApi (the same address and watch key the tile uses). */
class BedModel(app: Application) : AndroidViewModel(app) {
    private val c get() = getApplication<Application>()
    private val lock = Mutex()

    var snap by mutableStateOf<Snap?>(null); private set
    var fetchedAt by mutableStateOf(0L); private set          // SystemClock.elapsedRealtime of the last good fetch
    var problem by mutableStateOf<Problem?>(null); private set
    var loading by mutableStateOf(true); private set
    var busy by mutableStateOf(false); private set
    var toast by mutableStateOf<Pair<String, Boolean>?>(null); private set
    var pending by mutableIntStateOf(0); private set          // temperature steps tapped or turned, not sent yet
    var preset by mutableIntStateOf(0); private set           // while the side is off: -/+ steps for what "Turn on now" starts at (nothing is sent)
    var update by mutableStateOf<Offer?>(null); private set
    var updateBusy by mutableStateOf<Int?>(null); private set
    var updateMsg by mutableStateOf<String?>(null); private set
    var updateUpToDate by mutableStateOf(false); private set

    private var nudgeJob: Job? = null
    private var sending = false
    private var toastJob: Job? = null
    private var updateCheckedAtMs: Long? = null
    private var checkingUpdate = false
    private var updateRunId = 0

    fun refresh() {
        viewModelScope.launch { lock.withLock { fetch() } }
    }

    private suspend fun fetch() = withContext(Dispatchers.IO) {
        WatchUpdate.consumeLastError(c)?.let { updateMsg = it }
        if (!WearApi.ready(c)) { snap = null; problem = Problem.NotSignedIn; loading = false; return@withContext }
        try {
            val hit = WearApi.snapshotWithAge(c)
            val s = WatchLogic.snap(hit.json, WearApi.person(c))
            if (s == null) { snap = null; problem = Problem.NoSide }
            else {
                snap = s; fetchedAt = SystemClock.elapsedRealtime() - hit.ageMs; problem = null
                if (!WatchLogic.canPreset(s.me)) preset = 0
                withContext(Dispatchers.Main) { checkUpdate() }
            }
        } catch (e: Exception) {
            // 401: the watch key was revoked or the phone signed out; opening the app on the phone hands over a new one
            if (e.message?.startsWith("HTTP 401") == true) { snap = null; problem = Problem.SignedOut }
            else problem = Problem.Unreachable
        }
        loading = false
    }

    private fun updatePrefs() = c.getSharedPreferences("embertide_update", Context.MODE_PRIVATE)

    // ---------- in-app updates (at most once every 6 hours) ----------
    fun checkUpdate() {
        if (checkingUpdate || !WearApi.ready(c)) return
        val now = System.currentTimeMillis()
        val last = updateCheckedAtMs ?: updatePrefs().getLong("checked_at", 0L).takeIf { it > 0L }?.also { updateCheckedAtMs = it }
        if (last != null && now - last in 0L until UPDATE_EVERY_MS) return
        checkingUpdate = true
        viewModelScope.launch {
            try {
                val (res, err) = withContext(Dispatchers.IO) {
                    WatchUpdate.checkStatus(c) to WatchUpdate.consumeLastError(c)
                }
                if (err != null) updateMsg = err
                val checkedAt = System.currentTimeMillis()
                updateCheckedAtMs = checkedAt
                updatePrefs().edit().putLong("checked_at", checkedAt).apply()
                when (res) {
                    is WatchUpdate.CheckResult.Available -> { update = res.offer; updateUpToDate = false }
                    is WatchUpdate.CheckResult.UpToDate -> { update = null; updateUpToDate = true }
                    is WatchUpdate.CheckResult.Unavailable -> { update = null; updateUpToDate = false }
                }
            } finally {
                checkingUpdate = false
            }
        }
    }

    fun startUpdate() {
        val offer = update ?: return
        if (updateBusy != null) return
        val runId = ++updateRunId
        updateMsg = null
        updateBusy = 0
        viewModelScope.launch {
            val res = withContext(Dispatchers.IO) {
                WatchUpdate.install(c, offer) { pct ->
                    viewModelScope.launch {
                        if (updateRunId == runId && updateBusy != null) updateBusy = pct
                    }
                }
            }
            updateBusy = null
            updateRunId++
            if (res is WatchUpdate.InstallResult.Failed) {
                updateMsg = res.message
            }
        }
    }

    companion object {
        private const val UPDATE_EVERY_MS = 6L * 3600_000L
    }

    private fun say(text: String, ok: Boolean) {
        toast = text to ok
        toastJob?.cancel()
        toastJob = viewModelScope.launch { delay(3500); toast = null }
        buzz(ok)
    }

    private fun buzz(ok: Boolean) {
        val vib = c.getSystemService(Vibrator::class.java) ?: return
        if (!vib.hasVibrator()) return
        vib.vibrate(if (ok) VibrationEffect.createPredefined(VibrationEffect.EFFECT_CLICK)
                    else VibrationEffect.createWaveform(longArrayOf(0, 60, 80, 60), -1))
    }

    /** Both the tile and the watch-face complication follow whatever just happened. */
    private fun refreshOthers() {
        val app = c.applicationContext
        TileService.getUpdater(app).requestUpdate(EmberTileService::class.java)
        ComplicationDataSourceUpdateRequester.create(app, ComponentName(app, EmberComplicationService::class.java)).requestUpdateAll()
    }

    private fun path(rest: String) = "/api/people/${WearApi.person(c)}/$rest"

    /** Run one change, then show the result: buzz + a few words, refresh the screen, the tile and the complication. */
    private fun act(okText: (() -> String)?, block: () -> String?) {
        if (busy) return
        busy = true
        viewModelScope.launch {
            lock.withLock {
                val r = withContext(Dispatchers.IO) { runCatching { block() } }
                if (r.isSuccess) say(r.getOrNull() ?: okText?.invoke() ?: "Done", true) else say("Couldn't reach it", false)
                fetch()
                withContext(Dispatchers.IO) { refreshOthers() }
            }
            busy = false
        }
    }

    // ---------- flashes ----------
    fun flash(kind: String) = act({ "${if (kind == "hot") "Hot" else "Cold"} flash started" }) {
        WearApi.call(c, "POST", path("flash"), "{\"kind\":\"$kind\",\"source\":\"watch\"}"); null
    }
    fun extendFlash() = act({ "5 more minutes" }) { WearApi.call(c, "POST", path("flash/extend?source=watch")); null }
    fun stopFlash() = act({ "Flash stopped" }) { WearApi.call(c, "POST", path("flash/stop?source=watch")); null }

    // ---------- power (same rules as the tile; see WatchLogic.powerAction) ----------
    fun power() {
        val me = snap?.me ?: return
        val plan = WatchLogic.powerAction(me)
        act({ WatchLogic.powerDoneText(plan) }) {
            when (plan) {
                WatchLogic.Power.TurnOffManual -> WearApi.call(c, "POST", path("adjust/clear?source=watch"))
                WatchLogic.Power.OffTonight -> WearApi.call(c, "POST", path("tonight"), "{\"action\":\"skip\",\"source\":\"watch\"}")
                WatchLogic.Power.Resume -> {
                    WearApi.call(c, "POST", path("tonight"), "{\"action\":\"resume\",\"source\":\"watch\"}")
                    val now = WatchLogic.snap(WearApi.snapshot(c), WearApi.person(c))?.me
                    if (now != null && now.targetC == null) turnOn(now)
                }
                WatchLogic.Power.TurnOnNow -> turnOn(me, preset)
            }
            null
        }
    }

    private fun turnOn(s: Side, steps: Int = 0) {
        val sn = snap
        val t = if (sn != null) WatchLogic.startTemp(s, sn, steps) else WatchLogic.turnOnTemp(s)
        WearApi.call(c, "POST", path("adjust"), "{\"temp_c\":${(t * 100).roundToInt() / 100.0},\"until\":\"next_phase\",\"source\":\"watch\"}")
    }

    // ---------- temperature: taps and crown turns are gathered for a moment, then sent once ----------
    fun nudge(steps: Int) {
        val s = snap ?: return
        if (steps == 0 || problem == Problem.NotSignedIn) return
        val dir = if (steps > 0) 1 else -1
        if (WatchLogic.canPreset(s.me)) {                 // off: choose the starting temperature, send nothing
            if (WatchLogic.canPresetStep(s.me, s, preset, dir)) preset += dir
            return
        }
        if (!WatchLogic.canStep(s.me, s, pending, dir)) return
        pending += dir
        if (!sending) {
            nudgeJob?.cancel()
            nudgeJob = viewModelScope.launch { delay(600); flushNudge() }
        }
    }

    private suspend fun flushNudge() {
        sending = true
        val steps = pending
        val s = snap
        val plan = if (s == null) null else WatchLogic.adjustPlan(s.me, s, steps)
        if (plan == null) { pending -= steps; sending = false; return }
        lock.withLock {
            val r = withContext(Dispatchers.IO) { runCatching { WearApi.call(c, "POST", path("adjust"), WatchLogic.adjustBody(plan)) } }
            if (r.isSuccess) say("Set", true) else say("Couldn't reach it", false)
            fetch()
            withContext(Dispatchers.IO) { refreshOthers() }
        }
        pending -= steps
        sending = false
        if (pending != 0) nudgeJob = viewModelScope.launch { delay(300); flushNudge() }
    }
}
