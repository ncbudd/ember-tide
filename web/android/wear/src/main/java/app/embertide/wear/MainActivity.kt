package app.embertide.wear

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.wear.compose.material3.AppScaffold
import androidx.wear.compose.material3.ColorScheme
import androidx.wear.compose.material3.MaterialTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch


private val NIGHT = Color(0xFF100E12)
private val CARD = Color(0xFF252029)
private val TEXT = Color(0xFFF2EADF)
private val MUTE = Color(0xFFA39AA4)
private val ERR = Color(0xFFF2B8B5)

private fun scheme(accent: Color) = ColorScheme(
    primary = accent, onPrimary = NIGHT,
    primaryContainer = CARD, onPrimaryContainer = TEXT,
    secondary = accent, onSecondary = NIGHT,
    secondaryContainer = CARD, onSecondaryContainer = TEXT,
    background = NIGHT, onBackground = TEXT,
    surfaceContainer = CARD, onSurface = TEXT, onSurfaceVariant = MUTE,
    outline = MUTE, error = ERR, onError = NIGHT,
)

/**
 * The watch app, as pages you swipe between (see Pages.kt): your side of the bed with its times, temperature and on/off;
 * hot and cold flash; the partner's side (read-only, only if the server sends it); settings with "Heart rate at night".
 * The tile and the phone show the same things; everything goes through the same server calls.
 */
class MainActivity : ComponentActivity() {
    private val model: BedModel by viewModels()
    private var hrTick by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // fresh data when you look, every 30 seconds while you do, and nothing at all when the screen is off or the app is away
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                hrTick++
                while (true) { model.refresh(); delay(30_000) }
            }
        }
        setContent {
            val s = model.snap
            val accent = Color(WatchLogic.colorArgb(s?.me?.color ?: "teal"))
            MaterialTheme(colorScheme = scheme(accent)) {
                AppScaffold(modifier = Modifier.background(NIGHT)) {
                    WatchPages(model, hrTick, ::hrNext)
                }
            }
        }
    }

    // ---------- "Heart rate at night": permission, then "all the time" (Health Services needs it with the screen off) ----------
    private fun asked(p: String) = getSharedPreferences("embertide_hr", MODE_PRIVATE).getBoolean("asked:$p", false)
    private fun mark(p: String) = getSharedPreferences("embertide_hr", MODE_PRIVATE).edit().putBoolean("asked:$p", true).apply()

    private fun openSettings() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
    }

    private fun hrNext() {
        when (WatchHr.permState(this)) {
            "granted" -> {}
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

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        hrTick++
        val app = applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            WatchHr.apply(app)                 // the night may already be on
            WatchHr.reportPermission(app)      // let the phone show "allowed on the watch"
        }
    }
}
