package app.embertide

/**
 * The page a tapped notification should open, kept free of Android types so it can be unit-tested on the JVM
 * (EmberLinkTest). Only a plain in-app path is accepted: it starts with one "/", and holds nothing that could
 * name another site or app (no second "/" at the start, backslash, scheme or control character).
 */
object EmberLink {
    private const val MAX = 200

    /** The path to open, or null when it isn't a safe in-app path (the app then just opens). */
    @JvmStatic
    fun clean(raw: String?): String? {
        val p = raw?.trim() ?: return null
        if (p.isEmpty() || p.length > MAX || p[0] != '/') return null
        if (p.length > 1 && (p[1] == '/' || p[1] == '\\')) return null
        for (ch in p) {
            if (ch.code < 0x20 || ch.code == 0x7f || ch == '\\' || ch == ':' || ch == '<' || ch == '>' || ch == '"' || ch == '\'' || ch == ' ') return null
        }
        if (p.contains("//") || p.contains("..")) return null
        return p
    }
}
