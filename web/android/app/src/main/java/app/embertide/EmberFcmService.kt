package app.embertide

import android.util.Base64
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject

/** Google push: messages are Web Push-encrypted for this phone; anything we can't decrypt is dropped. */
class EmberFcmService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        Ember.prefs(this).edit().putString("fcm_token", token).putBoolean("push_registered", false).apply()
        if (Ember.prefs(this).getString("push_method", null) == "fcm") Thread { EmberPush.syncFcm(applicationContext) }.start()
    }

    override fun onMessageReceived(msg: RemoteMessage) {
        val e = msg.data["e"] ?: return
        try {
            val plain = EmberCrypto.decrypt(this, Base64.decode(e, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP))
            val m = JSONObject(String(plain, Charsets.UTF_8))
            if (m.optString("t") == "et") EmberPush.show(this, m)
        } catch (_: Exception) { /* not for us, or tampered with */ }
    }
}
