# selfcord.py Migration Plan

**Migration Date:** 2025-10-24 (Planned)
**Estimated Effort:** 2-4 hours
**Risk Level:** LOW-MEDIUM

---

## Overview

This document outlines the step-by-step migration from the broken `discord.py-self` library to the working `selfcord.py` library.

**Why Migrate:**
- discord.py-self has a fatal import bug affecting ALL Python 3.11+ versions
- selfcord.py successfully connects to Discord (tested and validated)
- High API compatibility reduces refactoring effort
- Active maintenance and community support

---

## Prerequisites

✅ **Completed:**
- Compatibility testing (see `SELFCORD_TEST_RESULTS.md`)
- Connectivity validation with real Discord token
- API analysis comparing discord.py-self and selfcord.py

⚠️ **Requirements:**
- Python 3.11.x (NOT 3.12 - incompatible with selfcord.py dependencies)
- Discord user account token (already configured in `.env`)
- Existing test suite passing (unit tests for contracts)

---

## Migration Steps

### Step 1: Update Dependencies (10 minutes)

#### 1.1 Update pyproject.toml

**File:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/pyproject.toml`

**Changes:**

```diff
 [project]
 name = "mycord"
 version = "0.1.0"
 description = "FastMCP server for Discord integration via selfcord.py"
 readme = "README.md"
-requires-python = ">=3.11"
+requires-python = ">=3.11,<3.12"  # selfcord.py incompatible with Python 3.12

 dependencies = [
     "fastmcp>=0.1.0",
-    "discord.py-self>=2.0.0",
+    "selfcord.py>=1.0.3",
     "pydantic>=2.0.0",
     "python-dotenv>=1.0.0",
 ]
```

**Rationale:**
- Python 3.12 constraint prevents installation failures (aiohttp==3.8.5 incompatibility)
- selfcord.py version 1.0.3 tested and validated

#### 1.2 Reinstall Dependencies

```bash
cd /home/bitnom/Code/apothic-monorepo/mcp/mycord

# Remove old dependency
uv remove discord.py-self

# Add new dependency
uv add selfcord.py

# Verify installation
uv pip list | grep selfcord
# Expected: selfcord-py==1.0.3
```

#### 1.3 Verify Python Version

```bash
# Check current Python version
python --version
# Expected: Python 3.11.14 (or 3.11.x)

# If wrong version, check .python-version
cat .python-version
# Should be: 3.11
```

**Validation:**
- ✅ selfcord-py==1.0.3 installed
- ✅ Python version is 3.11.x
- ✅ No installation errors

---

### Step 2: Refactor DiscordClientAdapter (TDD Approach) (60-90 minutes)

**File:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/mycord/adapters/discord_client.py`

#### 2.1 Update Imports

**Before:**
```python
import discord
```

**After:**
```python
import selfcord
import threading
from typing import Optional
```

#### 2.2 Refactor Client Initialization

**Before:**
```python
class DiscordClientAdapter:
    def __init__(self):
        self._client = discord.Client(intents=discord.Intents.none())
        self._token = os.getenv("DISCORD_TOKEN")
        # Event handlers...
```

**After:**
```python
class DiscordClientAdapter:
    def __init__(self):
        self._bot = selfcord.Bot()
        self._token = os.getenv("DISCORD_TOKEN")
        self._bot_thread: Optional[threading.Thread] = None
        self._connected = False

        # Register event handlers
        @self._bot.on("ready")
        async def on_ready(time):
            self._connected = True
            logger.info(f"Discord client connected (ready time: {time}s)")
```

**Key Changes:**
1. `discord.Client()` → `selfcord.Bot()`
2. No `intents` parameter needed (selfcord.py handles internally)
3. Event handlers use `@bot.on("event_name")` decorator pattern
4. Track connection state with `_connected` flag

#### 2.3 Refactor Connection Method

**Before:**
```python
async def connect(self):
    """Connect to Discord asynchronously"""
    try:
        await self._client.start(self._token)
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        raise
```

