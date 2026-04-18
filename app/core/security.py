from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from typing import Optional

from cryptography.fernet import Fernet


class TokenCipher:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")


class SignedTokenError(ValueError):
    """Raised when a signed token cannot be verified."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class SignedTokenManager:
    def __init__(self, secret: str, *, namespace: str) -> None:
        self._secret = hashlib.sha256(f"{namespace}:{secret}".encode("utf-8")).digest()

    def dumps(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{_b64url_encode(body)}.{_b64url_encode(signature)}"

    def loads(self, token: str) -> dict[str, Any]:
        try:
            body_part, signature_part = token.split(".", 1)
            body = _b64url_decode(body_part)
            signature = _b64url_decode(signature_part)
        except Exception as exc:
            raise SignedTokenError("Malformed signed token.") from exc

        expected_signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise SignedTokenError("Signed token verification failed.")

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise SignedTokenError("Malformed signed token payload.") from exc
        if not isinstance(payload, dict):
            raise SignedTokenError("Signed token payload must be an object.")
        return payload
