"""Unit tests for core contracts (Pydantic models)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


@pytest.mark.unit
def test_health_status_model_valid():
    """Test HealthStatus model accepts valid data."""
    from mycord.core.contracts import HealthStatus

    status = HealthStatus(
        status="ok",
        timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        discord_connected=True,
        user_id="123456789012345678",
        username="TestUser#1234",
    )

    assert status.status == "ok"
    assert status.discord_connected is True
    assert status.user_id == "123456789012345678"
    assert status.username == "TestUser#1234"


@pytest.mark.unit
def test_health_status_requires_status_field():
    """Test HealthStatus model rejects missing status field."""
    from mycord.core.contracts import HealthStatus

    with pytest.raises(ValidationError, match="status"):
        HealthStatus(timestamp=datetime.now(timezone.utc), discord_connected=False)


@pytest.mark.unit
def test_health_status_serialization_to_json():
    """Test HealthStatus model serializes to JSON correctly."""
    from mycord.core.contracts import HealthStatus

    status = HealthStatus(
        status="ok",
        timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        discord_connected=True,
        user_id="123456789012345678",
        username="TestUser#1234",
    )

    json_data = status.model_dump_json()

    assert '"status":"ok"' in json_data or '"status": "ok"' in json_data
    assert "2024-01-15T10:30:00Z" in json_data
    assert '"discord_connected":true' in json_data or '"discord_connected": true' in json_data


@pytest.mark.unit
def test_health_status_deserialization_from_dict():
    """Test HealthStatus model deserializes from dict correctly."""
    from mycord.core.contracts import HealthStatus

    data = {
        "status": "ok",
        "timestamp": "2024-01-15T10:30:00Z",
        "discord_connected": True,
        "user_id": "123456789012345678",
        "username": "TestUser#1234",
    }

    status = HealthStatus.model_validate(data)

    assert status.status == "ok"
    assert status.discord_connected is True
    assert status.user_id == "123456789012345678"


@pytest.mark.unit
def test_health_status_optional_fields():
    """Test HealthStatus model handles optional fields correctly."""
    from mycord.core.contracts import HealthStatus

    # When disconnected, user_id and username should be None
    status = HealthStatus(
        status="ok",
        timestamp=datetime.now(timezone.utc),
        discord_connected=False,
        user_id=None,
        username=None,
    )

    assert status.discord_connected is False
    assert status.user_id is None
    assert status.username is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status_value", "expected"),
    [
        ("ok", "ok"),
        ("error", "error"),
        ("degraded", "degraded"),
    ],
)
def test_health_status_various_status_values(status_value, expected):
    """Test HealthStatus model handles various status values."""
    from mycord.core.contracts import HealthStatus

    status = HealthStatus(
        status=status_value, timestamp=datetime.now(timezone.utc), discord_connected=False
    )

    assert status.status == expected
