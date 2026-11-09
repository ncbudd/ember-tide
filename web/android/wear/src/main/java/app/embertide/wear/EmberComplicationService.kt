package app.embertide.wear

import android.graphics.drawable.Icon
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.LongTextComplicationData
import androidx.wear.watchface.complications.data.MonochromaticImage
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Watch-face complication: your side's temperature at a glance ("64°", "Off", or "Hot 12m" during a flash), and in
 * the long slot "64° · Cooling". Updates every 10 minutes, and straight away after a press on the tile.
 */
class EmberComplicationService : SuspendingComplicationDataSourceService() {

    override fun getPreviewData(type: ComplicationType): ComplicationData? = data(type, WearApi.View("Left", "64°", "Cooling", "Cooling", true))

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val v = withContext(Dispatchers.IO) { WearApi.view(applicationContext) }
        return data(request.complicationType, v)
    }

    private fun data(type: ComplicationType, v: WearApi.View): ComplicationData? {
        val icon = MonochromaticImage.Builder(Icon.createWithResource(this, R.drawable.ic_stat_flash)).build()
        val main = if (v.flashLeftMs > 0) v.short else v.temp
        val described = PlainComplicationText.Builder("${v.name}: ${v.temp}, ${v.state}").build()
        return when (type) {
            // no title: many faces draw the title bigger than the text, and the number is what matters at a glance
            ComplicationType.SHORT_TEXT -> ShortTextComplicationData.Builder(PlainComplicationText.Builder(main).build(), described)
                .setMonochromaticImage(icon)
                .build()
            ComplicationType.LONG_TEXT -> LongTextComplicationData.Builder(PlainComplicationText.Builder("${v.temp} · ${v.state}").build(), described)
                .setMonochromaticImage(icon)
                .setTitle(PlainComplicationText.Builder(v.name).build())
                .build()
            else -> null
        }
    }
}
