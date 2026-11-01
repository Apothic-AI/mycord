#!/usr/bin/env bash
# provision.sh — detect and (optionally) fix the two version pins the Signal call path needs.
#
# Why this exists
# ---------------
# Signal's call path is split across two independently-versioned components and BOTH are
# load-bearing:
#
#   signal-cli            >= 0.14.9   passes the account's real device id to the tunnel
#                                    (upstream PR #2128). Older builds hard-code
#                                    local_device_id: 1, which is wrong for a linked device.
#   signal-call-tunnel    5a963d1     PR #5's pipe audio backend PLUS the receivedIce
#                                    sender_device_id fix. Upstream visigoth has neither.
#
# A mismatch does not fail at startup; it fails inside a call. So the default mode is
# detect-and-report, and it exits non-zero so CI can gate on it.
#
# Usage
#   scripts/provision.sh              # detect only; non-zero exit if anything is stale
#   scripts/provision.sh --check      # same, explicitly
#   scripts/provision.sh --apply      # install/build what is stale (network + sudo)
#   scripts/provision.sh --apply --tunnel-only
#   scripts/provision.sh --json       # machine-readable
#
# --apply is not destructive-by-default: it never touches the account store
# (~/.local/share/signal-cli), never links a device, and never removes a working binary until
# the replacement has been verified.
set -euo pipefail

