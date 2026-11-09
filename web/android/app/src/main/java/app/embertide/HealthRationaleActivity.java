package app.embertide;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.util.TypedValue;
import android.widget.ScrollView;
import android.widget.TextView;

/** Shown by Health Connect when you tap "Read privacy policy" for Ember & Tide. */
public class HealthRationaleActivity extends Activity {
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        TextView t = new TextView(this);
        int pad = (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 24, getResources().getDisplayMetrics());
        t.setPadding(pad, pad * 2, pad, pad);
        t.setTextColor(Color.parseColor("#F2EADF"));
        t.setTextSize(16);
        t.setLineSpacing(0, 1.3f);
        t.setText("How Ember & Tide uses your health data\n\n"
                + "Ember & Tide reads skin temperature, heart rate, heart rate variability, breathing rate, blood oxygen and sleep stages "
                + "from Health Connect. It never writes to Health Connect.\n\n"
                + "The readings go only to the Ember & Tide server you signed in to (your own, at home). It lines them up with your bed's "
                + "sensors so Autopilot can see how warm you ran each night and suggest better temperatures.\n\n"
                + "Nothing is sent to any other company or service. You can turn access off at any time in Health Connect, "
                + "or with Disconnect in the app's Settings.");
        ScrollView s = new ScrollView(this);
        s.setBackgroundColor(Color.parseColor("#100E12"));
        s.setFitsSystemWindows(true);
        s.addView(t);
        setContentView(s);
    }
}
