"""SSO ID-token verification and password policy. A real RSA key signs real tokens; every check must bite."""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException

from app.auth import check_password, verify_password
from app.sso import SSOError, verify_id_token

ISS, AUD, NONCE = "https://idp.example.com", "fred-client", "n-123"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(k, kid="k1"):
    n = k.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": kid, "n": b64(n.n.to_bytes(256, "big")), "e": b64(n.e.to_bytes(3, "big"))}]}


def token(k, claims=None, header=None):
    h = {"alg": "RS256", "kid": "k1"} | (header or {})
    c = {"iss": ISS, "aud": AUD, "exp": time.time() + 300, "iat": time.time(), "nonce": NONCE,
         "email": "Ana@Acme.com", "email_verified": True, "sub": "u1"} | (claims or {})
    signing = f"{b64(json.dumps(h).encode())}.{b64(json.dumps(c).encode())}"
    sig = k.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{b64(sig)}"


def test_valid_token_yields_lowercased_email(key):
    claims = verify_id_token(token(key), jwks(key), ISS, AUD, NONCE)
    assert claims["_email"] == "ana@acme.com"


def test_aud_list_requires_azp(key):
    verify_id_token(token(key, {"aud": [AUD, "other"], "azp": AUD}), jwks(key), ISS, AUD, NONCE)
    with pytest.raises(SSOError, match="different application"):
        verify_id_token(token(key, {"aud": [AUD, "other"]}), jwks(key), ISS, AUD, NONCE)


@pytest.mark.parametrize("claims,msg", [
    ({"iss": "https://evil.example.com"}, "different provider"),
    ({"aud": "someone-else"}, "different application"),
    ({"exp": time.time() - 3600}, "too long"),
    ({"nonce": "replayed"}, "doesn't match"),
    ({"email_verified": False}, "hasn't verified"),
    ({"email": None, "preferred_username": "not-an-email"}, "didn't share an email"),
])
def test_each_claim_check_bites(key, claims, msg):
    with pytest.raises(SSOError, match=msg):
        verify_id_token(token(key, claims), jwks(key), ISS, AUD, NONCE)


def test_entra_style_preferred_username(key):
    c = verify_id_token(token(key, {"email": None, "preferred_username": "bo@acme.com"}), jwks(key), ISS, AUD, NONCE)
    assert c["_email"] == "bo@acme.com"


def test_signature_from_another_key_is_rejected(key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(SSOError, match="signature is invalid"):
        verify_id_token(token(other), jwks(key), ISS, AUD, NONCE)


def test_tampered_payload_is_rejected(key):
    h, _, s = token(key).split(".")
    forged = b64(json.dumps({"iss": ISS, "aud": AUD, "exp": time.time() + 300, "nonce": NONCE,
                             "email": "ceo@acme.com"}).encode())
    with pytest.raises(SSOError, match="signature is invalid"):
        verify_id_token(f"{h}.{forged}.{s}", jwks(key), ISS, AUD, NONCE)


def test_alg_none_and_hs256_are_refused(key):
    for alg in ("none", "HS256"):
        with pytest.raises(SSOError, match="Unsupported"):
            verify_id_token(token(key, header={"alg": alg}), jwks(key), ISS, AUD, NONCE)


def test_unknown_kid_is_refused(key):
    two = {"keys": jwks(key, "a")["keys"] + jwks(key, "b")["keys"]}
    with pytest.raises(SSOError, match="doesn't publish"):
        verify_id_token(token(key, header={"kid": "zzz"}), two, ISS, AUD, NONCE)


@pytest.mark.parametrize("pw", ["password123", "aaaaaaaaaaaa", "ana.lopez-2026!", "qwerty1234"])
def test_guessable_passwords_refused(pw):
    with pytest.raises(HTTPException):
        check_password(pw, "ana.lopez@acme.com")


def test_good_password_accepted_and_sso_hash_never_verifies():
    check_password("correct horse battery", "ana@acme.com")
    assert not verify_password("anything", "sso$abcdef")
