package app.embertide.wear

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.clickable
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.foundation.rotary.RotaryScrollableDefaults
import androidx.wear.compose.material3.AppScaffold
import androidx.wear.compose.material3.Button
import androidx.wear.compose.material3.ButtonDefaults
import androidx.wear.compose.material3.ColorScheme
import androidx.wear.compose.material3.Icon
import androidx.wear.compose.material3.IconButton
import androidx.wear.compose.material3.IconButtonDefaults
import androidx.wear.compose.material3.MaterialTheme
import androidx.wear.compose.material3.ScreenScaffold
import androidx.wear.compose.material3.Text
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
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
 * The watch app: your side of the bed. Temperature (buttons or the crown), hot and cold flash, off tonight / resume,
 * the partner's side (read-only, only if the server sends it), and "Heart rate at night". The tile and the phone
 * show the same things; everything goes through the same server calls.
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
                    val nav = rememberSwipeDismissableNavController()
                    SwipeDismissableNavHost(navController = nav, startDestination = "main") {
                        composable("main") { MainScreen(model, hrTick) { nav.navigate("hr") } }
                        composable("hr") { HrScreen(hrTick, ::hrNext) }
                    }
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

// ---------------------------------------------------------------------------------------------------------------------

@Composable
private fun MainScreen(m: BedModel, hrTick: Int, onHr: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val list = rememberScalingLazyListState()
    val snap = m.snap
    // a ticking clock, only while something on the screen counts down
    var elapsed by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    val flashOn = snap != null && WatchLogic.flashing(snap.me, snap.nowS, elapsed - m.fetchedAt)
    LaunchedEffect(flashOn, m.fetchedAt) {
        elapsed = SystemClock.elapsedRealtime()
        while (true) { delay(if (flashOn) 1000 else 20_000); elapsed = SystemClock.elapsedRealtime() }
    }
    val since = if (snap == null) 0L else elapsed - m.fetchedAt
    var crown by remember { mutableStateOf(false) }
    var confirmOff by remember { mutableStateOf(false) }
    LaunchedEffect(confirmOff) { if (confirmOff) { delay(4000); confirmOff = false } }
    val focus = remember { FocusRequester() }
    LaunchedEffect(crown) {
        if (crown) { focus.requestFocus(); delay(10_000); crown = false }
    }
    var crownAcc by remember { mutableStateOf(0f) }
    val perm = remember(hrTick) { WatchHr.permState(ctx) }
    val wanted = remember(hrTick) { WatchHr.switchOn(ctx) }

    ScreenScaffold(scrollState = list) { pad ->
        ScalingLazyColumn(
            state = list, contentPadding = pad, modifier = Modifier.fillMaxSize(),
            rotaryScrollableBehavior = if (crown) null else RotaryScrollableDefaults.behavior(list),
        ) {
            item { Spacer(Modifier.height(14.dp)) }       // clears the clock at the top of the round screen
            if (snap == null) {
                item { Info(when (m.problem) {
                    Problem.NotSignedIn -> "Sign in on your phone" to "Open Ember & Tide on your phone. It hands your bed to this watch by itself."
                    Problem.SignedOut -> "Sign in on your phone" to "This watch was signed out. Open Ember & Tide on your phone to sign it in again."
                    Problem.NoSide -> "No side found" to "This account has no side of the bed."
                    Problem.Unreachable -> "Can't reach your bed right now" to "Check that your phone or Wi-Fi can reach your server."
                    null -> "Ember & Tide" to "Loading…"
                }, loading = m.problem == null) }
                if (m.problem == Problem.Unreachable) item { Wide("Try again", onClick = { m.refresh() }) }
            } else {
                val me = snap.me
                val accent = Color(WatchLogic.colorArgb(me.color))
                item {
                    Column2 {
                        Text(me.name, color = accent, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        Text(WatchLogic.stateLine(me, snap, since), color = TEXT, fontSize = 15.sp, textAlign = TextAlign.Center)
                        WatchLogic.nextLine(me, snap)?.let { Text(it, color = MUTE, fontSize = 12.sp, textAlign = TextAlign.Center) }
                    }
                }
                m.toast?.let { (t, ok) -> item { Text(t, color = if (ok) accent else ERR, fontSize = 13.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()) } }
                if (m.problem != null || WatchLogic.isStale(since)) item {
                    Text(if (m.problem == Problem.Unreachable) "Can't reach your bed right now · ${WatchLogic.ageText(since).lowercase()}" else WatchLogic.ageText(since),
                        color = if (m.problem != null) ERR else MUTE, fontSize = 11.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
                }
                // temperature: big number, − and +; tap the number to use the crown
                item {
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
                        StepButton("−", "Cooler", enabled = !m.busy && WatchLogic.canStep(me, snap, m.pending, -1)) { m.nudge(-1) }
                        Column2(Modifier
                            .clip(CircleShape).clickable { crown = !crown }
                            .onRotaryScrollEvent { e ->
                                if (!crown) false else {
                                    crownAcc += e.verticalScrollPixels
                                    while (crownAcc >= 60f) { crownAcc -= 60f; m.nudge(1) }
                                    while (crownAcc <= -60f) { crownAcc += 60f; m.nudge(-1) }
                                    crown = true
                                    true
                                }
                            }
                            .focusRequester(focus).focusable()) {
                            Text(WatchLogic.bigTemp(me, m.pending, snap), color = if (WatchLogic.isOn(me)) accent else MUTE, fontSize = 38.sp, fontWeight = FontWeight.Medium)
                            if (crown) Text("Turn the crown", color = MUTE, fontSize = 10.sp)
                        }
                        StepButton("+", "Warmer", enabled = !m.busy && WatchLogic.canStep(me, snap, m.pending, 1)) { m.nudge(1) }
                    }
                }
                // flashes
                if (flashOn) {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { m.extendFlash() }, enabled = !m.busy, modifier = Modifier.weight(1f).height(52.dp),
                                colors = ButtonDefaults.filledTonalButtonColors()) { Text("+5 min", maxLines = 1) }
                            Button(onClick = { m.stopFlash() }, enabled = !m.busy, modifier = Modifier.weight(1f).height(52.dp),
                                colors = ButtonDefaults.filledTonalButtonColors()) { Text("Stop", maxLines = 1) }
                        }
                    }
                } else {
                    item { Wide("Hot flash", icon = R.drawable.ic_stat_flash, enabled = !m.busy) { m.flash("hot") } }
                    item { Wide("Cold flash", icon = R.drawable.ic_stat_flash, enabled = !m.busy) { m.flash("cold") } }
                }
                // on / off, same rules as the tile
                item {
                    val plan = WatchLogic.powerAction(me)
                    if (confirmOff) Wide("Tap again to confirm", icon = R.drawable.ic_power, enabled = !m.busy) { confirmOff = false; m.power() }
                    else Wide(WatchLogic.powerLabel(me), icon = R.drawable.ic_power, enabled = !m.busy) {
                        if (plan == WatchLogic.Power.OffTonight) confirmOff = true else m.power()
                    }
                }
                snap.partner?.let { p ->
                    item {
                        Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(8.dp).clip(CircleShape).background(Color(WatchLogic.colorArgb(p.color))))
                            Spacer(Modifier.size(6.dp))
                            Text(WatchLogic.partnerLine(p, snap), color = MUTE, fontSize = 12.sp, maxLines = 2, textAlign = TextAlign.Center)
                        }
                    }
                }
            }
            // heart rate row: always there, says where it stands
            item {
                val status = when {
                    perm == "granted" && wanted -> "Allowed · on at night"
                    perm == "granted" -> "Allowed"
                    wanted -> "Needs your OK"
                    else -> "Tap to set up"
                }
                Button(onClick = onHr, modifier = Modifier.fillMaxWidth().padding(top = 6.dp), colors = ButtonDefaults.filledTonalButtonColors(),
                    label = { Text("Heart rate at night", maxLines = 1) }, secondaryLabel = { Text(status, maxLines = 1) })
            }
        }
    }
}

