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
 * What the native parts (tile, widget, flash notification) need to reach the server, plus a tiny HTTP client.
 * The web app hands over the server address, the session token and which person is "me" after sign-in.
 */
public final class Ember {
    private Ember() {}

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("embertide", Context.MODE_PRIVATE);
    }

    static String server(Context c) { return prefs(c).getString("server", ""); }
    static String token(Context c) { return prefs(c).getString("token", ""); }
    static String person(Context c) { return prefs(c).getString("person", ""); }

    static boolean ready(Context c) {
        return !server(c).isEmpty() && !token(c).isEmpty() && !person(c).isEmpty();
    }

    static void configure(Context c, String server, String token, String person) {
        prefs(c).edit().putString("server", server).putString("token", token).putString("person", person).apply();
    }

    static void clear(Context c) {
        prefs(c).edit().remove("token").apply();
    }

    /** Blocking request; call off the main thread. Returns the body, or throws. */
    static String call(Context c, String method, String path, String jsonBody) throws Exception {
        URL url = new URL(server(c) + path);
        HttpURLConnection h = (HttpURLConnection) url.openConnection();
        h.setRequestMethod(method);
        h.setInstanceFollowRedirects(false);       // never carry the token to wherever a redirect points
        h.setConnectTimeout(8000);
        h.setReadTimeout(10000);
        h.setRequestProperty("Authorization", "Bearer " + token(c));
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
        if (code >= 400) throw new Exception("HTTP " + code + " " + sb);
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
