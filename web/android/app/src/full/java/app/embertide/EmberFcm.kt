package app.embertide

import android.content.Context
import android.os.Build
import org.json.JSONObject

/** Google push (Firebase Cloud Messaging). This is the "full" build; the "minimal" build has a stub with the same entry points. */
object EmberFcm {
    /** Whether this build contains Google push at all. */
    const val IN_BUILD = true

    /** Works only when the app was built with the Firebase config and the phone has Play services. */
    fun available(c: Context): Boolean = try {
        com.google.firebase.FirebaseApp.getApps(c).isNotEmpty() || com.google.firebase.FirebaseApp.initializeApp(c) != null
    } catch (_: Throwable) { false }

    /** Register this phone with our server for Google push (blocking: call off the main thread). */
    fun sync(c: Context): String? {
        if (!Ember.ready(c)) return "not signed in"
        val p = Ember.prefs(c)
        return try {
            val token = com.google.android.gms.tasks.Tasks.await(com.google.firebase.messaging.FirebaseMessaging.getInstance().token)
            val k = EmberCrypto.keys(c)
            val body = JSONObject().put("kind", "fcm").put("endpoint", token).put("p256dh", k.publicB64).put("auth", k.authB64)
                .put("device", "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(60))
            Ember.call(c, "POST", "/api/notify/me/push", body.toString())
            p.edit().putString("push_method", "fcm").putString("fcm_token", token).putBoolean("push_registered", true).remove("push_error").apply()
            null
        } catch (e: Exception) {
            val msg = EmberPush.errorText(e.message) ?: "couldn't register"
            p.edit().putString("push_method", "fcm").putBoolean("push_registered", false).putString("push_error", msg).apply()
            msg
        }
    }

    /** Forget this phone's Google push token (best effort). */
    fun deleteToken() {
        try { com.google.firebase.messaging.FirebaseMessaging.getInstance().deleteToken() } catch (_: Throwable) {}
    }
}