REQUIRED_SIGNAL_CLI="0.14.9"
REQUIRED_TUNNEL_COMMIT="5a963d1"
TUNNEL_REMOTE_DEFAULT="https://github.com/michaelburch/signal-call-tunnel.git"
TUNNEL_SRC="${SIGNAL_TUNNEL_SRC:-$HOME/.local/src/signal-call-tunnel}"
# Resolve through mise when it is the thing on PATH: a mise shim is a symlink to the `mise`
# binary itself, so its hash is not the tunnel's hash and comparing them is meaningless.
resolve_bin() {
  local name="$1" on_path real via_mise
  on_path="$(command -v "$name" 2>/dev/null || true)"
  [ -n "$on_path" ] || { echo ""; return; }
  real="$(readlink -f "$on_path" 2>/dev/null || echo "$on_path")"
  case "$real" in
    */mise|*/shims/*)
      via_mise="$(mise which "$name" 2>/dev/null || true)"
      if [ -n "$via_mise" ] && [ -x "$via_mise" ]; then echo "$via_mise"; return; fi
      ;;
  esac
  echo "$on_path"
}

TUNNEL_BIN="${SIGNAL_CALL_TUNNEL_BIN:-$(resolve_bin signal-call-tunnel)}"
SIGNAL_CLI_BIN="${SIGNAL_CLI_BIN:-$(resolve_bin signal-cli)}"
SIGNAL_CLI_INSTALL_PREFIX="${SIGNAL_CLI_INSTALL_PREFIX:-/opt}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/tmp/mycord-signal-provision}"
GPG_KEY_URL="https://github.com/AsamK.gpg"

MODE="check"
JSON=0
TUNNEL_ONLY=0

# ---------------------------------------------------------------------------- output

c_ok=""; c_bad=""; c_warn=""; c_reset=""
if [ -t 1 ]; then c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_reset=$'\033[0m'; fi

problems=()
actions=()

note()  { printf '%s\n' "$*"; }
ok()    { printf '%s ok %s %s\n' "$c_ok" "$c_reset" "$*"; }
stale() { printf '%sSTALE%s %s\n' "$c_bad" "$c_reset" "$*"; problems+=("$*"); }
warn()  { printf '%s  ?  %s %s\n' "$c_warn" "$c_reset" "$*"; }
die()   { printf 'error: %s\n' "$*" >&2; exit 2; }

usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ---------------------------------------------------------------------------- args

while [ $# -gt 0 ]; do
  case "$1" in
    --check)         MODE="check" ;;
    --apply)         MODE="apply" ;;
    --json)          JSON=1 ;;
    --tunnel-only)   TUNNEL_ONLY=1 ;;
    --tunnel-src)    TUNNEL_SRC="$2"; shift ;;
    --help|-h)       usage ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------- helpers

# Version compare: returns 0 when $1 >= $2.
version_at_least() {
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]
}

installed_signal_cli() {
  if [ -z "$SIGNAL_CLI_BIN" ] || [ ! -x "$SIGNAL_CLI_BIN" ]; then echo ""; return; fi
  # --version is a build constant: no account access, no network, ~0.2 s.
  timeout 20 "$SIGNAL_CLI_BIN" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){1,3}' | head -1
}

tunnel_commit() {
  [ -d "$TUNNEL_SRC/.git" ] || { echo ""; return; }
  git -C "$TUNNEL_SRC" rev-parse HEAD 2>/dev/null | cut -c1-7
}

tunnel_contains_required() {
  [ -d "$TUNNEL_SRC/.git" ] || return 1
  git -C "$TUNNEL_SRC" cat-file -e "${REQUIRED_TUNNEL_COMMIT}^{commit}" 2>/dev/null || return 1
  git -C "$TUNNEL_SRC" merge-base --is-ancestor "$REQUIRED_TUNNEL_COMMIT" HEAD 2>/dev/null
}

tunnel_build_matches_install() {
  local built="$TUNNEL_SRC/signal-call-tunnel/target/release/signal-call-tunnel"
  [ -x "$built" ] && [ -n "$TUNNEL_BIN" ] && [ -x "$TUNNEL_BIN" ] || return 1
  [ "$(sha256sum "$built" | cut -d' ' -f1)" = "$(sha256sum "$TUNNEL_BIN" | cut -d' ' -f1)" ]
}

# Boot the tunnel in pipe mode and read its ready line. Needs no account and no network.
pipe_probe() {
  local bin="${1:-$TUNNEL_BIN}"
  [ -n "$bin" ] && [ -x "$bin" ] || return 1
  local socket_dir="$DOWNLOAD_DIR/sock"
  mkdir -p "$socket_dir"
  # call_id must be an integer (u64); local_device_id an integer (u32). A string is rejected
  # with "failed to parse config JSON".
  printf '%s\n' \
    '{"call_id": 9900001, "is_outgoing": true, "local_device_id": 1, "audio_mode": "pipe"}' |
    SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe \
    SIGNAL_CALL_TUNNEL_SOCKET_DIR="$socket_dir" \
    timeout 25 "$bin" 2>/dev/null |
    grep -q '"type":"ready".*unix:'
}

# ---------------------------------------------------------------------------- signal-cli

provision_signal_cli() {
  local version; version="$(installed_signal_cli)"
  local target="$SIGNAL_CLI_INSTALL_PREFIX/signal-cli-$REQUIRED_SIGNAL_CLI"

  if [ -n "$version" ] && version_at_least "$version" "$REQUIRED_SIGNAL_CLI"; then
    ok "signal-cli $version at $SIGNAL_CLI_BIN"
    return 0
  fi

  if [ -z "$version" ]; then
    stale "signal-cli: not found ($SIGNAL_CLI_BIN) — need >= $REQUIRED_SIGNAL_CLI"
  else
    stale "signal-cli: $version at $SIGNAL_CLI_BIN — need >= $REQUIRED_SIGNAL_CLI"
  fi

  if [ "$MODE" != "apply" ]; then
    actions+=("install signal-cli $REQUIRED_SIGNAL_CLI: re-run with --apply")
    return 1
  fi

  note "installing signal-cli $REQUIRED_SIGNAL_CLI (native build)"
  mkdir -p "$DOWNLOAD_DIR"
  local tarball="$DOWNLOAD_DIR/signal-cli-$REQUIRED_SIGNAL_CLI-Linux-native.tar.gz"
  local sig="$tarball.asc"
  local url="https://github.com/AsamK/signal-cli/releases/download/v$REQUIRED_SIGNAL_CLI"
  url="$url/signal-cli-$REQUIRED_SIGNAL_CLI-Linux-native.tar.gz"

  if [ ! -s "$tarball" ]; then
    curl -fsSL --retry 3 -o "$tarball" "$url" || die "download failed: $url"
    curl -fsSL --retry 3 -o "$sig" "$url.asc" || die "signature download failed"
  fi

  # Verify before extracting. A release binary is root-installed, so an unverified download
  # would be arbitrary code with sudo — the signature check is not a formality.
  if ! gpg --list-keys 2>/dev/null | grep -qi 'B5B09570\|AsamK'; then
    note "importing AsamK's GPG key from $GPG_KEY_URL"
    curl -fsSL "$GPG_KEY_URL" | gpg --import 2>/dev/null || die "could not import the signing key"
  fi
  gpg --verify "$sig" "$tarball" 2>/dev/null || die "GPG verification FAILED for $tarball"

  local extract="$DOWNLOAD_DIR/extract"
  rm -rf "$extract"; mkdir -p "$extract"
  tar xzf "$tarball" -C "$extract" || die "could not extract $tarball"
  [ -x "$extract/signal-cli" ] || die "tarball did not contain a signal-cli binary"

  sudo install -m 0755 "$extract/signal-cli" "$target" || die "install to $target failed"
  sudo ln -sfn "$target" "$SIGNAL_CLI_INSTALL_PREFIX/../usr/local/bin/signal-cli" 2>/dev/null ||
    sudo ln -sfn "$target" /usr/local/bin/signal-cli || die "could not point /usr/local/bin/signal-cli at $target"

  local after; after="$(installed_signal_cli)"
  [ "$after" = "$REQUIRED_SIGNAL_CLI" ] || die "post-install version is '$after', expected $REQUIRED_SIGNAL_CLI"
  ok "signal-cli $after installed at $target"
  SIGNAL_CLI_BIN="$(command -v signal-cli)"
}

# ---------------------------------------------------------------------------- tunnel

provision_tunnel() {
  local commit; commit="$(tunnel_commit)"

  if [ -z "$commit" ]; then
    stale "signal-call-tunnel checkout: none at $TUNNEL_SRC"
    if [ "$MODE" != "apply" ]; then
      actions+=("clone the tunnel: re-run with --apply (or clone $TUNNEL_REMOTE_DEFAULT)")
      return 1
    fi
    mkdir -p "$(dirname "$TUNNEL_SRC")"
    git clone "$TUNNEL_REMOTE_DEFAULT" "$TUNNEL_SRC" || die "git clone failed"
    git -C "$TUNNEL_SRC" checkout "$REQUIRED_TUNNEL_COMMIT" || die "checkout of $REQUIRED_TUNNEL_COMMIT failed"
    git -C "$TUNNEL_SRC" submodule update --init --recursive || die "submodule init failed"
    commit="$(tunnel_commit)"
  elif tunnel_contains_required; then
    ok "signal-call-tunnel checkout at $commit (contains $REQUIRED_TUNNEL_COMMIT)"
  else
    stale "signal-call-tunnel checkout: $commit — does not contain $REQUIRED_TUNNEL_COMMIT"
    if [ "$MODE" != "apply" ]; then
      actions+=("check out $REQUIRED_TUNNEL_COMMIT in $TUNNEL_SRC: re-run with --apply")
      return 1
    fi
    git -C "$TUNNEL_SRC" fetch --all --tags || die "git fetch failed"
    git -C "$TUNNEL_SRC" checkout "$REQUIRED_TUNNEL_COMMIT" || die "checkout failed"
    commit="$(tunnel_commit)"
    ok "checked out $commit"
  fi

  if [ -z "$TUNNEL_BIN" ] || [ ! -x "$TUNNEL_BIN" ]; then
    stale "signal-call-tunnel binary: not on PATH"
  elif tunnel_build_matches_install; then
    ok "signal-call-tunnel binary matches the checkout build ($TUNNEL_BIN)"
  else
    warn "installed tunnel differs from the checkout build (expected after a checkout change)"
  fi

  if [ "$MODE" = "apply" ]; then
    note "building signal-call-tunnel at $commit"
    # ./build.sh applies the ringrtc patches BEFORE compiling. A bare `cargo build` compiles
    # ringrtc first and needs two passes, because the patches change ringrtc's public API.
    ( cd "$TUNNEL_SRC" && ./build.sh ) || die "build failed"
  fi

  local built="$TUNNEL_SRC/signal-call-tunnel/target/release/signal-call-tunnel"
  if [ ! -x "$built" ]; then
    stale "signal-call-tunnel build artifact: absent ($built)"
    actions+=("build the tunnel: re-run with --apply")
    return 1
  fi

  if [ "$MODE" = "apply" ]; then
    local dest="$HOME/.cargo/bin/signal-call-tunnel"
    mkdir -p "$(dirname "$dest")"
    # Verify the new binary boots in pipe mode BEFORE replacing the working one.
    if ! pipe_probe "$built"; then
      die "the freshly built tunnel does not boot in pipe mode; leaving the installed binary alone"
    fi
    install -m 0755 "$built" "$dest" || die "could not install to $dest"
    TUNNEL_BIN="$dest"
    ok "installed tunnel to $dest (sha256 $(sha256sum "$built" | cut -c1-12)…)"
  fi
}

# ---------------------------------------------------------------------------- main

if [ "$JSON" = "0" ]; then
  note "mycord-signal provision ($MODE)"
  note "  required: signal-cli >= $REQUIRED_SIGNAL_CLI, tunnel commit $REQUIRED_TUNNEL_COMMIT"
  note "  tunnel src: $TUNNEL_SRC"
  note "  tunnel bin: ${TUNNEL_BIN:-<not found>}"
  note ""
fi

if [ "$TUNNEL_ONLY" = "0" ]; then
  provision_signal_cli || true
fi
provision_tunnel || true

# Final capability check. Everything above is version bookkeeping; this is the only line that
# proves the binary can actually produce a pipe socket.
if pipe_probe; then
  ok "pipe mode ready"
else
  stale "signal-call-tunnel cannot boot in pipe mode"
  actions+=("scripts/provision.sh --apply")
fi

if [ "$JSON" = "1" ]; then
  printf '{"mode":"%s","signal_cli_version":"%s","required_signal_cli":"%s",' \
    "$MODE" "$(installed_signal_cli)" "$REQUIRED_SIGNAL_CLI"
  printf '"tunnel_commit":"%s","required_tunnel_commit":"%s","tunnel_bin":"%s","problems":%d}\n' \
    "$(tunnel_commit)" "$REQUIRED_TUNNEL_COMMIT" "${TUNNEL_BIN:-}" "${#problems[@]}"
fi

if [ "${#problems[@]}" -gt 0 ]; then
  note ""
  note "${#problems[@]} problem(s):"
  for p in "${problems[@]}"; do note "  - $p"; done
  if [ "${#actions[@]}" -gt 0 ]; then
    note ""
    note "to fix:"
    for a in "${actions[@]}"; do note "  - $a"; done
  fi
  exit 3
fi

note ""
note "all pinned dependencies are current"
exit 0
