package app.embertide

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONObject
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.UnifiedPush
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/**
 * App notifications over UnifiedPush: a distributor app (ntfy) keeps the one connection open; our server posts
 * Web Push-encrypted messages to the address it gives us, and we show them as our own notifications with buttons
 * that act straight away with the app's sign-in.
 */
object EmberPush {
    const val CHANNEL = "alerts"
    private const val P_ENDPOINT = "push_endpoint"
    private const val P_KEY = "push_p256dh"
    private const val P_AUTH = "push_auth"
    private const val P_REG = "push_registered"      // the server has this endpoint
    private const val P_ERR = "push_error"
    private val ALLOWED = setOf("pause", "skip", "start_now", "resume", "manual_off", "feel_cold", "feel_right", "feel_warm",
        "pet_yes", "pet_me", "pet_unsure", "away_away", "away_night", "away_home", "away_back", "away_still",
        "care_done", "care_later")
    /** Buttons about one night (the server's NIGHT_ACTS): the notification must go when that night ends. */
    private val NIGHT_BOUND = setOf("pause", "skip", "start_now")

    fun distributors(c: Context): List<String> = UnifiedPush.getDistributors(c)
    fun saved(c: Context): String? = UnifiedPush.getAckDistributor(c) ?: UnifiedPush.getSavedDistributor(c)

    /** Google push works only when the app was built with the Firebase config and the phone has Play services. */
    fun fcmAvailable(c: Context): Boolean = try {
        com.google.firebase.FirebaseApp.getApps(c).isNotEmpty() || com.google.firebase.FirebaseApp.initializeApp(c) != null
    } catch (_: Exception) { false }

    fun method(c: Context): String? = Ember.prefs(c).getString("push_method", null)

