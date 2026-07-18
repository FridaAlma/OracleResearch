# =============================================================================
# Oracle - Configuration Tests
# =============================================================================

"""
Unit tests for the configuration module.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from config import (
    Config,
    ModelTier,
    LogLevel,
    get_config,
    reset_config,
    reload_config,
    validate_api_key,
    validate_model_id,
    validate_api_base_url,
)


# =============================================================================
# Test Configuration Class
# =============================================================================

class TestConfig:
    """Tests for the Config class."""
    
    def test_default_values(self):
        """Test that default values are set correctly."""
        config = Config()
        
        assert config.MODEL_ID == "deepseek-v4-flash"
        assert config.MODEL_PRO_ID == "deepseek-v4-pro"
        assert config.MODEL_TIER == ModelTier.AUTO
        assert config.API_BASE_URL == "https://api.deepseek.com"
        assert config.HOST == "127.0.0.1"
        assert config.PORT == 8000
        assert config.MAX_TOKENS == 16384
        assert config.REQUEST_TIMEOUT == 300.0
    
    def test_env_override(self):
        """Test that environment variables override defaults."""
        os.environ["MODEL_ID"] = "custom-model"
        os.environ["PORT"] = "9000"
        os.environ["MAX_TOKENS"] = "2000"
        
        try:
            config = Config()
            assert config.MODEL_ID == "custom-model"
            assert config.PORT == 9000
            assert config.MAX_TOKENS == 2000
        finally:
            # Clean up
            del os.environ["MODEL_ID"]
            del os.environ["PORT"]
            del os.environ["MAX_TOKENS"]
    
    def test_base_dir(self):
        """Test that BASE_DIR is set correctly."""
        config = Config()
        assert config.BASE_DIR.exists()
        assert config.BASE_DIR.is_dir()
    
    def test_paths_created(self):
        """Test that necessary directories are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            config = Config()
            
            # Check that directories are created under BASE_DIR
            assert (config.BASE_DIR / "data").exists()
            assert (config.BASE_DIR / "logs").exists()
            assert (config.BASE_DIR / "tools").exists()
            assert (config.BASE_DIR / "workspace").exists()
    
    def test_api_base_url_validation(self):
        """Test that API base URL is validated and corrected."""
        os.environ["API_BASE_URL"] = "api.test.com"
        
        try:
            config = Config()
            assert config.API_BASE_URL == "https://api.test.com"
        finally:
            del os.environ["API_BASE_URL"]


# =============================================================================
# Test Singleton Functions
# =============================================================================

class TestConfigSingleton:
    """Tests for configuration singleton functions."""
    
    def setup_method(self):
        """Reset config before each test."""
        reset_config()
    
    def teardown_method(self):
        """Reset config after each test."""
        reset_config()
    
    def test_get_config(self):
        """Test get_config returns Config instance."""
        config = get_config()
        assert isinstance(config, Config)
    
    def test_get_config_singleton(self):
        """Test that get_config returns the same instance."""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2
    
    def test_reset_config(self):
        """Test that reset_config creates a new instance."""
        config1 = get_config()
        reset_config()
        config2 = get_config()
        assert config1 is not config2
    
    def test_reload_config(self):
        """Test that reload_config updates values from environment."""
        os.environ["MAX_TOKENS"] = "9999"
        
        try:
            config = get_config()
            original_value = config.MAX_TOKENS
            
            os.environ["MAX_TOKENS"] = "8888"
            reload_config()
            
            assert config.MAX_TOKENS == 8888
            assert config.MAX_TOKENS != original_value
        finally:
            del os.environ["MAX_TOKENS"]
            reset_config()


# =============================================================================
# Test Validation Functions
# =============================================================================

class TestValidationFunctions:
    """Tests for validation functions."""
    
    def test_validate_api_key(self):
        """Test API key validation."""
        assert validate_api_key("valid-key-1234567890123456") is True
        assert validate_api_key("short") is False
        assert validate_api_key("") is False
        assert validate_api_key(None) is False
    
    def test_validate_model_id(self):
        """Test model ID validation."""
        assert validate_model_id("valid-model-123") is True
        assert validate_model_id("model_with_underscores") is True
        assert validate_model_id("model-with-dashes") is True
        assert validate_model_id("a") is False  # Too short
        assert validate_model_id("") is False
        assert validate_model_id("invalid model with spaces") is False
        assert validate_model_id(None) is False
    
    def test_validate_api_base_url(self):
        """Test API base URL validation."""
        assert validate_api_base_url("http://api.test.com") is True
        assert validate_api_base_url("https://api.test.com") is True
        assert validate_api_base_url("https://api.test.com/v1") is True
        assert validate_api_base_url("api.test.com") is False
        assert validate_api_base_url("") is False
        assert validate_api_base_url(None) is False


# =============================================================================
# Test Configuration Serialization
# =============================================================================

class TestConfigSerialization:
    """Tests for configuration serialization."""
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = Config()
        config_dict = config.to_dict()
        
        assert isinstance(config_dict, dict)
        assert "BASE_DIR" in config_dict
        assert "MODEL_ID" in config_dict
        assert "HOST" in config_dict
        assert "PORT" in config_dict
        assert config_dict["MODEL_ID"] == "deepseek-v4-flash"
