package app.embertide

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.BasalBodyTemperatureRecord
import androidx.health.connect.client.records.BodyTemperatureRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.NutritionRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RespiratoryRateRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SkinTemperatureRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlin.reflect.KClass
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Reads what the phone's Health Connect has (watch or ring at night, plus optional day context) and hands it to the
 * server's import, which lines it up with the bed and drops duplicates. Only reads; never writes. The person picks
 * which kinds of data to share (Settings → Watch or ring); only those are asked for and read. First sync covers the
 * last 7 days, later ones pick up from the last sync.
 */
object HealthSync {
    private const val PROVIDER = "com.google.android.apps.healthdata"
    private const val FIRST_DAYS = 7L
    private const val OVERLAP_MS = 2 * 3600 * 1000L          // re-read a little; the server drops duplicates
    private const val CHUNK = 20000
    private const val HOUR = 3600L
    private val lock = Mutex()                         // one sync at a time (app open, Sync now and the 6-hour job can overlap)

    private fun perm(k: KClass<out Record>) = HealthPermission.getReadPermission(k)

    /** The kinds of data that can be shared. "night" = used by Autopilot; "day" = context for the tuning guide. */
    class Kind(val key: String, val group: String, val perms: List<String>)

    val KINDS: List<Kind> = listOf(
        Kind("hr", "night", listOf(perm(HeartRateRecord::class))),
        Kind("skin_temp", "night", listOf(perm(SkinTemperatureRecord::class))),
        Kind("hrv", "night", listOf(perm(HeartRateVariabilityRmssdRecord::class))),
        Kind("resp_rate", "night", listOf(perm(RespiratoryRateRecord::class))),
        Kind("spo2", "night", listOf(perm(OxygenSaturationRecord::class))),
        Kind("sleep", "night", listOf(perm(SleepSessionRecord::class))),
        Kind("resting_hr", "night", listOf(perm(RestingHeartRateRecord::class))),
        Kind("body_temp", "night", listOf(perm(BodyTemperatureRecord::class), perm(BasalBodyTemperatureRecord::class))),
        Kind("exercise", "day", listOf(perm(ExerciseSessionRecord::class))),
        Kind("steps", "day", listOf(perm(StepsRecord::class))),
        Kind("active_kcal", "day", listOf(perm(ActiveCaloriesBurnedRecord::class))),
        Kind("caffeine", "day", listOf(perm(NutritionRecord::class))),
        Kind("weight", "day", listOf(perm(WeightRecord::class))),
    )
    private val BY_KEY = KINDS.associateBy { it.key }
    private val DEFAULT_ON: Set<String> = KINDS.filter { it.group == "night" }.map { it.key }.toSet()
    const val BACKGROUND: String = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND
    val SKIN: String = perm(SkinTemperatureRecord::class)

    /** Apps that copy data from another app into Health Connect: the original wins when both have the same reading. */
    private val SYNC_APPS = setOf("nl.appyhapps.healthsync", "com.urbandroid.sleep", "com.healthsync")

    /** "available", "update" (Health Connect needs installing or updating) or "unavailable". */
    fun sdk(c: Context): String = when (HealthConnectClient.getSdkStatus(c, PROVIDER)) {
        HealthConnectClient.SDK_AVAILABLE -> "available"
        HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> "update"
        else -> "unavailable"
    }

    fun client(c: Context) = HealthConnectClient.getOrCreate(c, PROVIDER)

    private fun featureOn(cl: HealthConnectClient, f: Int) =
        cl.features.getFeatureStatus(f) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    /** Kinds this phone's Health Connect can offer (skin temperature isn't everywhere yet). */
    fun available(c: Context): List<String> {
        val skin = featureOn(client(c), HealthConnectFeatures.FEATURE_SKIN_TEMPERATURE)
        return KINDS.map { it.key }.filter { it != "skin_temp" || skin }
    }

    /** The kinds the person chose to share. */
    fun enabled(c: Context): Set<String> =
        Ember.prefs(c).getStringSet("hc_types", null)?.filter { it in BY_KEY }?.toSet() ?: DEFAULT_ON

