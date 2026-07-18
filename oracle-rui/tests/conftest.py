# =============================================================================
# Oracle - Pytest Configuration
# =============================================================================

"""
Pytest configuration and fixtures for Oracle tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Environment variables for testing
os.environ["API_KEY"] = "test-api-key-12345678901234567890"
os.environ["API_BASE_URL"] = "https://api.test.com"
os.environ["MODEL_ID"] = "test-model"
os.environ["MODEL_PRO_ID"] = "test-model-pro"
os.environ["MODEL_TIER"] = "auto"
os.environ["HOST"] = "127.0.0.1"
os.environ["PORT"] = "8000"
os.environ["REQUEST_TIMEOUT"] = "30"
os.environ["MAX_TOKENS"] = "100"
os.environ["REQUIRE_AUTHENTICATION"] = "false"
os.environ["SHELL_ENABLED"] = "true"
os.environ["SHELL_TIMEOUT"] = "30"
os.environ["SHELL_SANDBOX_ENABLED"] = "false"
os.environ["DANGEROUS_TOOLS_ENABLED"] = "false"
os.environ["AUTH_SECRET_KEY"] = "test-secret-key-123456789012345678901234567890"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "7"
os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
os.environ["DEFAULT_ADMIN_EMAIL"] = "admin@test.com"
os.environ["DEFAULT_ADMIN_PASSWORD"] = "TestPassword123!"
os.environ["USERS_DB_PATH"] = str(PROJECT_ROOT / "tests" / "test_users.db")
os.environ["RATE_LIMIT_ENABLED"] = "false"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_data_dir() -> Path:
    """Get the test data directory."""
    data_dir = PROJECT_ROOT / "tests" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def clean_users_db() -> Generator[None, None, None]:
    """Clean up test users database before and after tests."""
    db_path = Path(os.environ.get("USERS_DB_PATH", "tests/test_users.db"))
    # Remove if exists
    if db_path.exists():
        db_path.unlink()
    yield
    # Clean up after test
    if db_path.exists():
        db_path.unlink()


# =============================================================================
# Test Configuration
# =============================================================================

def pytest_configure(config):
    """Pytest configuration."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end tests"
    )


# =============================================================================
# Custom Assertions
# =============================================================================

def assert_response_success(response):
    """Assert that a response is successful (2xx status code)."""
    assert 200 <= response.status_code < 300, \
        f"Expected success status code, got {response.status_code}: {response.text}"


def assert_response_error(response, status_code: int):
    """Assert that a response has the expected error status code."""
    assert response.status_code == status_code, \
        f"Expected status code {status_code}, got {response.status_code}: {response.text}"
