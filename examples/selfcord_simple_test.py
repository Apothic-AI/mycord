#!/usr/bin/env python3
"""
Simple selfcord.py connectivity test

Tests basic connection using bot.run() as documented.
"""

import os
import sys
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import selfcord

# Global state for test results
test_results = {
    "connected": False,
    "user_info": None,
    "connection_time": None,
    "error": None,
}

# Create bot
bot = selfcord.Bot()

@bot.on("ready")
async def on_ready(ready_time):
    """Called when bot successfully connects"""
    global test_results
    test_results["connected"] = True
    test_results["connection_time"] = datetime.now()
    test_results["user_info"] = {
        "username": bot.user.name if hasattr(bot, 'user') and hasattr(bot.user, 'name') else "unknown",
        "user_id": str(bot.user.id) if hasattr(bot, 'user') and hasattr(bot.user, 'id') else "unknown",
    }

    print(f"✅ Connected successfully!")
    print(f"   Username: {test_results['user_info']['username']}")
    print(f"   User ID: {test_results['user_info']['user_id']}")
    print(f"   Connection time: {test_results['connection_time'].isoformat()}")
    print(f"   Ready time from event: {ready_time}")

    # Wait 2 seconds then stop
    import asyncio
    await asyncio.sleep(2)
    print("✅ Test complete, shutting down...")
    # Note: selfcord.py may not have a clean shutdown method
    # We'll rely on the timeout in the main thread


def run_bot_thread(token):
    """Run bot in a thread since run() is blocking"""
    global test_results
    try:
        print("🔄 Starting bot.run()...")
        bot.run(token)
    except KeyboardInterrupt:
        print("\n⚠️  Bot run interrupted")
    except Exception as e:
        print(f"❌ Bot run failed: {type(e).__name__}: {e}")
        test_results["error"] = str(e)
        import traceback
        traceback.print_exc()


def main():
    """Main test function"""
    print("="*60)
    print("SELFCORD.PY SIMPLE CONNECTIVITY TEST")
    print("="*60)
    print(f"Start time: {datetime.now().isoformat()}")
    print(f"Python version: {sys.version.split()[0]}")

    # Load token
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "your_discord_user_token_here":
        print("❌ DISCORD_TOKEN not configured in .env")
        sys.exit(1)

    print("✅ Token loaded from environment")
    print(f"   Token length: {len(token)} characters")

    # Run bot in thread with timeout
    bot_thread = threading.Thread(target=run_bot_thread, args=(token,), daemon=True)
    bot_thread.start()

    # Wait for connection or timeout (30 seconds)
    print("⏳ Waiting for connection (30 second timeout)...")
    timeout = 30
    start = time.time()

    while time.time() - start < timeout:
        if test_results["connected"]:
            break
        if test_results["error"]:
            break
        time.sleep(0.5)

    # Give bot a couple seconds after connection
    if test_results["connected"]:
        time.sleep(2)

    # Results
    print("\n" + "="*60)
    print("TEST RESULTS")
    print("="*60)

    if test_results["connected"]:
        print("\n✅ CONNECTIVITY: PASS")
        print(f"   Connection successful in {(test_results['connection_time'] - datetime.fromisoformat(datetime.now().isoformat().split('.')[0])).total_seconds():.2f}s")
    elif test_results["error"]:
        print(f"\n❌ CONNECTIVITY: FAIL")
        print(f"   Error: {test_results['error']}")
    else:
        print(f"\n❌ CONNECTIVITY: TIMEOUT")
        print(f"   No connection established in {timeout} seconds")

    print(f"\nEnd time: {datetime.now().isoformat()}")

    # Cleanup
    print("\n🛑 Stopping bot thread...")
    # Thread is daemon so it will die when main exits

    return 0 if test_results["connected"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
