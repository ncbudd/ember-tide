package app.embertide.wear

import android.os.SystemClock
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.foundation.pager.HorizontalPager
import androidx.wear.compose.foundation.pager.rememberPagerState
import androidx.wear.compose.material3.Button
import androidx.wear.compose.material3.ButtonDefaults
import androidx.wear.compose.material3.HorizontalPagerScaffold
import androidx.wear.compose.material3.Icon
import androidx.wear.compose.material3.IconButton
import androidx.wear.compose.material3.IconButtonDefaults
import androidx.wear.compose.material3.ScreenScaffold
import androidx.wear.compose.material3.Text
import kotlinx.coroutines.delay

private val TEXT = Color(0xFFF2EADF)
private val MUTE = Color(0xFFA39AA4)
private val ERR = Color(0xFFF2B8B5)

/** The watch app's pages (swipe sideways): 1 the bed, 2 flashes, 3 the partner's side (only if the server shares it), 4 settings. */
@Composable
fun WatchPages(m: BedModel, hrTick: Int, onHr: () -> Unit) {
    val snap = m.snap
    // a ticking clock, only while something on the screen counts down
    var elapsed by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    val flashOn = snap != null && WatchLogic.flashing(snap.me, snap.nowS, elapsed - m.fetchedAt)
    LaunchedEffect(flashOn, m.fetchedAt) {
        elapsed = SystemClock.elapsedRealtime()
        while (true) { delay(if (flashOn) 1000 else 20_000); elapsed = SystemClock.elapsedRealtime() }
    }
    val since = if (snap == null) 0L else elapsed - m.fetchedAt
    val hasPartner = snap?.partner != null
    val count = if (hasPartner) 4 else 3
    val pager = rememberPagerState(pageCount = { count })
    // page 1's layout: the in-bed one at once; back to the full one only after "not in bed" has held for about one refresh
    var layout by remember { mutableStateOf(WatchLogic.Layout.Full) }
    var absentAt by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(m.fetchedAt) {
        if (snap == null) return@LaunchedEffect
        val inBed = snap.me.inBed == true
        val t = SystemClock.elapsedRealtime()
        if (inBed) absentAt = null else if (absentAt == null) absentAt = t
        layout = WatchLogic.layoutFor(layout, inBed, absentAt?.let { t - it } ?: 0L)
        if (layout == WatchLogic.Layout.InBed && !inBed) {
            delay(WatchLogic.OUT_HOLD_MS)
            layout = WatchLogic.layoutFor(layout, false, SystemClock.elapsedRealtime() - (absentAt ?: t))
        }
    }
    val bedMode = snap != null && layout == WatchLogic.Layout.InBed
    HorizontalPagerScaffold(pagerState = pager) {
        HorizontalPager(state = pager, modifier = Modifier.fillMaxSize()) { page ->
            when {
                page == 0 -> Crossfade(targetState = bedMode, animationSpec = tween(400), label = "bedLayout") { bed ->
                    if (bed) InBedPage(m, since, flashOn) else MainPage(m, since)
                }
                page == 1 -> FlashPage(m, since, flashOn)
                page == 2 && hasPartner -> PartnerPage(m)
                else -> SettingsPage(m, hrTick, onHr)
            }
        }
    }
}

