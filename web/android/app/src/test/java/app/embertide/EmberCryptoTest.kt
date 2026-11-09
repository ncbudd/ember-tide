package app.embertide

import org.junit.Assert.assertEquals
import org.junit.Test
import java.math.BigInteger
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.interfaces.ECPrivateKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECPrivateKeySpec
import java.util.Base64

/** The app decrypts exactly what RFC 8291's worked example (and so our server) produces. */
class EmberCryptoTest {
    private fun d(s: String) = Base64.getUrlDecoder().decode(s)

    @Test
    fun decryptsTheRfc8291Vector() {
        val params = (KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair().private as ECPrivateKey).params
        val priv = KeyFactory.getInstance("EC").generatePrivate(ECPrivateKeySpec(BigInteger(1, d("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94")), params))
        val body = d("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A" +
                "_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
        val out = EmberCrypto.decrypt(body, priv, d("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
            d("BTBZMqHH6r4Tts7J_aSIgg"))
        assertEquals("When I grow up, I want to be a watermelon", String(out))
    }
}
