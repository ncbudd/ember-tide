package app.embertide

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/** Debug builds only: pretend the watch sent a batch (the emulator pair can't talk over the Wear link).
 *  adb shell am broadcast -n app.embertide/.FakeWatchHr --ei bpm 62 --ei n 10
 *  → n readings one per 30 s ending now at about `bpm`, relayed like a real batch. */
class FakeWatchHr : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val bpm = i.getIntExtra("bpm", 62)
        val n = i.getIntExtra("n", 10)
        val now = System.currentTimeMillis() / 1000.0
        val s = JSONArray()
        for (k in 0 until n) s.put(JSONArray().put(now - (n - 1 - k) * 30).put(bpm + (k % 3) - 1))
        val body = JSONObject().put("samples", s).put("steps", JSONArray()).put("perm", "granted")
        val pending = goAsync()
        Thread { try { WatchHrRelay.relay(c.applicationContext, body) } finally { pending.finish() } }.start()
    }
}
