package app.embertide;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * What the native parts need to reach the server, plus a tiny HTTP client. The web app hands over the server address,
 * the session token and which person is "me" after sign-in.
 *
 * Two kinds of credential:
 * - the session ("token"): for work the phone does for the app itself, in the background (health uploads, phone
 *   context, watch heart rate, push registration). It lives only in this app's private storage, like the WebView's.
 * - a device key ("device_token", scope "phone"): what the home-screen widget, the Quick Settings tile, the flash
 *   notification and notification buttons use. It can only read the snapshot and do a fixed list of things on this
 *   person's own side. The watch gets its own ("watch") key from EmberWear, never the session.
 * A device key that stops working (401: revoked, or the session behind it ended) is made again from the session; if the
 * session is gone too, the phone shows the usual signed-out state.
 */
public final class Ember {
    private Ember() {}

    /** An HTTP error with its status code. */
    static final class HttpError extends Exception {
        final int code;
        HttpError(int code, String body) { super("HTTP " + code + " " + body); this.code = code; }
    }

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("embertide", Context.MODE_PRIVATE);
    }

    static String server(Context c) { return prefs(c).getString("server", ""); }
    static String token(Context c) { return prefs(c).getString("token", ""); }
    static String person(Context c) { return prefs(c).getString("person", ""); }

    static boolean ready(Context c) {
        return !server(c).isEmpty() && !token(c).isEmpty() && !person(c).isEmpty();
    }

    /** After sign-in (and on every app start). A different server, account or side drops the old device keys: they're
     *  made again for the new one on first use (and the watch's by EmberWear.sync). */
    static synchronized void configure(Context c, String server, String token, String person) {
        SharedPreferences p = prefs(c);
        boolean same = server.equals(server(c)) && token.equals(token(c)) && person.equals(person(c));
        SharedPreferences.Editor e = p.edit().putString("server", server).putString("token", token).putString("person", person);
        if (!same) e.remove("device_token").remove("device_token_id").remove("watch_token").remove("watch_token_id");
        e.apply();
    }

    static synchronized void clear(Context c) {
        prefs(c).edit().remove("token").remove("device_token").remove("device_token_id")
                .remove("watch_token").remove("watch_token_id").apply();
    }

    static String watchToken(Context c) { return prefs(c).getString("watch_token", ""); }

    /** Make a device key from the session ("phone" or "watch") and keep it; making one again replaces the old one on the
     *  server. Throws HttpError 401 when the session itself has ended (then the phone is signed out here too). */
    static synchronized String mint(Context c, String scope) throws Exception {
        String label = "watch".equals(scope) ? "Watch" : "Phone widget and tile";
        JSONObject r;
        try {
            r = new JSONObject(call(c, "POST", "/api/auth/me/device-tokens",
                    new JSONObject().put("scope", scope).put("label", label).toString()));
        } catch (HttpError e) {
            if (e.code == 401) clear(c);                 // the session ended: the usual signed-out state everywhere
            throw e;
        }
        String tok = r.getString("token");
        String key = "watch".equals(scope) ? "watch_token" : "device_token";
        prefs(c).edit().putString(key, tok).putString(key + "_id", r.optString("id")).apply();
        return tok;
    }

    /** Forget device keys the server no longer lists (revoked in Settings or Studio). Off the main thread. */
    static void dropRevokedKeys(Context c) {
        try {
            org.json.JSONArray mine = new JSONArray(call(c, "GET", "/api/auth/me/device-tokens", null));
            java.util.Set<String> ids = new java.util.HashSet<>();
            for (int i = 0; i < mine.length(); i++) ids.add(mine.getJSONObject(i).optString("id"));
            synchronized (Ember.class) {
                SharedPreferences.Editor e = prefs(c).edit();
                for (String key : new String[]{"device_token", "watch_token"}) {
                    if (!ids.contains(prefs(c).getString(key + "_id", ""))) e.remove(key).remove(key + "_id");
                }
                e.apply();
            }
        } catch (Exception ignored) { /* offline or an older server: keep what we have */ }
    }

    /** A call from the widget, tile or a notification button, with the phone's device key (made on first use). One
     *  retry with a fresh key after a 401; a 403 means the key may not do that and is not retried. */
    static String deviceCall(Context c, String method, String path, String jsonBody) throws Exception {
        String tok = prefs(c).getString("device_token", "");
        if (tok.isEmpty()) {
            try {
                tok = mint(c, "phone");
            } catch (HttpError e) {
                if (e.code == 404 || e.code == 405) return call(c, method, path, jsonBody);   // a server from before device keys
                throw e;
            }
        }
        try {
            return request(c, tok, method, path, jsonBody);
        } catch (HttpError e) {
            if (e.code != 401) throw e;
            return request(c, mint(c, "phone"), method, path, jsonBody);
        }
    }

    /** A notification button for `pid`: this person's own side goes with the device key; another side (rare: a shared
     *  household message) needs the account's session. */
    static String sideCall(Context c, String pid, String method, String path, String jsonBody) throws Exception {
        return pid.equals(person(c)) ? deviceCall(c, method, path, jsonBody) : call(c, method, path, jsonBody);
    }

    /** Blocking request with the session; call off the main thread. Returns the body, or throws. */
    static String call(Context c, String method, String path, String jsonBody) throws Exception {
        return request(c, token(c), method, path, jsonBody);
    }

    private static String request(Context c, String bearer, String method, String path, String jsonBody) throws Exception {
        URL url = new URL(server(c) + path);
        HttpURLConnection h = (HttpURLConnection) url.openConnection();
        h.setRequestMethod(method);
        h.setInstanceFollowRedirects(false);       // never carry the token to wherever a redirect points
        h.setConnectTimeout(8000);
        h.setReadTimeout(10000);
        h.setRequestProperty("Authorization", "Bearer " + bearer);
        h.setRequestProperty("X-Embertide", "1");
        h.setRequestProperty("X-Embertide-Client", "app");
        if (jsonBody != null) {
            h.setDoOutput(true);
            h.setRequestProperty("Content-Type", "application/json");
            try (OutputStream os = h.getOutputStream()) { os.write(jsonBody.getBytes(StandardCharsets.UTF_8)); }
        }
        int code = h.getResponseCode();
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(code < 400 ? h.getInputStream() : h.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
        } catch (Exception ignored) {}
        if (code >= 400) throw new HttpError(code, sb.toString());
        return sb.toString();
    }

    /** "me" from a snapshot JSON. */
    static JSONObject me(Context c, JSONObject snap) throws Exception {
        JSONArray people = snap.getJSONArray("people");
        String pid = person(c);
        for (int i = 0; i < people.length(); i++) {
            JSONObject p = people.getJSONObject(i);
            if (pid.equals(p.optString("id"))) return p;
        }
        return people.length() > 0 ? people.getJSONObject(0) : null;
    }

    static boolean fahrenheit(JSONObject person) {
        JSONObject s = person.optJSONObject("settings");
        return s == null || !"C".equals(s.optString("units", "F"));
    }

    static String temp(double c, boolean f) {
        return f ? Math.round(c * 9.0 / 5.0 + 32.0) + "°" : String.format(java.util.Locale.US, "%.1f°", c);
    }
}
