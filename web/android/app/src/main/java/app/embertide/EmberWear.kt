package app.embertide

import android.content.Context
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable

/** Hands the watch tile the server address and sign-in over Wear's encrypted phone-to-watch link (and takes it back). */
object EmberWear {
    private const val PATH = "/embertide/auth"

    @JvmStatic fun signIn(c: Context, server: String, token: String, person: String) {
        try {
            val req = PutDataMapRequest.create(PATH).apply {
                dataMap.putString("server", server)
                dataMap.putString("token", token)
                dataMap.putString("person", person)
                dataMap.putLong("ts", System.currentTimeMillis())
            }.asPutDataRequest().setUrgent()
            Wearable.getDataClient(c).putDataItem(req)
        } catch (_: Exception) { /* no Play services / no watch: nothing to do */ }
    }

    @JvmStatic fun signOut(c: Context) {
        try { Wearable.getDataClient(c).deleteDataItems(android.net.Uri.parse("wear://*$PATH")) } catch (_: Exception) {}
    }
}
