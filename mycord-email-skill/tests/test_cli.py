"""Tests for invocation building and the CLI router.

The passthrough router is the part most likely to break silently: if a himalaya
verb such as ``envelope`` were swallowed by argparse, agents would get a usage
error instead of mail. These tests pin the routing decision and the argv shape.
"""

from pathlib import Path

import pytest

from mycord_email.accounts import AccountMeta, save_meta, write_credentials
from mycord_email.cli import (
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    EXIT_USAGE,
    _resolve_account,
    _split_passthrough,
    build_parser,
    main,
)
from mycord_email.run import build_invocation

BRIDGE_CERT = "/tmp/test-proton-bridge.crt"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate MYCORD_EMAIL_HOME and clear account selection variables."""
    root = tmp_path / "cfg"
    monkeypatch.setenv("MYCORD_EMAIL_HOME", str(root))
    monkeypatch.delenv("MYCORD_EMAIL_ACCOUNT", raising=False)
    monkeypatch.setenv("MYCORD_EMAIL_EXTRA_CONFIG", "/nonexistent-extra.toml")
    return root


def make_account(name: str = "work", **overrides: object) -> AccountMeta:
    """Build and persist a plain IMAP account."""
    defaults: dict[str, object] = {
        "name": name,
        "email": f"{name}@example.com",
        "provider": "imap",
        "imap_server": "imaps://imap.example.com:993",
        "smtp_server": "smtps://smtp.example.com:465",
    }
    defaults.update(overrides)
    return AccountMeta(**defaults)  # type: ignore[arg-type]


def persist(account: AccountMeta, *, secret: bool = True) -> AccountMeta:
    """Write an account's meta, and its credentials unless asked not to."""
    save_meta(account)
    if secret:
        write_credentials(account, "user", "pw")
    return account


def test_build_invocation_shape(home: Path) -> None:
    account = persist(make_account())
    invocation = build_invocation(account, ["envelope", "list"])

    assert invocation.argv[0] == "himalaya"
    assert invocation.argv[1] == "-c"
    assert invocation.argv[3:5] == ["-a", "work"]
    assert invocation.argv[5:] == ["envelope", "list"]
    assert invocation.account == "work"
    assert invocation.env_keys == ["MYCORD_EMAIL_PASSWORD", "MYCORD_EMAIL_USERNAME"]


def test_invocation_merges_extra_config(home: Path, tmp_path: Path) -> None:
    account = persist(make_account())
    extra = tmp_path / "operator.toml"
    extra.write_text("[accounts.other]\ndefault = false\n")

    invocation = build_invocation(account, ["mailbox", "list"], extra_config=extra)
    chain = invocation.config_chain
    # The generated fragment must come LAST: himalaya lets later files win, so a
    # stale hand-written account of the same name cannot shadow ours.
    assert chain[-1] == invocation.config_path
    assert chain[0] == extra
    assert invocation.argv[2].split(":")[-1] == str(invocation.config_path)


def test_invocation_rejects_missing_extra_config(home: Path, tmp_path: Path) -> None:
    from mycord_email.accounts import AccountError

    account = persist(make_account())
    with pytest.raises(AccountError, match="config file not found"):
        build_invocation(account, ["x"], extra_config=tmp_path / "absent.toml")


def test_invocation_can_skip_credentials(home: Path) -> None:
    account = persist(make_account(), secret=False)
    invocation = build_invocation(account, ["mailbox", "list"], with_credentials=False)
    assert invocation.env_keys == []


def test_invocation_requires_credentials_by_default(home: Path) -> None:
    from mycord_email.accounts import AccountError

    account = persist(make_account(), secret=False)
    with pytest.raises(AccountError, match="set-secret"):
        build_invocation(account, ["mailbox", "list"])


def test_redacted_has_no_secret(home: Path) -> None:
    account = persist(make_account())
    rendered = build_invocation(account, ["envelope", "list"]).redacted()
    assert "pw" not in rendered.split()
    assert "himalaya" in rendered