@Composable
private fun Column2(modifier: Modifier = Modifier, content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) =
    androidx.compose.foundation.layout.Column(modifier.padding(horizontal = 6.dp), horizontalAlignment = Alignment.CenterHorizontally, content = content)

@Composable
private fun Info(text: Pair<String, String>, loading: Boolean) {
    Column2(Modifier.fillMaxWidth()) {
        Text(text.first, color = TEXT, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center)
        Spacer(Modifier.height(4.dp))
        Text(if (loading) "Loading…" else text.second, color = MUTE, fontSize = 13.sp, textAlign = TextAlign.Center)
    }
}

@Composable
private fun StepButton(label: String, desc: String, enabled: Boolean, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(52.dp),
        colors = IconButtonDefaults.filledTonalIconButtonColors()) { Text(label, fontSize = 26.sp, maxLines = 1) }
}

@Composable
private fun Wide(label: String, icon: Int? = null, enabled: Boolean = true, onClick: () -> Unit) {
    val iconSlot: (@Composable androidx.compose.foundation.layout.BoxScope.() -> Unit)? =
        if (icon == null) null else ({ Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(22.dp)) })
    Button(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth().height(52.dp),
        colors = ButtonDefaults.filledTonalButtonColors(), icon = iconSlot,
        label = { Text(label, maxLines = 1) })
}

@Composable
private fun HrScreen(tick: Int, next: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val list = rememberScalingLazyListState()
    val perm = remember(tick) { WatchHr.permState(ctx) }
    val wanted = remember(tick) { WatchHr.switchOn(ctx) }
    ScreenScaffold(scrollState = list) { pad ->
        ScalingLazyColumn(state = list, contentPadding = pad, modifier = Modifier.fillMaxSize()) {
            item { Text("Heart rate at night", color = TEXT, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center) }
            item {
                Text(when (perm) {
                    "granted" -> if (wanted) "Allowed. At night your watch shares its heart rate with your bed, so it can tell you from a pet on your side."
                                 else "Allowed. Turn on \"Watch heart rate (live)\" in the phone app's Settings to use it."
                    "needs_background" -> "One more step: allow it all the time, so it works with the screen off while you sleep."
                    else -> "Ember & Tide can use your watch's heart rate at night to tell you from a pet on your side of the bed. It's sent only to your own server, only at night."
                }, color = MUTE, fontSize = 13.sp, textAlign = TextAlign.Center)
            }
            if (perm != "granted") item {
                Wide(if (perm == "needs_background") "Allow all the time" else "Allow heart rate", onClick = next)
            }
        }
    }
}