    fun setEnabled(c: Context, keys: Collection<String>) {
        Ember.prefs(c).edit().putStringSet("hc_types", keys.filter { it in BY_KEY }.toSet()).apply()
    }

    /** Permissions to ask for: only the chosen kinds this phone offers, plus background reads where supported. */
    fun wanted(c: Context): Set<String> {
        val cl = client(c)
        val avail = available(c).toSet()
        val s = enabled(c).filter { it in avail }.flatMap { BY_KEY.getValue(it).perms }.toMutableSet()
        if (s.isNotEmpty() && featureOn(cl, HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND)) s += BACKGROUND
        return s
    }

    /** A kind counts as allowed when at least one of its permissions is granted (body temperature has two). */
    private fun allowed(kind: String, have: Set<String>) = BY_KEY[kind]?.perms?.any { it in have } == true

    suspend fun status(c: Context): JSONObject {
        val o = JSONObject().put("sdk", sdk(c))
        val p = Ember.prefs(c)
        o.put("last", p.getLong("hc_last", 0L))
        p.getString("hc_report", null)?.let { o.put("report", JSONObject(it)) }
        o.put("types", JSONArray(enabled(c).toList()))
        if (o.getString("sdk") != "available") return o
        val cl = client(c)
        val want = wanted(c)
        val have = cl.permissionController.getGrantedPermissions()
        val en = enabled(c)
        val avail = available(c)
        o.put("available", JSONArray(avail))
        o.put("allowed", JSONArray(KINDS.map { it.key }.filter { allowed(it, have) }))
        o.put("needs_permission", JSONArray(en.filter { it in avail && !allowed(it, have) }))
        o.put("granted", JSONArray(have.filter { it in want }.map { it.substringAfterLast('.') }))
        o.put("missing", JSONArray(want.filter { it !in have }.map { it.substringAfterLast('.') }))
        o.put("connected", KINDS.any { allowed(it.key, have) })
        o.put("skin_supported", "skin_temp" in avail)
        o.put("background", BACKGROUND in have)
        return o
    }

    private suspend fun <T : Record> readAll(cl: HealthConnectClient, type: KClass<T>, from: Instant, to: Instant): List<T> {
        val out = ArrayList<T>()
        var token: String? = null
        do {
            val r = cl.readRecords(ReadRecordsRequest(type, TimeRangeFilter.between(from, to), pageSize = 5000, pageToken = token))
            out += r.records
            token = r.pageToken
        } while (token != null && out.size < 200_000)
        return out
    }

    /** A friendly, stable name for where a reading came from. The server learns a clock correction per name. */
    private fun device(m: Metadata): String {
        val d = m.device
        val name = listOfNotNull(d?.manufacturer, d?.model).joinToString(" ").trim()
        if (name.isNotEmpty()) return name.take(60)
        return appName(m.dataOrigin.packageName)
    }

    private fun appName(pkg: String) = when (pkg) {
        "com.ouraring.oura" -> "Oura"
        "com.google.android.apps.fitness" -> "Google Fit"
        "com.fitbit.FitbitMobile" -> "Fitbit"
        "com.sec.android.app.shealth", "com.samsung.android.wear.shealth" -> "Samsung Health"
        "com.garmin.android.apps.connectmobile" -> "Garmin"
        "com.withings.wiscale2" -> "Withings"
        "com.whoop.android" -> "WHOOP"
        "nl.appyhapps.healthsync" -> "Health Sync"
        "com.google.android.apps.healthdata" -> "Health Connect"
        else -> pkg.substringAfterLast('.').take(60)
    }

    private class Series(val metric: String, val device: String, val pkg: String, val relative: Boolean = false) {
        val points = ArrayList<Pair<Double, Double>>()                 // seconds, value
        fun add(t: Instant, v: Double) { points += Pair(t.toEpochMilli() / 1000.0, v) }
        val fromSyncApp get() = pkg in SYNC_APPS
    }

