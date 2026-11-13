package app.embertide

import app.embertide.PlugPolicy.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlugPolicyTest {
    @Test fun windows() {
        assertTrue(PlugPolicy.inPlugWindow(19)); assertTrue(PlugPolicy.inPlugWindow(23)); assertTrue(PlugPolicy.inPlugWindow(1))
        assertFalse(PlugPolicy.inPlugWindow(2)); assertFalse(PlugPolicy.inPlugWindow(12)); assertFalse(PlugPolicy.inPlugWindow(18))
    }

    @Test fun checkIntervals() {
        assertEquals(5L, PlugPolicy.nextCheckMinutes(21, true))
        assertEquals(15L, PlugPolicy.nextCheckMinutes(21, false))      // only the heart-rate source: as before
        assertEquals(15L, PlugPolicy.nextCheckMinutes(4, true))        // after 02:00 the old cadence
        assertNull(PlugPolicy.nextCheckMinutes(14, true))              // by day nothing is scheduled
    }

    @Test fun pluggedInSendsAtOnce() {
        assertEquals(Action.PLUG_CHANGED, PlugPolicy.decide(false, true, true, 22, 2))
        assertEquals(Action.PLUG_CHANGED, PlugPolicy.decide(null, true, true, 22, 2))
    }

    @Test fun takenOffSendsOnce() {
        assertEquals(Action.PLUG_CHANGED, PlugPolicy.decide(true, false, true, 23, 2))
        assertEquals(Action.NONE, PlugPolicy.decide(false, false, true, 23, 2))     // already told
    }

    @Test fun noChangeNoSend() {
        assertEquals(Action.NONE, PlugPolicy.decide(true, true, true, 22, 5))
        assertEquals(Action.NONE, PlugPolicy.decide(null, false, true, 22, 5))      // nothing sent, nothing to say
    }

    @Test fun neverWhenNeitherSwitchIsOn() {
        assertEquals(Action.NONE, PlugPolicy.decide(false, true, false, 22, 2))
    }

    @Test fun outsideTheWindowThePlugIsLeftToTheNormalCadence() {
        assertEquals(Action.NONE, PlugPolicy.decide(false, true, true, 15, 2))
        assertEquals(Action.NONE, PlugPolicy.decide(false, true, true, 2, 2))
        assertEquals(Action.PERIODIC, PlugPolicy.decide(false, true, true, 3, 15))  // 15-min check-in as today
    }

    @Test fun periodicCheckInEveryFifteenMinutesAtNightOnly() {
        assertEquals(Action.PERIODIC, PlugPolicy.decide(true, true, true, 22, 14))
        assertEquals(Action.NONE, PlugPolicy.decide(true, true, true, 22, 5))
        assertEquals(Action.PERIODIC, PlugPolicy.decide(null, false, false, 5, Long.MAX_VALUE))
        assertEquals(Action.NONE, PlugPolicy.decide(true, true, true, 14, Long.MAX_VALUE))
    }
}
