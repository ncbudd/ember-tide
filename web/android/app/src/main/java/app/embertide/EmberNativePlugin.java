package app.embertide;

import android.Manifest;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/** The bridge from the web UI to the native parts. */
@CapacitorPlugin(name = "EmberNative", permissions = {
        @Permission(alias = "notifications", strings = {Manifest.permission.POST_NOTIFICATIONS})
})
public class EmberNativePlugin extends Plugin {

    @PluginMethod
    public void configure(PluginCall call) {
        Ember.configure(getContext(), call.getString("server", ""), call.getString("token", ""), call.getString("person", ""));
        EmberWear.signIn(getContext(), call.getString("server", ""), call.getString("token", ""), call.getString("person", ""));
        PhoneSources.INSTANCE.schedule(getContext());          // back after a sign-out (no-op when no source is on)
        new Thread(() -> EmberWidget.refreshAll(getContext())).start();
        call.resolve();
    }

    @PluginMethod
    public void signOut(PluginCall call) {
        Ember.clear(getContext());
        HealthSync.INSTANCE.unschedule(getContext());        // the background jobs have nothing to do signed out
        PhoneSources.INSTANCE.unschedule(getContext());
        EmberWear.signOut(getContext());
        FlashNotifier.clear(getContext());
        new Thread(() -> EmberWidget.refreshAll(getContext())).start();
        call.resolve();
    }

    @PluginMethod
    public void showFlash(PluginCall call) {
        long end = call.getLong("end", 0L);
        // The server's clock can differ from the phone's (simulator, a drifting clock): with the server's "now", count
        // down the time that's actually left instead of comparing against the phone's clock.
        long serverNow = call.getLong("now", 0L);
        if (serverNow > 0 && end > 0) end = System.currentTimeMillis() + (end - serverNow);
        FlashNotifier.show(getContext(), call.getString("kind", "hot"), end, call.getString("name", ""));
        call.resolve();
    }

    /** Whether a hardware keyboard is attached and open: {hardware: boolean}, as the `et-keyboard` event. */
    @PluginMethod
    public void keyboard(PluginCall call) {
        try {
            call.resolve(new JSObject(MainActivity.lastKeyboard));
        } catch (org.json.JSONException e) {
            call.reject("no keyboard info");
        }
    }

    /** A launcher shortcut / command waiting to run, handed over once ({action: string|null}). */
    @PluginMethod
    public void takeAction(PluginCall call) {
        JSObject r = new JSObject();
        r.put("action", MainActivity.pendingAction);
        MainActivity.pendingAction = null;
        call.resolve(r);
    }

    /** The current fold (hinge position in CSS px, posture), the same object the `et-fold` event carries. */
    @PluginMethod
    public void fold(PluginCall call) {
        try {
            call.resolve(new com.getcapacitor.JSObject(MainActivity.lastFold));
        } catch (org.json.JSONException e) {
            call.reject("no fold info");
        }
    }

    @PluginMethod
    public void clearFlash(PluginCall call) {
        FlashNotifier.clear(getContext());
        call.resolve();
    }

    @PluginMethod
    public void refreshWidgets(PluginCall call) {
        new Thread(() -> EmberWidget.refreshAll(getContext())).start();
        call.resolve();
    }

    /** Status and navigation bars follow the app's light or dark look. */
    @PluginMethod
    public void setBars(PluginCall call) {
        final int color = android.graphics.Color.parseColor(call.getString("color", "#100e12"));
        final boolean light = Boolean.TRUE.equals(call.getBoolean("light", false));
        getActivity().runOnUiThread(() -> {
            android.view.Window w = getActivity().getWindow();
            w.getDecorView().setBackgroundColor(color);
            if (Build.VERSION.SDK_INT < 35) { w.setStatusBarColor(color); w.setNavigationBarColor(color); }
            androidx.core.view.WindowInsetsControllerCompat c = androidx.core.view.WindowCompat.getInsetsController(w, w.getDecorView());
            c.setAppearanceLightStatusBars(light);
            c.setAppearanceLightNavigationBars(light);
        });
        call.resolve();
    }

    /** Android's share sheet (the WebView has no Web Share). */
    @PluginMethod
    public void share(PluginCall call) {
        android.content.Intent send = new android.content.Intent(android.content.Intent.ACTION_SEND)
                .setType("text/plain")
                .putExtra(android.content.Intent.EXTRA_SUBJECT, call.getString("title", "Ember & Tide"))
                .putExtra(android.content.Intent.EXTRA_TEXT, call.getString("text", ""));
        android.content.Intent chooser = android.content.Intent.createChooser(send, call.getString("title", "Share"));
        getActivity().startActivity(chooser);
        call.resolve();
    }

    @PluginMethod
    public void requestNotifications(PluginCall call) {
        if (Build.VERSION.SDK_INT < 33 || getPermissionState("notifications") == PermissionState.GRANTED) {
            JSObject r = new JSObject();
            r.put("granted", true);
            call.resolve(r);
            return;
        }
        requestPermissionForAlias("notifications", call, "notificationsResult");
    }

    @PermissionCallback
    private void notificationsResult(PluginCall call) {
        JSObject r = new JSObject();
        r.put("granted", getPermissionState("notifications") == PermissionState.GRANTED);
        call.resolve(r);
    }
}
