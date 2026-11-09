package app.embertide.wear

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.health.services.client.HealthServices
import androidx.health.services.client.PassiveListenerService
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.PassiveListenerConfig
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.guava.await
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Live heart rate for "is it me in bed?" (server: engine/watch.py). Battery first:
 * - Health Services PASSIVE monitoring only: the watch's own sensor hub keeps measuring heart rate as it always does
 *   (on a Galaxy Watch at the cadence set in Samsung Health → Heart rate) and hands it over in batches. No exercise
 *   session, no sensor listener, no wake locks.
 * - Registered only while the phone says the person's night is on (the server decides; the phone relays "wanted"),
 *   and dropped again in the morning, when the switch goes off, or if the phone hasn't confirmed for 14 hours.
 * - Batches go to the phone over Wear's Bluetooth link (MessageClient) at most every ~5 minutes; the phone posts them.
 */
object WatchHr {
    private const val TAG = "EmberWatchHr"
    const val MSG_PATH = "/embertide/hr"            // watch → phone: {samples: [[unix s, bpm]], steps: [[unix s, n]], perm}
    const val CTRL_PATH = "/embertide/watchhr"      // phone → watch (data item): on, wanted, ts
    private const val SEND_EVERY_MS = 5 * 60_000L
    private const val WANTED_TTL_MS = 14 * 3600_000L
    private const val KEEP_S = 2 * 3600

    private fun prefs(c: Context) = c.getSharedPreferences("embertide_hr", Context.MODE_PRIVATE)

    fun hrPermission(): String = if (Build.VERSION.SDK_INT >= 36) "android.permission.health.READ_HEART_RATE" else Manifest.permission.BODY_SENSORS
    fun bgPermission(): String? = when {
        Build.VERSION.SDK_INT >= 36 -> "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"
        Build.VERSION.SDK_INT >= 33 -> "android.permission.BODY_SENSORS_BACKGROUND"
        else -> null
    }
    private fun has(c: Context, p: String) = ContextCompat.checkSelfPermission(c, p) == PackageManager.PERMISSION_GRANTED
    fun permitted(c: Context): Boolean = has(c, hrPermission()) && (bgPermission()?.let { has(c, it) } ?: true)
    fun stepsPermitted(c: Context): Boolean = has(c, Manifest.permission.ACTIVITY_RECOGNITION)
    fun permState(c: Context): String = when {
        permitted(c) -> "granted"
        has(c, hrPermission()) -> "needs_background"
        else -> "needed"
    }

    fun switchOn(c: Context) = prefs(c).getBoolean("on", false)
    fun active(c: Context) = prefs(c).getBoolean("active", false)

    /** The phone's latest word (switch on/off, night wanted or not). Starts or stops passive monitoring to match. */
    suspend fun control(c: Context, on: Boolean, wanted: Boolean) {
        prefs(c).edit().putBoolean("on", on).putBoolean("wanted", wanted).putLong("wanted_at", System.currentTimeMillis()).apply()
        apply(c)
    }

    suspend fun apply(c: Context) {
        val p = prefs(c)
        val fresh = System.currentTimeMillis() - p.getLong("wanted_at", 0) < WANTED_TTL_MS
        val want = p.getBoolean("on", false) && p.getBoolean("wanted", false) && fresh && permitted(c)
        val client = HealthServices.getClient(c).passiveMonitoringClient
        try {
            if (want) {
                val types = mutableSetOf<DataType<*, *>>(DataType.HEART_RATE_BPM)
                if (stepsPermitted(c)) types += DataType.STEPS
                val caps = client.getCapabilitiesAsync().await()
                types.retainAll(caps.supportedDataTypesPassiveMonitoring)
                if (DataType.HEART_RATE_BPM !in types) { Log.w(TAG, "no passive heart rate on this watch"); return }
                client.setPassiveListenerServiceAsync(HrPassiveService::class.java,
                    PassiveListenerConfig.builder().setDataTypes(types).build()).await()
                p.edit().putBoolean("active", true).apply()
                Log.i(TAG, "passive heart rate on (${types.size} types)")
            } else if (p.getBoolean("active", false)) {
                client.clearPassiveListenerServiceAsync().await()
                p.edit().putBoolean("active", false).apply()
                flush(c, force = true)
                Log.i(TAG, "passive heart rate off")
            }
        } catch (e: Exception) {
            Log.w(TAG, "passive monitoring: ${e.message}")
        }
    }

    /** A batch from Health Services: keep the readings, send to the phone if the last send was 5+ minutes ago. */
    private val lock = Any()                         // the buffer is read and rewritten from two threads

    fun onBatch(c: Context, data: DataPointContainer) {
        synchronized(lock) { addBatch(c, data) }
        val app = c.applicationContext
        Thread { runCatching { flush(app, force = false) } }.start()     // never block the binder / main thread
    }

