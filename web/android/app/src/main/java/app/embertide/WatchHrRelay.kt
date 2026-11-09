package app.embertide

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONArray
import org.json.JSONObject

/**
 * "Watch heart rate (live)" on the phone side. The watch (any Wear OS watch, e.g. a Galaxy Watch) collects heart rate
 * passively at night and sends a batch over Bluetooth every ~5 minutes (path /embertide/hr); this relays it to the
 * server's POST /api/people/{pid}/watch-hr. No timers or wake locks here: Play services wakes this service only when
 * a batch arrives. Readings that couldn't be sent wait (up to two hours) and go with the next batch.
 * The watch is told what to do through a data item (/embertide/watchhr): the switch, and whether the person's night is
 * on (the server's answer, refreshed by every phone report and every relayed batch).
 */
object WatchHrRelay {
    private const val TAG = "EmberWatchHr"
    const val MSG_PATH = "/embertide/hr"
    const val CTRL_PATH = "/embertide/watchhr"
    private fun prefs(c: Context) = c.getSharedPreferences("ember_watch_hr", Context.MODE_PRIVATE)

    fun on(c: Context) = "watch_hr" in PhoneSources.enabled(c)

    /** Tell the watch: collect or not. Sent only when it changes (a data item syncs once, whenever the watch is near). */
    fun control(c: Context, wanted: Boolean? = null) {
        val p = prefs(c)
        val w = wanted ?: p.getBoolean("wanted", false)
        val on = on(c)
        p.edit().putBoolean("wanted", w).apply()
        val key = "$on/$w"
        // refresh at least every 6 hours so the watch's 14-hour safety stop never trips while things are fine
        if (p.getString("ctrl_sent", "") == key && System.currentTimeMillis() - p.getLong("ctrl_at", 0) < 6 * 3600_000L) return
        try {
            val req = PutDataMapRequest.create(CTRL_PATH).apply {
                dataMap.putBoolean("on", on)
                dataMap.putBoolean("wanted", on && w)
                dataMap.putLong("ts", System.currentTimeMillis())
            }.asPutDataRequest().setUrgent()
            Wearable.getDataClient(c).putDataItem(req)
            p.edit().putString("ctrl_sent", key).putLong("ctrl_at", System.currentTimeMillis()).apply()
        } catch (_: Exception) { /* no Play services / no watch */ }
    }

    /** A batch from the watch: post it (blocking; called on the listener's background thread). */
    fun relay(c: Context, body: JSONObject) {
        val p = prefs(c)
        body.optString("perm").takeIf { it.isNotEmpty() }?.let { p.edit().putString("perm", it).putLong("perm_at", System.currentTimeMillis()).apply() }
        if (!on(c) || !Ember.ready(c)) return
        // add whatever didn't get through last time
        val pending = runCatching { JSONObject(p.getString("pending", "") ?: "") }.getOrNull()
        val samples = merge(pending?.optJSONArray("samples"), body.optJSONArray("samples"))
        val steps = merge(pending?.optJSONArray("steps"), body.optJSONArray("steps"))
        if (samples.length() == 0 && steps.length() == 0) return
        val out = JSONObject().put("samples", samples).put("steps", steps).put("device", "watch")
        val res = runCatching { Ember.call(c, "POST", "/api/people/" + Ember.person(c) + "/watch-hr", out.toString()) }
        if (res.isSuccess) {
            p.edit().putString("pending", "").putLong("last_sent", System.currentTimeMillis())
                .putInt("last_count", samples.length()).apply()
            val wanted = runCatching { JSONObject(res.getOrThrow()).optBoolean("wanted", true) }.getOrDefault(true)
            control(c, wanted)
        } else {
            p.edit().putString("pending", out.toString()).putString("last_error", res.exceptionOrNull()?.message?.take(120)).apply()
            Log.d(TAG, "couldn't send watch heart rate: ${res.exceptionOrNull()?.message}")
        }
    }

    private fun merge(a: JSONArray?, b: JSONArray?): JSONArray {
        val cut = System.currentTimeMillis() / 1000.0 - 2 * 3600
        val seen = HashSet<Long>()
        val out = JSONArray()
        for (arr in listOf(a, b)) {
            if (arr == null) continue
            for (i in 0 until arr.length()) {
                val x = arr.optJSONArray(i) ?: continue
                val t = x.optDouble(0)
                if (t >= cut && seen.add(Math.round(t))) out.put(x)
            }
        }
        // the server takes at most 600 readings per post: keep the newest
        if (out.length() <= 600) return out
        val trimmed = JSONArray()
        for (i in out.length() - 600 until out.length()) trimmed.put(out.get(i))
        return trimmed
    }

    fun status(c: Context): JSONObject {
        val p = prefs(c)
        return JSONObject().put("perm", p.getString("perm", "unknown")).put("last_sent", p.getLong("last_sent", 0))
            .put("last_count", p.getInt("last_count", 0)).put("wanted", p.getBoolean("wanted", false))
    }
}

/** Play services wakes this when the watch sends a batch. */
class WatchHrListener : WearableListenerService() {
    override fun onMessageReceived(e: MessageEvent) {
        if (e.path != WatchHrRelay.MSG_PATH) return
        val body = runCatching { JSONObject(String(e.data)) }.getOrNull() ?: return
        WatchHrRelay.relay(applicationContext, body)
    }
}
