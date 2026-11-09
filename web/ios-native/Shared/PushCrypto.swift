// Web Push decryption (RFC 8291, aes128gcm), the same scheme the Android app uses (EmberCrypto.kt): the server
// encrypts every message for this phone, so Google and Apple only ever carry ciphertext.
// Keys live in the shared Keychain so the Notification Service Extension can decrypt before the alert is shown.

import CryptoKit
import Foundation

public enum PushCrypto {
    public struct Keys { public let publicB64: String; public let authB64: String }

    /// This install's key pair and auth secret, made once.
    public static func keys() -> Keys {
        if let priv = Keychain.get("push_priv"), let auth = Keychain.get("push_auth"),
           let key = try? P256.KeyAgreement.PrivateKey(rawRepresentation: unb64u(priv) ?? Data()) {
            return Keys(publicB64: b64u(key.publicKey.x963Representation), authB64: auth)
        }
        let key = P256.KeyAgreement.PrivateKey()
        var secret = Data(count: 16)
        _ = secret.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 16, $0.baseAddress!) }
        Keychain.set("push_priv", b64u(key.rawRepresentation))
        Keychain.set("push_auth", b64u(secret))
        return Keys(publicB64: b64u(key.publicKey.x963Representation), authB64: b64u(secret))
    }

    /// Decrypt one message for this install. Throws if it wasn't encrypted for us.
    public static func decrypt(_ data: Data) throws -> Data {
        guard let privB = Keychain.get("push_priv").flatMap(unb64u), let authB = Keychain.get("push_auth").flatMap(unb64u) else {
            throw CocoaError(.coderInvalidValue)
        }
        let priv = try P256.KeyAgreement.PrivateKey(rawRepresentation: privB)
        return try decrypt(data, privateKey: priv, authSecret: authB)
    }

    /// Pure function, checkable against the RFC 8291 test vector.
    public static func decrypt(_ data: Data, privateKey: P256.KeyAgreement.PrivateKey, authSecret: Data) throws -> Data {
        let bytes = [UInt8](data)
        guard bytes.count > 21 else { throw CocoaError(.coderInvalidValue) }
        let salt = Data(bytes[0..<16])
        let idLen = Int(bytes[20])
        guard bytes.count > 21 + idLen + 16 else { throw CocoaError(.coderInvalidValue) }
        let asPublic = Data(bytes[21..<(21 + idLen)])
        let body = Data(bytes[(21 + idLen)...])
        let sender = try P256.KeyAgreement.PublicKey(x963Representation: asPublic)
        let shared = try privateKey.sharedSecretFromKeyAgreement(with: sender)
        let uaPublic = privateKey.publicKey.x963Representation
        let ikm = hkdf(salt: authSecret, ikm: shared.withUnsafeBytes { Data($0) },
                       info: Data("WebPush: info\u{0}".utf8) + uaPublic + asPublic, length: 32)
        let cek = hkdf(salt: salt, ikm: ikm, info: Data("Content-Encoding: aes128gcm\u{0}".utf8), length: 16)
        let nonce = hkdf(salt: salt, ikm: ikm, info: Data("Content-Encoding: nonce\u{0}".utf8), length: 12)
        let box = try AES.GCM.SealedBox(combined: nonce + body)          // body = ciphertext ‖ 16-byte tag
        var plain = [UInt8](try AES.GCM.open(box, using: SymmetricKey(data: cek)))
        while let last = plain.last, last == 0 { plain.removeLast() }       // padding
        if plain.last == 2 { plain.removeLast() }                            // record delimiter
        return Data(plain)
    }

    private static func hkdf(salt: Data, ikm: Data, info: Data, length: Int) -> Data {
        let prk = HMAC<SHA256>.authenticationCode(for: ikm, using: SymmetricKey(data: salt))
        let okm = HMAC<SHA256>.authenticationCode(for: info + Data([1]), using: SymmetricKey(data: Data(prk)))
        return Data(okm).prefix(length)
    }

    public static func b64u(_ d: Data) -> String {
        d.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    public static func unb64u(_ s: String) -> Data? {
        var t = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while t.count % 4 != 0 { t += "=" }
        return Data(base64Encoded: t)
    }
}
