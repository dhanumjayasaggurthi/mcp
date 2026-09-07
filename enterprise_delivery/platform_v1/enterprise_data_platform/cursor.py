from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


class CursorError(ValueError):
    """Base class for cursor validation errors."""


class CursorExpired(CursorError):
    pass


class CursorTampered(CursorError):
    pass


class CursorScopeMismatch(CursorError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


@dataclass(frozen=True)
class CursorCodec:
    """HMAC-signed, opaque keyset cursor.

    Cursors carry only continuation state, never raw credentials. They are
    dataset/version scoped and expire, so a cursor cannot be replayed against a
    different data product after schema/index promotion.
    """

    secret: bytes
    ttl_seconds: int = 3600
    issuer: str = "rdh-v1"

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("cursor signing secret must be at least 32 bytes")
        if self.ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")

    def encode(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        position: Mapping[str, Any],
        sort: list[dict[str, str]],
        now: Optional[int] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> str:
        issued = int(time.time() if now is None else now)
        payload: Dict[str, Any] = {
            "iss": self.issuer,
            "iat": issued,
            "exp": issued + self.ttl_seconds,
            "dataset": dataset_id,
            "version": dataset_version,
            "position": dict(position),
            "sort": sort,
        }
        if extra:
            payload["extra"] = dict(extra)
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        body = _b64e(raw)
        sig = _b64e(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def decode(
        self,
        token: str,
        *,
        dataset_id: str,
        dataset_version: str,
        now: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            body, sig = token.split(".", 1)
        except ValueError as exc:
            raise CursorTampered("malformed cursor") from exc

        expected = _b64e(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise CursorTampered("cursor signature is invalid")

        try:
            payload = json.loads(_b64d(body))
        except Exception as exc:  # noqa: BLE001 - all decode failures are invalid cursors
            raise CursorTampered("cursor payload is invalid") from exc

        if payload.get("iss") != self.issuer:
            raise CursorScopeMismatch("cursor issuer mismatch")
        if payload.get("dataset") != dataset_id or payload.get("version") != dataset_version:
            raise CursorScopeMismatch("cursor does not belong to this dataset/version")

        current = int(time.time() if now is None else now)
        if current >= int(payload.get("exp", 0)):
            raise CursorExpired("cursor has expired")
        return payload
