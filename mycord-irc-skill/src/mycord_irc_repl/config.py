"""IRC connection settings loaded from the environment and a nearby .env file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_NICKNAME = "mycord-agent"


class IRCConfigurationError(ValueError):
    """Raised when the requested IRC connection settings are invalid."""


@dataclass(frozen=True)
class IRCConfig:
    """Connection settings for one IRC network."""

    server: str | None = None
    port: int | None = None
    nickname: str | None = None
    username: str | None = None
    realname: str | None = None
    tls: bool = True
    server_password: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None

    @property
    def effective_port(self) -> int:
        """Return the conventional IRC port for the configured TLS setting."""
        return self.port or (6697 if self.tls else 6667)

    def validate_for_connect(self) -> None:
        """Validate settings needed before connecting to a network."""
        if not self.server:
            message = "set IRC_SERVER or pass --server to connect"
            raise IRCConfigurationError(message)
        if not self.nickname:
            message = "set IRC_NICK or pass --nick to connect"
            raise IRCConfigurationError(message)
        if self.port is not None and not 1 <= self.port <= 65535:
            message = "IRC_PORT must be between 1 and 65535"
            raise IRCConfigurationError(message)
        if bool(self.sasl_username) != bool(self.sasl_password):
            message = "set both IRC_SASL_USERNAME and IRC_SASL_PASSWORD, or neither"
            raise IRCConfigurationError(message)
        if not self.tls and (self.server_password or self.sasl_password):
            message = "refusing to send IRC credentials without TLS"
            raise IRCConfigurationError(message)


def load_config(
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    overrides: Mapping[str, object] | None = None,
) -> IRCConfig:
    """Load IRC settings, with explicit overrides taking precedence.

    Values from the nearest ``.env`` are used first, then process environment
    variables, then command-line overrides. Secrets are never returned by the
    CLI's status command or passed as subprocess arguments.
    """
    values = _read_nearest_dotenv(cwd or Path.cwd())
    values.update(dict(env if env is not None else os.environ))

    override_map = dict(overrides or {})
    server = _optional_string(override_map.get("server")) or _optional_string(
        values.get("IRC_SERVER")
    )
    nickname = _optional_string(override_map.get("nickname")) or _optional_string(
        values.get("IRC_NICK")
    ) or DEFAULT_NICKNAME

    port_value = override_map.get("port")
    if port_value is None:
        port_value = values.get("IRC_PORT")
    try:
        port = int(str(port_value)) if port_value not in (None, "") else None
    except (TypeError, ValueError) as exc:
        message = "IRC_PORT must be an integer"
        raise IRCConfigurationError(message) from exc

    tls_override = override_map.get("tls")
    tls = _parse_bool(tls_override) if tls_override is not None else _parse_bool(
        values.get("IRC_TLS", "true")
    )

    return IRCConfig(
        server=server,
        port=port,
        nickname=nickname,
        username=_optional_string(override_map.get("username"))
        or _optional_string(values.get("IRC_USERNAME")),
        realname=_optional_string(override_map.get("realname"))
        or _optional_string(values.get("IRC_REALNAME")),
        tls=tls,
        server_password=_optional_string(values.get("IRC_SERVER_PASSWORD")),
        sasl_username=_optional_string(values.get("IRC_SASL_USERNAME")),
        sasl_password=_optional_string(values.get("IRC_SASL_PASSWORD")),
    )


def _read_nearest_dotenv(cwd: Path) -> dict[str, str]:
    """Read the first .env found from the working directory up to the root."""
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        return {
            key: value
            for key, value in dotenv_values(candidate).items()
            if key is not None and value is not None
        }
    return {}


def _optional_string(value: object) -> str | None:
    """Convert a non-empty configuration value to a stripped string."""
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _parse_bool(value: object) -> bool:
    """Parse an environment-style boolean."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    message = "IRC_TLS must be true or false"
    raise IRCConfigurationError(message)
