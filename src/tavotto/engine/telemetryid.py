"""Read the existing anonymous identifier; never generate or transmit one."""

from . import config


def from_settings(saved: dict) -> str | None:
    value = saved.get("install_id")
    return value if isinstance(value, str) and value else None


def current() -> str | None:
    return from_settings(config.load().get("telemetry") or {})
