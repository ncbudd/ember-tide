package app.embertide.wear

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * The watch app's one screen: "Heart rate at night". Explains what it's for and asks for the heart-rate permission
 * (then "all the time", which Health Services needs to deliver readings with the screen off). The phone's switch
 * (Settings → From this phone → Watch heart rate (live)) decides whether anything is collected.
 */
class HeartRateActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var button: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (resources.displayMetrics.widthPixels * 0.12).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad, pad, pad * 2)
        }
        col.addView(TextView(this).apply {
            text = "Heart rate at night"
            textSize = 17f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
        })
        status = TextView(this).apply {
            textSize = 14f
            setTextColor(Color.parseColor("#E8E2EC"))
            gravity = Gravity.CENTER
            setPadding(0, pad / 3, 0, pad / 3)
        }
        col.addView(status)
        button = Button(this).apply {
            textSize = 15f
            minHeight = (56 * resources.displayMetrics.density).toInt()
            setOnClickListener { next() }
        }
        col.addView(button, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.BLACK); addView(col) })
    }

    override fun onResume() { super.onResume(); refresh() }

    private fun refresh() {
        when (WatchHr.permState(this)) {
            "granted" -> {
                status.text = if (WatchHr.switchOn(this))
                    "Allowed. At night your watch shares its heart rate with your bed, so it can tell you from a pet on your side."
                else "Allowed. Turn on \"Watch heart rate (live)\" in the phone app's Settings to use it."
                button.text = "Done"
            }
            "needs_background" -> {
                status.text = "One more step: allow it all the time, so it works with the screen off while you sleep."
                button.text = "Allow all the time"
            }
            else -> {
                status.text = "Ember & Tide can use your watch's heart rate at night to tell you from a pet on your side of the bed. It's sent only to your own server, only at night."
                button.text = "Allow heart rate"
            }
        }
    }

    private fun next() {
        when (WatchHr.permState(this)) {
            "granted" -> finish()
            "needs_background" -> {
                val bg = WatchHr.bgPermission()
                if (bg != null && !shouldShowRequestPermissionRationale(bg) && asked(bg)) openSettings()
                else bg?.let { mark(it); requestPermissions(arrayOf(it), 2) }
            }
            else -> {
                val p = WatchHr.hrPermission()
                if (asked(p) && !shouldShowRequestPermissionRationale(p)) { openSettings(); return }
                mark(p)
                requestPermissions(arrayOf(p, Manifest.permission.ACTIVITY_RECOGNITION), 1)
            }
        }
    }

    private fun asked(p: String) = getSharedPreferences("embertide_hr", MODE_PRIVATE).getBoolean("asked:$p", false)
    private fun mark(p: String) = getSharedPreferences("embertide_hr", MODE_PRIVATE).edit().putBoolean("asked:$p", true).apply()

    private fun openSettings() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refresh()
        val app = applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            WatchHr.apply(app)                 // the night may already be on
            WatchHr.reportPermission(app)      // let the phone show "allowed on the watch"
        }
    }
}
