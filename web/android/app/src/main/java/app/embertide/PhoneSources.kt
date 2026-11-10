package app.embertide

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlarmManager
import android.app.NotificationManager
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * "From this phone": what the phone itself can tell Ember & Tide, each behind its own switch and permission.
 *   location  home / away / on the way home (distance to the saved home spot; no coordinates leave the phone)
 *   wifi      on the home Wi-Fi (the network saved when switched on)
 *   calendar  tonight's last event and tomorrow's first (times only, never titles)
 *   alarm     the next alarm
 *   charging  on the charger / bedtime mode (Do Not Disturb)
 *   watch     the watch connected to this phone
 *   dial      a bedside dial heard over Bluetooth at night (phone lying by the bed)
 * Collected by a WorkManager job every 15 min (more often while away in the evening), and when the alarm changes.
 * Sent to POST /api/people/{pid}/phone-context. No wake locks; the dial scan runs a few seconds and only at night
 * while the phone is charging or in bedtime mode.
 */
object PhoneSources {
    val ALL = listOf("location", "wifi", "calendar", "alarm", "charging", "watch", "dial", "watch_hr")
    private const val PREFS = "ember_phone"
    private const val WORK = "phone-context"
    val DIAL_UUID: UUID = UUID.fromString("7c1f0e2a-5d3b-4e8a-9b6c-ed0a11e1de00")

    fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun enabled(c: Context): Set<String> = prefs(c).getStringSet("on", emptySet())!!.filter { it in ALL }.toSet()

    fun setEnabled(c: Context, src: String, on: Boolean) {
        val now = enabled(c).toMutableSet()
        if (on) now += src else now -= src
        prefs(c).edit().putStringSet("on", now).apply()
        schedule(c)
        if (src == "watch_hr") WatchHrRelay.control(c)          // tell the watch at once (it starts only at night)
    }

    fun has(c: Context, perm: String) = ContextCompat.checkSelfPermission(c, perm) == PackageManager.PERMISSION_GRANTED

