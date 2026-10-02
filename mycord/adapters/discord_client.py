"""Discord client adapter using discord.py-self.

WARNING: discord.py-self is UNOFFICIAL and VIOLATES Discord's Terms of Service.
This library automates user accounts (selfbots), which can result in account BANS.
This is for personal automation only - never use in production or commercial settings.
"""

import logging

import discord

logger = logging.getLogger(__name__)


class DiscordClientAdapter:
    """Adapter for discord.py-self client operations.

    This adapter wraps discord.py-self client functionality and provides
    a clean interface for the application layer. It handles connection
    management, error handling, and provides defensive operation patterns.

    Attributes:
        client: The discord.py-self Client instance
    """

    def __init__(self, token: str) -> None:
        """Initialize Discord client adapter.

        Args:
            token: Discord user account token (NEVER log this value)

        Note:
            discord.py-self detects selfbot mode from the token itself during
            login, so no self_bot flag is required here. Passing `self_bot`
            is not part of discord.Client's accepted options.
        """
        self.client = discord.Client()
        self._token = token  # NEVER log this
        self._ready = False

        # Setup ready event handler
        @self.client.event
        async def on_ready() -> None:
            self._ready = True
            # NEVER log the token, only log non-sensitive info
            logger.info(
                "Discord client connected",
                extra={
                    "user_id": str(self.client.user.id) if self.client.user else None,
                    "username": str(self.client.user) if self.client.user else None,
                },
            )

    async def connect(self) -> None:
        """Connect to Discord (non-blocking).

        This starts the Discord client connection in the background.
        Use is_connected() to check connection status.
        """
        if not self.client.is_closed():
            logger.info("Discord client already started")
            return

        logger.info("Starting Discord client connection")
        # Start client in background (don't await, let it connect)
        # The on_ready event will set _ready = True
        import asyncio

        asyncio.create_task(self.client.start(self._token))

    async def disconnect(self) -> None:
        """Disconnect from Discord and cleanup resources."""
        if not self.client.is_closed():
            logger.info("Closing Discord client connection")
            await self.client.close()
            self._ready = False

    def is_connected(self) -> bool:
        """Check if Discord client is connected and ready.

        Returns:
            True if client is connected and ready, False otherwise
        """
        return self._ready and self.client.user is not None

    @property
    def user_id(self) -> str | None:
        """Get current user ID as string (Discord snowflake).

        Returns:
            User ID as string if connected, None otherwise
        """
        if self.client.user:
            return str(self.client.user.id)
        return None

    @property
    def username(self) -> str | None:
        """Get current username with discriminator.

        Returns:
            Username string if connected, None otherwise
        """
        if self.client.user:
            return str(self.client.user)
        return None