/** Keeps content inside the round screen: more room at the top (the clock) and the bottom (the page dots). */
@Composable
private fun RoundPage(modifier: Modifier = Modifier, content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    val h = LocalConfiguration.current.screenHeightDp
    val w = LocalConfiguration.current.screenWidthDp
    Column(modifier.fillMaxSize().padding(top = (h * 0.075f).dp, bottom = (h * 0.115f).dp, start = (w * 0.07f).dp, end = (w * 0.07f).dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally, content = content)
}

// ---------- page 1: the bed ----------

@Composable
private fun MainPage(m: BedModel, since: Long) {
    val snap = m.snap
    var crown by remember { mutableStateOf(false) }
    var confirmOff by remember { mutableStateOf(false) }
    LaunchedEffect(confirmOff) { if (confirmOff) { delay(4000); confirmOff = false } }
    val focus = remember { FocusRequester() }
    LaunchedEffect(crown) { if (crown) { focus.requestFocus(); delay(10_000); crown = false } }
    var crownAcc by remember { mutableStateOf(0f) }

    if (snap == null) {
        RoundPage {
            val (title, body) = when (m.problem) {
                Problem.NotSignedIn -> "Sign in on your phone" to "Open Ember & Tide on your phone. It hands your bed to this watch by itself."
                Problem.SignedOut -> "Sign in on your phone" to "This watch was signed out. Open Ember & Tide on your phone to sign it in again."
                Problem.NoSide -> "No side found" to "This account has no side of the bed."
                Problem.Unreachable -> "Can't reach your bed right now" to "Check that your phone or Wi-Fi can reach your server."
                null -> "Ember & Tide" to "Loading…"
            }
            Text(title, color = TEXT, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(4.dp))
            Text(body, color = MUTE, fontSize = 12.sp, textAlign = TextAlign.Center, maxLines = 4, overflow = TextOverflow.Ellipsis)
            if (m.problem == Problem.Unreachable) { Spacer(Modifier.height(6.dp)); Wide("Try again", fraction = 0.8f) { m.refresh() } }
        }
        return
    }

    val me = snap.me
    val accent = Color(WatchLogic.colorArgb(me.color))
    val on = WatchLogic.isOn(me)
    val canDown = !m.busy && if (on) WatchLogic.canStep(me, snap, m.pending, -1) else WatchLogic.canPresetStep(me, snap, m.preset, -1)
    val canUp = !m.busy && if (on) WatchLogic.canStep(me, snap, m.pending, 1) else WatchLogic.canPresetStep(me, snap, m.preset, 1)
    val plan = WatchLogic.powerAction(me)

    RoundPage {
        Text(me.name, color = accent, fontSize = 12.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(WatchLogic.stateLine(me, snap, since), color = TEXT, fontSize = 14.sp, textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis)
        // one line for whatever matters most: a result, a problem, or the times
        val toast = m.toast
        val info: Pair<String, Color>? = when {
            toast != null -> toast.first to (if (toast.second) accent else ERR)
            m.problem == Problem.Unreachable -> "Can't reach your bed right now · ${WatchLogic.ageText(since).lowercase()}" to ERR
            m.problem != null -> WatchLogic.ageText(since) to ERR
            WatchLogic.isStale(since) -> WatchLogic.ageText(since) to MUTE
            else -> WatchLogic.timesLine(me, snap)?.let { it.replace(" · ", "\n") to MUTE }   // two short lines, never a stray "PM"
        }
        Box(Modifier.fillMaxWidth().height(30.dp), contentAlignment = Alignment.Center) {
            info?.let { Text(it.first, color = it.second, fontSize = 11.sp, lineHeight = 13.sp, textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis) }
        }
        // temperature: big number, - and +; tap the number to use the crown (only while the side is on)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
            StepButton("−", canDown) { m.nudge(-1) }
            Column(Modifier.weight(1f).height(52.dp)
                .clip(CircleShape).clickable(enabled = on) { crown = !crown }
                .onRotaryScrollEvent { e ->
                    if (!crown || !on) false else {
                        crownAcc += e.verticalScrollPixels
                        while (crownAcc >= 60f) { crownAcc -= 60f; m.nudge(1) }
                        while (crownAcc <= -60f) { crownAcc += 60f; m.nudge(-1) }
                        crown = true
                        true
                    }
                }
                .focusRequester(focus).focusable(),
                verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                Text(WatchLogic.shownTemp(me, m.pending, m.preset, snap), color = if (on) accent else MUTE.copy(alpha = 0.8f), fontSize = 32.sp, lineHeight = 34.sp,
                    fontWeight = FontWeight.Medium, maxLines = 1)
                if (!on) Text("Off", color = MUTE, fontSize = 10.sp, lineHeight = 11.sp, maxLines = 1)
                else if (crown) Text("Turn the crown", color = MUTE, fontSize = 10.sp, lineHeight = 11.sp, maxLines = 1)
            }
            StepButton("+", canUp) { m.nudge(1) }
        }
        Spacer(Modifier.height(2.dp))
        if (confirmOff) Wide("Tap to confirm", icon = R.drawable.ic_power, enabled = !m.busy, fraction = 0.86f) { confirmOff = false; m.power() }
        else Wide(WatchLogic.powerLabel(me), icon = R.drawable.ic_power, enabled = !m.busy, fraction = 0.86f) {
            if (plan == WatchLogic.Power.OffTonight) confirmOff = true else m.power()
        }
    }
}

// ---------- page 1 while you are in bed: a big temperature, - and +, one on/off ----------

private val BED_TEXT = Color(0xFFE9DFCF)      // warm off-white, a little softer than the day text
private val BED_RED = Color(0xFFC0443C)       // dim red for "Red text on the night screen"
private val BED_BTN = Color(0xFF1B171D)       // dark buttons, no bright blocks
private val BED_DIM = Color(0xFF857D86)

@Composable
private fun InBedPage(m: BedModel, since: Long, flashOn: Boolean) {
    val snap = m.snap ?: return
    val me = snap.me
    val ink = if (me.simpleRed) BED_RED else BED_TEXT
    val dim = if (me.simpleRed) BED_RED.copy(alpha = 0.55f) else BED_DIM
    val on = WatchLogic.isOn(me)
    val canDown = !m.busy && if (on) WatchLogic.canStep(me, snap, m.pending, -1) else WatchLogic.canPresetStep(me, snap, m.preset, -1)
    val canUp = !m.busy && if (on) WatchLogic.canStep(me, snap, m.pending, 1) else WatchLogic.canPresetStep(me, snap, m.preset, 1)
    var confirmOff by remember { mutableStateOf(false) }
    LaunchedEffect(confirmOff) { if (confirmOff) { delay(4000); confirmOff = false } }
    // the crown turns the temperature straight away (no tap to arm), gathered like the taps
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    var crownAcc by remember { mutableStateOf(0f) }

    val toast = m.toast
    val status: Pair<String, Boolean>? = when {   // second = it is a problem (shown a little warmer)
        toast != null && !toast.second -> toast.first to true
        m.problem == Problem.Unreachable -> "Can't reach your bed" to true
        m.problem != null -> "Sign in on your phone" to true
        WatchLogic.isStale(since) -> WatchLogic.ageText(since) to false
        else -> null
    }
    val flash = me.flash
    RoundPage(Modifier
        .onRotaryScrollEvent { e ->
            crownAcc += e.verticalScrollPixels
            while (crownAcc >= 60f) { crownAcc -= 60f; m.nudge(1) }
            while (crownAcc <= -60f) { crownAcc += 60f; m.nudge(-1) }
            true
        }
        .focusRequester(focus).focusable()) {
        Box(Modifier.fillMaxWidth().height(16.dp), contentAlignment = Alignment.Center) {
            status?.let { Text(it.first, color = if (it.second) ERR.copy(alpha = 0.6f) else dim, fontSize = 10.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
        }
        Box(Modifier.fillMaxWidth().height(36.dp), contentAlignment = Alignment.Center) {
            if (flashOn && flash != null) {
                val left = WatchLogic.flashLeftMs(me, snap.nowS, since)
                Box(Modifier.height(32.dp).clip(CircleShape).background(BED_BTN).clickable(enabled = !m.busy) { m.stopFlash() }
                    .padding(horizontal = 14.dp), contentAlignment = Alignment.Center) {
                    Text(WatchLogic.flashLabel(flash.kind, left), color = ink, fontSize = 12.sp, maxLines = 1)
                }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            BedButton("−", canDown, 60.dp, ink) { m.nudge(-1) }
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(WatchLogic.shownTemp(me, m.pending, m.preset, snap), color = if (on) ink else dim, fontSize = 42.sp, lineHeight = 44.sp,
                    fontWeight = FontWeight.Medium, maxLines = 1)
            }
            BedButton("+", canUp, 60.dp, ink) { m.nudge(1) }
        }
        Spacer(Modifier.height(10.dp))
        val label = if (confirmOff) "Tap again" else WatchLogic.bedPowerLabel(me)
        Box(Modifier.fillMaxWidth(0.62f).height(60.dp).clip(CircleShape).background(BED_BTN)
            .clickable(enabled = !m.busy) {
                if (WatchLogic.needsOffConfirm(me) && !confirmOff) confirmOff = true else { confirmOff = false; m.power() }
            }, contentAlignment = Alignment.Center) {
            Text(label, color = ink.copy(alpha = if (m.busy) 0.5f else 1f), fontSize = 20.sp, fontWeight = FontWeight.Medium, maxLines = 1)
        }
    }
}

@Composable
private fun BedButton(label: String, enabled: Boolean, size: androidx.compose.ui.unit.Dp, ink: Color, onClick: () -> Unit) {
    Box(Modifier.size(size).clip(CircleShape).background(BED_BTN).clickable(enabled = enabled, onClick = onClick), contentAlignment = Alignment.Center) {
        Text(label, color = ink.copy(alpha = if (enabled) 1f else 0.3f), fontSize = 30.sp, maxLines = 1)
    }
}

// ---------- page 2: hot and cold flash ----------

@Composable
private fun FlashPage(m: BedModel, since: Long, flashOn: Boolean) {
    val snap = m.snap
    RoundPage {
        if (snap == null) {
            Text("Hot and cold flash", color = TEXT, fontSize = 15.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(4.dp))
            Text("Needs your bed. See the first page.", color = MUTE, fontSize = 12.sp, textAlign = TextAlign.Center)
            return@RoundPage
        }
        val f = snap.me.flash
        if (flashOn && f != null) {
            val hot = f.kind == "hot"
            val left = WatchLogic.flashLeftMs(snap.me, snap.nowS, since)
            Icon(painterResource(if (hot) R.drawable.ic_stat_flash else R.drawable.ic_sun), contentDescription = null,
                modifier = Modifier.size(28.dp), tint = Color(WatchLogic.colorArgb(snap.me.color)))
            Spacer(Modifier.height(2.dp))
            Text(WatchLogic.flashLabel(f.kind, left), color = TEXT, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center)
            Text(if (hot) "Cooling you down" else "Warming you up", color = MUTE, fontSize = 11.sp)
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(onClick = { m.extendFlash() }, enabled = !m.busy, modifier = Modifier.weight(1f).height(48.dp),
                    colors = ButtonDefaults.filledTonalButtonColors()) { Text("+5 min", maxLines = 1) }
                Button(onClick = { m.stopFlash() }, enabled = !m.busy, modifier = Modifier.weight(1f).height(48.dp),
                    colors = ButtonDefaults.filledTonalButtonColors()) { Text("Stop", maxLines = 1) }
            }
        } else {
            Text("Flash relief", color = MUTE, fontSize = 11.sp)
            Spacer(Modifier.height(4.dp))
            Wide("Hot flash", icon = R.drawable.ic_stat_flash, enabled = !m.busy, fraction = 0.92f) { m.flash("hot") }
            Spacer(Modifier.height(6.dp))
            Wide("Cold flash", icon = R.drawable.ic_sun, enabled = !m.busy, fraction = 0.92f) { m.flash("cold") }
            m.toast?.let { Spacer(Modifier.height(4.dp)); Text(it.first, color = if (it.second) Color(WatchLogic.colorArgb(snap.me.color)) else ERR, fontSize = 11.sp, textAlign = TextAlign.Center) }
        }
    }
}

// ---------- page 3: the partner's side (read-only) ----------

@Composable
private fun PartnerPage(m: BedModel) {
    val snap = m.snap ?: return
    val p = snap.partner ?: return
    val color = Color(WatchLogic.colorArgb(p.color))
    RoundPage {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.height(3.dp))
        Text(p.name, color = color, fontSize = 15.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(WatchLogic.stateLine(p, snap), color = TEXT, fontSize = 14.sp, textAlign = TextAlign.Center, maxLines = 2)
        p.targetC?.let { Text(WatchLogic.degrees(it, p.fahrenheit), color = color, fontSize = 32.sp, fontWeight = FontWeight.Medium, maxLines = 1) }
        WatchLogic.nextLine(p, snap)?.let { Text(it, color = MUTE, fontSize = 11.sp, textAlign = TextAlign.Center, maxLines = 2) }
        Spacer(Modifier.height(4.dp))
        Text("Their side, view only", color = MUTE, fontSize = 10.sp)
    }
}

// ---------- page 4: settings ----------

@Composable
private fun SettingsPage(m: BedModel, hrTick: Int, onHr: () -> Unit) {
    val ctx = LocalContext.current
    val list = rememberScalingLazyListState()
    val perm = remember(hrTick) { WatchHr.permState(ctx) }
    val wanted = remember(hrTick) { WatchHr.switchOn(ctx) }
    var info by remember { mutableStateOf(false) }
    val status = when {
        perm == "granted" && wanted -> "Allowed · on at night"
        perm == "granted" -> "Allowed"
        wanted -> "Needs your OK"
        else -> "Tap to set up"
    }
    val version = remember { runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: "" }
    val host = remember(m.snap) { runCatching { android.net.Uri.parse(WearApi.server(ctx)).host }.getOrNull().orEmpty() }
    ScreenScaffold(scrollState = list) { pad ->
        ScalingLazyColumn(state = list, contentPadding = pad, modifier = Modifier.fillMaxSize()) {
            item { Text("Settings", color = MUTE, fontSize = 12.sp, textAlign = TextAlign.Center) }
            item {
                Button(onClick = { info = !info }, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.filledTonalButtonColors(),
                    label = { Text("Heart rate at night", maxLines = 1) }, secondaryLabel = { Text(status, maxLines = 1) })
            }
            if (info) {
                item {
                    Text(when (perm) {
                        "granted" -> if (wanted) "At night your watch shares its heart rate with your bed, so it can tell you from a pet on your side."
                                     else "Turn on \"Watch heart rate (live)\" in the phone app's Settings to use it."
                        "needs_background" -> "One more step: allow it all the time, so it works with the screen off while you sleep."
                        else -> "Ember & Tide can use your watch's heart rate at night to tell you from a pet on your side of the bed. It's sent only to your own server, only at night."
                    }, color = MUTE, fontSize = 12.sp, textAlign = TextAlign.Center)
                }
            }
            if (perm != "granted") item { Wide(if (perm == "needs_background") "Allow all the time" else "Allow heart rate", onClick = onHr) }
            item {
                val line = when {
                    m.snap != null -> "Signed in as ${m.snap!!.me.name}"
                    m.problem == Problem.Unreachable -> "Signed in"
                    else -> "Not signed in"
                }
                Column(Modifier.fillMaxWidth().padding(top = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(line, color = TEXT, fontSize = 13.sp, textAlign = TextAlign.Center)
                    if (host.isNotEmpty() && WearApi.ready(ctx)) Text(host, color = MUTE, fontSize = 11.sp, textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            if (m.snap == null && m.problem != Problem.Unreachable) item {
                Text("Sign in on your phone. Open Ember & Tide there and this watch follows.", color = MUTE, fontSize = 11.sp, textAlign = TextAlign.Center)
            }
            item { Text(if (version.isNotEmpty()) "Version $version" else "Ember & Tide", color = MUTE, fontSize = 11.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(bottom = 18.dp)) }
        }
    }
}

// ---------- pieces ----------

@Composable
private fun StepButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(48.dp),
        colors = IconButtonDefaults.filledTonalIconButtonColors()) { Text(label, fontSize = 24.sp, maxLines = 1) }
}

@Composable
private fun Wide(label: String, icon: Int? = null, enabled: Boolean = true, fraction: Float = 1f, onClick: () -> Unit) {
    val iconSlot: (@Composable androidx.compose.foundation.layout.BoxScope.() -> Unit)? =
        if (icon == null) null else ({ Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(22.dp)) })
    Button(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth(fraction).height(48.dp),
        colors = ButtonDefaults.filledTonalButtonColors(), icon = iconSlot,
        label = { Text(label, maxLines = 1) })
}
