"""Core domain contracts - Pydantic models for data structures.

WARNING: This library uses discord.py-self which VIOLATES Discord's Terms of Service.
User accounts can be BANNED for selfbot activity. Use at your own risk.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(BaseModel):
    """Health status contract for the mycord MCP server.

    Attributes:
        status: Service status indicator (ok, error, degraded)
        timestamp: UTC timestamp of health check in ISO-8601 format
        discord_connected: Whether Discord client is currently connected
        user_id: Discord user ID (snowflake as string) if connected
        username: Discord username with discriminator if connected
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "timestamp": "2024-01-15T10:30:00Z",
                "discord_connected": True,
                "user_id": "123456789012345678",
                "username": "TestUser#1234",
            }
        }
    )

    status: str = Field(..., description="Service status: ok, error, or degraded")
    timestamp: datetime = Field(..., description="UTC timestamp of health check")
    discord_connected: bool = Field(
        default=False, description="Whether Discord client is connected"
    )
    user_id: str | None = Field(default=None, description="Discord user ID (snowflake as string)")
    username: str | None = Field(default=None, description="Discord username with discriminator")
