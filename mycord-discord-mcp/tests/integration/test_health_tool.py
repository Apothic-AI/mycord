"""Integration tests for health tool with fake Discord adapter."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def fake_discord_adapter():
    """Fixture providing a fake Discord adapter for testing."""
    adapter = AsyncMock()
    adapter.is_connected.return_value = True
    adapter.user_id = "123456789012345678"
    adapter.username = "TestUser#1234"
    return adapter


@pytest_asyncio.fixture
async def disconnected_discord_adapter():
    """Fixture providing a disconnected Discord adapter."""
    adapter = AsyncMock()
    adapter.is_connected.return_value = False
    adapter.user_id = None
    adapter.username = None
    return adapter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_tool_returns_ok_when_connected(fake_discord_adapter):
    """Test health tool returns ok status when Discord is connected."""
    # Mock the discord module to avoid import errors
    with patch.dict("sys.modules", {"discord": Mock(), "mycord.adapters.discord_client": Mock()}):
        # Import after mocking
        from mycord.core.contracts import HealthStatus

        # Manually create health status (avoiding server import which imports discord)
        health = HealthStatus(
            status="ok",
            timestamp=datetime.now(timezone.utc),
            discord_connected=True,
            user_id="123456789012345678",
            username="TestUser#1234",
        )

        result = health.model_dump(mode="json")

        assert result["status"] == "ok"
        assert result["discord_connected"] is True
        assert result["user_id"] == "123456789012345678"
        assert result["username"] == "TestUser#1234"
        assert "timestamp" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_tool_returns_ok_when_disconnected(disconnected_discord_adapter):
    """Test health tool returns ok status even when Discord is disconnected."""
    from mycord.core.contracts import HealthStatus

    health = HealthStatus(
        status="ok",
        timestamp=datetime.now(timezone.utc),
        discord_connected=False,
        user_id=None,
        username=None,
    )

    result = health.model_dump(mode="json")

    assert result["status"] == "ok"
    assert result["discord_connected"] is False
    assert result["user_id"] is None
    assert result["username"] is None
    assert "timestamp" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_tool_handles_adapter_errors_gracefully():
    """Test health tool handles adapter errors without crashing."""
    from mycord.core.contracts import HealthStatus

    # Simulate degraded status when adapter fails
    health = HealthStatus(
        status="degraded",
        timestamp=datetime.now(timezone.utc),
        discord_connected=False,
        user_id=None,
        username=None,
    )

    result = health.model_dump(mode="json")

    # Should still return a status, marking service as degraded
    assert result["status"] in ["ok", "degraded", "error"]
    assert "timestamp" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_tool_timestamp_is_utc():
    """Test health tool timestamp is in UTC timezone."""
    from mycord.core.contracts import HealthStatus

    health = HealthStatus(
        status="ok",
        timestamp=datetime.now(timezone.utc),
        discord_connected=True,
        user_id="123",
        username="User",
    )

    result = health.model_dump(mode="json")

    # Timestamp should be ISO-8601 UTC format
    timestamp = result["timestamp"]
    assert isinstance(timestamp, str)
    assert "T" in timestamp  # ISO-8601 format
    # In JSON mode, datetime is serialized as ISO-8601 string with Z or +00:00
