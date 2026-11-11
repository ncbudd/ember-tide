package app.embertide

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HealthMergeTest {
    private val h = HealthMerge.HOUR_MS
    private val now = 1_800_000_000_000L

    @Test
    fun everySyncLooksBackTwoDaysEvenRightAfterTheLastOne() {
        // the last sync was 5 minutes ago: the night's records written since then can start many hours earlier
        assertEquals(now - 48 * h, HealthMerge.readFrom(now, now - 5 * 60_000L, false))
        // a sync three days ago: from two hours before it
        assertEquals(now - 72 * h - 2 * h, HealthMerge.readFrom(now, now - 72 * h, false))
        // first sync and "Re-import last 7 days"
        assertEquals(now - 7 * 24 * h, HealthMerge.readFrom(now, 0L, false))
        assertEquals(now - 7 * 24 * h, HealthMerge.readFrom(now, now - h, true))
    }

    @Test
    fun aRecordThatStartedBeforeTheWindowIsStillFound() {
        // a heart-rate record from 01:00 to 02:00 written at 09:00; the previous sync was at 04:14. Health Connect matches
        // records by start time, so the query must start before 01:00 even though the window starts at 02:14 or earlier
        val lastSync = now - 5 * h
        val recordStart = now - 8 * h
        val q = HealthMerge.queryFrom(HealthMerge.readFrom(now, lastSync, false))
        assertTrue(q <= recordStart)
        assertTrue(HealthMerge.queryFrom(HealthMerge.readFrom(now, now - 60_000L, false)) <= now - 70 * h)
    }

    private fun pts(vararg t: Double) = t.map { Pair(it, 60.0) }.toMutableList()

    @Test
    fun copiesAreDroppedButReadingsOnlyTheCopyingAppHasAreKept() {
        val watch = HealthMerge.Pts("hr", false, pts(0.0, 60.0, 120.0))
        // Health Sync copied the first two (same moments) and has one reading the watch app doesn't (an hour later)
        val copy = HealthMerge.Pts("hr", true, pts(0.0, 60.0, 3600.0))
        val dropped = HealthMerge.dropCopies(listOf(watch, copy))
        assertEquals(2, dropped)
        assertEquals(listOf(3600.0), copy.points.map { it.first })
        assertEquals(3, watch.points.size)
    }

    @Test
    fun theOriginalIsNeverDroppedEvenWhenTheCopyHasMore() {
        // the old rule kept whichever app had more readings in each hour, which threw away the watch app's hour
        val watch = HealthMerge.Pts("hr", false, pts(0.0, 60.0))
        val copy = HealthMerge.Pts("hr", true, (0 until 100).map { Pair(it.toDouble(), 61.0) }.toMutableList())
        HealthMerge.dropCopies(listOf(watch, copy))
        assertEquals(2, watch.points.size)
        assertTrue(copy.points.all { it.first > 90.0 })
    }

    @Test
    fun differentMetricsDontDedupeEachOther() {
        val hr = HealthMerge.Pts("hr", false, pts(0.0))
        val spo2 = HealthMerge.Pts("spo2", true, pts(0.0))
        assertEquals(0, HealthMerge.dropCopies(listOf(hr, spo2)))
        // and with no original app at all, a copying app's readings are all kept
        val only = HealthMerge.Pts("hrv", true, pts(0.0, 1.0))
        assertEquals(0, HealthMerge.dropCopies(listOf(only)))
    }

    @Test
    fun oneSessionPerNightPreferringTheWatchApp() {
        val a = HealthMerge.Session("a", 0, 8 * h, 9 * h, true, 200)       // copy, more stages
        val b = HealthMerge.Session("b", 10 * 60_000L, 8 * h, 9 * h, false, 150)
        val nap = HealthMerge.Session("c", 14 * h, 15 * h, 15 * h, false, 5)
        val kept = HealthMerge.pickSessions(listOf(a, b, nap))
        assertEquals(listOf("b", "c"), kept.map { it.id })
    }

    @Test
    fun aSessionIsSentWhenNewOrChangedOnly() {
        val s = HealthMerge.Session("x", 0, 8 * h, 9 * h, false, 100)
        assertTrue(HealthMerge.needsSending(s, emptyMap(), false))
        assertFalse(HealthMerge.needsSending(s, mapOf("x" to 9 * h), false))
        assertTrue(HealthMerge.needsSending(s, mapOf("x" to 8 * h), false))      // the watch app updated it
        assertTrue(HealthMerge.needsSending(s, mapOf("x" to 9 * h), true))       // Re-import sends everything again
    }
}
