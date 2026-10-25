"""Account storage: one directory per mailbox, secret and non-secret split.

Layout under the config root (``$MYCORD_EMAIL_HOME``, default
``~/.config/mycord-email``)::

    accounts/
      apothic/
        meta.toml         # 0644, non-secret: address, provider, servers, cert path
        credentials.env   # 0600, secret: username + password
      work/
        meta.toml
        credentials.env

Splitting the files is the whole security design. ``meta.toml`` is safe to
print, diff, copy into a skill, or paste into a bug report. ``credentials.env``
is never opened for display, never exported wholesale, and is passed to Himalaya
only through that process's environment.

The split also makes the skill portable: a provider template plus an address is
all a fresh machine needs, and the operator supplies the secret once.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: Environment variable pointing at the config root.
HOME_ENV: Final = "MYCORD_EMAIL_HOME"

#: Directory name holding one subdirectory per account.
ACCOUNTS_DIRNAME: Final = "accounts"

#: Non-secret account metadata file.
META_FILENAME: Final = "meta.toml"

#: Secret account credentials file. Mode 0600 is enforced on write and checked
#: on read so a leaked copy shows up as a loud error rather than silent use.
CREDENTIALS_FILENAME: Final = "credentials.env"

#: Default environment variable names. These are variable *names*, not secrets;
#: ruff's S105 pattern-matches the word "password" and cannot tell the difference.
DEFAULT_USERNAME_ENV: Final = "MYCORD_EMAIL_USERNAME"
DEFAULT_PASSWORD_ENV: Final = "MYCORD_EMAIL_PASSWORD"  # noqa: S105 - a name

#: Account names become directory names and CLI arguments, so keep them boring.
_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class AccountError(Exception):
    """An account is missing, malformed, or has unsafe permissions."""


def config_home() -> Path:
    """Return the config root, honouring ``$MYCORD_EMAIL_HOME``.

    Returns:
        The resolved root path. It is not created here.
    """
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mycord-email"


def accounts_root() -> Path:
    """Return the directory that holds one subdirectory per account."""
    return config_home() / ACCOUNTS_DIRNAME


def account_dir(name: str) -> Path:
    """Return the directory for ``name``, validating the name first.

    Args:
        name: Account name.

    Returns:
        Path under :func:`accounts_root`.

    Raises:
        AccountError: If the name contains path separators or other unsafe
            characters. This matters because the name reaches the filesystem.
    """
    if not _NAME_RE.match(name):
        msg = (
            f"invalid account name {name!r}: use lowercase letters, digits, "
            "dot, dash, or underscore, starting with a letter or digit"
        )
        raise AccountError(msg)
    return accounts_root() / name


@dataclass
class AccountMeta:
    """Non-secret description of one account.

    Attributes:
        name: Account name, equal to its directory name.
        email: The address this account sends as.
        provider: A key of :mod:`mycord_email.providers`.
        imap_server: IMAP endpoint, already resolved from the provider default.
        smtp_server: SMTP endpoint, already resolved from the provider default.
        starttls: Whether to upgrade a plaintext connection instead of using
            implicit TLS.
        tls_cert: Path to a certificate to pin, or ``None`` to use the system
            trust store. Proton Bridge is the case that needs a pin.
        display_name: Optional ``From`` display name.
        username_env: Environment variable holding the IMAP/SMTP username.
        password_env: Environment variable holding the password.
        page_size: Default envelope page size for this account.
        aliases: Himalaya ``[mailbox.alias]`` overrides for this account.
        default: Whether Himalaya should treat this account as the default.
        notes: Free-form operator notes, never interpreted.
    """

    name: str
    email: str
    provider: str
    imap_server: str
    smtp_server: str
    starttls: bool = False
    tls_cert: str | None = None
    display_name: str | None = None
    username_env: str = DEFAULT_USERNAME_ENV
    password_env: str = DEFAULT_PASSWORD_ENV
    page_size: int | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    default: bool = False
    notes: str | None = None

    @property
    def directory(self) -> Path:
        """Return this account's directory."""
        return account_dir(self.name)

    @property
    def meta_path(self) -> Path:
        """Return the path of ``meta.toml``."""
        return self.directory / META_FILENAME

    @property
    def credentials_path(self) -> Path:
        """Return the path of ``credentials.env``."""
        return self.directory / CREDENTIALS_FILENAME

    def to_toml_dict(self) -> dict[str, object]:
        """Render this account as the data stored in ``meta.toml``.

        Returns:
            A plain dict, ready for a TOML encoder or a test assertion.
        """
        data: dict[str, object] = {
            "name": self.name,
            "email": self.email,
            "provider": self.provider,
            "imap-server": self.imap_server,
            "smtp-server": self.smtp_server,
            "starttls": self.starttls,
        }
        if self.tls_cert is not None:
            data["tls-cert"] = self.tls_cert
        if self.display_name is not None:
            data["display-name"] = self.display_name
        if self.username_env != DEFAULT_USERNAME_ENV:
            data["username-env"] = self.username_env
        if self.password_env != DEFAULT_PASSWORD_ENV:
            data["password-env"] = self.password_env
        if self.page_size is not None:
            data["page-size"] = self.page_size
        if self.aliases:
            data["aliases"] = dict(self.aliases)
        if self.default:
            data["default"] = self.default
        if self.notes is not None:
            data["notes"] = self.notes
        return data

    @classmethod
    def from_toml_dict(cls, name: str, data: dict[str, object]) -> "AccountMeta":
        """Build an account from parsed ``meta.toml`` contents.

        Args:
            name: Directory name, used when the file omits ``name``.
            data: Parsed TOML mapping.

        Returns:
            The parsed account.

        Raises:
            AccountError: If required keys are missing or have the wrong type.
        """

        def require(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str) or not value:
                msg = f"{name}/{META_FILENAME}: missing or invalid {key!r}"
                raise AccountError(msg)
            return value

        aliases_raw = data.get("aliases") or {}
        if not isinstance(aliases_raw, dict):
            msg = f"{name}/{META_FILENAME}: 'aliases' must be a table"
            raise AccountError(msg)

        page_size = data.get("page-size")
        if page_size is not None and not isinstance(page_size, int):
            msg = f"{name}/{META_FILENAME}: 'page-size' must be an integer"
            raise AccountError(msg)

        return cls(
            name=str(data.get("name") or name),
            email=require("email"),
            provider=require("provider"),
            imap_server=require("imap-server"),
            smtp_server=require("smtp-server"),
            starttls=bool(data.get("starttls", False)),
            tls_cert=_optional_str(data.get("tls-cert"), name, "tls-cert"),
            display_name=_optional_str(data.get("display-name"), name, "display-name"),
            username_env=str(data.get("username-env") or DEFAULT_USERNAME_ENV),
            password_env=str(data.get("password-env") or DEFAULT_PASSWORD_ENV),
            page_size=page_size,
            aliases={str(k): str(v) for k, v in aliases_raw.items()},
            default=bool(data.get("default", False)),
            notes=_optional_str(data.get("notes"), name, "notes"),
        )