**After:**
```python
def connect(self):
    """Connect to Discord (blocking call run in thread)"""
    if self._bot_thread and self._bot_thread.is_alive():
        logger.warning("Discord client already connecting/connected")
        return

    def run_bot():
        try:
            logger.info("Starting Discord bot connection...")
            self._bot.run(self._token)
        except Exception as e:
            logger.error(f"Bot run failed: {e}", exc_info=True)
            self._connected = False

    self._bot_thread = threading.Thread(target=run_bot, daemon=True, name="discord-bot")
    self._bot_thread.start()
    logger.info("Discord bot thread started")
```

**Key Changes:**
1. `async def` → `def` (no longer async)
2. Run `bot.run()` in background thread (it's blocking)
3. Use daemon thread for automatic cleanup
4. Remove `await` syntax

#### 2.4 Update Property Accessors

**Before:**
```python
@property
def user_id(self) -> Optional[str]:
    if self._client.user:
        return str(self._client.user.id)
    return None

@property
def username(self) -> Optional[str]:
    if self._client.user:
        return self._client.user.name
    return None
```

**After:**
```python
@property
def user_id(self) -> Optional[str]:
    if hasattr(self._bot, 'user') and self._bot.user:
        return str(self._bot.user.id)
    return None

@property
def username(self) -> Optional[str]:
    if hasattr(self._bot, 'user') and self._bot.user:
        # Note: selfcord.py may use .name or .username - verify both
        return getattr(self._bot.user, 'name', None) or getattr(self._bot.user, 'username', None)
    return None
```

**Key Changes:**
1. `_client` → `_bot`
2. Add `hasattr()` check for safety
3. Handle potential attribute name differences (`name` vs `username`)

#### 2.5 Update is_connected Method

**Before:**
```python
def is_connected(self) -> bool:
    return self._client.is_ready()
```

**After:**
```python
def is_connected(self) -> bool:
    return self._connected and self._bot_thread is not None and self._bot_thread.is_alive()
```

**Key Changes:**
1. Use tracked `_connected` flag instead of `.is_ready()`
2. Verify thread is alive

#### 2.6 Add Cleanup Method

**New Method:**
```python
def disconnect(self):
    """Disconnect from Discord and cleanup resources"""
    if self._bot_thread and self._bot_thread.is_alive():
        logger.info("Disconnecting Discord bot...")
        # selfcord.py may not have clean shutdown - thread will die when daemon exits
        self._connected = False
        # Note: May need to implement bot.close() if available
```

---

### Step 3: Update Tests (30-60 minutes)

**File:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/tests/integration/test_health_tool.py`

#### 3.1 Update Mock Adapter

**Before:**
```python
class FakeDiscordAdapter:
    def __init__(self, connected: bool = True):
        self._connected = connected
        # ...
```

**After:**
```python
class FakeDiscordAdapter:
    def __init__(self, connected: bool = True):
        self._connected = connected
        # No changes needed - interface stays the same
```

**Note:** Fake adapter doesn't need changes since we're testing contracts, not implementation.

#### 3.2 Verify Event Handler Tests

**Existing Test:**
```python
@pytest.mark.integration
def test_health_tool_returns_ok_when_connected():
    adapter = FakeDiscordAdapter(connected=True)
    adapter._user_id = "123456789"
    adapter._username = "TestUser"
    # ...
```

**Action:** Run tests to ensure they still pass with new adapter.

---

### Step 4: Manual Validation (15-30 minutes)

#### 4.1 Run Unit Tests

```bash
cd /home/bitnom/Code/apothic-monorepo/mcp/mycord

# Run all unit tests
uv run pytest -m unit -v --no-cov

# Expected: All tests pass (8 unit tests)
```

#### 4.2 Run Integration Tests

```bash
# Run integration tests
uv run pytest -m integration -v --no-cov

# Expected: All tests pass (4 integration tests)
```

#### 4.3 Run FastMCP Server

```bash
# Start server manually
make run
# OR: uv run python -m mycord.app.server

# Expected output:
# INFO - Initializing Discord client adapter
# INFO - Starting Discord bot connection...
# INFO - Discord bot thread started
# INFO - Starting mycord MCP server
# INFO - Listening on stdio
# (After ~5 seconds)
# INFO - Discord client connected (ready time: X.XXs)
```

#### 4.4 Test Health Tool

Use MCP client or FastMCP CLI to invoke `mycord.health` tool:

```bash
# Expected response (connected):
{
  "status": "ok",
  "timestamp": "2025-10-24T18:00:00Z",
  "discord_connected": true,
  "user_id": "590347867588263939",
  "username": "YourUsername"
}
```

**Validation Checklist:**
- ✅ Server starts without errors
- ✅ Discord connection established (~5 seconds)
- ✅ Health tool returns connected status
- ✅ User ID and username populated
- ✅ No errors in structured logs

---

### Step 5: Update Documentation (20-30 minutes)

#### 5.1 Update README.md

**File:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/README.md`

**Changes:**

1. **Remove blocker notice** (lines 11-28):
```diff
-## 🛑 CRITICAL BLOCKER - PROJECT CURRENTLY NON-FUNCTIONAL
-
-**As of 2024-10-24, this project CANNOT run due to a fatal bug in discord.py-self.**
-...
-See `PLANNING_AND_PROGRESS.md` for full technical analysis and resolution options.
-
----
```

2. **Update Dependencies section**:
```diff
 ### Prerequisites

-- Python 3.11 or higher
+- Python 3.11.x (REQUIRED - Python 3.12 is NOT compatible with selfcord.py)
 - Discord user account token (see "Obtaining Token" below)
 - uv package manager (recommended) - [Install uv](https://github.com/astral-sh/uv)
```

3. **Add Python 3.12 warning**:
```diff
 ### Setup

 ```bash
+# IMPORTANT: Use Python 3.11.x (NOT 3.12)
+# Check your version:
+python --version
+# Should be: Python 3.11.x
+
 # Clone and navigate to project
 cd /home/bitnom/Code/apothic-monorepo/mcp/mycord
```

4. **Update Overview**:
```diff
 ## Overview

-mycord is a FastMCP server that provides Discord integration via the Model Context Protocol (MCP). It enables AI assistants to interact with Discord through a standardized interface using discord.py-self for user account automation.
+mycord is a FastMCP server that provides Discord integration via the Model Context Protocol (MCP). It enables AI assistants to interact with Discord through a standardized interface using selfcord.py for user account automation.
```

5. **Update Acknowledgments**:
```diff
 ## Acknowledgments

 - [FastMCP](https://gofastmcp.com/) - FastMCP framework for MCP servers
-- [discord.py-self](https://github.com/dolfies/discord.py-self) - Unofficial Discord user account library
+- [selfcord.py](https://github.com/Shell1010/Selfcord) - Discord selfbot library
 - [Pydantic](https://docs.pydantic.dev/) - Data validation with Python type hints
```

#### 5.2 Update PLANNING_AND_PROGRESS.md

**File:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/PLANNING_AND_PROGRESS.md`

**Changes:**

1. **Update Current Status**:
```diff
 ## Current Status - Slice 1 Complete ✅

 ### Slice 1: Health Check & Foundation (Week 1)

 **Status:** ✅ COMPLETE
 **Test Coverage:** 90%+ (targeting 95% for contracts)
-**Completion Date:** 2024-10-24
+**Completion Date:** 2025-10-24
+**Migration:** Migrated from discord.py-self to selfcord.py
```

2. **Update Known Issues** (remove blocker, add Python 3.12 note):
```diff
 ### Slice 1

-1. **🔴 CRITICAL BLOCKER: discord.py-self Fatal Import Error (ALL Python 3.11+)**
-   - **Status**: 🛑 PROJECT BLOCKED - Cannot run server
-   ...
-   (Remove entire section)

+1. **⚠️  Python 3.12 Incompatibility**
+   - **Status**: 🟡 DOCUMENTED - Constraint enforced
+   - **Severity**: HIGH - Installation fails with Python 3.12
+   - **Issue**: selfcord.py dependency aiohttp==3.8.5 incompatible with Python 3.12
+   - **Error**: `'PyLongObject' has no member named 'ob_digit'` (Python 3.12 C API changes)
+   - **Resolution**: Use Python 3.11.x (tested: 3.11.14)
+   - **Enforcement**: `requires-python = ">=3.11,<3.12"` in pyproject.toml
+   - **Impact**: Development must use Python 3.11 until selfcord.py updates dependencies
```

3. **Add Migration Notes**:
```diff
+### Migration from discord.py-self to selfcord.py (2025-10-24)
+
+**Reason:** discord.py-self had fatal import bug affecting all Python 3.11+ versions
+
+**Changes:**
+- Replaced `discord.py-self>=2.0.0` with `selfcord.py>=1.0.3`
+- Refactored `DiscordClientAdapter` to use selfcord.Bot() API
+- Updated event handling from `@client.event` to `@bot.on("event_name")` decorators
+- Changed connection from async `await client.start()` to threaded `bot.run()`
+- Added Python 3.12 incompatibility constraint
+
+**Testing:** All tests pass (12/12), manual validation successful
+
+**Documentation:** See `SELFCORD_TEST_RESULTS.md` and `MIGRATION_PLAN.md`
```

---

### Step 6: Quality Gates (15 minutes)

**Run all validation checks:**

```bash
cd /home/bitnom/Code/apothic-monorepo/mcp/mycord

# 1. Format code
make fmt
# Expected: No changes needed

# 2. Lint code
make lint
# Expected: No errors

# 3. Run all tests
make test
# Expected: 12 tests pass, 90%+ coverage

# 4. Build project
make build
# Expected: No errors

# 5. Manual server test
make run
# Expected: Server starts, Discord connects
```

**Checklist:**
- ✅ Code formatted (black)
- ✅ No lint errors (ruff)
- ✅ All tests pass (pytest)
- ✅ 90%+ test coverage
- ✅ Server runs successfully
- ✅ Discord connection works
- ✅ Health tool responds correctly

---

## Rollback Plan

If migration fails:

### Option 1: Revert Dependencies

```bash
cd /home/bitnom/Code/apothic-monorepo/mcp/mycord

# Remove selfcord.py
uv remove selfcord.py

# Restore discord.py-self (will still be broken, but code will compile)
uv add discord.py-self

# Restore pyproject.toml from git
git checkout pyproject.toml
```

### Option 2: Use Bot Account (Official discord.py)

If selfcord.py also has issues:

```bash
# Switch to official discord.py (bot accounts only)
uv remove selfcord.py
uv add discord.py

# Update code to use bot accounts (no selfbot functionality)
# Requires bot token instead of user token
```

---

## Success Criteria

Migration is successful when:

1. ✅ All tests pass (12/12, 90%+ coverage)
2. ✅ Server starts without errors
3. ✅ Discord connection established (~5 seconds)
4. ✅ Health tool returns connected status with user info
5. ✅ No errors in structured logs
6. ✅ Documentation updated and accurate
7. ✅ Python 3.12 constraint enforced

---

## Timeline

| Step | Duration | Cumulative |
|------|----------|------------|
| 1. Update Dependencies | 10 min | 10 min |
| 2. Refactor Adapter (TDD) | 60-90 min | 70-100 min |
| 3. Update Tests | 30-60 min | 100-160 min |
| 4. Manual Validation | 15-30 min | 115-190 min |
| 5. Update Documentation | 20-30 min | 135-220 min |
| 6. Quality Gates | 15 min | 150-235 min |
| **Total** | **2.5-4 hours** | |

---

## References

- **Test Results:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/SELFCORD_TEST_RESULTS.md`
- **selfcord.py GitHub:** https://github.com/Shell1010/Selfcord
- **selfcord.py Wiki:** https://github.com/Shell1010/Selfcord/wiki
- **Example Usage:** `/home/bitnom/Code/apothic-monorepo/mcp/mycord/examples/selfcord_simple_test.py`

---

**Plan Created:** 2025-10-24
**Author:** Mycord MCP Development Agent
**Status:** READY FOR EXECUTION
