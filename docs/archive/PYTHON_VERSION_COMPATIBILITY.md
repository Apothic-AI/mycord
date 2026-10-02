# Python Version Compatibility Report - discord.py-self

**Date:** 2024-10-24
**Project:** mycord MCP Server
**Testing Duration:** ~30 minutes
**Outcome:** ✅ SUCCESS - Python 3.10.19 works perfectly

---

## Executive Summary

**Problem:** discord.py-self 2.0.1 failed to import on Python 3.11+ with a fatal TypeError in the `flatten_user` decorator.

**Solution:** Downgraded to Python 3.10.19 - discord.py-self works flawlessly without any code changes.

**Time Saved:** ~2-4 hours (avoided migration to alternative selfbot library like selfcord.py)

**Status:** ✅ PROJECT UNBLOCKED - can proceed with Slice 2 development

---

## Python Version Test Results

### Tested Versions

| Python Version | discord.py-self 2.0.1 | Import Test | Connection Test | Verdict |
|----------------|----------------------|-------------|-----------------|---------|
| 3.12.11 | ❌ FAILED | TypeError | N/A | BROKEN |
| 3.11.14 | ❌ FAILED | TypeError | N/A | BROKEN |
| 3.11.0 | ❌ FAILED | TypeError | N/A | BROKEN |
| **3.10.19** | ✅ **WORKS** | ✅ **SUCCESS** | ✅ **SUCCESS** | **WORKS!** |

### Detailed Test Results

#### Python 3.12.11 - FAILED
```bash
$ uv run python -c "import discord"
TypeError: <discord.utils.CachedSlotProperty object at 0x...> is not a callable object
```

#### Python 3.11.14 - FAILED
```bash
$ uv run python -c "import discord"
TypeError: <discord.utils.CachedSlotProperty object at 0x...> is not a callable object
```

#### Python 3.10.19 - SUCCESS ✅
```bash
$ mise exec -- uv run python -c "import discord; print(f'SUCCESS: discord.py-self imported! Version: {discord.__version__}')"
SUCCESS: discord.py-self imported! Version: 2.0.1

$ mise exec -- uv run python test_py310_discord.py
Python 3.10.19 discord.py-self test
discord.py-self version: 2.0.1
Attempting to connect to Discord...
SUCCESS: Connected as tomexmachina (ID: 590347867588263939)
Account type: User
```

---

## Root Cause Analysis

### The Bug

discord.py-self 2.0.1 has a bug in `discord/member.py` in the `flatten_user` decorator (line 213):

```python
# discord/member.py, line 213
utils.copy_doc(value)  # Fails when value is CachedSlotProperty
```

The `copy_doc` function calls `inspect.signature(original)` which fails on `CachedSlotProperty` descriptor objects because they are not callable.

### Python 3.11+ Changes

Python 3.11+ made changes to `inspect.signature()` behavior that cause it to fail when attempting to extract signatures from non-callable objects like property descriptors.

### Why Python 3.10 Works

Python 3.10's `inspect.signature()` is more lenient and doesn't fail on property descriptors, allowing discord.py-self to import successfully.

---

## Migration Steps Performed

### 1. Updated `.python-version`
```bash
echo "3.10.19" > .python-version
```

### 2. Installed Python 3.10.19 via mise
```bash
mise install python@3.10.19
```

### 3. Updated `pyproject.toml`

**Changed:**
- `requires-python = ">=3.10"` (was `>=3.11`)
- Added `"Programming Language :: Python :: 3.10"` to classifiers
- Updated `target-version = ["py310"]` in `[tool.black]`
- Updated `target-version = "py310"` in `[tool.ruff]`
- Updated `python_version = "3.10"` in `[tool.mypy]`

### 4. Reinstalled Dependencies
```bash
mise exec -- uv sync
```

**Result:** All 79 dependencies installed successfully with no errors.

---

## Verification Tests

### Import Test
```bash
$ mise exec -- uv run python -c "import discord; print(discord.__version__)"
2.0.1
```
✅ PASS

### Connection Test
```python
import discord
import asyncio

client = discord.Client()

@client.event
async def on_ready():
    print(f"SUCCESS: Connected as {client.user.name} (ID: {client.user.id})")
    await client.close()

asyncio.run(client.start(token))
```

**Output:**
```
SUCCESS: Connected as tomexmachina (ID: 590347867588263939)
Account type: User
```
✅ PASS

### Unit Tests
```bash
$ mise exec -- uv run pytest tests/ -v --no-cov
============================== 12 passed in 0.14s ==============================
```
✅ PASS

---

## Recommendations

### ✅ DO

1. **Use Python 3.10.19** (or latest Python 3.10.x) for this project
2. **Pin Python version** in `.python-version` to prevent accidental upgrades
3. **Document this constraint** in README.md and PLANNING_AND_PROGRESS.md
4. **Add CI check** to verify Python version in Slice 5 (GitHub Actions)
5. **Monitor discord.py-self releases** for Python 3.11+ compatibility fixes

### ❌ DO NOT

1. **Do NOT upgrade to Python 3.11+** without testing discord.py-self first
2. **Do NOT remove `.python-version`** file
3. **Do NOT change `requires-python` in pyproject.toml** to `>=3.11`
4. **Do NOT assume newer Python versions work** without explicit testing

---

## Alternative Approaches Considered (NOT NEEDED)

### Option 1: Wait for discord.py-self fix
- **Status:** GitHub issue #788 closed but NOT fixed
- **Verdict:** Uncertain timeline, project would remain blocked

### Option 2: Patch discord.py-self locally
- **Effort:** Medium (modify `member.py` flatten_user decorator)
- **Maintenance:** High (must re-patch on every update)
- **Verdict:** Fragile, not recommended

### Option 3: Migrate to selfcord.py
- **Effort:** High (2-4 hours to test and migrate)
- **Risk:** Unknown compatibility, smaller community
- **Verdict:** Unnecessary now that Python 3.10 works

### Option 4: Migrate to official discord.py (Bot API)
- **Effort:** Medium (1-2 hours)
- **Impact:** Lose selfbot functionality (user account features)
- **Verdict:** Changes project scope, not desirable

---

## Conclusion

**Python 3.10.19 works perfectly with discord.py-self 2.0.1.**

No library migration needed. No code changes needed. Simple Python version downgrade resolved the critical blocker.

**Project Status:** ✅ UNBLOCKED - Ready to proceed with Slice 2 development

---

## Future Monitoring

### Watch for discord.py-self Updates

Monitor these resources for Python 3.11+ compatibility fixes:

- GitHub: https://github.com/dolfies/discord.py-self
- Issue #788: https://github.com/dolfies/discord.py-self/issues/788
- PyPI: https://pypi.org/project/discord.py-self/

### Upgrade Path (When Available)

When discord.py-self releases Python 3.11+ support:

1. Test in isolated environment first
2. Update `.python-version` to newer Python version
3. Update `pyproject.toml` tool configurations
4. Run full test suite to verify compatibility
5. Update documentation to reflect new minimum version
6. Update CI to test on newer Python versions

---

**Document Version:** 1.0
**Last Updated:** 2024-10-24
**Author:** mycord-mcp-dev-agent
**Status:** ACTIVE - Python 3.10.19 required
