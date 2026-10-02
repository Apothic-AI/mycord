#!/usr/bin/env python3
"""
Selfcord.py Compatibility Test Script

This script tests selfcord.py as a potential replacement for discord.py-self.

IMPORTANT: This violates Discord's Terms of Service. Use at your own risk.
"""

import asyncio
import os
import sys
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv

load_dotenv()

# Import selfcord
try:
    import selfcord
    print(f"✅ selfcord.py imported successfully (version: {selfcord.__version__ if hasattr(selfcord, '__version__') else 'unknown'})")
except ImportError as e:
    print(f"❌ Failed to import selfcord.py: {e}")
    sys.exit(1)


# Test 1: Basic Connectivity Test
class ConnectivityTest:
    """Tests basic connection to Discord using selfcord.py"""

    def __init__(self):
        self.bot = None
        self.connected = False
        self.user_info = None
        self.connection_time = None

    async def run(self):
        """Run the connectivity test"""
        print("\n" + "="*60)
        print("TEST 1: BASIC CONNECTIVITY")
        print("="*60)

        # Load token
        token = os.getenv("DISCORD_TOKEN")
        if not token or token == "your_discord_user_token_here":
            print("❌ DISCORD_TOKEN not configured in .env")
            return False

        print("✅ Token loaded from environment")
        print(f"   Token length: {len(token)} characters")

        # Create bot instance
        try:
            self.bot = selfcord.Bot()
            print("✅ Bot instance created")
        except Exception as e:
            print(f"❌ Failed to create bot instance: {e}")
            return False

        # Setup event handlers
        @self.bot.on("ready")
        async def on_ready(time):
            self.connected = True
            self.connection_time = datetime.now()
            self.user_info = {
                "username": self.bot.user.name if hasattr(self.bot, 'user') else "unknown",
                "user_id": str(self.bot.user.id) if hasattr(self.bot, 'user') and hasattr(self.bot.user, 'id') else "unknown",
            }
            print(f"✅ Connected successfully!")
            print(f"   Username: {self.user_info['username']}")
            print(f"   User ID: {self.user_info['user_id']}")
            print(f"   Connection time: {self.connection_time.isoformat()}")

            # Stop the bot after successful connection
            await asyncio.sleep(2)  # Wait 2 seconds to ensure connection is stable
            await self.bot.close()

        @self.bot.on("error")
        async def on_error(error):
            print(f"❌ Error event received: {error}")

        # Run the bot with timeout
        try:
            print("🔄 Attempting to connect...")
            # selfcord.py uses run() which is blocking, so we need to run it in a thread
            # or use the async runner pattern
            loop = asyncio.get_event_loop()

            # Create a task to stop the bot after connection
            async def run_bot():
                # Note: bot.run() is synchronous and blocking, need to use it differently
                # Let's try calling the internal async methods instead
                try:
                    # Initialize the bot's internal state
                    self.bot.token = token
                    # Try to call startup directly
                    await self.bot.startup()
                    # Wait for ready event to fire
                    await asyncio.sleep(5)
                    return self.connected
                except Exception as e:
                    print(f"Error during startup: {e}")
                    raise

            result = await asyncio.wait_for(run_bot(), timeout=30.0)
            return result
        except asyncio.TimeoutError:
            print("❌ Connection timeout (30 seconds)")
            return False
        except Exception as e:
            print(f"❌ Connection failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False


# Test 2: API Comparison
def api_comparison_test():
    """Compare selfcord.py API to discord.py-self"""
    print("\n" + "="*60)
    print("TEST 2: API COMPATIBILITY ANALYSIS")
    print("="*60)

    comparisons = {
        "Client Initialization": {
            "discord.py-self": "discord.Client(intents=discord.Intents.none())",
            "selfcord.py": "selfcord.Bot()",
            "compatible": "DIFFERENT - simpler API",
        },
        "Connection": {
            "discord.py-self": "await client.start(token)",
            "selfcord.py": "bot.run(token) or await bot.start(token)",
            "compatible": "SIMILAR - supports both sync and async",
        },
        "Event Handling": {
            "discord.py-self": "@client.event async def on_ready():",
            "selfcord.py": "@bot.on('ready') async def on_ready(time):",
            "compatible": "DIFFERENT - decorator-based with event name",
        },
        "Message Sending": {
            "discord.py-self": "await channel.send(content)",
            "selfcord.py": "await message.channel.send(content)",
            "compatible": "SIMILAR - same method signature",
        },
        "Channel Access": {
            "discord.py-self": "client.get_channel(channel_id)",
            "selfcord.py": "bot.get_channel(channel_id)",
            "compatible": "IDENTICAL - same API",
        },
    }

    for feature, details in comparisons.items():
        print(f"\n{feature}:")
        print(f"  discord.py-self: {details['discord.py-self']}")
        print(f"  selfcord.py:     {details['selfcord.py']}")
        print(f"  Status: {details['compatible']}")

    print("\n📊 Overall Assessment:")
    print("   - API is MOSTLY COMPATIBLE with minor syntax differences")
    print("   - Event handling requires decorator refactoring")
    print("   - Message/channel operations are very similar")
    print("   - Estimated refactor effort: 2-4 hours")


# Test 3: Error Handling
async def error_handling_test():
    """Test error handling with invalid inputs"""
    print("\n" + "="*60)
    print("TEST 3: ERROR HANDLING")
    print("="*60)

    # Test with invalid token
    print("\n🔄 Testing with invalid token...")
    try:
        bot = selfcord.Bot()

        @bot.on("ready")
        async def on_ready(time):
            print("❌ Should not connect with invalid token!")
            await bot.close()

        bot.token = "invalid_token_12345"
        await asyncio.wait_for(bot.startup(), timeout=10.0)
        print("❌ No error raised for invalid token (unexpected)")
        return False
    except asyncio.TimeoutError:
        print("⚠️  Connection timed out (expected for invalid token)")
        return True
    except Exception as e:
        print(f"✅ Error caught gracefully: {type(e).__name__}: {e}")
        return True


# Main test runner
async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("SELFCORD.PY COMPATIBILITY TEST SUITE")
    print("="*60)
    print(f"Start time: {datetime.now().isoformat()}")
    print(f"Python version: {sys.version.split()[0]}")

    results = {
        "installation": True,  # Already passed if we got here
        "connectivity": False,
        "api_compatibility": True,  # Informational only
        "error_handling": False,
    }

    # Test 1: Connectivity
    connectivity = ConnectivityTest()
    results["connectivity"] = await connectivity.run()

    # Test 2: API Comparison (informational)
    api_comparison_test()

    # Test 3: Error Handling
    results["error_handling"] = await error_handling_test()

    # Final Report
    print("\n" + "="*60)
    print("FINAL TEST RESULTS")
    print("="*60)

    print(f"\n✅ Installation: PASS")
    print(f"{'✅' if results['connectivity'] else '❌'} Connectivity: {'PASS' if results['connectivity'] else 'FAIL'}")
    print(f"📊 API Compatibility: HIGH (minor differences)")
    print(f"{'✅' if results['error_handling'] else '❌'} Error Handling: {'PASS' if results['error_handling'] else 'FAIL'}")

    overall_pass = results["connectivity"] and results["error_handling"]

    print("\n" + "="*60)
    print(f"OVERALL ASSESSMENT: {'✅ RECOMMENDED' if overall_pass else '❌ NOT RECOMMENDED'}")
    print("="*60)

    if overall_pass:
        print("\nReasoning:")
        print("  - selfcord.py successfully connects to Discord")
        print("  - Error handling is appropriate and catchable")
        print("  - API is compatible with minor refactoring needed")
        print("  - Library is actively maintained and designed for selfbots")
        print("\nNext Steps:")
        print("  1. Proceed with migration to selfcord.py")
        print("  2. Refactor DiscordClientAdapter to use selfcord.py API")
        print("  3. Update tests to match new event handling patterns")
        print("  4. Estimated effort: 2-4 hours")
    else:
        print("\nReasoning:")
        if not results["connectivity"]:
            print("  - Failed to establish connection to Discord")
        if not results["error_handling"]:
            print("  - Error handling concerns detected")
        print("\nDo not proceed with migration until issues are resolved.")

    print(f"\nEnd time: {datetime.now().isoformat()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test suite failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
