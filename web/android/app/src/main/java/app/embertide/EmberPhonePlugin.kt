package app.embertide

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import com.getcapacitor.JSObject
import com.getcapacitor.PermissionState
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import com.getcapacitor.annotation.PermissionCallback
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * "From this phone" switches for the web UI. A switch asks for its permission only when turned on; background
 * location (needed so home/away works with the app closed) is its own second step, which Android shows as a
 * settings page ("Allow all the time").
 */
@CapacitorPlugin(
    name = "EmberPhone",
    permissions = [
        Permission(alias = "location", strings = [Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION]),
        Permission(alias = "calendar", strings = [Manifest.permission.READ_CALENDAR]),
        Permission(alias = "bluetooth", strings = [Manifest.permission.BLUETOOTH_SCAN]),
    ],
)
class EmberPhonePlugin : Plugin() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    // re-arm the periodic job and the on-charger trigger whenever the app starts (e.g. after an update)
    override fun load() { runCatching { PhoneSources.schedule(context) } }

    private fun reply(call: PluginCall, o: JSONObject) = call.resolve(JSObject(o.toString()))
    private fun status(call: PluginCall, extra: (JSONObject) -> Unit = {}) =
        reply(call, PhoneSources.status(context).also(extra))

    @PluginMethod
    fun status(call: PluginCall) = status(call) {}

    /** {source, on}. Turning on: ask what's missing, then (location/Wi-Fi/dial) the background step. */
    @PluginMethod
    fun setEnabled(call: PluginCall) {
        val src = call.getString("source") ?: return call.reject("source missing")
        if (src !in PhoneSources.ALL) return call.reject("unknown source")
        val on = call.getBoolean("on", false) == true
        if (!on) {
            PhoneSources.setEnabled(context, src, false)
            if (src == "wifi") PhoneSources.forgetSsid(context)
            PhoneSources.kick(context)
            // the server forgets it too, on the next send (sharing list); send now even if nothing else is on
            scope.launch { runCatching { PhoneSources.send(context) }; status(call) }
            return
        }
        askNext(call, src)
    }

    private fun aliasesFor(src: String): List<String> = when (src) {
        "location", "wifi" -> listOf("location")
        "calendar" -> listOf("calendar")
        "dial" -> if (Build.VERSION.SDK_INT >= 31) listOf("bluetooth", "location") else listOf("location")
        else -> emptyList()
    }

    private fun askNext(call: PluginCall, src: String) {
        val missing = aliasesFor(src).firstOrNull { getPermissionState(it) != PermissionState.GRANTED }
        if (missing != null) {
            call.data.put("_asked", missing)
            requestPermissionForAlias(missing, call, "permDone")
            return
        }
        finishEnable(call, src)
    }

    @PermissionCallback
    private fun permDone(call: PluginCall) {
        val src = call.getString("source") ?: return call.reject("source missing")
        val asked = call.getString("_asked")
        if (asked != null && getPermissionState(asked) != PermissionState.GRANTED) {
            call.data.remove("_asked")
            return status(call) { it.put("denied", asked) }       // the UI explains and offers system settings
        }
        call.data.remove("_asked")
        askNext(call, src)
    }

    private fun finishEnable(call: PluginCall, src: String) {
        PhoneSources.setEnabled(context, src, true)
        val askBackground = PhoneSources.needsBackground(src) && !PhoneSources.backgroundOk(context)
        scope.launch {
            if (src == "location" && !PhoneSources.homeSet(context)) PhoneSources.setHomeHere(context)
            runCatching { PhoneSources.send(context) }
            status(call) { if (askBackground) it.put("background_asked", true) }
            // Second step, "Allow all the time": Android 11+ shows it as a settings page. Asked without waiting for a
            // result (Capacitor loses that result when the settings page replaces the dialog); the page re-reads the
            // status when the app comes back.
            if (askBackground) activity?.runOnUiThread {
                androidx.core.app.ActivityCompat.requestPermissions(activity, arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), 7301)
            }
        }
    }

    /** "I'm home now": save this spot as home. */
    @PluginMethod
    fun setHomeHere(call: PluginCall) {
        scope.launch {
            val ok = PhoneSources.setHomeHere(context)
            if (ok) runCatching { PhoneSources.send(context) }
            status(call) { it.put("saved", ok) }
        }
    }

    @PluginMethod
    fun setRadius(call: PluginCall) {
        PhoneSources.setRadius(context, call.getInt("meters", 150) ?: 150)
        status(call)
    }

    /** Re-learn the home Wi-Fi from the network the phone is on now. */
    @PluginMethod
    fun setHomeWifi(call: PluginCall) {
        PhoneSources.forgetSsid(context)
        scope.launch { runCatching { PhoneSources.send(context) }; status(call) }
    }

    @PluginMethod
    fun calendars(call: PluginCall) = reply(call, JSONObject().put("calendars", PhoneSources.calendars(context)))

    @PluginMethod
    fun setCalendarsExcluded(call: PluginCall) {
        val arr = call.getArray("ids") ?: return call.reject("ids missing")
        PhoneSources.setExcluded(context, (0 until arr.length()).map { arr.getString(it) })
        scope.launch { runCatching { PhoneSources.send(context) }; status(call) }
    }

    @PluginMethod
    fun sendNow(call: PluginCall) {
        scope.launch {
            try { PhoneSources.send(context); status(call) } catch (e: Exception) { call.reject(e.message ?: "send failed") }
        }
    }

    /** The app's page in system settings (permissions denied for good). */
    @PluginMethod
    fun openSettings(call: PluginCall) {
        val i = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(i)
        call.resolve()
    }
}