def test_fragment_written_to_config_root(home: Path) -> None:
    account = persist(make_account())
    invocation = build_invocation(account, ["envelope", "list"])
    assert invocation.config_path.parent == home
    assert invocation.config_path.is_file()
    assert "[accounts.work]" in invocation.config_path.read_text()


def test_split_passthrough_extracts_globals() -> None:
    globals_out, rest = _split_passthrough(["-a", "job", "envelope", "search", "subject", "hello"])
    assert globals_out == ["-a", "job"]
    assert rest == ["envelope", "search", "subject", "hello"]


def test_split_passthrough_accepts_equals_form() -> None:
    globals_out, rest = _split_passthrough(["--account=job", "flag", "add"])
    assert globals_out == ["--account=job"]
    assert rest == ["flag", "add"]


def test_resolve_account_prefers_explicit(home: Path) -> None:
    persist(make_account("a", default=True))
    persist(make_account("b"))
    assert _resolve_account("b").name == "b"


def test_resolve_account_uses_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist(make_account("a"))
    persist(make_account("b"))
    monkeypatch.setenv("MYCORD_EMAIL_ACCOUNT", "b")
    assert _resolve_account(None).name == "b"


def test_resolve_account_uses_default_marker(home: Path) -> None:
    persist(make_account("a"))
    persist(make_account("b", default=True))
    assert _resolve_account(None).name == "b"


def test_resolve_account_single(home: Path) -> None:
    persist(make_account("only"))
    assert _resolve_account(None).name == "only"


def test_resolve_account_ambiguous(home: Path) -> None:
    from mycord_email.accounts import AccountError

    persist(make_account("a"))
    persist(make_account("b"))
    with pytest.raises(AccountError, match="multiple accounts"):
        _resolve_account(None)


def test_resolve_account_none_configured(home: Path) -> None:
    from mycord_email.accounts import AccountError

    with pytest.raises(AccountError, match="no accounts configured"):
        _resolve_account(None)


