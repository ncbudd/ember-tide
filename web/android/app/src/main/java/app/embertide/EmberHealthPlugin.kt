package app.embertide

import android.content.Intent
import androidx.activity.result.ActivityResult
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.ActivityCallback
import com.getcapacitor.annotation.CapacitorPlugin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Health Connect from the web UI: status, connect (permission sheet), sync now, open Health Connect. */
@CapacitorPlugin(name = "EmberHealth")
class EmberHealthPlugin : Plugin() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private fun reply(call: PluginCall, o: JSONObject) = call.resolve(JSObject(o.toString()))

    @PluginMethod
    fun status(call: PluginCall) {
        scope.launch {
            try {
                val st = HealthSync.status(context)
                // the 6-hour job is cancelled on sign-out; signed in again, bring it back (KEEP: no-op if already there)
                if (st.optBoolean("connected") && Ember.ready(context)) HealthSync.schedule(context)
                reply(call, st)
            } catch (e: Exception) { call.reject(e.message ?: "status failed") }
        }
    }

    /** Which kinds of data to share: {types: ["hr", "sleep", ...]}. Only these are asked for and read. */
    @PluginMethod
    fun setTypes(call: PluginCall) {
        val arr = call.getArray("types")
        if (arr == null) { call.reject("types missing"); return }
        val keys = (0 until arr.length()).map { arr.getString(it) }
        HealthSync.setEnabled(context, keys)
        scope.launch {
            try { reply(call, HealthSync.status(context)) } catch (e: Exception) { call.reject(e.message ?: "status failed") }
        }
    }

    @PluginMethod
    fun connect(call: PluginCall) {
        when (HealthSync.sdk(context)) {
            "available" -> {
                val want = HealthSync.wanted(context)
                if (want.isEmpty()) {
                    scope.launch { try { reply(call, HealthSync.status(context)) } catch (e: Exception) { call.reject(e.message ?: "status failed") } }
                    return
                }
                val intent = PermissionController.createRequestPermissionResultContract()
                    .createIntent(context, want)
                startActivityForResult(call, intent, "connected")
            }
            "update" -> { openStore(); call.reject("Install or update Health Connect, then try again") }
            else -> call.reject("Health Connect isn't available on this phone")
        }
    }

    @ActivityCallback
    private fun connected(call: PluginCall, result: ActivityResult) {
        scope.launch {
            try {
                val st = HealthSync.status(context)
                if (st.optBoolean("connected")) HealthSync.schedule(context)
                reply(call, st)
            } catch (e: Exception) { call.reject(e.message ?: "connect failed") }
        }
    }

    @PluginMethod
    fun sync(call: PluginCall) {
        val full = call.getBoolean("full", false) == true
        scope.launch {
            try { reply(call, HealthSync.sync(context, full)) } catch (e: Exception) { call.reject(e.message ?: "sync failed") }
        }
    }

    @PluginMethod
    fun openSettings(call: PluginCall) {
        val i = Intent(HealthConnectClient.ACTION_HEALTH_CONNECT_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try { context.startActivity(i) } catch (e: Exception) { openStore() }
        call.resolve()
    }

    @PluginMethod
    fun disconnect(call: PluginCall) {
        scope.launch {
            try {
                HealthSync.client(context).permissionController.revokeAllPermissions()
                HealthSync.unschedule(context)
                reply(call, HealthSync.status(context))
            } catch (e: Exception) { call.reject(e.message ?: "disconnect failed") }
        }
    }

    private fun openStore() {
        val uri = android.net.Uri.parse("market://details?id=com.google.android.apps.healthdata&url=healthconnect%3A%2F%2Fonboarding")
        try { context.startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (_: Exception) {}
    }
}