    /** Permissions each switch needs (runtime ones only; background location is asked as its own step). */
    fun needs(src: String): List<String> = when (src) {
        "location", "wifi" -> listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        "calendar" -> listOf(Manifest.permission.READ_CALENDAR)
        "dial" -> if (Build.VERSION.SDK_INT >= 31) listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.ACCESS_FINE_LOCATION)
                  else listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        else -> emptyList()
    }
    fun needsBackground(src: String) = src in listOf("location", "wifi", "dial")
    fun backgroundOk(c: Context) = Build.VERSION.SDK_INT < 29 || has(c, Manifest.permission.ACCESS_BACKGROUND_LOCATION)

    private val NET = Constraints.Builder().setRequiredNetworkType(androidx.work.NetworkType.CONNECTED).build()

    /** Signed out: stop the periodic job and the on-charge / night triggers (schedule() brings them back). */
    fun unschedule(c: Context) {
        val wm = WorkManager.getInstance(c)
        for (n in listOf(WORK, "$WORK-now", "$WORK-night", "$WORK-retry", "$WORK-oncharge")) wm.cancelUniqueWork(n)
        c.getSystemService(AlarmManager::class.java)?.cancel(android.app.PendingIntent.getBroadcast(c, 41, Intent(c, NightAlarmReceiver::class.java),
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT))
    }

    fun schedule(c: Context) {
        val wm = WorkManager.getInstance(c)
        if (enabled(c).isEmpty()) { wm.cancelUniqueWork(WORK); return }
        // NETWORK: while the phone dozes, Android gives a background job internet only if it asks for it (without it
        // the scan ran but the send failed with "unable to resolve host", 2026-11-08). UPDATE so phones that already
        // have the old job get the new constraint.
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<PhoneContextWorker>(15, TimeUnit.MINUTES).setConstraints(NET).build())
        armOnCharge(c)
        armNightAlarm(c)
    }

    /** Night check-ins on time. Overnight Android held the 15-min job "ready" for up to 2.5 h (2026-11-08), so at night
     *  (19:00-11:00) an inexact alarm that may fire while dozing wakes the app every ~15 min and starts an EXPEDITED
     *  job (runs at once, with network). By day nothing extra is scheduled; the alarm re-arms itself for 19:00. */
    fun armNightAlarm(c: Context) {
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        val pi = android.app.PendingIntent.getBroadcast(c, 41, Intent(c, NightAlarmReceiver::class.java),
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
        val on = enabled(c)
        if ("dial" !in on && "charging" !in on && "watch_hr" !in on) { am.cancel(pi); return }
        val next = if (nightWindow()) System.currentTimeMillis() + 15 * 60_000L else Calendar.getInstance().apply {
            if (get(Calendar.HOUR_OF_DAY) >= 19) add(Calendar.DAY_OF_YEAR, 1)
            set(Calendar.HOUR_OF_DAY, 19); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0)
        }.timeInMillis
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next, pi)
    }

    /** Send now as an expedited job (falls back to a normal one when the expedited quota is used up). */
    fun kickNow(c: Context) {
        if (enabled(c).isEmpty()) return
        WorkManager.getInstance(c).enqueueUniqueWork("$WORK-night", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<PhoneContextWorker>().setConstraints(NET)
                .setExpedited(androidx.work.OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build())
    }

    /** Send (and look for the dial) as soon as the phone goes on the charger. Apps can't listen for "plugged in"
     *  in the background any more, so this is a one-off job that waits for charging; it is re-armed only once the
     *  phone is off the charger again, so it can't loop while charging. It waits a minute on the charger before
     *  looking, so a quick top-up or a phone set down for a moment doesn't count. */
    fun armOnCharge(c: Context) {
        val on = enabled(c)
        if ("dial" !in on && "charging" !in on) { WorkManager.getInstance(c).cancelUniqueWork("$WORK-oncharge"); return }
        if (isCharging(c)) return
        WorkManager.getInstance(c).enqueueUniqueWork("$WORK-oncharge", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<PhoneContextWorker>().setInputData(workDataOf("oncharge" to true))
                .setConstraints(Constraints.Builder().setRequiresCharging(true).build()).build())
    }

    /** On the charger: plugged in, whether or not the battery is filling right now. Pixels hold the charge at night
     *  (battery protection / adaptive charging) and then report "not charging" while plugged in (2026-11-08). */
    fun isCharging(c: Context): Boolean {
        val bat = c.registerReceiver(null, android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return false
        val st = bat.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        return bat.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0 ||
            st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL
    }

    /** Collect and send now (switch changed, app opened, alarm changed, a follow-up while driving home). */
    fun kick(c: Context, delayMin: Long = 0) {
        if (enabled(c).isEmpty()) return
        val b = OneTimeWorkRequestBuilder<PhoneContextWorker>().setConstraints(NET)
        if (delayMin > 0) b.setInitialDelay(delayMin, TimeUnit.MINUTES)
        WorkManager.getInstance(c).enqueueUniqueWork("$WORK-now", if (delayMin > 0) ExistingWorkPolicy.KEEP else ExistingWorkPolicy.REPLACE, b.build())
    }

    /** Didn't get through: try again only once there's a network, backing off 1 → 2 → 4 → 8 → 15 min (then the
     *  regular 15-min job takes over), so a phone with no signal isn't woken every minute all night. */
    private fun retryLater(c: Context) {
        val p = prefs(c)
        val n = p.getInt("retry_n", 0)
        p.edit().putInt("retry_n", n + 1).apply()
        if (n >= 4) return
        val delay = minOf(15L, 1L shl n)
        WorkManager.getInstance(c).enqueueUniqueWork("$WORK-retry", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<PhoneContextWorker>().setInitialDelay(delay, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(androidx.work.NetworkType.CONNECTED).build()).build())
    }

    // ---------- home spot ----------
    fun homeSet(c: Context) = prefs(c).contains("home_lat")
    fun radiusM(c: Context) = prefs(c).getInt("home_radius_m", 150)
    fun setRadius(c: Context, m: Int) = prefs(c).edit().putInt("home_radius_m", m.coerceIn(50, 2000)).apply()

    suspend fun setHomeHere(c: Context): Boolean {
        val loc = locate(c) ?: return false
        prefs(c).edit().putFloat("home_lat", loc.latitude.toFloat()).putFloat("home_lon", loc.longitude.toFloat()).apply()
        return true
    }

    @SuppressLint("MissingPermission")
    suspend fun locate(c: Context): Location? {
        if (!has(c, Manifest.permission.ACCESS_FINE_LOCATION) && !has(c, Manifest.permission.ACCESS_COARSE_LOCATION)) return null
        val lm = c.getSystemService(LocationManager::class.java) ?: return null
        val providers = buildList {
            if (Build.VERSION.SDK_INT >= 31) add(LocationManager.FUSED_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)        // battery: Wi-Fi/cell positioning is plenty for home vs away; no GPS
        }.filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }
        // a recent fix is good enough and costs nothing
        val recent = providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .filter { System.currentTimeMillis() - it.time < 3 * 60_000 }.maxByOrNull { it.time }
        if (recent != null) return recent
        // one fresh fix: fused first, then the others (fused can stay silent without Google's location service)
        if (Build.VERSION.SDK_INT >= 30) for (pr in providers) {
            val fix = withTimeoutOrNull(12_000) {
                suspendCancellableCoroutine<Location?> { cont ->
                    val cancel = android.os.CancellationSignal()
                    cont.invokeOnCancellation { cancel.cancel() }
                    if (Build.VERSION.SDK_INT >= 31) {
                        val req = android.location.LocationRequest.Builder(0L)
                            .setQuality(android.location.LocationRequest.QUALITY_BALANCED_POWER_ACCURACY).setDurationMillis(12_000).build()
                        lm.getCurrentLocation(pr, req, cancel, ContextCompat.getMainExecutor(c)) { if (cont.isActive) cont.resume(it) }
                    } else lm.getCurrentLocation(pr, cancel, ContextCompat.getMainExecutor(c)) { if (cont.isActive) cont.resume(it) }
                }
            }
            val fresh = fix != null && (android.os.SystemClock.elapsedRealtimeNanos() - fix.elapsedRealtimeNanos) < 90_000_000_000L
            android.util.Log.d("EmberPhone", "location from $pr: ${if (fix == null) "none" else if (fresh) "fix" else "old fix"}")
            if (fresh) return fix
        }
        return providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.maxByOrNull { it.time }
    }

    // ---------- collect ----------
    suspend fun collect(c: Context): JSONObject {
        val on = enabled(c)
        val out = JSONObject()
        val now = System.currentTimeMillis()
        val p = prefs(c)
        if ("location" in on && homeSet(c)) {
            locate(c)?.let { loc ->
                val home = Location("home").apply { latitude = p.getFloat("home_lat", 0f).toDouble(); longitude = p.getFloat("home_lon", 0f).toDouble() }
                val km = loc.distanceTo(home) / 1000.0
                val atHome = km * 1000 <= radiusM(c) + min(loc.accuracy.toDouble(), 150.0)
                out.put("home", atHome)
                if (!atHome) {
                    out.put("distance_km", Math.round(km * 100) / 100.0)
                    val prevKm = p.getFloat("prev_km", -1f).toDouble()
                    val prevT = p.getLong("prev_t", 0)
                    val mins = (now - prevT) / 60_000.0
                    if (prevKm > 0 && mins in 3.0..40.0 && prevKm - km >= 0.5) {
                        val speed = ((prevKm - km) / (mins / 60.0)).coerceIn(10.0, 130.0)
                        out.put("heading_home", true)
                        out.put("eta_min", (Math.round(km / speed * 60 * 10) / 10.0).coerceAtMost(24 * 60.0))   // the server's limit
                    } else out.put("heading_home", false)
                }
                p.edit().putFloat("prev_km", km.toFloat()).putLong("prev_t", now).apply()
            }
        }
        if ("wifi" in on) wifiHome(c)?.let { out.put("wifi_home", it) }
        if ("calendar" in on && has(c, Manifest.permission.READ_CALENDAR)) {
            val (end, early) = calendar(c, now)
            out.put("evening_event_end", end ?: 0.0)
            out.put("early_start", early ?: 0.0)
        }
        if ("alarm" in on) {
            val next = c.getSystemService(AlarmManager::class.java)?.nextAlarmClock?.triggerTime
            out.put("next_alarm", if (next != null && next - now in 0..24 * 3600_000L) next / 1000.0 else 0.0)
        }
        var charging = false
        var bedtime = false
        if ("charging" in on || "dial" in on) {
            charging = isCharging(c)                     // plugged in counts, even while the phone holds its charge
            val nm = c.getSystemService(NotificationManager::class.java)
            bedtime = (nm?.currentInterruptionFilter ?: NotificationManager.INTERRUPTION_FILTER_ALL) != NotificationManager.INTERRUPTION_FILTER_ALL
            out.put("charging", charging).put("bedtime_mode", bedtime)
        }
        if ("watch" in on) {
            val n = withContext(Dispatchers.IO) { runCatching { Tasks.await(Wearable.getNodeClient(c).connectedNodes, 5, TimeUnit.SECONDS) }.getOrNull() }
            if (n != null) out.put("watch_connected", n.any { it.isNearby })
        }
        val screenOff = c.getSystemService(android.os.PowerManager::class.java)?.isInteractive == false
        if ("dial" in on && (charging || bedtime || screenOff) && (nightWindow() || fakeDials(c) != null)) {
            out.put("dials", validDials(scanDials(c)))
        }
        out.put("sharing", JSONArray(on.toList()))
        out.put("device", Build.MODEL.take(60))
        return out
    }

    fun nightWindow(): Boolean { val h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY); return h >= 19 || h < 11 }

    @SuppressLint("MissingPermission")
    private fun wifiHome(c: Context): Boolean? {
        if (!has(c, Manifest.permission.ACCESS_FINE_LOCATION)) return null
        val wm = c.applicationContext.getSystemService(WifiManager::class.java) ?: return null
        @Suppress("DEPRECATION") val info = wm.connectionInfo ?: return false
        @Suppress("DEPRECATION") val ssid = info.ssid?.trim('"')
        if (ssid.isNullOrBlank() || ssid == "<unknown ssid>") return false
        val saved = prefs(c).getString("home_ssid", null)
        if (saved == null) { prefs(c).edit().putString("home_ssid", ssid).apply(); return true }   // first time: this is home
        return ssid == saved
    }
    fun homeSsid(c: Context): String? = prefs(c).getString("home_ssid", null)
    fun forgetSsid(c: Context) = prefs(c).edit().remove("home_ssid").apply()

    /** Tonight's last event end and tomorrow's first early start (03:00–11:00), skipping all-day, cancelled and free. */
    fun calendar(c: Context, now: Long): Pair<Double?, Double?> {
        val excluded = prefs(c).getStringSet("cal_excluded", emptySet())!!
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
            .appendPath((now - 12 * 3600_000L).toString()).appendPath((now + 30 * 3600_000L).toString()).build()
        val cols = arrayOf(CalendarContract.Instances.BEGIN, CalendarContract.Instances.END, CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.STATUS, CalendarContract.Instances.AVAILABILITY, CalendarContract.Instances.CALENDAR_ID,
            CalendarContract.Instances.VISIBLE)
        var end: Long? = null
        var early: Long? = null
        val tomorrow = Calendar.getInstance().apply { if (get(Calendar.HOUR_OF_DAY) >= 12) add(Calendar.DAY_OF_YEAR, 1) }
        runCatching {
            c.contentResolver.query(uri, cols, null, null, null)?.use { cur ->
                while (cur.moveToNext()) {
                    val b = cur.getLong(0); val e = cur.getLong(1)
                    if (cur.getInt(2) == 1 || cur.getInt(3) == CalendarContract.Instances.STATUS_CANCELED ||
                        cur.getInt(4) == CalendarContract.Instances.AVAILABILITY_FREE || cur.getInt(6) == 0 ||
                        cur.getLong(5).toString() in excluded) continue
                    if (e > now && b - now <= 6 * 3600_000L && e - b < 20 * 3600_000L) end = max(end ?: 0, e)
                    val s = Calendar.getInstance().apply { timeInMillis = b }
                    if (b > now && s.get(Calendar.YEAR) == tomorrow.get(Calendar.YEAR) && s.get(Calendar.DAY_OF_YEAR) == tomorrow.get(Calendar.DAY_OF_YEAR)
                        && s.get(Calendar.HOUR_OF_DAY) in 3..10) early = min(early ?: Long.MAX_VALUE, b)
                }
            }
        }
        return Pair(end?.let { it / 1000.0 }, early?.let { it / 1000.0 })
    }

    /** Calendars on this phone, for "leave this one out". */
    fun calendars(c: Context): JSONArray {
        val out = JSONArray()
        if (!has(c, Manifest.permission.READ_CALENDAR)) return out
        val excluded = prefs(c).getStringSet("cal_excluded", emptySet())!!
        runCatching {
            c.contentResolver.query(CalendarContract.Calendars.CONTENT_URI,
                arrayOf(CalendarContract.Calendars._ID, CalendarContract.Calendars.CALENDAR_DISPLAY_NAME, CalendarContract.Calendars.VISIBLE),
                null, null, null)?.use { cur ->
                while (cur.moveToNext()) {
                    val id = cur.getLong(0).toString()
                    out.put(JSONObject().put("id", id).put("name", cur.getString(1) ?: "Calendar").put("visible", cur.getInt(2) == 1).put("included", id !in excluded))
                }
            }
        }
        return out
    }
    fun setExcluded(c: Context, ids: List<String>) = prefs(c).edit().putStringSet("cal_excluded", ids.toSet()).apply()

    // ---------- bedside dials (iBeacon) ----------
    @SuppressLint("MissingPermission")
    suspend fun scanDials(c: Context): JSONArray {
        val fake = fakeDials(c)
        if (fake != null) return fake                    // debug builds only (FakeDial receiver)
        if (Build.VERSION.SDK_INT >= 31 && !has(c, Manifest.permission.BLUETOOTH_SCAN)) return JSONArray()
        if (!has(c, Manifest.permission.ACCESS_FINE_LOCATION)) return JSONArray()
        val scanner = c.getSystemService(BluetoothManager::class.java)?.adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner ?: return JSONArray()
        val prefix = byteArrayOf(0x02, 0x15) + uuidBytes(DIAL_UUID)
        val data = prefix + ByteArray(5)
        val mask = ByteArray(prefix.size) { 0xFF.toByte() } + ByteArray(5)
        val filter = ScanFilter.Builder().setManufacturerData(0x004C, data, mask).build()
        // full-duty listening for the short 8 s window: balanced mode listens ~25% of the time and caught 0-1 of the
        // dial's ~1/s advertisements (2026-11-07); 8 s every 15 min, only at night on the charger, costs next to nothing
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        val heard = HashMap<Int, MutableList<Int>>()
        val cb = object : ScanCallback() {
            override fun onScanResult(type: Int, r: ScanResult) {
                val m = r.scanRecord?.getManufacturerSpecificData(0x004C) ?: return
                if (m.size < 23) return
                val minor = ((m[20].toInt() and 0xFF) shl 8) or (m[21].toInt() and 0xFF)
                synchronized(heard) { heard.getOrPut(minor) { mutableListOf() }.add(r.rssi) }
            }
        }
        runCatching { scanner.startScan(listOf(filter), settings, cb) }.onFailure { return JSONArray() }
        // Listen up to 15 s (with the screen off Android listens only in short slices), but stop as soon as each dial
        // heard has given a few readings. The scan is ALWAYS stopped, even if the job is cancelled mid-listen: a
        // cancelled job once left a scan running for 20+ minutes (2026-11-08).
        try {
            val start = System.currentTimeMillis()
            while (System.currentTimeMillis() - start < 15_000) {
                delay(500)
                val enough = synchronized(heard) { heard.isNotEmpty() && heard.values.all { it.size >= 5 } }
                if (enough && System.currentTimeMillis() - start >= 3_000) break
            }
        } finally {
            runCatching { scanner.stopScan(cb) }
        }
        val out = JSONArray()
        synchronized(heard) {
            heard.entries.map { (minor, rs) -> minor to rs.sorted()[rs.size / 2] }.sortedByDescending { it.second }
                .forEach { (minor, rssi) -> out.put(JSONObject().put("minor", minor).put("rssi", rssi).put("seen_at", System.currentTimeMillis() / 1000.0)) }
        }
        return out
    }

    /** Only what the server accepts: signal -120..0 dBm (a beacon's 127 means "unknown"), minor 1..65535, at most 8. */
    private fun validDials(all: JSONArray): JSONArray {
        val out = JSONArray()
        for (i in 0 until all.length()) {
            val d = all.optJSONObject(i) ?: continue
            val rssi = d.optDouble("rssi", Double.NaN)
            if (d.optInt("minor", 0) < 1 || rssi.isNaN() || rssi > 0 || rssi == 127.0) continue
            if (out.length() < 8) out.put(if (rssi < -120) d.put("rssi", -120) else d)
        }
        return out
    }

    private fun uuidBytes(u: UUID): ByteArray {
        val b = ByteArray(16)
        for (i in 0 until 8) b[i] = (u.mostSignificantBits ushr (8 * (7 - i))).toByte()
        for (i in 0 until 8) b[8 + i] = (u.leastSignificantBits ushr (8 * (7 - i))).toByte()
        return b
    }

    /** Sightings injected by the debug-only FakeDial receiver (the emulator has no real beacons). */
    private fun fakeDials(c: Context): JSONArray? {
        val p = prefs(c)
        val at = p.getLong("fake_dials_at", 0)
        if (at == 0L || System.currentTimeMillis() - at > 10 * 60_000) return null
        return runCatching { JSONArray(p.getString("fake_dials", "[]")) }.getOrNull()
    }

    // ---------- send ----------
    private val sendLock = Mutex()                 // one send at a time; the server wants 5 s between reports
    private var lastPostAt = 0L

    suspend fun send(c: Context): JSONObject? = sendLock.withLock {
        if (!Ember.ready(c)) return@withLock null
        val wait = lastPostAt + 5_500L - System.currentTimeMillis()
        if (wait in 1..5_500L) delay(wait)             // e.g. switching a source off sends twice in a row
        sendNow(c)
    }

    private suspend fun sendNow(c: Context): JSONObject? {
        val body = collect(c)
        val res = withContext(Dispatchers.IO) {
            runCatching { Ember.call(c, "POST", "/api/people/" + Ember.person(c) + "/phone-context", body.toString()) }
        }
        lastPostAt = System.currentTimeMillis()
        // a 429 means the server already has a report from the last few seconds: not an error, nothing to retry
        if (res.exceptionOrNull()?.message?.startsWith("HTTP 429") == true) return body
        val ok = res.isSuccess
        prefs(c).edit().putLong("last_sent", if (ok) System.currentTimeMillis() else prefs(c).getLong("last_sent", 0))
            .putString("last_body", body.toString()).putString("last_error", if (ok) null else res.exceptionOrNull()?.message?.take(120)).apply()
        // didn't get through (offline, or too soon after the last send): try again in a minute rather than in 15
        if (!ok) { retryLater(c); return body }
        prefs(c).edit().putInt("retry_n", 0).apply()
        // live watch heart rate: the server says whether the person's night is on; the watch collects only then
        if ("watch_hr" in enabled(c)) runCatching { JSONObject(res.getOrThrow()) }.getOrNull()
            ?.takeIf { it.has("watch_hr_wanted") }?.let { WatchHrRelay.control(c, it.optBoolean("watch_hr_wanted")) }
        // while away in the evening, look again in 5 min so "on the way home" is caught in time
        val h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        if (body.optBoolean("home", true) == false && (h >= 16 || h < 3)) kick(c, 5)
        return body
    }

    fun status(c: Context): JSONObject {
        val p = prefs(c)
        val on = enabled(c)
        val srcs = JSONObject()
        for (s in ALL) {
            val perms = needs(s)
            val granted = perms.all { has(c, it) }
            val state = when {
                s == "dial" && c.getSystemService(BluetoothManager::class.java)?.adapter == null -> "unavailable"
                s == "watch_hr" && WatchHrRelay.status(c).optString("perm") in listOf("needed", "needs_background") -> "needs_watch_permission"
                !granted -> "needs_permission"
                needsBackground(s) && !backgroundOk(c) -> "needs_background"
                else -> "ok"
            }
            srcs.put(s, JSONObject().put("on", s in on).put("state", state))
        }
        return JSONObject().put("sources", srcs).put("device", Build.MODEL)
            .put("home_set", homeSet(c)).put("radius_m", radiusM(c)).put("home_ssid", homeSsid(c) ?: JSONObject.NULL)
            .put("last_sent", p.getLong("last_sent", 0)).put("last_error", p.getString("last_error", null) ?: JSONObject.NULL)
            .put("last", runCatching { JSONObject(p.getString("last_body", "{}")!!) }.getOrDefault(JSONObject()))
            .put("signed_in", Ember.ready(c)).put("watch_hr", WatchHrRelay.status(c))
    }
}

class PhoneContextWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    /** Only used for an expedited run on Android 11 and older: a quiet, short-lived notification. */
    override suspend fun getForegroundInfo(): androidx.work.ForegroundInfo {
        val nm = applicationContext.getSystemService(android.app.NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(android.app.NotificationChannel("phone_check", "Bedside check", android.app.NotificationManager.IMPORTANCE_MIN))
        val n = androidx.core.app.NotificationCompat.Builder(applicationContext, "phone_check")
            .setSmallIcon(applicationContext.applicationInfo.icon).setContentTitle("Checking the bedside").setSilent(true).build()
        return androidx.work.ForegroundInfo(4107, n)
    }

    override suspend fun doWork(): Result {
        if (inputData.getBoolean("oncharge", false)) { PhoneSources.kick(applicationContext, 1); return Result.success() }
        return try { PhoneSources.send(applicationContext); Result.success() } catch (_: Exception) { Result.retry() }
            .also { PhoneSources.armOnCharge(applicationContext) }
    }
}

/** The night alarm: check in now (expedited) and set the next alarm. */
class NightAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        runCatching { if (PhoneSources.nightWindow()) PhoneSources.kickNow(c) }
        runCatching { PhoneSources.armNightAlarm(c) }
    }
}

/** After an app update or a reboot: re-arm the 15-min check and the on-charger trigger, and send once. */
class PhoneRestartReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action != Intent.ACTION_MY_PACKAGE_REPLACED && i.action != Intent.ACTION_BOOT_COMPLETED) return
        runCatching { PhoneSources.schedule(c); PhoneSources.kick(c) }
    }
}

/** The next alarm changed (an allowed background broadcast): send it right away. */
class PhoneAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action == AlarmManager.ACTION_NEXT_ALARM_CLOCK_CHANGED && "alarm" in PhoneSources.enabled(c)) PhoneSources.kick(c)
    }
}

@Suppress("unused") private fun Double.abs() = abs(this)