def test_cli_accounts_list(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    persist(make_account("a", default=True))
    assert main(["accounts", "list"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "a@example.com" in out
    assert "secret ok" in out


def test_cli_accounts_list_reports_missing_secret(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    persist(make_account("a"), secret=False)
    assert main(["accounts", "list"]) == EXIT_OK
    assert "secret MISSING" in capsys.readouterr().out


def test_cli_accounts_show_never_prints_secret(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    persist(make_account("a"))
    assert main(["accounts", "show", "a"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "MYCORD_EMAIL_PASSWORD" in out
    # The secret value is 'pw'; it must not appear as a value.
    assert " pw" not in out


def test_cli_accounts_add(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "accounts",
            "add",
            "work",
            "work@example.com",
            "--provider",
            "imap-starttls",
            "--page-size",
            "25",
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "wrote" in out
    assert "set-secret work" in out

    from mycord_email.accounts import load_account

    account = load_account("work")
    assert account.starttls is True
    assert account.page_size == 25


def test_cli_accounts_add_rejects_unknown_provider(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["providers"]) == EXIT_OK
    capsys.readouterr()
    # argparse choices reject it before our handler runs.
    with pytest.raises(SystemExit):
        main(["accounts", "add", "w", "w@x.y", "--provider", "nope"])


def test_cli_providers_lists_bridge(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["providers"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "proton-bridge" in out
    assert "gmail" in out


def test_cli_config_show(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    persist(make_account("a", tls_cert=None))
    assert main(["config", "show", "a"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[accounts.a]" in out
    assert "imaps://imap.example.com:993" in out


def test_cli_config_show_defaults_to_selected(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    persist(make_account("solo"))
    assert main(["config", "show"]) == EXIT_OK
    assert "[accounts.solo]" in capsys.readouterr().out


def test_cli_exec_prints_command(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    persist(make_account("a"))
    assert main(["exec", "envelope", "list"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "himalaya -c" in out
    assert "# account:    a" in out


def test_cli_passthrough_missing_account_exits_3(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["envelope", "list"]) == EXIT_NO_ACCOUNT
    assert "no accounts configured" in capsys.readouterr().err


def test_cli_passthrough_runs_himalaya(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist(make_account("a"))

    captured: dict[str, object] = {}

    def fake_run(account: AccountMeta, args: list[str], **kwargs: object) -> int:
        captured["account"] = account.name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("mycord_email.cli.run", fake_run)
    assert main(["envelope", "search", "subject", "code"]) == EXIT_OK
    assert captured["account"] == "a"
    assert captured["args"] == ["envelope", "search", "subject", "code"]


def test_cli_passthrough_forwards_global_flags(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist(make_account("a"))
    persist(make_account("b"))

    captured: dict[str, object] = {}

    def fake_run(account: AccountMeta, args: list[str], **kwargs: object) -> int:
        captured["account"] = account.name
        captured["args"] = args
        return 0

    monkeypatch.setattr("mycord_email.cli.run", fake_run)
    assert main(["-a", "b", "message", "read", "42"]) == EXIT_OK
    assert captured["account"] == "b"
    assert captured["args"] == ["message", "read", "42"]


def test_cli_bare_invocation_shows_help(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "passthrough" not in capsys.readouterr().out.split("epilog")[0]


def test_build_parser_is_importable() -> None:
    parser = build_parser()
    args = parser.parse_args(["accounts", "list"])
    assert args.command == "accounts"
    assert args.accounts_command == "list"


def test_cli_accounts_rm_requires_confirmation(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist(make_account("a"))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["accounts", "rm", "a"]) != EXIT_OK
    assert (home / "accounts" / "a").exists()


def test_cli_accounts_rm_with_yes(home: Path) -> None:
    persist(make_account("a"))
    assert main(["accounts", "rm", "a", "--yes"]) == EXIT_OK
    assert not (home / "accounts" / "a").exists()


def test_cli_set_secret_from_stdin(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist(make_account("a"), secret=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2\n"))
    assert main(["accounts", "set-secret", "a", "--username", "u"]) == EXIT_OK

    from mycord_email.accounts import read_credentials

    assert read_credentials(make_account("a"))["MYCORD_EMAIL_PASSWORD"] == "hunter2"


def test_unknown_wrapper_flag_errors(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # argparse owns usage errors and exits 2; assert the code rather than a return.
    with pytest.raises(SystemExit) as excinfo:
        main(["accounts", "list", "--bogus"])
    assert excinfo.value.code == EXIT_USAGE
    assert "unrecognized" in capsys.readouterr().err


def test_cli_forwards_himalaya_global_flag(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # himalaya has its own leading global flags (`--json`). They must reach it
    # rather than be rejected as an unknown wrapper flag.
    persist(make_account("a"))
    captured: dict[str, object] = {}

    def fake_run(account: AccountMeta, args: list[str], **kwargs: object) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr("mycord_email.cli.run", fake_run)
    assert main(["--json", "envelope", "list"]) == EXIT_OK
    assert captured["args"] == ["--json", "envelope", "list"]


def test_cli_wrapper_flags_before_himalaya_global_flag(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persist(make_account("a"))
    persist(make_account("b"))
    captured: dict[str, object] = {}

    def fake_run(account: AccountMeta, args: list[str], **kwargs: object) -> int:
        captured["account"] = account.name
        captured["args"] = args
        return 0

    monkeypatch.setattr("mycord_email.cli.run", fake_run)
    assert main(["-a", "b", "--json", "envelope", "list"]) == EXIT_OK
    assert captured["account"] == "b"
    assert captured["args"] == ["--json", "envelope", "list"]


def test_cli_own_command_still_routed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # `accounts` is ours; it must not be handed to himalaya.
    persist(make_account("a"))
    assert main(["accounts", "list"]) == EXIT_OK
    assert "a@example.com" in capsys.readouterr().out
