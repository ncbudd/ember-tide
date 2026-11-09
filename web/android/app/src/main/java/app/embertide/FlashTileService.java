package app.embertide;

import android.graphics.drawable.Icon;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.widget.Toast;

/** Quick Settings tile: one tap starts a hot flash on your side, from the pull-down shade (even locked). */
public class FlashTileService extends TileService {

    @Override
    public void onStartListening() {
        Tile t = getQsTile();
        if (t == null) return;
        t.setLabel("Hot flash");
        if (Build.VERSION.SDK_INT >= 29) t.setSubtitle(Ember.ready(this) ? "Ember & Tide" : "Sign in first");
        t.setIcon(Icon.createWithResource(this, R.drawable.ic_stat_flash));
        t.setState(Ember.ready(this) ? Tile.STATE_INACTIVE : Tile.STATE_UNAVAILABLE);
        t.updateTile();
    }

    @Override
    public void onClick() {
        if (!Ember.ready(this)) return;
        final Tile t = getQsTile();
        if (t != null) { t.setState(Tile.STATE_ACTIVE); t.updateTile(); }
        new Thread(() -> {
            String msg;
            try {
                Ember.call(getApplicationContext(), "POST", "/api/people/" + Ember.person(this) + "/flash", "{\"kind\":\"hot\",\"source\":\"tile\"}");
                EmberWidget.refreshAll(getApplicationContext());
                msg = "Hot flash started";
            } catch (Exception e) {
                msg = "Couldn't reach Ember & Tide";
            }
            final String m = msg;
            new android.os.Handler(getMainLooper()).post(() -> {
                Toast.makeText(getApplicationContext(), m, Toast.LENGTH_SHORT).show();
                if (t != null) { t.setState(Tile.STATE_INACTIVE); t.updateTile(); }
            });
        }).start();
    }
}