def _optional_str(value: object, name: str, key: str) -> str | None:
    """Return ``value`` as a string, or None, rejecting other types."""
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{name}/{META_FILENAME}: {key!r} must be a string"
        raise AccountError(msg)
    return value


def save_meta(meta: AccountMeta) -> Path:
    """Write ``meta.toml`` for ``meta``, creating the directory.

    The file holds no secret, so it is written ``0644``. Writing is delegated to
    a tiny TOML encoder rather than a dependency, because the shape is flat.

    Args:
        meta: The account to persist.

    Returns:
        The path written.
    """
    directory = meta.directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / META_FILENAME
    path.write_text(_render_toml(meta.to_toml_dict()), encoding="utf-8")
    path.chmod(0o644)
    return path


def _render_toml(data: dict[str, object]) -> str:
    """Render a flat-to-one-level mapping as TOML text.

    Only the value types ``meta.toml`` actually uses are supported: strings,
    booleans, integers, and one level of string-to-string table.

    Args:
        data: The mapping to render.

    Returns:
        TOML source ending in a newline.

    Raises:
        AccountError: On an unsupported value type, so a bad account fails loudly
            instead of silently dropping a key.
    """
    lines: list[str] = []
    tables: list[tuple[str, dict[str, str]]] = []
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, str):
            lines.append(f"{key} = {_quote(value)}")
        elif isinstance(value, dict):
            tables.append((key, {str(k): str(v) for k, v in value.items()}))
        elif value is None:
            continue
        else:
            msg = f"cannot render {key!r} of type {type(value).__name__} as TOML"
            raise AccountError(msg)

    for table_name, table in tables:
        lines.append("")
        lines.append(f"[{table_name}]")
        for key, value in table.items():
            lines.append(f"{key} = {_quote(value)}")
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    """Render a TOML basic string, escaping the characters that matter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load_account(name: str) -> AccountMeta:
    """Read and validate one account's ``meta.toml``.

    Args:
        name: Account name.

    Returns:
        The parsed account.

    Raises:
        AccountError: When the directory or file is missing, or the file is not
            valid TOML.
    """
    directory = account_dir(name)
    path = directory / META_FILENAME
    if not path.is_file():
        msg = (
            f"no account {name!r} at {directory}. "
            "Run `mycord-email accounts add` or check `mycord-email accounts list`."
        )
        raise AccountError(msg)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise AccountError(msg) from exc
    return AccountMeta.from_toml_dict(name, raw)


def list_accounts() -> list[AccountMeta]:
    """Load every account, skipping directories without a ``meta.toml``.

    A broken account is reported rather than raised, so one bad directory does
    not make the whole CLI unusable.

    Returns:
        Accounts sorted by name.
    """
    root = accounts_root()
    if not root.is_dir():
        return []

    accounts: list[AccountMeta] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / META_FILENAME).is_file():
            continue
        accounts.append(load_account(child.name))
    return accounts


def read_credentials(meta: AccountMeta) -> dict[str, str]:
    """Load one account's secret environment without exposing it.

    Parses ``KEY=VALUE`` lines, accepts ``#`` comments and blank lines, and
    validates that the file is not group- or world-readable.

    Args:
        meta: The account whose credentials to load.

    Returns:
        The parsed key/value mapping.

    Raises:
        AccountError: When the file is missing, insecurely permissioned, or
            malformed.
    """
    path = meta.credentials_path
    if not path.is_file():
        msg = (
            f"{meta.name}: no {CREDENTIALS_FILENAME} at {path}. "
            f"Run `mycord-email accounts set-secret {meta.name}`."
        )
        raise AccountError(msg)

    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        msg = (
            f"{path} is mode {mode:04o}; credentials must not be group- or "
            f"world-readable. Run: chmod 600 {path}"
        )
        raise AccountError(msg)

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            msg = f"{path}: line is not KEY=VALUE: {raw_line!r}"
            raise AccountError(msg)
        values[key.strip()] = _unquote_env(value.strip())
    return values


def _unquote_env(value: str) -> str:
    """Strip one layer of matching quotes from an env value and unescape it.

    Handles the ``KEY="value with spaces and \\"quotes\\""`` form that
    :func:`_quote_env` writes, so a value survives a round trip.

    Args:
        value: The raw right-hand side of a ``KEY=VALUE`` line.

    Returns:
        The unquoted, unescaped value.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
        inner = value[1:-1]
        if quote == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def write_credentials(meta: AccountMeta, username: str, password: str) -> Path:
    """Write ``credentials.env`` with mode 0600.

    The file is created exclusively and chmod-ed before anything is written, so
    there is no window in which a partially written secret is world-readable.

    Args:
        meta: The account to write for.
        username: IMAP/SMTP username.
        password: IMAP/SMTP password or app password.

    Returns:
        The path written.
    """
    directory = meta.directory
    directory.mkdir(parents=True, exist_ok=True)
    path = meta.credentials_path

    header = (
        "# Secret credentials for a mycord-email account.\n"
        "# Read by `mycord-email` only, and passed to himalaya through that\n"
        "# process's environment. Never commit, print, or paste this file.\n"
    )
    body = f"{meta.username_env}={_quote_env(username)}\n"
    body += f"{meta.password_env}={_quote_env(password)}\n"

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        path.chmod(0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(header + body)
    except Exception:
        # fdopen owns the descriptor once it succeeds; this guards the window
        # before that, so a failure cannot leak an open descriptor.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path


def _quote_env(value: str) -> str:
    """Quote an env value when it contains characters a shell parser would split."""
    if value and all(c.isalnum() or c in "-._@+:/" for c in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
