"""Web Push encryption matches RFC 8291 exactly (so the phone can decrypt it) and can't be read or forged without the keys."""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from embertide import webpush as W


def _priv(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(W.b64u_decode(b64), "big"), ec.SECP256R1())


def test_rfc8291_appendix_a_vector():
    out = W.encrypt(b"When I grow up, I want to be a watermelon",
                    p256dh="BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                    auth="BTBZMqHH6r4Tts7J_aSIgg",
                    salt=W.b64u_decode("DGv6ra1nlYgDCS1FRnbzlw"),
                    private_key=_priv("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
    want = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A"
            "_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
    assert base64.urlsafe_b64encode(out).rstrip(b"=").decode() == want


def _keys():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()   # noqa: E731
    import os
    return k, b(pub), b(os.urandom(16))


def test_round_trip_and_fresh_keys_each_time():
    k, pub, auth = _keys()
    a, b = W.encrypt(b'{"t":"et","title":"hi"}', pub, auth), W.encrypt(b'{"t":"et","title":"hi"}', pub, auth)
    assert a != b                                   # new salt + key every message
    assert W.decrypt(a, k, auth) == b'{"t":"et","title":"hi"}'


def test_wrong_auth_secret_cannot_decrypt():
    k, pub, auth = _keys()
    data = W.encrypt(b"secret", pub, auth)
    _, _, other = _keys()
    with pytest.raises(Exception):
        W.decrypt(data, k, other)


def test_key_validation():
    _, pub, auth = _keys()
    assert W.valid_keys(pub, auth)
    assert not W.valid_keys("nope", auth)
    assert not W.valid_keys(pub, "c2hvcnQ")
