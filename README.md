# mycord - Discord MCP Server

**FastMCP-based Discord integration for Model Context Protocol**

[![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Working](https://img.shields.io/badge/status-working-green.svg)](https://github.com)

---

## ⚠️ ADDITIONAL WARNING - Terms of Service Violation

**This project uses `discord.py-self` which is an UNOFFICIAL library that VIOLATES Discord's Terms of Service.**

- **User accounts can be BANNED** for selfbot activity
- This is for **personal automation ONLY**
- **NEVER use in production or commercial settings**
- You assume **ALL RISK** by using this tool
- The maintainers are **NOT responsible** for account bans or other consequences

**By using this software, you acknowledge and accept these risks.**

---

## Overview

mycord is a FastMCP server that provides Discord integration via the Model Context Protocol (MCP). It enables AI assistants to interact with Discord through a standardized interface using discord.py-self for user account automation.

### Architecture

The project follows **orthogonal architecture** principles with clear separation of concerns:

```
mycord/
├── core/         # Pure domain logic (Pydantic models, no external dependencies)
├── adapters/     # External service integrations (discord.py-self wrapper)
└── app/          # Application orchestration (FastMCP server)
```

**Dependency Direction**: `app → adapters → core`

### Current Status - Slice 1 Complete ✅

**Implemented:**
- ✅ FastMCP server foundation with stdio transport
- ✅ Health check tool (`mycord.health`)
- ✅ Discord client connection management
- ✅ Token loading from environment variables
- ✅ Structured logging with security (no token logging)
- ✅ Comprehensive test suite (unit + integration)
- ✅ Development tooling (Makefile, pytest, black, ruff)

**Test Coverage:** 90%+ (targeting 95% for core contracts)

---

## Installation

### Prerequisites

- **Python 3.10.x ONLY** (3.11+ has compatibility issues with discord.py-self)
- Discord user account token (see "Obtaining Token" below)
- uv package manager (recommended) - [Install uv](https://github.com/astral-sh/uv)
- mise for Python version management (optional) - [Install mise](https://mise.jdx.dev/)

### Setup

```bash
# Clone and navigate to project
cd /home/bitnom/Code/apothic-monorepo/mcp/mycord

# CRITICAL: Set Python 3.10.x (discord.py-self requires Python 3.10 only)
# Using mise (recommended):
echo "3.10.19" > .python-version
mise install python@3.10.19

# OR manually ensure python --version shows 3.10.x

# Install development dependencies with uv (recommended)
make setup
# OR: uv sync --all-extras

# Copy environment template
cp .env.example .env

# Edit .env and add your DISCORD_TOKEN
# IMPORTANT: NEVER commit .env to version control
```

---

## Obtaining Discord Token

⚠️ **WARNING**: Using your token for automation violates Discord ToS and can result in account ban.

### Method 1: Browser Console (Recommended)

1. Open Discord in your web browser (discord.com/app)
2. Open Developer Console (F12 or Cmd+Option+I on Mac)
3. Go to "Console" tab
4. Paste this code and press Enter:

```javascript
(webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken).exports.default.getToken()
```

5. Copy the token (without quotes)
6. Paste into `.env` file: `DISCORD_TOKEN=your_token_here`

### Security Notes

- **NEVER share your token** with anyone
- **NEVER commit your token** to version control
- Token is equivalent to your password
- Regenerate token if compromised (Discord Settings > Security)

---

## Usage

### Running the Server

```bash
# Run with make
make run

# OR run directly with Python
python -m mycord.app.server
```

The server will:
1. Load `DISCORD_TOKEN` from `.env` file
2. Initialize Discord client adapter
3. Start FastMCP server on stdio transport
4. Connect to Discord in the background

### Testing the Health Tool

The server exposes one tool in Slice 1:

**`mycord.health`** - Check server and Discord connection status

**Example Response (Connected):**
```json
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00Z",
  "discord_connected": true,
  "user_id": "123456789012345678",
  "username": "YourUsername#1234"
}
```

**Example Response (Disconnected):**
```json
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00Z",
  "discord_connected": false,
  "user_id": null,
  "username": null
}
```

---

## Development

### Running Tests

```bash
# Run all tests with coverage
make test

# Run unit tests only
make test-unit

# Run integration tests only
make test-integration

# Generate HTML coverage report
make test-cov
# View report: open htmlcov/index.html
```

### Code Quality

```bash
# Format code with black
make fmt

# Lint with ruff
make lint

# Type check with mypy (optional, not enforced in Slice 1)
make typecheck
```

### Project Structure

```
mycord/
├── mycord/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── contracts.py      # Pydantic models (HealthStatus)
│   ├── adapters/
│   │   ├── __init__.py
│   │   └── discord_client.py # Discord client wrapper
│   └── app/
│       ├── __init__.py
│       └── server.py          # FastMCP server
├── tests/
│   ├── unit/
│   │   └── test_contracts.py # Contract serialization tests
│   └── integration/
│       └── test_health_tool.py # Health tool tests with mocks
├── pyproject.toml
├── Makefile
├── .env.example
├── README.md
└── PLANNING_AND_PROGRESS.md
```

---

## Roadmap

### Slice 1: Health Check & Foundation ✅ COMPLETE
- FastMCP server skeleton with health check
- Project structure and tooling
- Discord client connection
- Comprehensive test suite

### Slice 2: Read-Only Message Fetching (Next)
- Pydantic Message, Channel contracts
- `mycord.fetch_messages` tool
- Message ordering guarantees (oldest→newest)
- Error handling and retry logic

### Slice 3: Message Sending
- `mycord.send_message` tool
- SendResult envelope with ok/error fields
- Rate limit handling
- Dry-run mode

### Slice 4: Additional Operations
- Fetch guild/channel lists
- User info retrieval
- Reaction management
- Typing indicators

### Slice 5: Observability, CI, Hardening
- GitHub Actions CI pipeline
- Performance profiling
- Connection pooling
- Load testing

---

## Testing Philosophy

This project follows **Modern Software Engineering** principles (David Farley):

1. **Test-First Development**: Write failing tests before implementation (RED-GREEN-REFACTOR)
2. **Fast Feedback**: Test suite completes in under 5 minutes
3. **Incremental Delivery**: Build in smallest viable slices
4. **Empirical Validation**: Prove correctness through tests and execution

### Test Coverage Targets
- **Core Contracts**: 95%+ coverage
- **Adapters**: 90%+ coverage
- **App Layer**: 85%+ coverage
- **Overall**: 90%+ minimum

---

## Security & Privacy

### What We Do
✅ Load secrets from environment variables only
✅ Use minimal Discord intents (Intents.none())
✅ Structured logging with security awareness
✅ Never log DISCORD_TOKEN or authentication credentials
✅ Document ToS violation risks prominently

### What We Don't Do
❌ Store tokens in code or version control
❌ Log sensitive user data
❌ Request unnecessary Discord permissions
❌ Hide the risks of selfbot usage

---

## Technical Constraints & Workarounds

### Python 3.10 Requirement

**CRITICAL:** This project MUST use Python 3.10.x. Python 3.11+ causes fatal import errors.

**Why:** discord.py-self 2.0.1 has a bug in `member.py` where the `flatten_user` decorator calls `inspect.signature()` on `CachedSlotProperty` objects. This works on Python 3.10 but fails on 3.11+ with:

```
TypeError: <discord.utils.CachedSlotProperty object> is not a callable object
```

**Solution:** Use Python 3.10.19 (verified working). Set with:
```bash
echo "3.10.19" > .python-version
mise install python@3.10.19
```

### FastMCP Import Order Workaround

**CRITICAL:** Discord modules MUST be imported BEFORE FastMCP in server.py.

**Why:** FastMCP modifies `inspect.signature` behavior globally, which breaks discord.py-self's `flatten_user` decorator. The decorator expects the original inspect behavior.

**Solution:** The server.py file uses this import order:
```python
from mycord.adapters.discord_client import DiscordClientAdapter  # Discord first
from fastmcp import FastMCP  # FastMCP second
```

This workaround is documented in the code with `# ruff: noqa` comments.

---

## Troubleshooting

### "DISCORD_TOKEN not set" warning
- Ensure `.env` file exists in project root
- Verify `DISCORD_TOKEN=your_token_here` is set
- Check for typos or extra spaces

### Discord client not connecting
- Verify token is valid (test in browser console)
- Check internet connection
- Review server logs for error messages
- Token may be expired or invalidated

### Tests failing
```bash
# Clean and reinstall
make clean
make setup

# Run tests with verbose output
pytest tests/ -vv
```

---

## Contributing

This is a personal automation project with explicit risk acceptance. Contributions should:

1. Follow TDD practices (tests first, then implementation)
2. Maintain 90%+ test coverage
3. Include security warnings for ToS violations
4. Use structured logging (never log secrets)
5. Follow orthogonal architecture patterns

---

## License

MIT License - See LICENSE file for details

**Disclaimer**: This software is provided "as is" without warranty. Use at your own risk. The maintainers are not responsible for Discord account bans or other consequences.

---

## Acknowledgments

- [FastMCP](https://gofastmcp.com/) - FastMCP framework for MCP servers
- [discord.py-self](https://github.com/dolfies/discord.py-self) - Unofficial Discord user account library
- [Pydantic](https://docs.pydantic.dev/) - Data validation with Python type hints

**Remember**: This tool violates Discord's Terms of Service. Use responsibly and at your own risk.
