package app.embertide;

import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.net.Uri;
import android.view.InputDevice;
import android.content.res.Configuration;
import android.graphics.Rect;
import android.webkit.WebView;

import androidx.core.content.ContextCompat;
import androidx.core.util.Consumer;
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter;
import androidx.window.layout.DisplayFeature;
import androidx.window.layout.FoldingFeature;
import androidx.window.layout.WindowInfoTracker;
import androidx.window.layout.WindowLayoutInfo;

import com.getcapacitor.BridgeActivity;

import org.json.JSONObject;

public class MainActivity extends BridgeActivity {
    /** The newest fold description handed to the web app (JSON), for EmberNative.fold() after a reload. */
    static volatile String lastFold = "{\"fold\":false}";
    /** Whether a built-in hardware keyboard is present and open (e.g. the Minimal Phone 2's QWERTY), as JSON for the web app. */
    static volatile String lastKeyboard = "{\"hardware\":false}";
    /** A launcher shortcut or command ("hot", "hot flash", "cooler"…) waiting for the web app to pick up. */
    static volatile String pendingAction = null;

    private WindowInfoTrackerCallbackAdapter windowInfo;
    private final Consumer<WindowLayoutInfo> foldListener = this::pushFold;

    @Override
    public void onCreate(android.os.Bundle savedInstanceState) {
        registerPlugin(EmberNativePlugin.class);
        registerPlugin(EmberHealthPlugin.class);
        registerPlugin(EmberPushPlugin.class);
        registerPlugin(EmberPhonePlugin.class);
        super.onCreate(savedInstanceState);
        applyOrientation(getResources().getConfiguration());
        windowInfo = new WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(this));
        pushKeyboard(getResources().getConfiguration());
        takeIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        takeIntent(intent);
    }

    @Override
    public void onResume() {
        super.onResume();
        pushKeyboard(getResources().getConfiguration());   // the page may have (re)loaded since the last report
    }

    @Override
    public void onStart() {
        super.onStart();
        windowInfo.addWindowLayoutInfoListener(this, ContextCompat.getMainExecutor(this), foldListener);
    }

    @Override
    public void onStop() {
        windowInfo.removeWindowLayoutInfoListener(foldListener);
        super.onStop();
    }

    /**
     * Folding and unfolding (and resizing in split screen) arrive here instead of restarting the activity
     * (configChanges in the manifest), so the web app keeps its state; the orientation rule is re-checked.
     */
    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        applyOrientation(newConfig);
        pushKeyboard(newConfig);
    }

    /** A QWERTY keyboard that's attached and not slid away/hidden: the web app switches to its compact keyboard layout. */
    private void pushKeyboard(Configuration c) {
        // the configuration alone isn't enough (emulators and some phones report QWERTY for internal key devices):
        // there must also be a real, non-virtual keyboard with letters
        boolean hw = c.keyboard == Configuration.KEYBOARD_QWERTY && c.hardKeyboardHidden != Configuration.HARDKEYBOARDHIDDEN_YES
                && hasLetterKeyboard();
        lastKeyboard = "{\"hardware\":" + hw + "}";
        WebView wv = getBridge() == null ? null : getBridge().getWebView();
        if (wv != null) {
            String js = "window.__etKeyboard=" + lastKeyboard + ";window.dispatchEvent(new CustomEvent('et-keyboard',{detail:window.__etKeyboard}));";
            wv.post(() -> wv.evaluateJavascript(js, null));
        }
    }

    private static boolean hasLetterKeyboard() {
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice d = InputDevice.getDevice(id);
            // built in only (the Minimal Phone 2's QWERTY): a Bluetooth keyboard on a tablet still gets every shortcut,
            // just not the compact layout. Emulators pass the PC keyboard through as an external one.
            if (d == null || d.isVirtual()) continue;
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q && d.isExternal()) continue;
            if (d.getKeyboardType() == InputDevice.KEYBOARD_TYPE_ALPHABETIC
                    && (d.getSources() & InputDevice.SOURCE_KEYBOARD) == InputDevice.SOURCE_KEYBOARD) return true;
        }
        return false;
    }

    /**
     * Launcher shortcuts (Hot flash, Cold flash, Off tonight, Simple screen), a command-bar launcher's text
     * ("hot flash", "cooler", "off tonight") via ACTION_SEND, or embertide://do?q=hot+flash. The web app runs it
     * with the same code as its keyboard shortcuts (signed in, the right person, plain feedback).
     */
    private void takeIntent(Intent intent) {
        if (intent == null) return;
        // our own launcher shortcuts run straight away: they arrive through the NON-exported alias (only the system's
        // shortcut launcher can open it). The same extra on the exported activity came from another app or an
        // intent:// web link, so it's asked about like any outside command.
        String a = intent.getStringExtra("et_action");
        android.content.ComponentName cn = intent.getComponent();
        boolean outside = a != null && (cn == null || !cn.getClassName().endsWith(".ShortcutActivity"));
        if (a == null && Intent.ACTION_SEND.equals(intent.getAction()) && "text/plain".equals(intent.getType())) {
            a = intent.getStringExtra(Intent.EXTRA_TEXT);
            outside = true;
        }
        Uri u = intent.getData();
        if (a == null && u != null && "embertide".equals(u.getScheme())) {
            a = u.getQueryParameter("q");
            if (a == null) a = u.getHost();
            outside = true;
        }
        if (a == null || a.trim().isEmpty()) return;
        a = a.trim();
        if (a.length() > 80) a = a.substring(0, 80);
        // from another app or a web link: the web app asks before doing anything to the bed
        if (outside) a = "ask:" + a;
        pendingAction = a;
        WebView wv = getBridge() == null ? null : getBridge().getWebView();
        if (wv != null) {
            String js = "window.__etAction=" + JSONObject.quote(a) + ";window.dispatchEvent(new CustomEvent('et-action',{detail:window.__etAction}));";
            wv.post(() -> wv.evaluateJavascript(js, null));
        }
        intent.removeExtra("et_action");
    }

    // phones (and a foldable's cover screen) stay upright: landscape leaves no room for the plan.
    // Tablets and unfolded foldables turn freely.
    private void applyOrientation(Configuration c) {
        int want = c.smallestScreenWidthDp < 600
                ? ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                : ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED;
        if (getRequestedOrientation() != want) setRequestedOrientation(want);
    }

    /**
     * The hinge, for the web app. Older WebViews (before Chrome 138) don't expose CSS viewport segments, so the
     * fold comes from Jetpack WindowManager instead: its position in CSS pixels relative to the page, the
     * posture (half-opened = book or tabletop, flat) and whether it splits the screen. Sent as an `et-fold`
     * event and kept in `window.__etFold`; `{fold:false}` when there's no fold in this window.
     */
    private void pushFold(WindowLayoutInfo info) {
        String json = "{\"fold\":false}";
        WebView wv = getBridge() == null ? null : getBridge().getWebView();
        for (DisplayFeature f : info.getDisplayFeatures()) {
            if (!(f instanceof FoldingFeature) || wv == null) continue;
            FoldingFeature ff = (FoldingFeature) f;
            int[] at = new int[2];
            wv.getLocationInWindow(at);
            float d = getResources().getDisplayMetrics().density;
            Rect b = ff.getBounds();
            try {
                json = new JSONObject()
                        .put("fold", true)
                        .put("x", Math.round((b.left - at[0]) / d)).put("y", Math.round((b.top - at[1]) / d))
                        .put("width", Math.round(b.width() / d)).put("height", Math.round(b.height() / d))
                        .put("orientation", ff.getOrientation() == FoldingFeature.Orientation.VERTICAL ? "vertical" : "horizontal")
                        .put("state", ff.getState() == FoldingFeature.State.HALF_OPENED ? "half-opened" : "flat")
                        .put("separating", ff.isSeparating())
                        .put("occluding", ff.getOcclusionType() == FoldingFeature.OcclusionType.FULL)
                        .toString();
            } catch (org.json.JSONException ignored) { }
            break;
        }
        lastFold = json;
        if (wv != null) {
            String js = "window.__etFold=" + json + ";window.dispatchEvent(new CustomEvent('et-fold',{detail:window.__etFold}));";
            wv.post(() -> wv.evaluateJavascript(js, null));
        }
    }
}
