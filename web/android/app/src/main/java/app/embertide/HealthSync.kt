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
 * last 7 days; every later one re-reads at least the last 48 hours, because watch apps write a night's data into
 * Health Connect hours late (HealthMerge explains the window). The server drops what it already has.
 */
object HealthSync {
    private const val PROVIDER = "com.google.android.apps.healthdata"
    private const val TAG = "EmberHealth"
    private const val CHUNK = 20000
    private const val HOUR = 3600L
    private const val MAX_RECORDS = 500_000
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

    /** Every page of one record type (Health Connect returns at most a page at a time; the token fetches the next). */
    private suspend fun <T : Record> readAll(cl: HealthConnectClient, type: KClass<T>, from: Instant, to: Instant, cut: MutableSet<String>): List<T> {
        val out = ArrayList<T>()
        var token: String? = null
        do {
            val r = cl.readRecords(ReadRecordsRequest(type, TimeRangeFilter.between(from, to), pageSize = 5000, pageToken = token))
            out += r.records
            token = r.pageToken?.takeIf { it.isNotEmpty() }
        } while (token != null && out.size < MAX_RECORDS)
        if (token != null) cut += type.simpleName ?: "records"
        return out
    }

    /** What was read, per kind and per app, for the Settings card and the log: counts and time ranges only, never values. */
    private class Tally {
        private class Row { var records = 0; var samples = 0; var first = Long.MAX_VALUE; var last = Long.MIN_VALUE }
        private val map = LinkedHashMap<String, Row>()

        fun add(kind: String, origin: String, samples: Int, firstMs: Long, lastMs: Long) {
            val t = map.getOrPut("$kind|$origin") { Row() }
            t.records++
            t.samples += samples
            if (samples > 0) { t.first = minOf(t.first, firstMs); t.last = maxOf(t.last, lastMs) }
        }

        fun json(): JSONArray {
            val a = JSONArray()
            for ((k, t) in map) {
                val (kind, origin) = k.split("|", limit = 2)
                val o = JSONObject().put("kind", kind).put("from", origin).put("records", t.records).put("samples", t.samples)
                if (t.samples > 0) o.put("first", t.first).put("last", t.last)
                a.put(o)
            }
            return a
        }

        fun log(window: String) {
            android.util.Log.i(TAG, "sync window $window")
            for ((k, t) in map) {
                val range = if (t.samples > 0) "${Instant.ofEpochMilli(t.first)}..${Instant.ofEpochMilli(t.last)}" else "-"
                android.util.Log.i(TAG, "read $k: ${t.records} records, ${t.samples} samples, $range")
            }
        }
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
        val merge = HealthMerge.Pts(metric, pkg in SYNC_APPS, ArrayList())
        val points: MutableList<Pair<Double, Double>> get() = merge.points        // seconds, value
        fun add(t: Instant, v: Double) { points += Pair(t.toEpochMilli() / 1000.0, v) }
    }

    private fun series(map: MutableMap<String, Series>, metric: String, m: Metadata, relative: Boolean = false): Series {
        val dev = device(m)
        val pkg = m.dataOrigin.packageName
        return map.getOrPut("$metric|$dev|$pkg|$relative") { Series(metric, dev, pkg, relative) }
    }