    private fun series(map: MutableMap<String, Series>, metric: String, m: Metadata, relative: Boolean = false): Series {
        val dev = device(m)
        val pkg = m.dataOrigin.packageName
        return map.getOrPut("$metric|$dev|$pkg|$relative") { Series(metric, dev, pkg, relative) }
    }

    /**
     * The same readings often arrive twice: once from the app that recorded them and once from an app that copies
     * them (Health Sync copying Samsung Health, for example). Per metric and per hour, keep only the source with the
     * most readings in that hour; a tie goes to the original app over a copying one. Returns how many were dropped.
     */
    private fun dedupe(all: MutableMap<String, Series>): Int {
        var dropped = 0
        for ((_, group) in all.values.groupBy { it.metric }) {
            if (group.size < 2) continue
            val counts = HashMap<Long, HashMap<Series, Int>>()
            for (s in group) for ((t, _) in s.points) {
                val h = (t / HOUR).toLong()
                val m = counts.getOrPut(h) { HashMap() }
                m[s] = (m[s] ?: 0) + 1
            }
            val winner = counts.mapValues { (_, m) ->
                m.entries.maxWith(compareBy<Map.Entry<Series, Int>> { it.value }.thenBy { if (it.key.fromSyncApp) 0 else 1 }).key
            }
            for (s in group) {
                val before = s.points.size
                s.points.retainAll { (t, _) -> winner[(t / HOUR).toLong()] === s }
                dropped += before - s.points.size
            }
        }
        all.values.removeAll { it.points.isEmpty() }
        return dropped
    }

    /** Sum amounts (steps, calories) into hourly totals, stamped at the end of each hour. */
    private fun hourly(s: Series, end: Instant, amount: Double, now: Instant) {
        val h = Math.ceil(end.toEpochMilli() / 1000.0 / HOUR) * HOUR
        if (h > now.toEpochMilli() / 1000.0) return      // the hour isn't over: it's sent whole on a later sync (the overlap re-reads it)
        val i = s.points.indexOfLast { it.first == h }
        if (i >= 0) s.points[i] = Pair(h, s.points[i].second + amount) else s.points += Pair(h, amount)
    }

    /** One sync. Returns a short report (also saved for the Settings card). */
    suspend fun sync(c: Context, full: Boolean = false): JSONObject = lock.withLock { syncLocked(c, full) }

