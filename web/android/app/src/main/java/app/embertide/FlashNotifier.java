package app.embertide;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * The ongoing "Hot flash · 11:20 left" notification with +5 min and Stop. Public visibility, so the buttons work on
 * the lock screen even when the phone hides other notifications' content.
 */
public final class FlashNotifier {
    static final String CHANNEL = "flash";
    static final int ID = 4401;

    private FlashNotifier() {}

    static void channel(Context c) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Flash running", NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Shows a running hot or cold flash with +5 min and Stop");
            ch.setSound(null, null);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            c.getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    static PendingIntent action(Context c, String act, int req) {
        Intent i = new Intent(c, Receiver.class).setAction(act);
        return PendingIntent.getBroadcast(c, req, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    static void show(Context c, String kind, long endMillis, String name) {
        channel(c);
        Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(c, 1, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        boolean hot = "hot".equals(kind);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, CHANNEL) : new Notification.Builder(c);
        b.setSmallIcon(R.drawable.ic_stat_flash)
                .setContentTitle(hot ? "Hot flash" : "Cold flash")
                .setContentText((name == null || name.isEmpty() ? "" : name + " · ") + "then back to the rhythm")
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(tap)
                .setWhen(endMillis)
                .setShowWhen(true)
                .setUsesChronometer(true)
                .setColor(hot ? 0xFF5FB8B0 : 0xFFEE8A55)
                .addAction(new Notification.Action.Builder(null, "+5 min", action(c, "app.embertide.FLASH_EXTEND", 2)).build())
                .addAction(new Notification.Action.Builder(null, "Stop", action(c, "app.embertide.FLASH_STOP", 3)).build());
        if (Build.VERSION.SDK_INT >= 24) b.setChronometerCountDown(true);
        if (Build.VERSION.SDK_INT >= 31) b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        c.getSystemService(NotificationManager.class).notify(ID, b.build());
    }

    static void clear(Context c) {
        c.getSystemService(NotificationManager.class).cancel(ID);
    }

    /** Buttons on the notification and the widget land here. */
    public static class Receiver extends BroadcastReceiver {
        @Override
        public void onReceive(Context c, Intent intent) {
            final String act = intent.getAction() == null ? "" : intent.getAction();
            final Context app = c.getApplicationContext();
            final PendingResult pending = goAsync();
            new Thread(() -> {
                try {
                    String pid = Ember.person(app);
                    switch (act) {
                        case "app.embertide.FLASH_EXTEND": Ember.call(app, "POST", "/api/people/" + pid + "/flash/extend?source=notification", null); break;
                        case "app.embertide.FLASH_STOP": Ember.call(app, "POST", "/api/people/" + pid + "/flash/stop?source=notification", null); clear(app); break;
                        case "app.embertide.HOT_FLASH": Ember.call(app, "POST", "/api/people/" + pid + "/flash", "{\"kind\":\"hot\",\"source\":\"widget\"}"); break;
                        case "app.embertide.COLD_FLASH": Ember.call(app, "POST", "/api/people/" + pid + "/flash", "{\"kind\":\"cold\",\"source\":\"widget\"}"); break;
                        case "app.embertide.POWER": EmberWidget.togglePower(app); break;
                        case "app.embertide.ADJ_UP": EmberWidget.adjust(app, 1); break;
                        case "app.embertide.ADJ_DOWN": EmberWidget.adjust(app, -1); break;
                        case "app.embertide.NOTIF_ACT":
                            EmberPush.act(app, String.valueOf(intent.getStringExtra("act")), intent.getIntExtra("minutes", 0),
                                    String.valueOf(intent.getStringExtra("label")), String.valueOf(intent.getStringExtra("person")), intent.getIntExtra("nid", 0));
                            return;
                        default: break;
                    }
                    EmberWidget.refreshAll(app);
                } catch (Exception e) {
                    android.util.Log.w("EmberTide", "action " + act + " failed: " + e.getMessage());
                } finally {
                    pending.finish();
                }
            }).start();
        }
    }
}