    /**
     * Sum amounts (steps, calories) into hourly totals, stamped at the end of each hour. Only whole hours inside the
     * query are sent: the hour that isn't over yet comes on a later sync, and the first hour of the query may be
     * missing records that started before it (records are found by start time), so it's left to the sync that has it.
     */
    private fun hourly(s: Series, end: Instant, amount: Double, now: Instant, qFrom: Instant) {
        val h = Math.ceil(end.toEpochMilli() / 1000.0 / HOUR) * HOUR
        if (h > now.toEpochMilli() / 1000.0) return
        if (h - HOUR < qFrom.toEpochMilli() / 1000.0 + HOUR) return
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
        val from = Instant.ofEpochMilli(HealthMerge.readFrom(now.toEpochMilli(), last, full))
        val qFrom = Instant.ofEpochMilli(HealthMerge.queryFrom(from.toEpochMilli()))
        val cut = LinkedHashSet<String>()
        val tally = Tally()
        fun origin(m: Metadata) = appName(m.dataOrigin.packageName)

        val all = LinkedHashMap<String, Series>()
        val read = JSONObject()
        if (use("hr")) {
            val rs = readAll(cl, HeartRateRecord::class, qFrom, now, cut)
            read.put("heart_rate", rs.sumOf { it.samples.size })
            for (r in rs) {
                val s = series(all, "hr", r.metadata)
                r.samples.forEach { s.add(it.time, it.beatsPerMinute.toDouble()) }
                tally.add("heart_rate", origin(r.metadata), r.samples.size,
                    r.samples.minOfOrNull { it.time.toEpochMilli() } ?: 0L, r.samples.maxOfOrNull { it.time.toEpochMilli() } ?: 0L)
            }
        }
        if (use("hrv")) {
            val rs = readAll(cl, HeartRateVariabilityRmssdRecord::class, qFrom, now, cut)
            read.put("hrv", rs.size)
            rs.forEach { series(all, "hrv", it.metadata).add(it.time, it.heartRateVariabilityMillis); tally.add("hrv", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
        }
        if (use("resp_rate")) {
            val rs = readAll(cl, RespiratoryRateRecord::class, qFrom, now, cut)
            read.put("breathing", rs.size)
            rs.forEach { series(all, "resp_rate", it.metadata).add(it.time, it.rate); tally.add("breathing", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
        }
        if (use("spo2")) {
            val rs = readAll(cl, OxygenSaturationRecord::class, qFrom, now, cut)
            read.put("spo2", rs.size)
            rs.forEach { series(all, "spo2", it.metadata).add(it.time, it.percentage.value); tally.add("spo2", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
        }
        if (use("skin_temp") && SKIN in have) {
            val rs = readAll(cl, SkinTemperatureRecord::class, qFrom, now, cut)
            read.put("skin_temp", rs.sumOf { it.deltas.size })
            for (r in rs) {
                val base = r.baseline?.inCelsius
                // with a baseline we know the real temperature; without one it's a change from the person's usual
                val s = series(all, "skin_temp", r.metadata, relative = base == null)
                r.deltas.forEach { s.add(it.time, (base ?: 0.0) + it.delta.inCelsius) }
                tally.add("skin_temp", origin(r.metadata), r.deltas.size, r.startTime.toEpochMilli(), r.endTime.toEpochMilli())
            }
        }
        if (use("resting_hr")) {
            val rs = readAll(cl, RestingHeartRateRecord::class, qFrom, now, cut)
            read.put("resting_hr", rs.size)
            rs.forEach { series(all, "resting_hr", it.metadata).add(it.time, it.beatsPerMinute.toDouble()); tally.add("resting_hr", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
        }
        if (use("body_temp")) {
            var n = 0
            if (perm(BodyTemperatureRecord::class) in have) {
                val rs = readAll(cl, BodyTemperatureRecord::class, qFrom, now, cut)
                n += rs.size
                rs.forEach { series(all, "body_temp", it.metadata).add(it.time, it.temperature.inCelsius); tally.add("body_temp", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
            }
            if (perm(BasalBodyTemperatureRecord::class) in have) {
                val rs = readAll(cl, BasalBodyTemperatureRecord::class, qFrom, now, cut)
                n += rs.size
                rs.forEach { series(all, "body_temp", it.metadata).add(it.time, it.temperature.inCelsius); tally.add("body_temp", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
            }
            read.put("body_temp", n)
        }
        if (use("exercise")) {
            val rs = readAll(cl, ExerciseSessionRecord::class, qFrom, now, cut)
            read.put("exercise", rs.size)
            rs.forEach {
                val min = (it.endTime.toEpochMilli() - it.startTime.toEpochMilli()) / 60000.0
                if (min > 0) series(all, "exercise_min", it.metadata).add(it.endTime, Math.round(min * 10) / 10.0)
                tally.add("exercise", origin(it.metadata), 1, it.startTime.toEpochMilli(), it.endTime.toEpochMilli())
            }
        }
        if (use("steps")) {
            val rs = readAll(cl, StepsRecord::class, qFrom, now, cut)
            read.put("steps", rs.size)
            rs.forEach { hourly(series(all, "steps", it.metadata), it.endTime, it.count.toDouble(), now, qFrom); tally.add("steps", origin(it.metadata), 1, it.startTime.toEpochMilli(), it.endTime.toEpochMilli()) }
        }
        if (use("active_kcal")) {
            val rs = readAll(cl, ActiveCaloriesBurnedRecord::class, qFrom, now, cut)
            read.put("active_kcal", rs.size)
            rs.forEach { hourly(series(all, "active_kcal", it.metadata), it.endTime, it.energy.inKilocalories, now, qFrom); tally.add("active_kcal", origin(it.metadata), 1, it.startTime.toEpochMilli(), it.endTime.toEpochMilli()) }
        }
        if (use("caffeine")) {
            val rs = readAll(cl, NutritionRecord::class, qFrom, now, cut).filter { (it.caffeine?.inMilligrams ?: 0.0) > 0 }
            read.put("caffeine", rs.size)
            rs.forEach { series(all, "caffeine_mg", it.metadata).add(it.startTime, it.caffeine!!.inMilligrams); tally.add("caffeine", origin(it.metadata), 1, it.startTime.toEpochMilli(), it.startTime.toEpochMilli()) }
        }
        if (use("weight")) {
            val rs = readAll(cl, WeightRecord::class, qFrom, now, cut)
            read.put("weight", rs.size)
            rs.forEach { series(all, "weight_kg", it.metadata).add(it.time, Math.round(it.weight.inKilograms * 10) / 10.0); tally.add("weight", origin(it.metadata), 1, it.time.toEpochMilli(), it.time.toEpochMilli()) }
        }
        // the same reading copied by another app (Health Sync copying Samsung Health): keep the original
        val copyApps = all.values.filter { it.merge.fromSyncApp }.map { appName(it.pkg) }.toSortedSet()
        val dropped = HealthMerge.dropCopies(all.values.map { it.merge })
        all.values.removeAll { it.points.isEmpty() }

        val pid = Ember.person(c)
        val stored = JSONObject()
        val already = JSONObject()
        val warnings = LinkedHashSet<String>()             // the same night note comes back once per metric; keep one
        val notes = LinkedHashSet<String>()
        if (dropped > 0) notes += "$dropped readings were copies that ${copyApps.joinToString(" and ").ifEmpty { "another app" }} made of your watch's own, so only the originals were sent."
        var nights = 0
        for (s in all.values) {
            s.points.sortBy { it.first }
            var i = 0
            while (i < s.points.size) {
                val chunk = JSONArray()
                for (j in i until minOf(i + CHUNK, s.points.size)) chunk.put(JSONArray().put(s.points[j].first).put(s.points[j].second))
                i += CHUNK
                val body = JSONObject().put("device", s.device).put("metric", s.metric).put("points", chunk).put("platform", "android")
                if (s.metric == "skin_temp") body.put("unit", "C").put("relative", s.relative)
                if (s.metric == "body_temp") body.put("unit", "C")
                val rep = JSONObject(Ember.call(c, "POST", "/api/people/$pid/wearable", body.toString()))
                stored.put(s.metric, stored.optInt(s.metric) + rep.optInt("stored"))
                val dup = rep.optJSONObject("rejected")?.optInt("duplicate") ?: 0
                if (dup > 0) already.put(s.metric, already.optInt(s.metric) + dup)
                rep.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) warnings += "${s.device}: ${w.getString(k)}" }
                rep.optJSONArray("notes")?.let { n -> for (k in 0 until n.length()) notes += "${s.device}: ${n.getString(k)}" }
                nights += rep.optJSONArray("rechecked")?.length() ?: 0
            }
        }

        var sessions = 0
        var sessionsSame = 0
        var sessionsNoStages = 0
        val sentBefore = sentSessions(prefs.getString("hc_sessions_sent", null))
        val sentNow = HashMap<String, Long>()
        if (use("sleep")) {
            // Sleep sessions start the evening before and are often written the next morning: read them from a day
            // before the window, and send each one that's new or changed since it was last sent (by id + last change).
            val recs = readAll(cl, SleepSessionRecord::class, qFrom, now, cut)
            recs.forEach { tally.add("sleep", origin(it.metadata), it.stages.size, it.startTime.toEpochMilli(), it.endTime.toEpochMilli()) }
            read.put("sleep", recs.size)
            sessionsNoStages = recs.count { it.stages.isEmpty() }
            val withStages = recs.filter { it.stages.isNotEmpty() }
            val byId = withStages.associateBy { it.metadata.id }
            val picked = HealthMerge.pickSessions(withStages.map {
                HealthMerge.Session(it.metadata.id, it.startTime.toEpochMilli(), it.endTime.toEpochMilli(),
                    it.metadata.lastModifiedTime.toEpochMilli(), it.metadata.dataOrigin.packageName in SYNC_APPS, it.stages.size)
            })
            for (ps in picked) {
                val r = byId[ps.id] ?: continue
                if (!HealthMerge.needsSending(ps, sentBefore, full)) { sentNow[ps.id] = ps.modifiedMs; sessionsSame++; continue }
                val segs = JSONArray()
                r.stages.forEach { segs.put(JSONObject().put("start", it.startTime.toEpochMilli() / 1000.0).put("end", it.endTime.toEpochMilli() / 1000.0).put("stage", it.stage)) }
                val dev = device(r.metadata)
                val rep = try {
                    JSONObject(Ember.call(c, "POST", "/api/people/$pid/stages",
                        JSONObject().put("source", "health-connect").put("label", "$dev · Health Connect").put("segments", segs).toString()))
                } catch (e: Ember.HttpError) {
                    if (e.code !in 400..499) throw e
                    // the server can't use this one (e.g. no usable stages): say so, and don't send it again every sync
                    notes += "A sleep session from $dev on ${r.startTime.atZone(java.time.ZoneId.systemDefault()).toLocalDate()} couldn't be used, so the bed's estimate is shown for that night."
                    sentNow[ps.id] = ps.modifiedMs
                    continue
                }
                sessions++
                sentNow[ps.id] = ps.modifiedMs
                rep.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) warnings += "Sleep $dev: ${w.getString(k)}" }
            }
            if (sessionsNoStages > 0) notes += "$sessionsNoStages sleep session${if (sessionsNoStages > 1) "s" else ""} came without sleep stages, so the bed's estimate is used for ${if (sessionsNoStages > 1) "those nights" else "that night"}."
        }
        if (sessions > 0) stored.put("sleep_sessions", sessions)
        if (sessionsSame > 0) already.put("sleep_sessions", sessionsSame)
        if (cut.isNotEmpty()) notes += "Health Connect had more than could be read in one go (${cut.joinToString()}); the rest comes on the next sync."

        val window = "${from}..${now} (query from $qFrom)"
        tally.log(window)
        android.util.Log.i(TAG, "sent: stored $stored, already there $already, dropped copies $dropped")
        val report = JSONObject().put("ts", now.toEpochMilli()).put("from", from.toEpochMilli()).put("query_from", qFrom.toEpochMilli())
            .put("read", read).put("detail", tally.json())
            .put("stored", stored).put("already", already).put("copies_skipped", dropped)
            .put("warnings", JSONArray(warnings.toList())).put("notes", JSONArray(notes.toList())).put("nights_rechecked", nights)
        val sentJson = JSONObject()
        for ((k, v) in sentNow) sentJson.put(k, v)
        prefs.edit().putLong("hc_last", now.toEpochMilli()).putString("hc_report", report.toString())
            .putString("hc_sessions_sent", sentJson.toString()).apply()
        return report
    }

    /** The sessions sent so far, id → last change (epoch ms). Only sessions still inside the read window are kept. */
    private fun sentSessions(s: String?): Map<String, Long> {
        if (s.isNullOrEmpty()) return emptyMap()
        return try {
            val o = JSONObject(s)
            o.keys().asSequence().associateWith { o.optLong(it) }
        } catch (e: org.json.JSONException) { emptyMap() }
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