    private suspend fun syncLocked(c: Context, full: Boolean): JSONObject {
        if (!Ember.ready(c)) throw IllegalStateException("Sign in first")
        if (sdk(c) != "available") throw IllegalStateException("Health Connect isn't available on this phone")
        val cl = client(c)
        val have = cl.permissionController.getGrantedPermissions()
        val on = enabled(c)
        fun use(kind: String) = kind in on && allowed(kind, have)
        val prefs = Ember.prefs(c)
        val now = Instant.now()
        val last = prefs.getLong("hc_last", 0L)
        val from = if (full || last == 0L) now.minusSeconds(FIRST_DAYS * 86400) else Instant.ofEpochMilli(last - OVERLAP_MS)

        val all = LinkedHashMap<String, Series>()
        val read = JSONObject()
        if (use("hr")) {
            val rs = readAll(cl, HeartRateRecord::class, from, now)
            read.put("heart_rate", rs.sumOf { it.samples.size })
            for (r in rs) { val s = series(all, "hr", r.metadata); r.samples.forEach { s.add(it.time, it.beatsPerMinute.toDouble()) } }
        }
        if (use("hrv")) {
            val rs = readAll(cl, HeartRateVariabilityRmssdRecord::class, from, now)
            read.put("hrv", rs.size)
            rs.forEach { series(all, "hrv", it.metadata).add(it.time, it.heartRateVariabilityMillis) }
        }
        if (use("resp_rate")) {
            val rs = readAll(cl, RespiratoryRateRecord::class, from, now)
            read.put("breathing", rs.size)
            rs.forEach { series(all, "resp_rate", it.metadata).add(it.time, it.rate) }
        }
        if (use("spo2")) {
            val rs = readAll(cl, OxygenSaturationRecord::class, from, now)
            read.put("spo2", rs.size)
            rs.forEach { series(all, "spo2", it.metadata).add(it.time, it.percentage.value) }
        }
        if (use("skin_temp") && SKIN in have) {
            val rs = readAll(cl, SkinTemperatureRecord::class, from, now)
            read.put("skin_temp", rs.sumOf { it.deltas.size })
            for (r in rs) {
                val base = r.baseline?.inCelsius
                // with a baseline we know the real temperature; without one it's a change from the person's usual
                val s = series(all, "skin_temp", r.metadata, relative = base == null)
                r.deltas.forEach { s.add(it.time, (base ?: 0.0) + it.delta.inCelsius) }
            }
        }
        if (use("resting_hr")) {
            val rs = readAll(cl, RestingHeartRateRecord::class, from, now)
            read.put("resting_hr", rs.size)
            rs.forEach { series(all, "resting_hr", it.metadata).add(it.time, it.beatsPerMinute.toDouble()) }
        }
        if (use("body_temp")) {
            var n = 0
            if (perm(BodyTemperatureRecord::class) in have) {
                val rs = readAll(cl, BodyTemperatureRecord::class, from, now)
                n += rs.size
                rs.forEach { series(all, "body_temp", it.metadata).add(it.time, it.temperature.inCelsius) }
            }
            if (perm(BasalBodyTemperatureRecord::class) in have) {
                val rs = readAll(cl, BasalBodyTemperatureRecord::class, from, now)
                n += rs.size
                rs.forEach { series(all, "body_temp", it.metadata).add(it.time, it.temperature.inCelsius) }
            }
            read.put("body_temp", n)
        }
        if (use("exercise")) {
            val rs = readAll(cl, ExerciseSessionRecord::class, from, now)
            read.put("exercise", rs.size)
            rs.forEach {
                val min = (it.endTime.toEpochMilli() - it.startTime.toEpochMilli()) / 60000.0
                if (min > 0) series(all, "exercise_min", it.metadata).add(it.endTime, Math.round(min * 10) / 10.0)
            }
        }
        if (use("steps")) {
            val rs = readAll(cl, StepsRecord::class, from, now)
            read.put("steps", rs.size)
            rs.forEach { hourly(series(all, "steps", it.metadata), it.endTime, it.count.toDouble(), now) }
        }
        if (use("active_kcal")) {
            val rs = readAll(cl, ActiveCaloriesBurnedRecord::class, from, now)
            read.put("active_kcal", rs.size)
            rs.forEach { hourly(series(all, "active_kcal", it.metadata), it.endTime, it.energy.inKilocalories, now) }
        }
        if (use("caffeine")) {
            val rs = readAll(cl, NutritionRecord::class, from, now).filter { (it.caffeine?.inMilligrams ?: 0.0) > 0 }
            read.put("caffeine", rs.size)
            rs.forEach { series(all, "caffeine_mg", it.metadata).add(it.startTime, it.caffeine!!.inMilligrams) }
        }
        if (use("weight")) {
            val rs = readAll(cl, WeightRecord::class, from, now)
            read.put("weight", rs.size)
            rs.forEach { series(all, "weight_kg", it.metadata).add(it.time, Math.round(it.weight.inKilograms * 10) / 10.0) }
        }
        val dropped = dedupe(all)

        val pid = Ember.person(c)
        val stored = JSONObject()
        val warnings = LinkedHashSet<String>()             // the same night note comes back once per metric; keep one
        val notes = LinkedHashSet<String>()
        if (dropped > 0) notes += "Skipped $dropped readings that another app had already copied into Health Connect."
        var nights = 0
        for (s in all.values) {
            s.points.sortBy { it.first }
            var i = 0
            while (i < s.points.size) {
                val chunk = JSONArray()
                for (j in i until minOf(i + CHUNK, s.points.size)) chunk.put(JSONArray().put(s.points[j].first).put(s.points[j].second))
                i += CHUNK
                val body = JSONObject().put("device", s.device).put("metric", s.metric).put("points", chunk)
                if (s.metric == "skin_temp") body.put("unit", "C").put("relative", s.relative)
                if (s.metric == "body_temp") body.put("unit", "C")
                val rep = JSONObject(Ember.call(c, "POST", "/api/people/$pid/wearable", body.toString()))
                stored.put(s.metric, stored.optInt(s.metric) + rep.optInt("stored"))
                rep.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) warnings += "${s.device}: ${w.getString(k)}" }
                rep.optJSONArray("notes")?.let { n -> for (k in 0 until n.length()) notes += "${s.device}: ${n.getString(k)}" }
                nights += rep.optJSONArray("rechecked")?.length() ?: 0
            }
        }

        var sessions = 0
        if (use("sleep")) {
            // the same night can be in Health Connect twice (the watch app and a copying app): keep one per night,
            // preferring the original app, then the one with more stage detail
            val sess = readAll(cl, SleepSessionRecord::class, from, now).filter { it.stages.isNotEmpty() }
                .sortedWith(compareBy<SleepSessionRecord> { if (it.metadata.dataOrigin.packageName in SYNC_APPS) 1 else 0 }.thenByDescending { it.stages.size })
            val kept = ArrayList<SleepSessionRecord>()
            for (r in sess) {
                val overlaps = kept.any { k ->
                    val a = maxOf(k.startTime.toEpochMilli(), r.startTime.toEpochMilli())
                    val b = minOf(k.endTime.toEpochMilli(), r.endTime.toEpochMilli())
                    b - a > 0.5 * (r.endTime.toEpochMilli() - r.startTime.toEpochMilli())
                }
                if (!overlaps) kept += r
            }
            for (r in kept.sortedBy { it.startTime }) {
                if (!full && last > 0 && r.metadata.lastModifiedTime.toEpochMilli() < last) continue   // already sent, unchanged
                val segs = JSONArray()
                r.stages.forEach { segs.put(JSONObject().put("start", it.startTime.toEpochMilli() / 1000.0).put("end", it.endTime.toEpochMilli() / 1000.0).put("stage", it.stage)) }
                val dev = device(r.metadata)
                val rep = JSONObject(Ember.call(c, "POST", "/api/people/$pid/stages",
                    JSONObject().put("source", "health-connect").put("label", "$dev · Health Connect").put("segments", segs).toString()))
                sessions++
                rep.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) warnings += "Sleep $dev: ${w.getString(k)}" }
            }
        }
        if (sessions > 0) stored.put("sleep_sessions", sessions)

        val report = JSONObject().put("ts", now.toEpochMilli()).put("from", from.toEpochMilli()).put("read", read)
            .put("stored", stored).put("warnings", JSONArray(warnings.toList())).put("notes", JSONArray(notes.toList())).put("nights_rechecked", nights)
        prefs.edit().putLong("hc_last", now.toEpochMilli()).putString("hc_report", report.toString()).apply()
        return report
    }

    /** Background sync every 6 hours on any network (needs the background-read permission to see new data). */
    fun schedule(c: Context) {
        val req = PeriodicWorkRequestBuilder<Worker>(6, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(c).enqueueUniquePeriodicWork("hc-sync", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun unschedule(c: Context) = WorkManager.getInstance(c).cancelUniqueWork("hc-sync")

    class Worker(c: Context, p: WorkerParameters) : CoroutineWorker(c, p) {
        override suspend fun doWork(): Result = try {
            sync(applicationContext)
            Result.success()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: SecurityException) {
            Result.success()                       // no background permission: the app syncs when it's opened instead
        } catch (e: Exception) {
            if (Ember.ready(applicationContext)) {
                android.util.Log.w("EmberTide", "health sync failed: ${e.message}")
                Result.retry()
            } else Result.success()                // signed out: nothing to retry
        }
    }
}
