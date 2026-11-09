package app.embertide.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.wear.tiles.TileService

/** Debug only: what the phone normally hands over (server, token, person), set from adb. */
class DebugAuth : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        WearApi.save(c, i.getStringExtra("server") ?: "", i.getStringExtra("token") ?: "", i.getStringExtra("person") ?: "")
        TileService.getUpdater(c.applicationContext).requestUpdate(EmberTileService::class.java)   // a receiver context may not bind
        android.util.Log.i("EmberTide", "watch signed in from adb: ${WearApi.ready(c)}")
    }
}