    /** Register this phone with our server for Google push (blocking: call off the main thread). */
    fun syncFcm(c: Context): String? {
        if (!Ember.ready(c)) return "not signed in"
        val p = Ember.prefs(c)
        return try {
            val token = com.google.android.gms.tasks.Tasks.await(com.google.firebase.messaging.FirebaseMessaging.getInstance().token)
            val k = EmberCrypto.keys(c)
            val body = JSONObject().put("kind", "fcm").put("endpoint", token).put("p256dh", k.publicB64).put("auth", k.authB64)
                .put("device", "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(60))
            Ember.call(c, "POST", "/api/notify/me/push", body.toString())
            p.edit().putString("push_method", "fcm").putString("fcm_token", token).putBoolean(P_REG, true).remove(P_ERR).apply()
            null
        } catch (e: Exception) {
            val msg = errorText(e.message) ?: "couldn't register"
            p.edit().putString("push_method", "fcm").putBoolean(P_REG, false).putString(P_ERR, msg).apply()
            msg
        }
    }

    /** The server's reason from an "HTTP 4xx {json}" error: FastAPI's detail is a string, or a list of {msg} items. */
    internal fun errorText(raw: String?): String? {
        val body = raw?.substringAfter('{', "")?.takeIf { it.isNotEmpty() } ?: return null
        return try {
            when (val d = JSONObject("{$body").opt("detail")) {
                is String -> d
                is org.json.JSONArray -> (0 until d.length())
                    .mapNotNull { d.optJSONObject(it)?.optString("msg")?.takeIf { m -> m.isNotEmpty() } }.joinToString("; ")
                else -> null
            }?.take(160)?.ifEmpty { null }
        } catch (_: Exception) { null }
    }

    fun status(c: Context): JSONObject {
        val p = Ember.prefs(c)
        return JSONObject()
            .put("fcm_available", fcmAvailable(c))
            .put("allowed", c.getSystemService(NotificationManager::class.java).areNotificationsEnabled())
            .put("method", method(c) ?: JSONObject.NULL)
            .put("distributors", org.json.JSONArray(distributors(c)))
            .put("distributor", saved(c) ?: JSONObject.NULL)
            .put("endpoint", p.getString(P_ENDPOINT, null) != null)
            .put("registered", p.getBoolean(P_REG, false))
            .put("error", p.getString(P_ERR, null) ?: JSONObject.NULL)
    }

    fun register(c: Context) {
        Ember.prefs(c).edit().remove(P_ERR).putString("push_method", "up").apply()
        UnifiedPush.register(c, "default", "Ember & Tide", null)
    }

    /** Stop app notifications on this phone: tell our server first (needs the session), then the distributor. */
    fun disable(c: Context) {
        val p = Ember.prefs(c)
        if (method(c) == "fcm") {
            val tok = p.getString("fcm_token", null)
            if (tok != null && Ember.ready(c)) {
                try { Ember.call(c, "POST", "/api/notify/me/push/remove", JSONObject().put("endpoint", tok).toString()) } catch (_: Exception) {}
            }
            try { com.google.firebase.messaging.FirebaseMessaging.getInstance().deleteToken() } catch (_: Exception) {}
            p.edit().remove("fcm_token").remove(P_REG).remove("push_method").remove(P_ERR).apply()
            return
        }
        val ep = p.getString(P_ENDPOINT, null)
        if (ep != null && Ember.ready(c)) {
            try { Ember.call(c, "POST", "/api/notify/me/push/remove", JSONObject().put("endpoint", ep).toString()) } catch (_: Exception) {}
        }
        try { UnifiedPush.unregister(c, "default") } catch (_: Exception) {}
        p.edit().remove(P_ENDPOINT).remove(P_KEY).remove(P_AUTH).remove(P_REG).remove("push_method").apply()
    }

    /** Send our endpoint to the server (after sign-in, or when the distributor gives us a new one). */
    fun sync(c: Context): String? {
        val p = Ember.prefs(c)
        val ep = p.getString(P_ENDPOINT, null) ?: return "no endpoint yet"
        if (!Ember.ready(c)) return "not signed in"
        return try {
            val body = JSONObject().put("endpoint", ep).put("p256dh", p.getString(P_KEY, "")).put("auth", p.getString(P_AUTH, ""))
                .put("device", "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(60))
            Ember.call(c, "POST", "/api/notify/me/push", body.toString())
            p.edit().putBoolean(P_REG, true).remove(P_ERR).apply()
            null
        } catch (e: Exception) {
            val msg = errorText(e.message) ?: "couldn't register"
            p.edit().putBoolean(P_REG, false).putString(P_ERR, msg).apply()
            msg
        }
    }

    internal fun saveEndpoint(c: Context, e: PushEndpoint) {
        val keys = e.pubKeySet
        Ember.prefs(c).edit().putString(P_ENDPOINT, e.url).putString(P_KEY, keys?.pubKey ?: "").putString(P_AUTH, keys?.auth ?: "")
            .putBoolean(P_REG, false).apply()
    }

    internal fun failed(c: Context, why: String) { Ember.prefs(c).edit().putString(P_ERR, why).putBoolean(P_REG, false).apply() }
    internal fun forget(c: Context) { Ember.prefs(c).edit().remove(P_ENDPOINT).remove(P_KEY).remove(P_AUTH).remove(P_REG).apply() }

    fun channel(c: Context) {
        val ch = NotificationChannel(CHANNEL, "Alerts", NotificationManager.IMPORTANCE_HIGH)
        ch.description = "Your side left on, pre-condition while you're away, the morning summary, bed problems"
        c.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    /** One notification per kind: a newer one replaces it, and clear(kind) can take it away. */
    private val KIND_IDS = listOf("action_done", "away_check", "away_precool", "digest", "empty_side", "left_on", "morning",
        "pet_question", "problem", "test", "maintenance")
    fun idFor(kind: String): Int {
        val i = KIND_IDS.indexOf(kind)
        return if (i >= 0) 5000 + i else 5100 + (kind.hashCode() and 0x3ff)   // known kinds never collide; others hash above them
    }

    /** Show one message from the server. Only messages encrypted for this phone are accepted. */
    internal fun show(c: Context, m: JSONObject) {
        channel(c)
        val id = idFor(m.optString("kind"))
        // a tap opens the page that fixes it (the server's `link`), if it is a plain in-app path; otherwise just the app.
        // The request code is this notification's own id, so one message's link never overwrites another's.
        val tap = Intent(c, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        EmberLink.clean(m.optString("link"))?.let { tap.putExtra(MainActivity.EXTRA_LINK, it) }
        val open = PendingIntent.getActivity(c, id, tap, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = Notification.Builder(c, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_flash)
            .setContentTitle(m.optString("title").take(120))
            .setContentText(m.optString("body").take(300))
            .setStyle(Notification.BigTextStyle().bigText(m.optString("body").take(600)))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setColor(0xFF5FB8B0.toInt())
        val acts = m.optJSONArray("actions")
        val person = m.optString("person").ifEmpty { Ember.person(c) }
        var nightBound = false
        if (acts != null) for (i in 0 until minOf(3, acts.length())) {
            val a = acts.getJSONObject(i)
            val act = a.optString("act")
            if (act !in ALLOWED) continue                     // only things the app knows how to do
            if (act in NIGHT_BOUND) nightBound = true
            val intent = Intent(c, FlashNotifier.Receiver::class.java).setAction("app.embertide.NOTIF_ACT")
                .putExtra("act", act).putExtra("minutes", a.optInt("minutes", 0)).putExtra("label", a.optString("label").take(40))
                .putExtra("person", person).putExtra("nid", id).putExtra("ref", a.optString("ref").take(40))
            val pi = PendingIntent.getBroadcast(c, id * 4 + i, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            b.addAction(Notification.Action.Builder(null, a.optString("label").take(40), pi).build())
        }
        if (nightBound) {
            // "expires" (epoch seconds) is when the server's button codes stop working (the night's end); without it, 12 hours
            val endMs = m.optLong("expires", 0L) * 1000L
            val leftMs = if (endMs > 0L) endMs - System.currentTimeMillis() else 12L * 3600L * 1000L
            if (leftMs <= 0L) return                          // its night is already over (a late push): show nothing
            b.setTimeoutAfter(leftMs)
        }
        c.getSystemService(NotificationManager::class.java).notify(id, b.build())
    }

    /** A notification button: do it, then turn the notification into a quiet "done". */
    @JvmStatic fun act(c: Context, act: String, minutes: Int, label: String, person: String, nid: Int, ref: String = "") {
        val pid = person.ifEmpty { Ember.person(c) }
        channel(c)
        // take the buttons away at once, so a second tap can't send it again while the call runs
        c.getSystemService(NotificationManager::class.java).notify(nid, Notification.Builder(c, CHANNEL).setSmallIcon(R.drawable.ic_stat_flash)
            .setContentTitle("Working…").setContentText(label).setOnlyAlertOnce(true).setTimeoutAfter(30000).build())
        var confirm: String? = null          // the server's own words for what a care button did
        val ok = try {
            if (act.startsWith("care_") && ref.matches(Regex("[A-Za-z0-9_-]{1,40}"))) {    // a care reminder: it's done / remind me next week
                val done = act == "care_done"
                val r = Ember.sideCall(c, pid, "POST", "/api/care/$ref/" + (if (done) "done" else "snooze"), "{}")
                confirm = try { JSONObject(r).optString(if (done) "after_done" else "after_snooze").take(120).ifEmpty { null } } catch (_: Exception) { null }
                    ?: (if (done) "Marked done." else "I'll remind you next week.")
            }
            else if (act.startsWith("care_")) throw IllegalArgumentException("no job")
            else if (act == "manual_off") Ember.sideCall(c, pid, "POST", "/api/people/$pid/adjust/clear?source=notification", null)
            else if (act.startsWith("feel_"))           // the morning check-in: how the temperature felt last night
                Ember.sideCall(c, pid, "POST", "/api/people/$pid/feedback", JSONObject().put("temp", act.removePrefix("feel_")).toString())
            else if (act.startsWith("pet_") && ref.matches(Regex("[A-Za-z0-9]{1,40}")))   // "was that the dog?" answered from the notification
                Ember.sideCall(c, pid, "POST", "/api/people/$pid/pet-question/$ref", JSONObject().put("answer", act.removePrefix("pet_")).toString())
            else if (act.startsWith("pet_")) throw IllegalArgumentException("no question")
            else if (act.startsWith("away_") && ref.matches(Regex("\\d{4}-\\d{2}-\\d{2}")))   // "were you away last night?"
                Ember.sideCall(c, pid, "POST", "/api/people/$pid/away-check", JSONObject().put("night", ref).put("answer", act.removePrefix("away_")).toString())
            else if (act.startsWith("away_")) throw IllegalArgumentException("no question")
            else Ember.sideCall(c, pid, "POST", "/api/people/$pid/tonight",
                JSONObject().put("action", act).put("source", "notification").apply { if (minutes > 0) put("minutes", minutes) }.toString())
            true
        } catch (_: Exception) { false }
        channel(c)
        val done = Notification.Builder(c, CHANNEL).setSmallIcon(R.drawable.ic_stat_flash)
            .setContentTitle(if (ok) (confirm ?: "Done: $label") else "Couldn't reach Ember & Tide")
            .setContentText(if (ok) (if (confirm != null) "" else "Open the app to change it") else "Try again from the app")
            .setOnlyAlertOnce(true).setAutoCancel(true).setTimeoutAfter(if (ok) 6000 else 30000)
        c.getSystemService(NotificationManager::class.java).notify(nid, done.build())
        EmberWidget.refreshAll(c)
    }
}

/** Receives what the distributor (ntfy) hands us. */
class EmberPushService : PushService() {
    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        EmberPush.saveEndpoint(this, endpoint)
        Thread { EmberPush.sync(applicationContext) }.start()
    }

    override fun onMessage(message: PushMessage, instance: String) {
        if (!message.decrypted) return                       // not encrypted for this phone: ignore it
        try {
            val m = JSONObject(String(message.content, Charsets.UTF_8))
            if (m.optString("t") == "et") EmberPush.show(this, m)
        } catch (_: Exception) {}
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        EmberPush.failed(this, when (reason) {
            FailedReason.NETWORK -> "ntfy couldn't reach its server"
            FailedReason.ACTION_REQUIRED -> "open ntfy and finish setting it up"
            else -> "ntfy couldn't register ($reason)"
        })
    }

    override fun onUnregistered(instance: String) { EmberPush.forget(this) }
}
