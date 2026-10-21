"""Environment loading + fail-fast validation."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

_PREFIX = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
_TRUE = {"1", "true", "yes", "on"}


class ConfigError(Exception):
    """Raised with a message naming missing/invalid variables (never their values)."""


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    return default if raw is None or raw == "" else raw.strip().lower() in _TRUE


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    storage: str  # postgres | memory
    database_url: str | None
    debug_token: str | None
    pairing_secret: str
    log_message_text: bool
    anonymous_allowed: bool
    # Passed to the SDK's MsalConnectionManager / AgentApplication.
    sdk_config: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        problems: list[str] = []
        anonymous = _flag(env, _PREFIX + "ANONYMOUS_ALLOWED", False)

        if not anonymous:
            for suffix in ("CLIENTID", "CLIENTSECRET", "TENANTID"):
                if not env.get(_PREFIX + suffix):
                    problems.append(f"{_PREFIX}{suffix} is required (or set ANONYMOUS_ALLOWED=true for local testing)")

        storage = (env.get("STORAGE") or "postgres").lower()
        if storage not in ("postgres", "memory"):
            problems.append("STORAGE must be 'postgres' or 'memory'")
        if storage == "postgres" and not env.get("DATABASE_URL"):
            problems.append("DATABASE_URL is required when STORAGE=postgres")
        if not env.get("PAIRING_SECRET"):
            problems.append("PAIRING_SECRET is required")
        try:
            port = int(env.get("PORT") or 8080)
        except ValueError:
            port = 8080
            problems.append("PORT must be an integer")
        if problems:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems))

        sdk_env = {k: v for k, v in env.items() if k.startswith("CONNECTIONS__")}
        if anonymous:
            # The SDK still wants a service connection to exist; placeholders are never used to call out.
            for suffix, placeholder in (("CLIENTID", "anonymous"), ("CLIENTSECRET", "anonymous"),
                                        ("TENANTID", "anonymous"), ("AUTHTYPE", "ClientSecret")):
                sdk_env.setdefault(_PREFIX + suffix, placeholder)
        from microsoft_agents.activity import load_configuration_from_env

        return cls(
            port=port,
            log_level=(env.get("LOG_LEVEL") or "INFO").upper(),
            storage=storage,
            database_url=env.get("DATABASE_URL") or None,
            debug_token=env.get("DEBUG_TOKEN") or None,
            pairing_secret=env["PAIRING_SECRET"],
            log_message_text=_flag(env, "LOG_MESSAGE_TEXT", True),
            anonymous_allowed=anonymous,
            sdk_config=load_configuration_from_env(sdk_env),
        )
