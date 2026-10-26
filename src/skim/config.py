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
    # Phase 2 (all optional: without Graph credentials or JOBS_TOKEN the job endpoints are simply absent)
    jobs_token: str | None = field(default=None, repr=False)
    graph_credentials: tuple[str, str, str] | None = field(default=None, repr=False)  # (client id, secret, tenant)
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_max_concurrency: int = 4
    job_time_budget_seconds: float = 240.0
    backfill_since_days: int = 180
    backfill_max_messages_per_channel: int = 2000
    # Phase 4 student API (P4-D-104). Off unless API_ENABLED; then SUPABASE_URL is required.
    api_enabled: bool = False
    supabase_url: str | None = None
    supabase_jwt_secret: str | None = field(default=None, repr=False)  # legacy HS256 fallback only
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
        def num(name: str, default, cast, minimum):
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            try:
                v = cast(raw)
            except ValueError:
                problems.append(f"{name} must be a number")
                return default
            if v < minimum:
                problems.append(f"{name} must be >= {minimum}")
                return default
            return v

        concurrency = num("GRAPH_MAX_CONCURRENCY", 4, int, 1)
        budget = num("JOB_TIME_BUDGET_SECONDS", 240.0, float, 1)
        since_days = num("BACKFILL_SINCE_DAYS", 180, int, 1)
        max_msgs = num("BACKFILL_MAX_MESSAGES_PER_CHANNEL", 2000, int, 1)
        api_enabled = _flag(env, "API_ENABLED", False)
        supabase_url = (env.get("SUPABASE_URL") or "").strip().rstrip("/") or None
        if api_enabled and not (supabase_url and supabase_url.startswith("https://")):
            problems.append("SUPABASE_URL (https://<ref>.supabase.co) is required when API_ENABLED=true")
        creds = tuple(env.get(_PREFIX + k, "") for k in ("CLIENTID", "CLIENTSECRET", "TENANTID"))
        graph_credentials = creds if all(creds) and not anonymous else None
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
            jobs_token=env.get("JOBS_TOKEN") or None,
            graph_credentials=graph_credentials,
            graph_base_url=(env.get("GRAPH_BASE_URL") or "https://graph.microsoft.com/v1.0").rstrip("/"),
            graph_max_concurrency=concurrency,
            job_time_budget_seconds=budget,
            backfill_since_days=since_days,
            backfill_max_messages_per_channel=max_msgs,
            api_enabled=api_enabled,
            supabase_url=supabase_url,
            supabase_jwt_secret=env.get("SUPABASE_JWT_SECRET") or None,
            sdk_config=load_configuration_from_env(sdk_env),
        )
