package app.embertide;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.util.SizeF;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.text.DateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * Home-screen widget, resizable. The layout follows the size you drag it to:
 * 1×1 temperature only · 2×1 + hot flash · 4×1 name, state, hot flash, power · 4×2+ next change, −/+, hot/cold.
 */
public class EmberWidget extends AppWidgetProvider {

    /** One inexact alarm (no special permission) that refreshes every widget; a newer one replaces the last. */
    static void scheduleRefresh(Context c, long inMillis) {
        android.app.AlarmManager am = (android.app.AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = FlashNotifier.action(c, "app.embertide.REFRESH", 90);
        am.set(android.app.AlarmManager.RTC, System.currentTimeMillis() + Math.max(15_000L, inMillis), pi);
    }

    @Override
    public void onUpdate(Context c, AppWidgetManager m, int[] ids) {
        refreshAsync(c);
    }

    @Override
    public void onAppWidgetOptionsChanged(Context c, AppWidgetManager m, int id, Bundle opts) {
        refreshAsync(c);                                // Android 11 and older pick the layout from the size here
    }

    private void refreshAsync(Context c) {
        final Context app = c.getApplicationContext();
        final PendingResult pending = goAsync();
        new Thread(() -> {
            try { refreshAll(app); } finally { pending.finish(); }
        }).start();
    }

    static void refreshAll(Context c) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        int[] ids = m.getAppWidgetIds(new ComponentName(c, EmberWidget.class));
        Shown data = load(c);      // also re-shows or clears the flash notification, so it runs with no widget too
        if (ids.length == 0) return;
        if (Build.VERSION.SDK_INT >= 31) {
            Map<SizeF, RemoteViews> sizes = new HashMap<>();
            sizes.put(new SizeF(40f, 40f), fill(c, R.layout.widget_xs, data));
            sizes.put(new SizeF(110f, 40f), fill(c, R.layout.widget_s, data));
            sizes.put(new SizeF(220f, 40f), fill(c, R.layout.widget, data));
            sizes.put(new SizeF(220f, 150f), fill(c, R.layout.widget_l, data));
            RemoteViews v = new RemoteViews(sizes);
            for (int id : ids) m.updateAppWidget(id, v);
        } else {
            for (int id : ids) {
                Bundle o = m.getAppWidgetOptions(id);
                int w = o.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 250);
                int h = o.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 60);
                int layout = w < 110 ? R.layout.widget_xs : w < 220 ? R.layout.widget_s : h < 150 ? R.layout.widget : R.layout.widget_l;
                m.updateAppWidget(id, fill(c, layout, data));
            }
        }
    }

    /** What the widget shows, fetched once and poured into whichever layouts are needed. */
    static final class Shown {
        String name = "Ember & Tide", temp = "–", state = "", stateShort = "", next = "";
    }

    static Shown load(Context c) {
        Shown d = new Shown();
        if (!Ember.ready(c)) {
            d.state = "Open the app to sign in";
            d.stateShort = "Sign in";
            return d;
        }
        try {
            JSONObject snap = new JSONObject(Ember.call(c, "GET", "/api/snapshot", null));
            JSONObject me = Ember.me(c, snap);
            boolean f = Ember.fahrenheit(me);
            JSONObject n = me.getJSONObject("night");
            JSONObject bed = me.optJSONObject("bed");
            boolean on = !n.isNull("target_c");
            d.name = me.optString("name");
            d.temp = on ? Ember.temp(n.getDouble("target_c"), f) : "–";
            JSONObject flash = n.optJSONObject("flash");
            double now = snap.optDouble("now");
            if (flash != null && now < flash.optDouble("end")) {
                int min = (int) Math.ceil((flash.optDouble("end") - now) / 60);
                boolean hot = "hot".equals(flash.optString("kind"));
                d.state = (hot ? "Hot flash · " : "Cold flash · ") + min + " min";
                d.stateShort = (hot ? "Hot · " : "Cold · ") + min + "m";
                FlashNotifier.show(c, flash.optString("kind"),
                        System.currentTimeMillis() + (long) ((flash.optDouble("end") - now) * 1000), d.name);   // time left, not the server's clock
                // keep the minutes left roughly right, and show the end of the flash when it comes (the widget's own
                // update period is 30 min): wake up again in 5 min or just after the flash ends, whichever is first
                long left = (long) ((flash.optDouble("end") - now) * 1000);
                scheduleRefresh(c, Math.min(left + 5_000L, 5 * 60_000L));
            } else if (!on) {
                String st = n.optString("state");
                d.state = "skipped".equals(st) ? "Off tonight" : "paused".equals(st) ? "Paused" : "Off right now";
                d.stateShort = "paused".equals(st) ? "Paused" : "Off";
                FlashNotifier.clear(c);
            } else {
                double m = bed == null || bed.isNull("measured_c") ? Double.NaN : bed.optDouble("measured_c");
                double t = n.getDouble("target_c");
                d.stateShort = Double.isNaN(m) ? "On" : (t < m - 0.4 ? "Cooling" : t > m + 0.4 ? "Warming" : "Holding");
                d.state = d.stateShort + (Double.isNaN(m) ? "" : " · bed " + Ember.temp(m, f));
                FlashNotifier.clear(c);
            }
            JSONObject nx = me.optJSONObject("next");
            if (nx != null && nx.has("at") && !nx.isNull("at")) {
                String at = DateFormat.getTimeInstance(DateFormat.SHORT).format(new Date((long) (nx.getDouble("at") * 1000)));
                d.next = nx.isNull("temp_c") ? "off at " + at : nx.optString("name") + " " + Ember.temp(nx.getDouble("temp_c"), f) + " at " + at;
            }
            lastGood = d;
            failures = 0;
        } catch (Exception e) {
            // keep the last good reading for a few misses (a blip shouldn't blank the widget); Offline after repeated ones
            Shown old = lastGood;
            if (old != null && ++failures < 3) {
                Shown s = new Shown();
                s.name = old.name; s.temp = old.temp; s.stateShort = old.stateShort; s.next = old.next;
                s.state = old.state.endsWith(" · not updated") ? old.state : old.state + " · not updated";
                return s;
            }
            d.state = "Can't reach your server";
            d.stateShort = "Offline";
        }
        return d;
    }

    private static Shown lastGood;
    private static int failures;

    static RemoteViews fill(Context c, int layout, Shown d) {
        RemoteViews v = new RemoteViews(c.getPackageName(), layout);
        Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        v.setOnClickPendingIntent(R.id.w_root, PendingIntent.getActivity(c, 10, open, PendingIntent.FLAG_IMMUTABLE));
        v.setTextViewText(R.id.w_temp, d.temp);
        boolean small = layout == R.layout.widget_xs || layout == R.layout.widget_s;
        if (small) {
            v.setTextViewText(R.id.w_state_short, d.stateShort);
        } else {
            v.setTextViewText(R.id.w_name, d.name);
            v.setTextViewText(R.id.w_state, d.state);
            v.setOnClickPendingIntent(R.id.w_power, FlashNotifier.action(c, "app.embertide.POWER", 12));
        }
        if (layout != R.layout.widget_xs) v.setOnClickPendingIntent(R.id.w_hot, FlashNotifier.action(c, "app.embertide.HOT_FLASH", 11));
        if (layout == R.layout.widget_l) {
            v.setTextViewText(R.id.w_next, d.next.isEmpty() ? "" : "Next: " + d.next);
            v.setOnClickPendingIntent(R.id.w_cold, FlashNotifier.action(c, "app.embertide.COLD_FLASH", 13));
            v.setOnClickPendingIntent(R.id.w_down, FlashNotifier.action(c, "app.embertide.ADJ_DOWN", 14));
            v.setOnClickPendingIntent(R.id.w_up, FlashNotifier.action(c, "app.embertide.ADJ_UP", 15));
        }
        return v;
    }

    /**
     * Power: on by hand → off; off tonight / paused → back on; running → off for tonight; otherwise (daytime, out of
     * bed, done) → on by hand.
     */
    static void togglePower(Context c) throws Exception {
        String pid = Ember.person(c);
        JSONObject me = Ember.me(c, new JSONObject(Ember.call(c, "GET", "/api/snapshot", null)));
        JSONObject n = me.getJSONObject("night");
        String st = n.optString("state");
        JSONObject o = n.optJSONObject("override");
        if (o != null && o.optBoolean("manual")) {
            // a by-hand session (a nap, or turned on in the morning) isn't part of tonight: "off tonight" wouldn't stop it
            Ember.call(c, "POST", "/api/people/" + pid + "/adjust/clear?source=widget", null);
            return;
        }
        if (!n.isNull("target_c") && !"skipped".equals(st) && !"paused".equals(st)) {
            Ember.call(c, "POST", "/api/people/" + pid + "/tonight", "{\"action\":\"skip\",\"source\":\"widget\"}");
            return;
        }
        if ("skipped".equals(st) || "paused".equals(st)) {
            Ember.call(c, "POST", "/api/people/" + pid + "/tonight", "{\"action\":\"resume\",\"source\":\"widget\"}");
            me = Ember.me(c, new JSONObject(Ember.call(c, "GET", "/api/snapshot", null)));
            if (!me.getJSONObject("night").isNull("target_c")) return;
        }
        // daytime, out of bed or done: on by hand at the fall-asleep temperature, until the rhythm takes over
        org.json.JSONArray ph = me.optJSONArray("phases");
        double t = ph != null && ph.length() > 0 ? ph.getJSONObject(0).getDouble("temp_c") : 21.1;
        Ember.call(c, "POST", "/api/people/" + pid + "/adjust", String.format(java.util.Locale.US, "{\"temp_c\":%.2f,\"until\":\"next_phase\",\"source\":\"widget\"}", t));
    }

    /** −/+ one step (1°F or 0.5°C) until the next phase, like the app. While idle it turns the side on at the bed's temperature ± a step. */
    static void adjust(Context c, int sign) throws Exception {
        JSONObject me = Ember.me(c, new JSONObject(Ember.call(c, "GET", "/api/snapshot", null)));
        double step = Ember.fahrenheit(me) ? 5.0 / 9.0 : 0.5;
        JSONObject n = me.getJSONObject("night");
        String st = n.optString("state");
        String body;
        if (n.isNull("target_c") && !"skipped".equals(st) && !"paused".equals(st)) {
            JSONObject bed = me.optJSONObject("bed");
            double base = bed != null && !bed.isNull("setpoint_c") ? bed.getDouble("setpoint_c") : 21.11;
            body = String.format(java.util.Locale.US, "{\"temp_c\":%.2f,\"until\":\"next_phase\",\"source\":\"widget\"}", base + sign * step);
        } else {
            body = String.format(java.util.Locale.US, "{\"delta_c\":%.4f,\"until\":\"next_phase\",\"source\":\"widget\"}", sign * step);
        }
        Ember.call(c, "POST", "/api/people/" + Ember.person(c) + "/adjust", body);
    }
}
