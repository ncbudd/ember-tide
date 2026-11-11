package app.embertide

import java.util.Arrays

/**
 * The decisions behind a Health Connect sync, kept free of Android types so they can be unit-tested on the JVM
 * (HealthMergeTest). HealthSync does the reading and sending.
 *
 * Why the read window looks back so far: Samsung Health (and other watch apps) write a night's heart rate, blood
 * oxygen and sleep session into Health Connect hours after they were recorded, often only once the watch syncs in the
 * morning. Health Connect finds series and session records by their START time, and a heart-rate record can span an
 * hour or more. A sync that only looks back to the previous sync never sees records written late with earlier start
 * times, so whole stretches of the night were lost. Every sync now re-reads the last 48 hours (plus a day for records
 * that started earlier); the server drops what it already has.
 */
object HealthMerge {
    const val HOUR_MS = 3_600_000L
    const val DAY_MS = 24 * HOUR_MS
    const val FIRST_DAYS = 7L                 // first sync, and "Re-import last 7 days"
    const val TRAIL_MS = 48 * HOUR_MS         // every sync re-reads at least this far back
    const val OVERLAP_MS = 2 * HOUR_MS        // and a little before the previous sync, if that was longer ago
    const val RECORD_SPAN_MS = DAY_MS         // records are matched by start time: start the query this much earlier
    const val COPY_S = 30.0                   // a copying app's reading within half a minute of the original app's is a copy

    /** Start of the window this sync must cover (epoch ms). */
    fun readFrom(nowMs: Long, lastMs: Long, full: Boolean): Long =
        if (full || lastMs <= 0L) nowMs - FIRST_DAYS * DAY_MS else minOf(lastMs - OVERLAP_MS, nowMs - TRAIL_MS)

    /** Where to start the Health Connect query so a record that began before the window but runs into it is found. */
    fun queryFrom(fromMs: Long): Long = fromMs - RECORD_SPAN_MS

    /** One series of readings from one app: (seconds, value) pairs. */
    class Pts(val metric: String, val fromSyncApp: Boolean, val points: MutableList<Pair<Double, Double>>)

    /**
     * Apps such as Health Sync copy another app's readings into Health Connect, so the same reading can be there
     * twice. Drop a copying app's reading only when an original app has a reading at the same moment (within
     * COPY_S); anything only the copying app has is kept. Returns how many were dropped.
     */
    fun dropCopies(all: Collection<Pts>): Int {
        var dropped = 0
        for ((_, group) in all.groupBy { it.metric }) {
            val originals = group.filter { !it.fromSyncApp }.flatMap { s -> s.points.map { it.first } }.toDoubleArray()
            if (originals.isEmpty()) continue
            Arrays.sort(originals)
            for (s in group) {
                if (!s.fromSyncApp) continue
                val before = s.points.size
                s.points.retainAll { p -> !hasNear(originals, p.first, COPY_S) }
                dropped += before - s.points.size
            }
        }
        return dropped
    }

    /** Is there a value in `sorted` within `tol` of `t`? */
    fun hasNear(sorted: DoubleArray, t: Double, tol: Double): Boolean {
        var i = Arrays.binarySearch(sorted, t - tol)
        if (i < 0) i = -i - 1
        return i < sorted.size && sorted[i] <= t + tol
    }

    /** What matters about a sleep session for choosing and re-sending it. */
    class Session(val id: String, val startMs: Long, val endMs: Long, val modifiedMs: Long, val fromSyncApp: Boolean, val stages: Int)

    /**
     * One session per night: the watch app's own over a copying app's, then the one with more stage detail. A session
     * overlapping more than half of one already kept is the same night.
     */
    fun pickSessions(all: List<Session>): List<Session> {
        val ordered = all.sortedWith(compareBy<Session> { if (it.fromSyncApp) 1 else 0 }.thenByDescending { it.stages })
        val kept = ArrayList<Session>()
        for (r in ordered) {
            val len = (r.endMs - r.startMs).coerceAtLeast(1L)
            val same = kept.any { k -> minOf(k.endMs, r.endMs) - maxOf(k.startMs, r.startMs) > 0.5 * len }
            if (!same) kept += r
        }
        return kept.sortedBy { it.startMs }
    }

    /** Send a session unless this exact version (same id, same last change) was sent before. */
    fun needsSending(s: Session, sent: Map<String, Long>, full: Boolean): Boolean = full || sent[s.id] != s.modifiedMs
}
