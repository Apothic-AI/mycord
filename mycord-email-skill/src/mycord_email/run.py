"""Build and execute Himalaya invocations with the right config and secrets.

The wrapper's job at runtime is narrow and worth stating precisely:

* assemble himalaya's ``-c <fragment>:<operator configs>`` merge so generated
  accounts and the operator's own config coexist,
* load exactly one account's secrets into *that child process's* environment,
* exec himalaya with stdout/stderr/exit code untouched.

Everything a caller might want to inspect but not run — the argv, the config
path, the environment keys — is available from :func:`build_invocation` without
executing anything, which is what the tests exercise.
"""

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from mycord_email.accounts import (
    AccountError,
    AccountMeta,
    config_home,
    read_credentials,
)
from mycord_email.config import write_config

#: Env var overriding the himalaya binary, for a pinned or remote build.
BINARY_ENV: Final = "MYCORD_EMAIL_BINARY"

#: Default himalaya binary name resolved on PATH.
DEFAULT_BINARY: Final = "himalaya"

#: Env var naming extra himalaya config files to merge after ours.
EXTRA_CONFIG_ENV: Final = "MYCORD_EMAIL_EXTRA_CONFIG"

#: The operator's own himalaya config, merged when it exists. Merging it (rather
#: than replacing it) is what lets a generated account and a hand-written one
#: coexist on the same machine.
DEFAULT_EXTRA_CONFIG: Final = Path.home() / ".config" / "himalaya" / "config.toml"


@dataclass
class Invocation:
    """A fully resolved himalaya call that has not run yet.

    Attributes:
        argv: Complete argument vector, binary first.
        config_path: The generated fragment on disk.
        config_chain: Every config file passed to ``-c``, in merge order.
        env_keys: Names of the environment variables this call will add. Values
            are deliberately absent so an invocation can be logged safely.
        account: The account being used.
    """

    argv: list[str]
    config_path: Path
    config_chain: list[Path]
    env_keys: list[str] = field(default_factory=list)
    account: str = ""

    def redacted(self) -> str:
        """Render the command as a string with no secrets present.

        Returns:
            A shell-quoted command line safe to print or log.
        """
        return " ".join(shlex.quote(part) for part in self.argv)


def _extra_configs(explicit: Path | None) -> list[Path]:
    """Return the operator config files to merge after the generated fragment.

    Args:
        explicit: A path given on the command line, which wins outright.

    Returns:
        Existing config paths, in merge order. Empty when there is nothing to
        merge, which is the normal case on a fresh machine.
    """
    if explicit is not None:
        if not explicit.is_file():
            msg = f"config file not found: {explicit}"
            raise AccountError(msg)
        return [explicit]

    from_env = os.environ.get(EXTRA_CONFIG_ENV, "").strip()
    if from_env:
        # Same ``:``-separated convention himalaya itself uses for -c.
        return [Path(part).expanduser() for part in from_env.split(":") if part]

    return [DEFAULT_EXTRA_CONFIG] if DEFAULT_EXTRA_CONFIG.is_file() else []


def _fragment_path(accounts: list[AccountMeta], default_name: str) -> Path:
    """Write the generated config fragment and return its path.

    The fragment is written into the config root rather than a temp dir so a
    failed run can be inspected and ``mycord-email config show`` can print the
    same bytes himalaya actually read.

    Args:
        accounts: Accounts to render into the fragment.
        default_name: Account to mark default.

    Returns:
        Path of the fragment.
    """
    path = config_home() / "himalaya.generated.toml"
    write_config(accounts, path, default_name=default_name)
    return path


def build_invocation(
    account: AccountMeta,
    args: list[str],
    *,
    extra_config: Path | None = None,
    binary: str | None = None,
    with_credentials: bool = True,
) -> Invocation:
    """Assemble a himalaya call without running it.

    Args:
        account: The account to select.
        args: himalaya subcommand and its arguments, e.g. ``["envelope", "list"]``.
        extra_config: Operator config to merge after the generated fragment.
        binary: himalaya binary; defaults to ``$MYCORD_EMAIL_BINARY`` or PATH.
        with_credentials: Load the account's secrets and report the env keys the
            child will receive. Set False to build an invocation for a dry run
            that should not touch ``credentials.env``.

    Returns:
        The resolved invocation.

    Raises:
        AccountError: When credentials are required but missing or unsafe.
    """
    resolved_binary = binary or os.environ.get(BINARY_ENV, DEFAULT_BINARY)
    fragment = _fragment_path([account], account.name)
    # Merge order is load-bearing: himalaya lets the LAST file win on a key
    # collision. The generated fragment therefore goes last, so a stale
    # hand-written `[accounts.<name>]` for the same mailbox cannot silently
    # redirect the connection to an old port. Operator files still supply their
    # own accounts and any global defaults, because our fragment only ever writes
    # `accounts.<name>.*` keys.
    chain = [*_extra_configs(extra_config), fragment]

    argv = [
        resolved_binary,
        "-c",
        ":".join(str(p) for p in chain),
        "-a",
        account.name,
        *args,
    ]

    env_keys: list[str] = []
    if with_credentials:
        # Read here so a missing or 0644 credential file fails before we spawn
        # anything, and so the error names the account the caller asked for.
        creds = read_credentials(account)
        env_keys = sorted(creds)

    return Invocation(
        argv=argv,
        config_path=fragment,
        config_chain=chain,
        env_keys=env_keys,
        account=account.name,
    )


def build_env(account: AccountMeta) -> dict[str, str]:
    """Return the child environment: this process's environment plus secrets.

    The account's credentials are added under the variable names its ``meta.toml``
    declares, because the generated himalaya config references exactly those
    names. Nothing is exported into the parent process.

    Args:
        account: The account whose credentials to load.

    Returns:
        A fresh environment mapping.

    Raises:
        AccountError: When the credential file is missing or insecurely
            permissioned.
    """
    env = dict(os.environ)
    env.update(read_credentials(account))
    return env


def run(
    account: AccountMeta,
    args: list[str],
    *,
    extra_config: Path | None = None,
    binary: str | None = None,
) -> int:
    """Run himalaya for one account and return its exit code.

    Output is inherited rather than captured: himalaya already renders tables,
    JSON, and message bodies, and buffering them here would only lose colour and
    fail on large attachments.

    Args:
        account: The account to select.
        args: himalaya subcommand and arguments.
        extra_config: Operator config to merge after the generated fragment.
        binary: himalaya binary override.

    Returns:
        himalaya's exit code, or 127 when the binary is not executable.

    Raises:
        AccountError: When credentials are missing or unsafe.
    """
    invocation = build_invocation(account, args, extra_config=extra_config, binary=binary)
    env = build_env(account)
    try:
        completed = subprocess.run(  # noqa: S603 - argv is fixed, no shell
            invocation.argv,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        binary_name = invocation.argv[0]
        print(
            f"himalaya not found: {binary_name}. Install it, or set {BINARY_ENV}.",
            file=sys.stderr,
        )
        return 127
    return completed.returncode


def show_generated_config(account: AccountMeta) -> str:
    """Render the fragment for ``account`` without writing it.

    Args:
        account: The account to render.

    Returns:
        The TOML text, with environment variable *names* but no secret values.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fragment.toml"
        write_config([account], path, default_name=account.name)
        return path.read_text(encoding="utf-8")
