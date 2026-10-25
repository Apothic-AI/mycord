"""Generate Himalaya's TOML config from account metadata.

Himalaya's config is the authority on *how to connect* — TLS mode, certificate
pinning, SASL mechanism, password resolution — and it is easy to get wrong by
hand. This module renders it from the provider table plus an account's
``meta.toml``, and deliberately does the two things a naive generator gets
wrong:

1. **Secrets are resolved by command, not embedded.** A password written into
   the TOML would land in a file that is (rightly) world-readable. Instead the
   generated fragment names an environment variable for the username and a
   ``printenv`` command for the password, so the secret lives only in the
   account's ``credentials.env`` and that process's environment.
2. **Nothing is guessed.** A missing provider or a provider that needs a
   certificate without one configured is an error at generate time, not an
   authentication failure at 2 a.m.

The output is a *fragment*: it holds only ``[accounts.<name>]`` tables plus the
per-account settings that belong to that name. Himalaya merges files given as a
``:``-separated list, so the wrapper passes this fragment first and the operator's
own config second, and the operator keeps whatever global defaults they like.
"""

from pathlib import Path

from mycord_email.accounts import AccountError, AccountMeta
from mycord_email.providers import PROVIDERS, get_provider


def generate_config(account: AccountMeta, *, default: bool | None = None) -> str:
    """Render one account as a Himalaya TOML fragment.

    Args:
        account: The account to render.
        default: Override the ``default`` flag. ``None`` keeps the account's own
            setting. The wrapper sets this explicitly so exactly one account in a
            merged run is the default.

    Returns:
        TOML source declaring ``[accounts.<name>]``.

    Raises:
        AccountError: When the account names an unknown provider, or its provider
            requires a pinned certificate and none is configured.
    """
    try:
        provider = get_provider(account.provider)
    except KeyError as exc:
        known = ", ".join(sorted(PROVIDERS))
        msg = f"{account.name}: unknown provider {account.provider!r}. Known: {known}"
        raise AccountError(msg) from exc

    if provider.needs_cert and not account.tls_cert:
        msg = (
            f"{account.name}: provider {provider.name!r} requires a pinned TLS "
            "certificate; set 'tls-cert' in meta.toml"
        )
        raise AccountError(msg)

    is_default = account.default if default is None else default
    lines: list[str] = [f"[accounts.{account.name}]"]
    lines.append(f"default = {'true' if is_default else 'false'}")
    lines.append(f'email = "{_escape(account.email)}"')
    if account.display_name:
        lines.append(f'display-name = "{_escape(account.display_name)}"')
    lines.append("")

    lines.extend(
        _endpoint_block(
            kind="imap",
            server=account.imap_server,
            username_env=account.username_env,
            password_env=account.password_env,
            starttls=account.starttls,
            tls_cert=account.tls_cert,
        )
    )
    lines.append("")
    lines.extend(
        _endpoint_block(
            kind="smtp",
            server=account.smtp_server,
            username_env=account.username_env,
            password_env=account.password_env,
            starttls=account.starttls,
            tls_cert=account.tls_cert,
        )
    )

    # Per-account settings must be emitted as dotted keys immediately after the
    # account header, because `envelope.list.page-size` still sits inside the
    # `[accounts.<name>]` table in TOML. Writing them bare after a later
    # bracketed table would attach them to the wrong owner.
    if account.page_size is not None:
        lines.append("")
        lines.append(f"envelope.list.page-size = {account.page_size}")

    for key, value in sorted(account.aliases.items()):
        lines.append(f'mailbox.alias.{key} = "{_escape(value)}"')

    return "\n".join(lines) + "\n"


def _endpoint_block(
    *,
    kind: str,
    server: str,
    username_env: str,
    password_env: str,
    starttls: bool,
    tls_cert: str | None,
) -> list[str]:
    """Render the ``imap.*`` or ``smtp.*`` keys for one endpoint.

    Args:
        kind: ``"imap"`` or ``"smtp"``.
        server: Server URL.
        username_env: Environment variable name holding the username.
        password_env: Environment variable name holding the password.
        starttls: Whether to negotiate STARTTLS on a plaintext connection.
        tls_cert: Certificate to pin, if any.

    Returns:
        The rendered lines.
    """
    lines = [f'{kind}.server = "{_escape(server)}"']
    if starttls:
        lines.append(f"{kind}.starttls = true")
    if tls_cert:
        lines.append(f'{kind}.tls.cert = "{_escape(tls_cert)}"')
    lines.append(f'{kind}.sasl.plain.username = "${{{username_env}}}"')
    # The password is fetched by subprocess at command time. Himalaya treats a
    # failing command as an authentication error, which is the behaviour we want:
    # a missing credential fails loudly instead of connecting anonymously.
    lines.append(f'{kind}.sasl.plain.password.command = ["sh", "-c", "printenv {password_env}"]')
    return lines


def _escape(value: str) -> str:
    """Escape a value for a TOML basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_config(
    accounts: list[AccountMeta], path: Path, *, default_name: str | None = None
) -> Path:
    """Write a merged fragment for several accounts to ``path``.

    Args:
        accounts: Accounts to render.
        path: Destination file.
        default_name: Which account to mark as default. When ``None`` and more
            than one account is present, the first is used, because Himalaya
            refuses to run without a default.

    Returns:
        The path written.

    Raises:
        AccountError: When ``accounts`` is empty or ``default_name`` is unknown.
    """
    if not accounts:
        msg = "no accounts to write"
        raise AccountError(msg)

    names = {a.name for a in accounts}
    if default_name is not None and default_name not in names:
        msg = f"default account {default_name!r} is not among {sorted(names)}"
        raise AccountError(msg)
    if default_name is None:
        default_name = accounts[0].name

    chunks = [generate_config(a, default=(a.name == default_name)) for a in accounts]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(chunks), encoding="utf-8")
    path.chmod(0o600)
    return path
