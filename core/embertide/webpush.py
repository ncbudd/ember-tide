"""Web Push message encryption (RFC 8291, "aes128gcm" from RFC 8188) for app notifications over UnifiedPush.

The phone app makes a key pair and an auth secret and gives us only the public half. Each message is encrypted for it,
so the push server in between (ntfy, Cloudflare) only ever carries ciphertext, and nobody who learns the push address
can make the app show a notification: without the auth secret the app can't decrypt it and drops it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64u_decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def valid_keys(p256dh: str, auth: str) -> bool:
    try:
        pub, sec = b64u_decode(p256dh), b64u_decode(auth)
        if len(pub) != 65 or pub[0] != 4 or len(sec) != 16:
            return False
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
        return True
    except Exception:  # noqa: BLE001
        return False


def encrypt(plaintext: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            private_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """One aes128gcm record: salt(16) | rs(4) | idlen(1) | our public key(65) | ciphertext."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = private_key or ec.generate_private_key(ec.SECP256R1())
    as_public = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    rs = 4096
    if len(plaintext) + 1 + 16 > rs:
        raise ValueError("message too long for one record")
    body = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)     # 0x02: last (and only) record
    return salt + struct.pack(">I", rs) + bytes([len(as_public)]) + as_public + body


def decrypt(data: bytes, private_key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The receiving side, used by the tests to prove the format round-trips."""
    salt, rs, idlen = data[:16], struct.unpack(">I", data[16:20])[0], data[20]
    as_public = data[21:21 + idlen]
    body = data[21 + idlen:]
    ua_public = private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = private_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(b64u_decode(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body, None)
    assert rs >= len(body)
    return plain.rstrip(b"\x00")[:-1]
