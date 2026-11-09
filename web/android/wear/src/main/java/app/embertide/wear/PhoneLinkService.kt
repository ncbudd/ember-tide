package app.embertide.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.wear.tiles.TileService
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/** The phone app signs the watch in (and out) over Wear's encrypted phone-to-watch link, and switches night heart
 *  rate on and off (/embertide/watchhr: the phone's switch + whether the person's night is on). */
class PhoneLinkService : WearableListenerService() {
    override fun onDataChanged(events: DataEventBuffer) {
        for (e in events) {
            when (e.dataItem.uri.path) {
                "/embertide/auth" -> {
                    if (e.type == DataEvent.TYPE_DELETED) {
                        WearApi.save(this, "", "", "")
                    } else {
                        val m = DataMapItem.fromDataItem(e.dataItem).dataMap
                        WearApi.save(this, m.getString("server", ""), m.getString("token", ""), m.getString("person", ""))
                    }
                    TileService.getUpdater(this).requestUpdate(EmberTileService::class.java)
                }
                WatchHr.CTRL_PATH -> {
                    val m = if (e.type == DataEvent.TYPE_DELETED) null else DataMapItem.fromDataItem(e.dataItem).dataMap
                    val on = m?.getBoolean("on", false) ?: false
                    val wanted = m?.getBoolean("wanted", false) ?: false
                    runBlocking { withTimeoutOrNull(15_000) { WatchHr.control(this@PhoneLinkService, on, wanted) } }
                    if (on) Thread { runCatching { WatchHr.reportPermission(applicationContext) } }.start()
                }
            }
        }
    }
}

/** Passive registrations don't survive a reboot: put it back if the night was on. */
class WatchBootReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action != Intent.ACTION_BOOT_COMPLETED) return
        val pending = goAsync()
        Thread {
            try { runBlocking { withTimeoutOrNull(8_000) { WatchHr.apply(c.applicationContext) } } } finally { pending.finish() }
        }.start()
    }
}
