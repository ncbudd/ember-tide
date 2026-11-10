package app.embertide

import android.content.Context
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import java.util.concurrent.TimeUnit

/** Hands the watch tile the server address and its own limited watch key (never the phone's session) over Wear's
 *  encrypted phone-to-watch link, and takes it back. */
object EmberWear {
    private const val PATH = "/embertide/auth"
    private val ALL = android.net.Uri.parse("wear://*$PATH")

    /** After sign-in and on every app start; blocking, off the main thread. Makes a watch key when a watch is connected
     *  (or one was signed in before) and hands it over when the watch doesn't have it yet. That also replaces the
     *  session that app versions before device keys handed to the watch. A key revoked while no watch is connected
     *  isn't made again: the watch is signed out instead. */
    @JvmStatic fun sync(c: Context) {
        try {
            if (!Ember.ready(c)) return
            val nodes = Tasks.await(Wearable.getNodeClient(c).connectedNodes, 10, TimeUnit.SECONDS)
            val handed = handedOver(c)                 // what the watch was given last (null: nothing)
            if (nodes.isEmpty() && handed == null) return           // no watch: no key
            var key = Ember.watchToken(c)
            if (key.isEmpty()) {
                if (nodes.isEmpty()) { signOut(c); return }         // revoked (a lost watch?): don't hand out a new one
                key = try { Ember.mint(c, "watch") } catch (e: Ember.HttpError) {
                    if (e.code == 401) signOut(c)                   // signed out: so is the watch
                    return
                }
            }
            if (handed != key) put(c, Ember.server(c), key, Ember.person(c))
        } catch (_: Exception) { /* no Play services / no watch / offline: try again next start */ }
    }

    private fun handedOver(c: Context): String? {
        val buf = Tasks.await(Wearable.getDataClient(c).getDataItems(ALL), 10, TimeUnit.SECONDS)
        try {
            for (item in buf) return DataMapItem.fromDataItem(item).dataMap.getString("token", "")
            return null
        } finally { buf.release() }
    }

    private fun put(c: Context, server: String, token: String, person: String) {
        val req = PutDataMapRequest.create(PATH).apply {
            dataMap.putString("server", server)
            dataMap.putString("token", token)
            dataMap.putString("person", person)
            dataMap.putLong("ts", System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        Tasks.await(Wearable.getDataClient(c).putDataItem(req), 10, TimeUnit.SECONDS)
    }

    @JvmStatic fun signOut(c: Context) {
        try { Wearable.getDataClient(c).deleteDataItems(ALL) } catch (_: Exception) {}
    }
}
