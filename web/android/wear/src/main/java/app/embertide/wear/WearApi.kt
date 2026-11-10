package app.embertide.wear

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** What the tile needs: the server, the watch key the phone handed over (a limited `et_d_` key that can only show and
 *  change this person's side; older phone apps handed over their session, which the phone replaces on its next start),
 *  and a tiny HTTP client. */
object WearApi {
    private fun prefs(c: Context) = c.getSharedPreferences("embertide", Context.MODE_PRIVATE)
    fun server(c: Context) = prefs(c).getString("server", "") ?: ""
    fun token(c: Context) = prefs(c).getString("token", "") ?: ""
    fun person(c: Context) = prefs(c).getString("person", "") ?: ""
    fun ready(c: Context) = server(c).isNotEmpty() && token(c).isNotEmpty() && person(c).isNotEmpty()

    fun save(c: Context, server: String, token: String, person: String) {
        prefs(c).edit().putString("server", server).putString("token", token).putString("person", person).apply()
    }

    /** Blocking; call off the main thread. */
    fun call(c: Context, method: String, path: String, body: String? = null): String {
        val h = URL(server(c) + path).openConnection() as HttpURLConnection
        h.requestMethod = method
        h.instanceFollowRedirects = false           // never carry the token to wherever a redirect points
        h.connectTimeout = 8000
        h.readTimeout = 10000
        h.setRequestProperty("Authorization", "Bearer ${token(c)}")
        h.setRequestProperty("X-Embertide", "1")
        h.setRequestProperty("X-Embertide-Client", "app")
        if (body != null) {
            h.doOutput = true
            h.setRequestProperty("Content-Type", "application/json")
            h.outputStream.use { it.write(body.toByteArray()) }
        }
        val code = h.responseCode
        val text = (if (code < 400) h.inputStream else h.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
        if (code >= 400) throw IllegalStateException("HTTP $code")
        return text
    }

    fun me(c: Context, snap: JSONObject): JSONObject? {
        val people = snap.getJSONArray("people")
        for (i in 0 until people.length()) if (people.getJSONObject(i).optString("id") == person(c)) return people.getJSONObject(i)
        return null
    }

    /** What the tile and the complication show. `flashLeftMs` is time left on a flash (0 if none), measured on the
     *  server's clock so a phone, watch and server that disagree about the time still count down correctly. */
    data class View(val name: String, val temp: String, val state: String, val short: String, val on: Boolean,
                    val flashKind: String? = null, val flashLeftMs: Long = 0, val ok: Boolean = true)

    fun view(c: Context): View {
        if (!ready(c)) return View("Ember & Tide", "–", "Open the app on your phone to sign in", "Sign in", false, ok = false)
        return try {
            val snap = JSONObject(call(c, "GET", "/api/snapshot"))
            val me = me(c, snap) ?: return View("Ember & Tide", "–", "This account has no side", "–", false, ok = false)
            val f = me.optJSONObject("settings")?.optString("units", "F") != "C"
            val n = me.getJSONObject("night")
            val on = !n.isNull("target_c")
            val bed = me.optJSONObject("bed")
            val flash = n.optJSONObject("flash")
            val now = snap.optDouble("now")
            val left = if (flash != null && now < flash.optDouble("end")) ((flash.optDouble("end") - now) * 1000).toLong() else 0L
            val kind = if (left > 0) flash!!.optString("kind") else null
            val (state, short) = when {
                left > 0 -> "${if (kind == "hot") "Hot" else "Cold"} flash · ${Math.ceil(left / 60000.0).toInt()} min" to
                    "${if (kind == "hot") "Hot" else "Cold"} ${Math.ceil(left / 60000.0).toInt()}m"
                !on -> when (n.optString("state")) { "skipped" -> "Off tonight" to "Off"; "paused" -> "Paused" to "Paused"; else -> "Tap power to turn on" to "Off" }
                else -> {
                    val m = if (bed == null || bed.isNull("measured_c")) Double.NaN else bed.optDouble("measured_c")
                    val t = n.getDouble("target_c")
                    val w = if (m.isNaN()) "On" else if (t < m - 0.4) "Cooling" else if (t > m + 0.4) "Warming" else "Holding"
                    w to w
                }
            }
            View(me.optString("name"), if (on) temp(n.getDouble("target_c"), f) else "Off", state, short, on, kind, left)
        } catch (e: Exception) {
            // 401: the watch key was revoked or the phone signed out; opening the app on the phone hands over a new one
            if (e.message?.startsWith("HTTP 401") == true) View("Ember & Tide", "–", "Open the app on your phone to sign in again", "Sign in", false, ok = false)
            else View("Ember & Tide", "–", "Can't reach your server", "–", false, ok = false)
        }
    }

    fun temp(cDeg: Double, f: Boolean): String = if (f) "${Math.round(cDeg * 9 / 5 + 32)}°" else String.format(java.util.Locale.US, "%.1f°", cDeg)
}
