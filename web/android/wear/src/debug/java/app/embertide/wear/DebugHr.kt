package app.embertide.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/** Debug builds only: act as if the phone said "collect now" (emulators can't pair over the Wear link).
 *  adb shell am broadcast -n app.embertide/.wear.DebugHr --ez on true --ez wanted true   (or --ez flush true) */
class DebugHr : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val pending = goAsync()
        Thread {
            try {
                if (i.getBooleanExtra("flush", false)) WatchHr.flush(c.applicationContext, force = true)
                else runBlocking { withTimeoutOrNull(9_000) { WatchHr.control(c.applicationContext, i.getBooleanExtra("on", true), i.getBooleanExtra("wanted", true)) } }
                val p = c.getSharedPreferences("embertide_hr", Context.MODE_PRIVATE)
                android.util.Log.i("EmberWatchHr", "debug: active=${WatchHr.active(c)} perm=${WatchHr.permState(c)} buf=${(p.getString("buf", "") ?: "").length} chars")
            } finally { pending.finish() }
        }.start()
    }
}
