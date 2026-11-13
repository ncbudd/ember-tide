package app.embertide

import android.content.Context
import android.util.Base64
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.interfaces.ECPrivateKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import java.security.spec.PKCS8EncodedKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Web Push decryption (RFC 8291 / aes128gcm) for Google push: our server encrypts each message for this phone, so Google
 * only carries ciphertext. The key pair and auth secret live in the app's private storage (never backed up).
 */
object EmberCrypto {
    private fun b64u(b: ByteArray) = Base64.encodeToString(b, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
    private fun unb64u(s: String) = Base64.decode(s, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)

    data class Keys(val publicB64: String, val authB64: String)

    /** This install's keys, made once. */
    fun keys(c: Context): Keys {
        val p = Ember.prefs(c)
        val pub = p.getString("fcm_pub", null)
        val auth = p.getString("fcm_auth", null)
        if (pub != null && auth != null && p.getString("fcm_priv", null) != null) return Keys(pub, auth)
        val kpg = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }
        val kp = kpg.generateKeyPair()
        val secret = ByteArray(16).also { SecureRandom().nextBytes(it) }
        val k = Keys(b64u(uncompressed(kp.public as ECPublicKey)), b64u(secret))
        p.edit().putString("fcm_priv", b64u(kp.private.encoded)).putString("fcm_pub", k.publicB64).putString("fcm_auth", k.authB64).apply()
        return k
    }

    fun decrypt(c: Context, data: ByteArray): ByteArray {
        val p = Ember.prefs(c)
        val priv = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(unb64u(p.getString("fcm_priv", "")!!)))
        return decrypt(data, priv, unb64u(p.getString("fcm_pub", "")!!), unb64u(p.getString("fcm_auth", "")!!))
    }

    /** Pure function so it can be checked against the RFC's test vector. */
    fun decrypt(data: ByteArray, priv: PrivateKey, uaPublic: ByteArray, authSecret: ByteArray): ByteArray {
        val salt = data.copyOfRange(0, 16)
        val idLen = data[20].toInt() and 0xff
        val asPublic = data.copyOfRange(21, 21 + idLen)
        val body = data.copyOfRange(21 + idLen, data.size)
        val params = (priv as ECPrivateKey).params
        val x = BigInteger(1, asPublic.copyOfRange(1, 33))
        val y = BigInteger(1, asPublic.copyOfRange(33, 65))
        val senderKey = KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(ECPoint(x, y), params))
        val ka = KeyAgreement.getInstance("ECDH").apply { init(priv); doPhase(senderKey, true) }
        val shared = ka.generateSecret()
        val ikm = hkdf(authSecret, shared, "WebPush: info\u0000".toByteArray() + uaPublic + asPublic, 32)
        val cek = hkdf(salt, ikm, "Content-Encoding: aes128gcm\u0000".toByteArray(), 16)
        val nonce = hkdf(salt, ikm, "Content-Encoding: nonce\u0000".toByteArray(), 12)
        // nosemgrep: kotlin.lang.security.gcm-detection.gcm-detection -- decrypt only; the nonce is the RFC 8188 value derived per message from the sender's random salt, never chosen here
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, SecretKeySpec(cek, "AES"), GCMParameterSpec(128, nonce)) }
        val plain = cipher.doFinal(body)
        var end = plain.size - 1
        while (end >= 0 && plain[end] == 0.toByte()) end--          // padding, then the record delimiter (0x02)
        return plain.copyOfRange(0, end)
    }

    private fun hkdf(salt: ByteArray, ikm: ByteArray, info: ByteArray, len: Int): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(salt, "HmacSHA256"))
        val prk = mac.doFinal(ikm)
        mac.init(SecretKeySpec(prk, "HmacSHA256"))
        return mac.doFinal(info + byteArrayOf(1)).copyOfRange(0, len)
    }

    private fun uncompressed(k: ECPublicKey): ByteArray {
        fun fixed(b: BigInteger): ByteArray { val a = b.toByteArray(); return when { a.size == 32 -> a; a.size > 32 -> a.copyOfRange(a.size - 32, a.size); else -> ByteArray(32 - a.size) + a } }
        return ByteBuffer.allocate(65).put(4).put(fixed(k.w.affineX)).put(fixed(k.w.affineY)).array()
    }
}
