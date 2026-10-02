"""FastMCP server for mycord - Discord integration via MCP.

WARNING: This server uses discord.py-self which VIOLATES Discord's Terms of Service.
Using this tool can result in account BANS. This is for personal automation only.
NEVER use this in production or commercial settings.

Environment Variables:
    DISCORD_TOKEN: Your Discord user account token (required)
"""

# ruff: noqa: E402, I001
# E402: Import order is intentional (load_dotenv must run first, discord before FastMCP)
# I001: Import block unsorted is intentional (discord must be imported before FastMCP)

import logging
import os
from datetime import datetime, timezone
from typing import Any

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()  # Load .env before accessing environment variables

# CRITICAL: Import discord-related modules BEFORE FastMCP
# FastMCP modifies inspect.signature behavior which breaks discord.py-self's
# flatten_user decorator in member.py. Importing discord first works around this.
from mycord.adapters.discord_client import DiscordClientAdapter
from mycord.core.contracts import HealthStatus

# Import FastMCP AFTER discord modules to avoid inspect.signature conflict
from fastmcp import FastMCP

# Setup structured logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global Discord adapter (initialized on server start)
_discord_adapter: DiscordClientAdapter | None = None

# Create FastMCP server
mcp = FastMCP(name="mycord", version="0.1.0")


async def create_health_status(adapter: DiscordClientAdapter | None) -> dict[str, Any]:
    """Create health status dict from Discord adapter state.

    This is a pure function that can be tested independently of FastMCP.

    Args:
        adapter: Discord client adapter instance (can be None)

    Returns:
        Dictionary with health status information
    """
    try:
        # Determine connection status
        if adapter is None:
            connected = False
            user_id = None
            username = None
            status = "ok"  # Server is ok, just not connected yet
        else:
            try:
                connected = adapter.is_connected()
                user_id = adapter.user_id if connected else None
                username = adapter.username if connected else None
                status = "ok"
            except Exception:
                logger.error("Error checking Discord connection", exc_info=True)
                connected = False
                user_id = None
                username = None
                status = "degraded"

        # Create health status
        health = HealthStatus(
            status=status,
            timestamp=datetime.now(timezone.utc),
            discord_connected=connected,
            user_id=user_id,
            username=username,
        )

        return health.model_dump(mode="json")

    except Exception:
        logger.error("Error creating health status", exc_info=True)
        # Return minimal error status
        return {
            "status": "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discord_connected": False,
            "user_id": None,
            "username": None,
        }


@mcp.tool()
async def health() -> dict[str, Any]:
    """Check health status of the mycord MCP server.

    Returns the current health status including Discord connection state.
    This tool can be used to verify the server is running and whether
    Discord connection is active.

    Returns:
        Health status information with the following fields:
        - status: Service status (ok, error, degraded)
        - timestamp: ISO-8601 UTC timestamp of health check
        - discord_connected: Whether Discord client is connected
        - user_id: Discord user ID if connected (None otherwise)
        - username: Discord username if connected (None otherwise)

    Example:
        {
            "status": "ok",
            "timestamp": "2024-01-15T10:30:00Z",
            "discord_connected": true,
            "user_id": "123456789012345678",
            "username": "TestUser#1234"
        }
    """
    logger.info("Health check requested")
    return await create_health_status(_discord_adapter)


async def initialize_discord() -> None:
    """Initialize Discord client adapter from environment.

    This loads the DISCORD_TOKEN from environment and creates the adapter.
    Connection happens in the background.
    """
    global _discord_adapter

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.warning(
            "DISCORD_TOKEN not set - Discord features will be unavailable. "
            "Health endpoint will still work but show disconnected status."
        )
        _discord_adapter = None
        return

    logger.info("Initializing Discord client adapter")
    _discord_adapter = DiscordClientAdapter(token)

    # Start connection in background (non-blocking)
    await _discord_adapter.connect()


async def shutdown_discord() -> None:
    """Shutdown Discord client adapter and cleanup resources."""
    global _discord_adapter

    if _discord_adapter:
        logger.info("Shutting down Discord client adapter")
        await _discord_adapter.disconnect()
        _discord_adapter = None


if __name__ == "__main__":
    import asyncio

    # Initialize Discord before running server
    asyncio.run(initialize_discord())

    try:
        logger.info("Starting mycord MCP server")
        mcp.run()
    finally:
        # Cleanup on shutdown
        asyncio.run(shutdown_discord())
