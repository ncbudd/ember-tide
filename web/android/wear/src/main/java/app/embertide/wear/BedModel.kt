package app.embertide.wear

import android.app.Application
import android.content.ComponentName
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

    private var nudgeJob: Job? = null
    private var sending = false
    private var toastJob: Job? = null

    fun refresh() {
        viewModelScope.launch { lock.withLock { fetch() } }
    }

    private suspend fun fetch() = withContext(Dispatchers.IO) {
        if (!WearApi.ready(c)) { snap = null; problem = Problem.NotSignedIn; loading = false; return@withContext }
        try {
            val s = WatchLogic.snap(JSONObject(WearApi.call(c, "GET", "/api/snapshot")), WearApi.person(c))
            if (s == null) { snap = null; problem = Problem.NoSide }
            else { snap = s; fetchedAt = SystemClock.elapsedRealtime(); problem = null }
        } catch (e: Exception) {
            // 401: the watch key was revoked or the phone signed out; opening the app on the phone hands over a new one
            if (e.message?.startsWith("HTTP 401") == true) { snap = null; problem = Problem.SignedOut }
            else problem = Problem.Unreachable
        }
        loading = false
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
                    val now = WatchLogic.snap(JSONObject(WearApi.call(c, "GET", "/api/snapshot")), WearApi.person(c))?.me
                    if (now != null && now.targetC == null) turnOn(now)
                }
                WatchLogic.Power.TurnOnNow -> turnOn(me)
            }
            null
        }
    }

    private fun turnOn(s: Side) {
        WearApi.call(c, "POST", path("adjust"), "{\"temp_c\":${WatchLogic.turnOnTemp(s)},\"until\":\"next_phase\",\"source\":\"watch\"}")
    }

    // ---------- temperature: taps and crown turns are gathered for a moment, then sent once ----------
    fun nudge(steps: Int) {
        val s = snap ?: return
        if (steps == 0 || problem == Problem.NotSignedIn) return
        val dir = if (steps > 0) 1 else -1
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
