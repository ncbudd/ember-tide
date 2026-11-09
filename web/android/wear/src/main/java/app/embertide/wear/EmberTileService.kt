package app.embertide.wear

import android.content.ComponentName
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders.dp
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.DimensionBuilders.sp
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.LayoutElementBuilders.Box
import androidx.wear.protolayout.LayoutElementBuilders.Column
import androidx.wear.protolayout.LayoutElementBuilders.FontStyle
import androidx.wear.protolayout.LayoutElementBuilders.Image
import androidx.wear.protolayout.LayoutElementBuilders.Row
import androidx.wear.protolayout.LayoutElementBuilders.Spacer
import androidx.wear.protolayout.LayoutElementBuilders.Text
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.guava.future
import org.json.JSONObject

/**
 * The watch tile: your name, the temperature, what it's doing, and two big buttons.
 * Normally Hot flash and on/off; during a flash they become "+5 min" and "Stop", the two things you want then.
 * A press is carried out first and confirmed with a short buzz, so the tile you see afterwards is the result.
 * While a flash runs the tile refreshes itself when it ends (otherwise every 10 minutes).
 */
class EmberTileService : TileService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private companion object {
        const val RES = "3"
        const val NIGHT = 0xFF100E12.toInt()
        const val TEXT = 0xFFF2EADF.toInt()
        const val MUTE = 0xFFA39AA4.toInt()
        const val TIDE = 0xFF5FB8B0.toInt()
        const val EMBER = 0xFFEE8A55.toInt()
        const val BTN = 0xFF252029.toInt()
    }

    private var renderId = 0L

    override fun onTileRequest(req: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> = scope.future {
        val c = applicationContext
        var note: String? = null
        if (WearApi.ready(c)) {
            val pid = WearApi.person(c)
            // button ids carry the render they came from ("hot#12"): a tile refresh that repeats an old click id
            // (some watches keep lastClickableId) never starts a second flash
            val clicked = req.currentState.lastClickableId
            val parts = clicked.split("#")
            val prefs = c.getSharedPreferences("embertide_tile", MODE_PRIVATE)
            val fresh = parts.size == 2 && (parts[1].toLongOrNull() ?: -1L) > prefs.getLong("handled", -1L)
            if (fresh) prefs.edit().putLong("handled", parts[1].toLong()).apply()
            val did: Result<String>? = if (!fresh) null else when (parts[0]) {
                "hot" -> runCatching { WearApi.call(c, "POST", "/api/people/$pid/flash", "{\"kind\":\"hot\",\"source\":\"watch\"}"); "Hot flash started" }
                "extend" -> runCatching { WearApi.call(c, "POST", "/api/people/$pid/flash/extend?source=watch"); "5 more minutes" }
                "stop" -> runCatching { WearApi.call(c, "POST", "/api/people/$pid/flash/stop?source=watch"); "Flash stopped" }
                "power" -> runCatching { togglePower() }
                else -> null
            }
            if (did != null) {
                note = did.getOrElse { "Couldn't reach it" }
                buzz(did.isSuccess)
                updateComplications()
            }
        }
        renderId = c.getSharedPreferences("embertide_tile", MODE_PRIVATE).let { pr ->
            (pr.getLong("render", 0L) + 1).also { pr.edit().putLong("render", it).apply() }
        }
        val v = WearApi.view(c)
        // during a flash, come back just after it ends; otherwise every 10 minutes
        val fresh = if (v.flashLeftMs > 0) (v.flashLeftMs + 5_000L).coerceIn(30_000L, 10 * 60_000L) else 10 * 60_000L
        TileBuilders.Tile.Builder()
            .setResourcesVersion(RES)
            .setFreshnessIntervalMillis(fresh)
            .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout(v, note)))
            .build()
    }

    override fun onTileResourcesRequest(req: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> = scope.future {
        fun img(id: Int) = ResourceBuilders.ImageResource.Builder()
            .setAndroidResourceByResId(ResourceBuilders.AndroidImageResourceByResId.Builder().setResourceId(id).build()).build()
        ResourceBuilders.Resources.Builder().setVersion(RES)
            .addIdToImageMapping("flash", img(R.drawable.ic_stat_flash))
            .addIdToImageMapping("power", img(R.drawable.ic_power))
            .addIdToImageMapping("stop", img(R.drawable.ic_stop))
            .build()
    }

    /** A short tick when it worked, a double buzz when it didn't (you're probably not looking at the screen). */
    private fun buzz(ok: Boolean) {
        val vib = getSystemService(Vibrator::class.java) ?: return
        if (!vib.hasVibrator()) return
        vib.vibrate(if (ok) VibrationEffect.createPredefined(VibrationEffect.EFFECT_CLICK)
                    else VibrationEffect.createWaveform(longArrayOf(0, 60, 80, 60), -1))
    }

    private fun updateComplications() {
        ComplicationDataSourceUpdateRequester.create(applicationContext, ComponentName(applicationContext, EmberComplicationService::class.java))
            .requestUpdateAll()
    }

    /**
     * Same rule as the phone widget: on by hand → off; off tonight / paused → back on; running → off for tonight;
     * otherwise (daytime, out of bed, done) → on by hand at your fall-asleep temperature, until the rhythm takes over.
     */
    private fun togglePower(): String {
        val c = applicationContext
        val pid = WearApi.person(c)
        fun me() = WearApi.me(c, JSONObject(WearApi.call(c, "GET", "/api/snapshot")))!!
        var p = me()
        var n = p.getJSONObject("night")
        val st = n.optString("state")
        if (n.optJSONObject("override")?.optBoolean("manual") == true) {
            // a by-hand session (a nap, or turned on in the morning) isn't part of tonight: "off tonight" wouldn't stop it
            WearApi.call(c, "POST", "/api/people/$pid/adjust/clear?source=watch")
            return "Turned off"
        }
        if (!n.isNull("target_c") && st != "skipped" && st != "paused") {
            WearApi.call(c, "POST", "/api/people/$pid/tonight", "{\"action\":\"skip\",\"source\":\"watch\"}")
            return "Off for tonight"
        }
        if (st == "skipped" || st == "paused") {
            WearApi.call(c, "POST", "/api/people/$pid/tonight", "{\"action\":\"resume\",\"source\":\"watch\"}")
            p = me(); n = p.getJSONObject("night")
            if (!n.isNull("target_c")) return "Back on"
        }
        val phases = p.optJSONArray("phases")
        val temp = if (phases != null && phases.length() > 0) phases.getJSONObject(0).getDouble("temp_c") else 21.1
        WearApi.call(c, "POST", "/api/people/$pid/adjust", "{\"temp_c\":$temp,\"until\":\"next_phase\",\"source\":\"watch\"}")
        return "On"
    }

    private fun text(s: String, size: Float, color: Int, weight: Int = LayoutElementBuilders.FONT_WEIGHT_NORMAL) =
        Text.Builder().setText(s).setMaxLines(1)
            .setFontStyle(FontStyle.Builder().setSize(sp(size)).setColor(argb(color)).setWeight(weight).build()).build()

    private fun button(id: String, label: String, content: LayoutElementBuilders.LayoutElement): LayoutElementBuilders.LayoutElement =
        Box.Builder().setWidth(dp(52f)).setHeight(dp(52f))
            .setModifiers(ModifiersBuilders.Modifiers.Builder()
                .setBackground(ModifiersBuilders.Background.Builder().setColor(argb(BTN))
                    .setCorner(ModifiersBuilders.Corner.Builder().setRadius(dp(26f)).build()).build())
                .setClickable(ModifiersBuilders.Clickable.Builder().setId("$id#$renderId").setOnClick(ActionBuilders.LoadAction.Builder().build()).build())
                .setSemantics(ModifiersBuilders.Semantics.Builder().setContentDescription(label).build())
                .build())
            .addContent(content)
            .build()

    private fun icon(id: String, tint: Int) = Image.Builder().setResourceId(id).setWidth(dp(26f)).setHeight(dp(26f))
        .setColorFilter(LayoutElementBuilders.ColorFilter.Builder().setTint(argb(tint)).build()).build()

    private fun layout(v: WearApi.View, note: String?): LayoutElementBuilders.LayoutElement {
        val flashing = v.flashLeftMs > 0
        val accent = if (v.flashKind == "cold") EMBER else TIDE
        val col = Column.Builder().setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .addContent(text(v.name, 13f, MUTE))
            .addContent(text(v.temp, if (v.on) 40f else 30f, if (v.on) accent else MUTE))
            .addContent(text(note ?: v.state, 13f, if (note != null || flashing) accent else TEXT))
            .addContent(Spacer.Builder().setHeight(dp(8f)).build())
        if (WearApi.ready(applicationContext) && v.ok) {
            val row = Row.Builder()
            if (flashing) {
                row.addContent(button("extend", "Five more minutes", text("+5", 18f, accent, LayoutElementBuilders.FONT_WEIGHT_BOLD)))
                    .addContent(Spacer.Builder().setWidth(dp(14f)).build())
                    .addContent(button("stop", "Stop the flash", icon("stop", TEXT)))
            } else {
                row.addContent(button("hot", "Hot flash", icon("flash", TIDE)))
                    .addContent(Spacer.Builder().setWidth(dp(14f)).build())
                    .addContent(button("power", if (v.on) "Turn off for tonight" else "Turn on", icon("power", TEXT)))
            }
            col.addContent(row.build())
        }
        return Box.Builder().setWidth(expand()).setHeight(expand())
            .setModifiers(ModifiersBuilders.Modifiers.Builder().setBackground(ModifiersBuilders.Background.Builder().setColor(argb(NIGHT)).build()).build())
            .addContent(col.build()).build()
    }
}
