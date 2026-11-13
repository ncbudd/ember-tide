package app.embertide

/**
 * When to send the phone's context because it was put on (or taken off) the charger. Pure, so it can be tested.
 *
 * JobScheduler's "requires charging" constraint is unreliable (it can wait many minutes, or for "stable" charging,
 * on a wireless charger or with battery protection on), so in the evening a short chained alarm reads the plug state
 * itself and calls this to decide whether to send now.
 */
object PlugPolicy {
    enum class Action { NONE, PLUG_CHANGED, PERIODIC }

    const val PLUG_START_HOUR = 19      // evening window: 19:00 up to (not including) 02:00 local
    const val PLUG_END_HOUR = 2
    const val FAST_MIN = 5L             // check interval inside the window
    const val NIGHT_MIN = 15L           // check interval for the rest of the night (and the normal send cadence)
    const val PERIODIC_DUE_MIN = 13L    // an inexact alarm drifts a little: a send is due after this long

    fun inPlugWindow(hour: Int) = hour >= PLUG_START_HOUR || hour < PLUG_END_HOUR
    fun inNightWindow(hour: Int) = hour >= 19 || hour < 11

    /** Minutes until the next night alarm: 5 in the evening window when the plug state matters, else 15 at night;
     *  null outside the night (the caller sets the alarm for 19:00). */
    fun nextCheckMinutes(hour: Int, watchesPlug: Boolean): Long? = when {
        watchesPlug && inPlugWindow(hour) -> FAST_MIN
        inNightWindow(hour) -> NIGHT_MIN
        else -> null
    }

    /**
     * @param lastSentPlugged the plug state in the last report that got through (null: none yet)
     * @param plugged the plug state now
     * @param watchesPlug the "charging" or "dial" sharing switch is on
     * @param minutesSinceLastCheckIn minutes since the last night check-in was started (large when never)
     */
    fun decide(lastSentPlugged: Boolean?, plugged: Boolean, watchesPlug: Boolean, hour: Int, minutesSinceLastCheckIn: Long): Action {
        if (watchesPlug && inPlugWindow(hour)) {
            // changed since the server was told; with nothing sent yet, "on the charger" is news but "off" is not
            val changed = if (lastSentPlugged == null) plugged else lastSentPlugged != plugged
            if (changed) return Action.PLUG_CHANGED
        }
        if (inNightWindow(hour) && minutesSinceLastCheckIn >= PERIODIC_DUE_MIN) return Action.PERIODIC
        return Action.NONE
    }
}
