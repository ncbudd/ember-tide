package app.embertide

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/**
 * Debug only: show an app notification as if the server had pushed it (no Google or ntfy needed).
 *
 *   am broadcast -n app.embertide/.FakePush --es kind digest --es title "Test" --es body "Hello" --es link /settings#watch
 *
 * Leave out --es link for a notification without a page.
 */
class FakePush : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val m = JSONObject().put("t", "et").put("kind", i.getStringExtra("kind") ?: "test")
            .put("title", i.getStringExtra("title") ?: "Test").put("body", i.getStringExtra("body") ?: "A test notification.")
        i.getStringExtra("link")?.let { m.put("link", it) }
        EmberPush.show(c, m)
    }
}
