package app.embertide

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SkinTemperatureRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.metadata.Device
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.units.TemperatureDelta
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.math.sin

/**
 * Debug only: writes a night from a pretend watch into Health Connect.
 *
 *   am broadcast -n app.embertide/.HealthTestData                      → a synthetic night, 23:00 → 07:00 last night
 *   am broadcast -n app.embertide/.HealthTestData --es file hc.json    → a real-shaped night from a JSON file in the
 *       app's external files dir (/sdcard/Android/data/app.embertide/files/hc.json):
 *       {"hr": [[epochSeconds, bpm], ...], "stages": [{"start": s, "end": s, "stage": "light|deep|rem|awake"}, ...]}
 *       moved forward by whole days so the night ends as recently as possible (time of day is kept).
 *       Optional: --ei shift_min N moves it a further N minutes (a watch with a wrong clock).
 */
class HealthTestData : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val pending = goAsync()
        val file = i.getStringExtra("file")
        val extraShift = i.getIntExtra("shift_min", 0) * 60L
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val recs = if (file != null) fromFile(c, file, extraShift) else synthetic()
                HealthSync.client(c).insertRecords(recs)
                android.util.Log.i("EmberTide", "test data written: ${recs.size} records" + (file?.let { " from $it" } ?: ""))
            } catch (e: Exception) {
                android.util.Log.w("EmberTide", "test data failed: $e")
            } finally {
                pending.finish()
            }
        }
    }

    private val meta = { Metadata.autoRecorded(Device(type = Device.TYPE_WATCH, manufacturer = "Test", model = "Watch")) }

    private fun synthetic(): List<Record> {
        val zone = ZoneId.systemDefault()
        val today = ZonedDateTime.now(zone).toLocalDate()
        val start = today.minusDays(1).atTime(23, 0).atZone(zone).toInstant()
        val end = today.atTime(7, 0).atZone(zone).toInstant()
        val off = { t: Instant -> zone.rules.getOffset(t) }
        val hr = (0 until 96).map { k -> HeartRateRecord.Sample(start.plusSeconds(k * 300L), (58 + 6 * sin(k / 9.0)).toLong()) }
        val skin = (0 until 48).map { k ->
            SkinTemperatureRecord.Delta(start.plusSeconds(k * 600L), TemperatureDelta.celsius(0.6 * sin(k / 10.0) + k * 0.01))
        }
        val kinds = listOf(SleepSessionRecord.STAGE_TYPE_LIGHT, SleepSessionRecord.STAGE_TYPE_DEEP, SleepSessionRecord.STAGE_TYPE_LIGHT,
            SleepSessionRecord.STAGE_TYPE_REM, SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED)
        val stages = (0 until 16).map { k ->
            val s = start.plusSeconds(k * 1800L)
            SleepSessionRecord.Stage(s, s.plusSeconds(1800L), kinds[k % kinds.size])
        }
        return listOf<Record>(
            HeartRateRecord(startTime = start, startZoneOffset = off(start), endTime = end, endZoneOffset = off(end), samples = hr, metadata = meta()),
            SkinTemperatureRecord(startTime = start, startZoneOffset = off(start), endTime = end, endZoneOffset = off(end), metadata = meta(),
                deltas = skin, baseline = null, measurementLocation = SkinTemperatureRecord.MEASUREMENT_LOCATION_WRIST),
            SleepSessionRecord(startTime = start, startZoneOffset = off(start), endTime = end, endZoneOffset = off(end), metadata = meta(),
                title = "Test night", notes = null, stages = stages),
        )
    }

    private fun fromFile(c: Context, name: String, extraShift: Long): List<Record> {
        val f = File(c.getExternalFilesDir(null), File(name).name)              // only the app's own folder
        val j = JSONObject(f.readText())
        val hrA = j.getJSONArray("hr")
        val stA = j.optJSONArray("stages")
        var last = 0L
        for (k in 0 until hrA.length()) last = maxOf(last, hrA.getJSONArray(k).getLong(0))
        if (stA != null) for (k in 0 until stA.length()) last = maxOf(last, stA.getJSONObject(k).getDouble("end").toLong())
        val now = Instant.now().epochSecond
        val days = (now - last) / 86400                                             // whole days: time of day stays real
        val shift = days * 86400 + extraShift
        val zone = ZoneId.systemDefault()
        val off = { t: Instant -> zone.rules.getOffset(t) }
        val out = mutableListOf<Record>()
        // heart rate: one record per hour (Health Connect caps samples per record)
        val pts = (0 until hrA.length()).map { hrA.getJSONArray(it) }
            .map { Instant.ofEpochSecond(it.getLong(0) + shift) to it.getDouble(1).toLong() }
            .filter { it.first.epochSecond <= now && it.second in 25..220 }
            .sortedBy { it.first }
        pts.groupBy { it.first.epochSecond / 3600 }.forEach { (_, g) ->
            val s = g.first().first
            val e = g.last().first.plusSeconds(1)
            out += HeartRateRecord(startTime = s, startZoneOffset = off(s), endTime = e, endZoneOffset = off(e),
                samples = g.map { HeartRateRecord.Sample(it.first, it.second) }, metadata = meta())
        }
        if (stA != null && stA.length() > 0) {
            val code = mapOf("awake" to SleepSessionRecord.STAGE_TYPE_AWAKE, "light" to SleepSessionRecord.STAGE_TYPE_LIGHT,
                "deep" to SleepSessionRecord.STAGE_TYPE_DEEP, "rem" to SleepSessionRecord.STAGE_TYPE_REM)
            val st = (0 until stA.length()).map { stA.getJSONObject(it) }.map {
                SleepSessionRecord.Stage(Instant.ofEpochSecond(it.getDouble("start").toLong() + shift),
                    Instant.ofEpochSecond(it.getDouble("end").toLong() + shift), code[it.getString("stage")] ?: SleepSessionRecord.STAGE_TYPE_SLEEPING)
            }.filter { it.endTime.epochSecond <= now }.sortedBy { it.startTime }
            if (st.isNotEmpty()) {
                val s = st.first().startTime
                val e = st.last().endTime
                out += SleepSessionRecord(startTime = s, startZoneOffset = off(s), endTime = e, endZoneOffset = off(e), metadata = meta(),
                    title = "Real night (test)", notes = null, stages = st)
            }
        }
        return out
    }
}
