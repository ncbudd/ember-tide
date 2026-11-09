package app.embertide

import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import org.unifiedpush.android.connector.UnifiedPush

/** App notifications from the web UI: status, turn on (pick ntfy), turn off, send a test. */
@CapacitorPlugin(name = "EmberPush")
class EmberPushPlugin : Plugin() {
    private fun st(): JSObject = JSObject(EmberPush.status(context).toString())

    @PluginMethod
    fun status(call: PluginCall) = call.resolve(st())

    /**
     * Turn notifications on. method "auto" uses Google push when this build and the server both support it, otherwise
     * ntfy (UnifiedPush); "fcm" / "up" pick one.
     */
    @PluginMethod
    fun enable(call: PluginCall) {
        val want = call.getString("method", "auto")
        Thread {
            val serverFcm = try { org.json.JSONObject(Ember.call(context, "GET", "/api/notify/me/push", null)).optBoolean("fcm") } catch (_: Exception) { false }
            val useFcm = want == "fcm" || (want == "auto" && serverFcm && EmberPush.fcmAvailable(context))
            if (useFcm) {
                if (EmberPush.method(context) == "up") EmberPush.disable(context)
                EmberPush.syncFcm(context)
                call.resolve(st())
            } else {
                if (EmberPush.method(context) == "fcm") EmberPush.disable(context)
                activity.runOnUiThread { pickDistributor(call) }
            }
        }.start()
    }

    private fun pickDistributor(call: PluginCall) {
        val act = activity
        // keep the distributor already chosen; with one installed, use it; with several, ask once
        UnifiedPush.tryUseCurrentOrDefaultDistributor(act) { ok ->
            if (ok) { EmberPush.register(context); call.resolve(st()) }
            else UnifiedPush.tryPickDistributor(act) { picked ->
                if (picked) EmberPush.register(context)
                call.resolve(st())
            }
        }
    }

    /** After sign-in: if notifications were set up before, make sure the server has this phone. */
    @PluginMethod
    fun sync(call: PluginCall) {
        Thread {
            val p = Ember.prefs(context)
            // every time the app opens: the server may have dropped this phone (its push address went stale), and
            // registering again is harmless, so don't trust the phone's own "registered" flag
            when (EmberPush.method(context)) {
                "fcm" -> EmberPush.syncFcm(context)
                "up" -> if (p.getString("push_endpoint", null) == null) EmberPush.register(context) else EmberPush.sync(context)
            }
            call.resolve(st())
        }.start()
    }

    /** Take one kind of notification off the phone (it was answered here or by someone else). */
    @PluginMethod
    fun clear(call: PluginCall) {
        val kind = call.getString("kind", "") ?: ""
        if (kind.isNotEmpty()) context.getSystemService(android.app.NotificationManager::class.java).cancel(EmberPush.idFor(kind))
        call.resolve()
    }

    @PluginMethod
    fun disable(call: PluginCall) {
        Thread { EmberPush.disable(context); call.resolve(st()) }.start()
    }

    @PluginMethod
    fun test(call: PluginCall) {
        Thread {
            try { Ember.call(context, "POST", "/api/notify/me/push/test", null); call.resolve(st()) }
            catch (e: Exception) { call.reject(e.message?.substringAfter("{\"detail\":\"")?.substringBefore("\"}") ?: "Test failed") }
        }.start()
    }
}
