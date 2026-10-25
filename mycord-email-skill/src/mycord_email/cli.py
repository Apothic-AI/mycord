"""Command-line interface for mycord-email.

Two halves:

* ``mycord-email accounts ...`` manages account definitions and secrets.
* ``mycord-email <anything else>`` forwards to himalaya with the selected
  account's config and credentials applied.

The forwarding half is intentionally a passthrough with no argument rewriting.
Himalaya's own CLI is the interface agents should learn — ``envelope search
subject "verification code"``, ``message read --json``, ``flag add --flag seen``
— and re-wrapping those verbs here would only create a second dialect to keep in
sync. The wrapper adds *which mailbox*, not *how to read mail*.
"""

import argparse
import getpass
import os
import shutil
import sys
from pathlib import Path

from mycord_email.accounts import (
    AccountError,
    AccountMeta,
    accounts_root,
    config_home,
    list_accounts,
    load_account,
    read_credentials,
    save_meta,
    write_credentials,
)
from mycord_email.providers import PROVIDERS, get_provider, provider_names
from mycord_email.run import (
    BINARY_ENV,
    EXTRA_CONFIG_ENV,
    build_invocation,
    run,
    show_generated_config,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_ACCOUNT = 3
EXIT_ERROR = 1

#: Subcommands this wrapper owns. Anything else is handed to himalaya verbatim.
OWN_COMMANDS = ("accounts", "providers", "config", "exec")


def _print_error(message: str) -> None:
    """Write an error line to stderr."""
    print(f"mycord-email: {message}", file=sys.stderr)


def _resolve_account(name: str | None) -> AccountMeta:
    """Return the requested account, or the only/marked-default one.

    Precedence: an explicit ``-a`` name, then ``$MYCORD_EMAIL_ACCOUNT``, then the
    single account marked ``default``, then the only account on disk.

    Args:
        name: Account name from the command line, if any.

    Returns:
        The resolved account.

    Raises:
        AccountError: When nothing matches, or the choice is ambiguous.
    """
    if name:
        return load_account(name)

    from_env = os.environ.get("MYCORD_EMAIL_ACCOUNT", "").strip()
    if from_env:
        return load_account(from_env)

    accounts = list_accounts()
    if not accounts:
        msg = (
            f"no accounts configured under {accounts_root()}. "
            "Create one with `mycord-email accounts add`."
        )
        raise AccountError(msg)

    marked = [a for a in accounts if a.default]
    if len(marked) == 1:
        return marked[0]
    if len(accounts) == 1:
        return accounts[0]

    names = ", ".join(a.name for a in accounts)
    msg = f"multiple accounts ({names}); pick one with -a NAME"
    raise AccountError(msg)


def cmd_accounts_list() -> int:
    """Print every configured account, without secrets."""
    accounts = list_accounts()
    if not accounts:
        print(f"no accounts under {accounts_root()}")
        return EXIT_OK

    width = max(len(a.name) for a in accounts)
    for account in accounts:
        marker = "*" if account.default else " "
        try:
            creds = read_credentials(account)
            secret_state = f"secret ok ({', '.join(sorted(creds))})"
        except AccountError as exc:
            secret_state = f"secret MISSING ({exc})"
        print(
            f"{marker} {account.name.ljust(width)}  {account.email}  "
            f"[{account.provider}]  {secret_state}"
        )
    return EXIT_OK


def cmd_accounts_show(name: str) -> int:
    """Print one account's metadata and secret *names*, never values."""
    account = load_account(name)
    print(f"name:        {account.name}")
    print(f"email:       {account.email}")
    print(f"provider:    {account.provider}")
    print(f"imap:        {account.imap_server}")
    print(f"smtp:        {account.smtp_server}")
    print(f"starttls:    {account.starttls}")
    if account.tls_cert:
        print(f"tls-cert:    {account.tls_cert}")
    if account.display_name:
        print(f"display-name:{account.display_name}")
    print(f"username-env:{account.username_env}")
    print(f"password-env:{account.password_env}")
    print(f"directory:   {account.directory}")
    try:
        creds = read_credentials(account)
        print(f"credentials: present, keys={sorted(creds)}")
    except AccountError as exc:
        print(f"credentials: MISSING ({exc})")
    return EXIT_OK


def cmd_accounts_add(args: argparse.Namespace) -> int:
    """Create or update one account's ``meta.toml``.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    try:
        provider = get_provider(args.provider)
    except KeyError:
        _print_error(
            f"unknown provider {args.provider!r}; choose from {', '.join(provider_names())}"
        )
        return EXIT_USAGE

    meta = AccountMeta(
        name=args.name,
        email=args.email,
        provider=provider.name,
        imap_server=args.imap or provider.imap_server,
        smtp_server=args.smtp or provider.smtp_server,
        starttls=provider.starttls if args.starttls is None else args.starttls,
        tls_cert=args.tls_cert,
        display_name=args.display_name,
        username_env=args.username_env,
        password_env=args.password_env,
        page_size=args.page_size,
        default=args.default,
    )
    path = save_meta(meta)
    print(f"wrote {path}")
    if provider.notes:
        for note in provider.notes:
            print(f"  note: {note}")
    if not meta.credentials_path.is_file():
        print(
            f"  next: mycord-email accounts set-secret {meta.name}",
        )
    return EXIT_OK


def cmd_accounts_set_secret(args: argparse.Namespace) -> int:
    """Prompt for and store an account's username and password.

    Values are read without echo and never printed. ``--password-stdin`` accepts a
    secret on stdin for scripted use.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    account = load_account(args.name)

    username = args.username
    if username is None:
        username = input(f"username for {account.name} [{account.email}]: ").strip()
        if not username:
            username = account.email

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif sys.stdin.isatty():
        password = getpass.getpass(f"password for {account.name}: ")
    else:
        password = sys.stdin.readline().rstrip("\n")

    if not password:
        _print_error("empty password; nothing written")
        return EXIT_ERROR

    path = write_credentials(account, username, password)
    print(f"wrote {path} (mode 600)")
    return EXIT_OK


def cmd_accounts_rm(args: argparse.Namespace) -> int:
    """Delete an account directory after an explicit confirmation.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    account = load_account(args.name)
    if not args.yes:
        answer = input(f"delete account {account.name!r} at {account.directory}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("aborted")
            return EXIT_ERROR

    shutil.rmtree(account.directory)
    print(f"removed {account.directory}")
    return EXIT_OK


def cmd_providers() -> int:
    """Print the supported provider table."""
    width = max(len(name) for name in PROVIDERS)
    for name, provider in sorted(PROVIDERS.items()):
        print(f"{name.ljust(width)}  {provider.summary}")
        print(f"{' '.ljust(width)}  imap {provider.imap_server}  smtp {provider.smtp_server}")
    return EXIT_OK


def cmd_config_show(name: str) -> int:
    """Print the himalaya fragment that would be generated for one account."""
    account = load_account(name)
    sys.stdout.write(show_generated_config(account))
    return EXIT_OK


def cmd_exec(args: argparse.Namespace) -> int:
    """Print the resolved himalaya command instead of running it."""
    account = _resolve_account(args.account)
    invocation = build_invocation(
        account,
        args.rest,
        extra_config=args.extra_config,
        with_credentials=False,
    )
    print(invocation.redacted())
    print(f"# account:    {invocation.account}")
    print(f"# config:     {':'.join(str(p) for p in invocation.config_chain)}")
    print(f"# env keys:   {', '.join(invocation.env_keys) or '(not loaded: dry run)'}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser with this wrapper's own subcommands declared and a passthrough
        for everything else.
    """
    parser = argparse.ArgumentParser(
        prog="mycord-email",
        description=(
            "Run himalaya against a named, per-account config. "
            f"Config root: ${config_home()!s} overridable via MYCORD_EMAIL_HOME."
        ),
        epilog=(
            "Any command not listed here is passed straight to himalaya, e.g.\n"
            "  mycord-email envelope list --page-size 10\n"
            "  mycord-email -a work message read --json 42\n"
            "  mycord-email envelope search subject 'verification code' order by date desc"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument(
        "-a",
        "--account",
        default=None,
        help="account name (default: $MYCORD_EMAIL_ACCOUNT, else the marked default)",
    )
    parser.add_argument(
        "--extra-config",
        type=Path,
        default=None,
        help="operator himalaya config to merge after the generated fragment "
        f"(default: ${EXTRA_CONFIG_ENV} or ~/.config/himalaya/config.toml)",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help=f"himalaya binary (default: ${BINARY_ENV} or 'himalaya')",
    )

    subs = parser.add_subparsers(dest="command")

    accounts = subs.add_parser("accounts", help="manage account definitions")
    account_subs = accounts.add_subparsers(dest="accounts_command", required=True)

    account_subs.add_parser("list", help="list accounts (no secrets)")

    show = account_subs.add_parser("show", help="show one account (no secret values)")
    show.add_argument("name")

    add = account_subs.add_parser("add", help="create or update an account definition")
    add.add_argument("name")
    add.add_argument("email")
    add.add_argument(
        "--provider",
        default="imap",
        choices=provider_names(),
        help="connection template (default: imap)",
    )
    add.add_argument("--imap", default=None, help="override IMAP server URL")
    add.add_argument("--smtp", default=None, help="override SMTP server URL")
    add.add_argument(
        "--starttls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override the provider's TLS mode",
    )
    add.add_argument("--tls-cert", default=None, help="certificate to pin")
    add.add_argument("--display-name", default=None)
    add.add_argument("--username-env", default="MYCORD_EMAIL_USERNAME")
    add.add_argument("--password-env", default="MYCORD_EMAIL_PASSWORD")
    add.add_argument("--page-size", type=int, default=None)
    add.add_argument(
        "--default",
        action="store_true",
        help="mark this account the default for the CLI",
    )

    secret = account_subs.add_parser("set-secret", help="store username and password")
    secret.add_argument("name")
    secret.add_argument("--username", default=None)
    secret.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting",
    )

    rm = account_subs.add_parser("rm", help="delete an account")
    rm.add_argument("name")
    rm.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    subs.add_parser("providers", help="list provider templates")

    config = subs.add_parser("config", help="inspect generated himalaya config")
    config_subs = config.add_subparsers(dest="config_command", required=True)
    config_show = config_subs.add_parser("show", help="print the generated fragment")
    config_show.add_argument("name", nargs="?", default=None)

    # `exec` prints what would run instead of running it — the debugging verb.
    # REMAINDER keeps himalaya's own flags out of argparse's hands.
    exec_parser = subs.add_parser(
        "exec", help="print the resolved himalaya command instead of running it"
    )
    exec_parser.add_argument("rest", nargs=argparse.REMAINDER)

    return parser


def _split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Strip this wrapper's leading global flags, leaving everything else.

    Only flags this wrapper owns are recognised, and only before the command
    starts. Everything after them belongs to himalaya — including its own global
    flags such as ``--json``, which must survive untouched.

    Args:
        argv: Arguments after the program name.

    Returns:
        Tuple of (wrapper global flags, remaining arguments).
    """
    globals_out: list[str] = []
    index = 0
    value_flags = ("-a", "--account", "--extra-config", "--binary")

    while index < len(argv):
        token = argv[index]
        if token in value_flags and index + 1 < len(argv):
            globals_out.extend([token, argv[index + 1]])
            index += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in value_flags if flag.startswith("--")):
            globals_out.append(token)
            index += 1
            continue
        break

    return globals_out, argv[index:]


def _is_own_command(rest: list[str]) -> bool:
    """Decide whether ``rest`` invokes this wrapper rather than himalaya.

    The decision keys off the first *command* token. A bare invocation and an
    explicit ``-h``/``--help`` show wrapper help; a known wrapper subcommand runs
    it; anything else — ``envelope``, ``message``, or even himalaya's own leading
    ``--json`` — is a passthrough.

    Args:
        rest: Arguments after the wrapper's global flags are stripped.

    Returns:
        True when the wrapper should handle the invocation itself.
    """
    if not rest:
        return True
    if rest[0] in ("-h", "--help"):
        return True
    return rest[0] in OWN_COMMANDS


def _passthrough_namespace(global_flags: list[str], rest: list[str]) -> argparse.Namespace:
    """Parse only the global flags, leaving ``rest`` for himalaya.

    Args:
        global_flags: The extracted wrapper flags.
        rest: The himalaya command and its arguments.

    Returns:
        A namespace with ``account``, ``extra_config``, ``binary``, and ``rest``.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-a", "--account", default=None)
    parser.add_argument("--extra-config", type=Path, default=None)
    parser.add_argument("--binary", default=None)
    parsed, _ = parser.parse_known_args(global_flags)
    parsed.rest = rest
    return parsed


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    raw = list(sys.argv[1:] if argv is None else argv)

    # Decide between this wrapper's own subcommands and a himalaya passthrough
    # before argparse runs: argparse would reject `envelope`, `message`, `mailbox`,
    # and himalaya's own `--json` as unknown.
    global_flags, rest = _split_passthrough(raw)
    if not _is_own_command(rest):
        return _dispatch_passthrough(_passthrough_namespace(global_flags, rest))

    parser = build_parser()
    args = parser.parse_args(raw)

    try:
        if args.command == "accounts":
            if args.accounts_command == "list":
                return cmd_accounts_list()
            if args.accounts_command == "show":
                return cmd_accounts_show(args.name)
            if args.accounts_command == "add":
                return cmd_accounts_add(args)
            if args.accounts_command == "set-secret":
                return cmd_accounts_set_secret(args)
            if args.accounts_command == "rm":
                return cmd_accounts_rm(args)
        if args.command == "providers":
            return cmd_providers()
        if args.command == "config":
            name = args.name or _resolve_account(args.account).name
            return cmd_config_show(name)
        if args.command == "exec":
            if not args.rest:
                _print_error("exec needs a command, e.g. `exec envelope list`")
                return EXIT_USAGE
            return cmd_exec(args)
    except AccountError as exc:
        _print_error(str(exc))
        return EXIT_NO_ACCOUNT

    parser.print_help()
    return EXIT_OK


def _dispatch_passthrough(args: argparse.Namespace) -> int:
    """Run himalaya with the resolved account.

    Args:
        args: Namespace carrying ``account``, ``extra_config``, ``binary``, and
            ``rest``.

    Returns:
        himalaya's exit code.
    """
    try:
        account = _resolve_account(args.account)
        return run(
            account,
            args.rest,
            extra_config=args.extra_config,
            binary=args.binary,
        )
    except AccountError as exc:
        _print_error(str(exc))
        return EXIT_NO_ACCOUNT


if __name__ == "__main__":
    raise SystemExit(main())
