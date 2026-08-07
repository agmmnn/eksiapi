"""Runtime configuration for the reverse-engineered Android API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AndroidFingerprint:
    """Android client values included in authentication requests."""

    version: str = "2.4.10"
    build: int = 144
    platform: str = "g"
    device_model: str = "Google sdk_gphone_x86_64"
    tls_impersonate: str = "chrome110"

    @property
    def user_agent(self) -> str:
        return f"eksisozluk-android/{self.build}"


DEFAULT_FINGERPRINT = AndroidFingerprint()
