package app.embertide

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EmberLinkTest {
    @Test
    fun plainInAppPathsPass() {
        for (p in listOf("/", "/sleep", "/settings#watch", "/settings#hub", "/studio/devices", "/studio/notifications", "/act/abc_123", "/a?b=1&c=2"))
            assertEquals(p, EmberLink.clean(p))
        assertEquals("/sleep", EmberLink.clean("  /sleep "))
    }

    @Test
    fun anythingElseIsIgnored() {
        val bad = listOf(null, "", " ", "sleep", "settings#watch", "//evil.example", "/\\evil.example", "\\\\evil", "https://evil.example/x",
            "javascript:alert(1)", "/x://y", "/a//b", "/../etc", "/a\nb", "/a\u0000b", "/a\tb", "/a\u007fb", "/a b", "/a\\b",
            "/<script>", "/" + "a".repeat(250))
        for (p in bad) assertNull("should be refused: $p", EmberLink.clean(p))
    }
}
