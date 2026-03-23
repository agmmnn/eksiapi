"""
Ekşi Sözlük Api-Secret generation — fully reversed from app v2.4.4

Plaintext format (confirmed by Frida hook on Cipher.doFinal):
  {randomHex}-{APP_UUID}-{len^2}-{adjustedTime}-{dayOff}-{hourOff}-{minOff}-eksisozluk-android/137-{clientSecret}

Where:
  randomHex    = random lowercase hex, length in [40, 80]
  APP_UUID     = "c8ecd738-dc33-45a4-a977-ae8e2a51c644"  (embedded in _.sf.a)
  len^2        = len(randomHex)^2
  adjustedTime = serverTimeMs - dayOff*86400000 - hourOff*3600000 - minOff*60000
  dayOff       = random int in [1, 5000]
  hourOff      = random int in [1, 5000]
  minOff       = random int in [1, 10000]
  clientSecret = the Client-Secret UUID sent in headers (caller-supplied)

RSA: RSA/ECB/PKCS1Padding with embedded 2048-bit public key
Output: Base64 NO_WRAP (no newlines)
"""

import base64
import random
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# ── Embedded RSA public key (from _.sf.c0) ───────────────────────────────────
_RSA_PUBKEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4cNO1MGajB7fTxuZ1bC+"
    "lSwMuob7YgTH441nWgTA+BDlw5bdYGyAIrTCkaSLrwimgG5rHT2izPqzn1rGRoqm"
    "OV2VwIMkTF0FwmZ+STDu09zF2y7y7/OkZ9FaNOQTBDoCS1t2z38WC6YwzA4b/GTr"
    "c/FFfMnVw4GPgIWlsxkNYMIspbtLEWcQGaa76e1nGPWxKgN0vF6T2lvhJaHnva9s"
    "La9v+V2gcIlELF2KyIbNaN0zoy0bna7Mh1FA8Z/8BFPH2aIIdvvhIycZHcISZdsd"
    "8giHsXSYkZlOqP7JS8ChKgWUccPNQlI+n7NbxmIGIFfWPXFIOc5sWbNQ+RtrLYrJ"
    "owIDAQAB"
)

# ── App constants (from _.sf fields) ─────────────────────────────────────────
APP_UUID    = "c8ecd738-dc33-45a4-a977-ae8e2a51c644"   # _.sf.a
APP_VERSION = "eksisozluk-android/137"                  # _.sf build string
HEX_MIN     = 40
HEX_MAX     = 80
DAY_MAX     = 5000
HOUR_MAX    = 5000
MIN_MAX     = 10000

_PUBLIC_KEY = serialization.load_der_public_key(
    base64.b64decode(_RSA_PUBKEY_B64),
    backend=default_backend()
)


def generate_api_secret(server_time_ms: int, client_secret: str) -> str:
    """
    Reproduces _.x60.g(_.qw.e(client_secret, _.Qy.k(server_time_ms))).

    Args:
        server_time_ms: timestamp from GET /v2/clientsettings/time  (Data field)
        client_secret:  the Client-Secret UUID for this session

    Returns:
        Base64 Api-Secret string (no newlines)
    """
    day_off  = random.randint(1, DAY_MAX)
    hour_off = random.randint(1, HOUR_MAX)
    min_off  = random.randint(1, MIN_MAX)

    adjusted_ms = (server_time_ms
                   - day_off  * 86_400_000
                   - hour_off *  3_600_000
                   - min_off  *     60_000)

    # random hex substring (6 UUIDs concatenated without dashes, sliced)
    pool = (uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex +
            uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex)
    hex_len = random.randint(HEX_MIN, HEX_MAX)
    random_hex = pool[:hex_len]

    plaintext = (
        f"{random_hex}-{APP_UUID}-{hex_len * hex_len}"
        f"-{adjusted_ms}"
        f"-{day_off}-{hour_off}-{min_off}"
        f"-{APP_VERSION}"
        f"-{client_secret}"
    )

    ciphertext = _PUBLIC_KEY.encrypt(
        plaintext.encode("utf-8"),
        padding.PKCS1v15()
    )
    return base64.b64encode(ciphertext).decode("ascii")


if __name__ == "__main__":
    test_server_time = 1774216389918
    test_client_secret = str(uuid.uuid4())
    for i in range(3):
        secret = generate_api_secret(test_server_time, test_client_secret)
        print(f"[{i+1}] len={len(secret)} Api-Secret={secret[:60]}...")
    print("\n[+] Api-Secret generation working.")
