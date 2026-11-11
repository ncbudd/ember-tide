package app.embertide

import android.content.Context

/** The "minimal" build has no Google push: this stub keeps the same entry points as the "full" build's EmberFcm. */
object EmberFcm {
    /** Whether this build contains Google push at all. */
    const val IN_BUILD = false

    fun available(c: Context): Boolean = false

    fun sync(c: Context): String? = "Google push isn't part of this build"

    fun deleteToken() {}
}
