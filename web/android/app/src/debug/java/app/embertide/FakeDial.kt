package app.embertide

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/**
 * Debug only: the emulator has no Bluetooth beacons, so pretend a bedside dial was heard (counts for 10 min, and
 * lets the scan run outside the night window), then send right away.
 *
 *   am broadcast -n app.embertide/.FakeDial --ei minor 1 --ei rssi -60      → the left dial, close by
 *   am broadcast -n app.embertide/.FakeDial --ei minor 1 --ei rssi -80 --ei minor2 2 --ei rssi2 -90
 *   am broadcast -n app.embertide/.FakeDial --ez clear true                  → stop pretending
 */
class FakeDial : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val p = PhoneSources.prefs(c).edit()
        if (i.getBooleanExtra("clear", false)) {
            p.remove("fake_dials").remove("fake_dials_at").apply()
        } else {
            val now = System.currentTimeMillis() / 1000.0
            val arr = JSONArray()
            arr.put(JSONObject().put("minor", i.getIntExtra("minor", 1)).put("rssi", i.getIntExtra("rssi", -60)).put("seen_at", now))
            if (i.hasExtra("minor2")) arr.put(JSONObject().put("minor", i.getIntExtra("minor2", 2)).put("rssi", i.getIntExtra("rssi2", -90)).put("seen_at", now))
            p.putString("fake_dials", arr.toString()).putLong("fake_dials_at", System.currentTimeMillis()).apply()
        }
        PhoneSources.kick(c)
    }
}