    private fun addBatch(c: Context, data: DataPointContainer) {
        val boot = System.currentTimeMillis() - SystemClock.elapsedRealtime()
        val buf = buffer(c)
        val out = buf.getJSONArray("samples")
        var last = if (out.length() > 0) out.getJSONArray(out.length() - 1).getDouble(0) else 0.0
        for (dp in data.getData(DataType.HEART_RATE_BPM).sortedBy { it.timeDurationFromBoot }) {
            val bpm = dp.value
            if (bpm < 25 || bpm > 230) continue
            val t = dp.getTimeInstant(java.time.Instant.ofEpochMilli(boot)).toEpochMilli() / 1000.0
            if (t - last < 30) continue        // one reading per 30 s is plenty (some watches report every second while moving)
            out.put(JSONArray().put(t).put(Math.round(bpm * 10) / 10.0))
            last = t
        }
        for (dp in data.getData(DataType.STEPS)) {
            val t = dp.getEndInstant(java.time.Instant.ofEpochMilli(boot)).toEpochMilli() / 1000.0
            if (dp.value <= 0) continue
            val st = buf.getJSONArray("steps")
            val prev = if (st.length() > 0) st.getJSONArray(st.length() - 1) else null
            // steps add up into one entry per minute
            if (prev != null && t - prev.getDouble(0) < 60) prev.put(1, prev.getLong(1) + dp.value)
            else st.put(JSONArray().put(t).put(dp.value))
        }
        save(c, buf)
    }

    private fun buffer(c: Context): JSONObject {
        val o = runCatching { JSONObject(prefs(c).getString("buf", "") ?: "") }.getOrNull() ?: JSONObject()
        if (!o.has("samples")) o.put("samples", JSONArray())
        if (!o.has("steps")) o.put("steps", JSONArray())
        // keep only the last two hours (the phone or server may be unreachable for a while)
        val cut = System.currentTimeMillis() / 1000.0 - KEEP_S
        for (k in listOf("samples", "steps")) {
            val a = o.getJSONArray(k)
            val kept = JSONArray()
            for (i in 0 until a.length()) if (a.getJSONArray(i).getDouble(0) >= cut) kept.put(a.getJSONArray(i))
            o.put(k, kept)
        }
        return o
    }

    private fun save(c: Context, o: JSONObject) = prefs(c).edit().putString("buf", o.toString()).apply()

    /** Hand the buffered readings to the phone (blocking; called from the passive service's worker thread). */
    fun flush(c: Context, force: Boolean) {
        val p = prefs(c)
        if (!force && System.currentTimeMillis() - p.getLong("sent_at", 0) < SEND_EVERY_MS) return
        val buf = synchronized(lock) { buffer(c) }
        if (buf.getJSONArray("samples").length() == 0 && buf.getJSONArray("steps").length() == 0 && !force) return
        val sentCount = buf.getJSONArray("samples").length() to buf.getJSONArray("steps").length()
        buf.put("perm", permState(c))
        val bytes = buf.toString().toByteArray()
        val ok = runCatching {
            val nodes = Tasks.await(Wearable.getNodeClient(c).connectedNodes, 5, TimeUnit.SECONDS)
            var sent = false
            for (n in nodes) {
                Tasks.await(Wearable.getMessageClient(c).sendMessage(n.id, MSG_PATH, bytes), 5, TimeUnit.SECONDS)
                sent = true
            }
            sent
        }.getOrDefault(false)
        if (ok) {
            // drop only what went out: readings that arrived during the send stay for the next one
            synchronized(lock) {
                val now = buffer(c)
                for ((k, n) in listOf("samples" to sentCount.first, "steps" to sentCount.second)) {
                    val sent = buf.getJSONArray(k)
                    val upTo = if (n > 0) sent.getJSONArray(n - 1).getDouble(0) else Double.NEGATIVE_INFINITY
                    val a = now.getJSONArray(k); val kept = JSONArray()
                    for (i in 0 until a.length()) if (a.getJSONArray(i).getDouble(0) > upTo) kept.put(a.getJSONArray(i))
                    now.put(k, kept)
                }
                save(c, now)
            }
            p.edit().putLong("sent_at", System.currentTimeMillis()).apply()
            Log.d(TAG, "sent ${buf.getJSONArray("samples").length()} readings to the phone")
        }
        // a phone that hasn't confirmed the night for a long time (switched off, uninstalled): stop on our own
        if (System.currentTimeMillis() - p.getLong("wanted_at", 0) > WANTED_TTL_MS && active(c)) {
            kotlinx.coroutines.runBlocking { apply(c) }
        }
    }

    /** Tell the phone what the watch can do (permission state), e.g. right after "Allow" on the watch. */
    fun reportPermission(c: Context) = flush(c, force = true)
}

/** Health Services hands passive heart rate here in batches (it binds with PASSIVE_DATA_BINDING; no one else can). */
class HrPassiveService : PassiveListenerService() {
    override fun onNewDataPointsReceived(dataPoints: DataPointContainer) {
        WatchHr.onBatch(this, dataPoints)
    }
}
