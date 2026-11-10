package app.embertide;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/**
 * Launcher shortcuts (Hot flash, Cold flash, Off tonight, Simple screen) land here, not on MainActivity.
 * It hands the action over once and opens the app with a plain intent, so the action is never part of the
 * app's own launch intent: reopening the app from Recents (even after the process was killed) can't run it again.
 * Not exported: only the system's shortcut launcher can open it, so these run without the "ask first" step.
 */
public class ShortcutActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent in = getIntent();
        boolean fresh = savedInstanceState == null
                && (in == null || (in.getFlags() & Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) == 0);
        String a = in == null ? null : in.getStringExtra("et_action");
        if (fresh && a != null && !a.trim().isEmpty()) {
            a = a.trim();
            MainActivity.pendingAction = a.length() > 80 ? a.substring(0, 80) : a;
            MainActivity.pendingAt = android.os.SystemClock.elapsedRealtime();
        }
        startActivity(new Intent(this, MainActivity.class)
                .setAction(Intent.ACTION_MAIN)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        finish();
    }
}
