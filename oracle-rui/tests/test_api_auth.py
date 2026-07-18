# =============================================================================
# Oracle - API Authentication Tests
# =============================================================================

"""
Unit tests for the API authentication module.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest

# Import after setting up environment
os.environ["USERS_DB_PATH"] = str(Path(__file__).parent / "test_users.db")

from api.auth import (
    AuthConfig,
    User,
    UserBase,
    UserCreate,
    UserInDB,
    UserInDBBase,
    Token,
    TokenData,
    UserDatabase,
    UserManager,
    create_access_token,
    create_refresh_token,
    verify_token,
    get_password_hash,
    verify_password,
    router as auth_router,
)
from config import get_config


# =============================================================================
# Test Password Hashing
# =============================================================================

class TestPasswordHashing:
    """Tests for password hashing functions."""
    
    def test_get_password_hash(self):
        """Test password hashing."""
        password = "test-password-123!"
        hashed = get_password_hash(password)
        
        assert isinstance(hashed, str)
        assert len(hashed) > 0
        assert hashed != password
    
    def test_verify_password_success(self):
        """Test password verification with correct password."""
        password = "test-password-123!"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_failure(self):
        """Test password verification with incorrect password."""
        password = "test-password-123!"
        wrong_password = "wrong-password-456!"
        hashed = get_password_hash(password)
        
        assert verify_password(wrong_password, hashed) is False


# =============================================================================
# Test JWT Token Management
# =============================================================================

class TestJWTTokenManagement:
    """Tests for JWT token management."""
    
    def test_create_access_token(self):
        """Test access token creation."""
        data = {"sub": "testuser", "email": "test@test.com"}
        token = create_access_token(data)
        
        assert isinstance(token, str)
        assert len(token) > 0
    
    def test_create_refresh_token(self):
        """Test refresh token creation."""
        data = {"sub": "testuser"}
        token = create_refresh_token(data)
        
        assert isinstance(token, str)
        assert len(token) > 0
    
    def test_verify_token_success(self):
        """Test token verification with valid token."""
        data = {"sub": "testuser", "email": "test@test.com"}
        token = create_access_token(data)
        
        payload = verify_token(token)
        
        assert payload["sub"] == "testuser"
        assert payload["email"] == "test@test.com"
        assert "exp" in payload
        assert "iat" in payload
    
    def test_verify_token_expired(self):
        """Test token verification with expired token."""
        data = {"sub": "testuser"}
        # Create token with very short expiration
        token = create_access_token(data, expires_delta=timedelta(seconds=0))
        
        with pytest.raises(Exception):  # Should raise HTTPException
            verify_token(token)


# =============================================================================
# Test User Models
# =============================================================================

class TestUserModels:
    """Tests for user model classes."""
    
    def test_user_base(self):
        """Test UserBase model."""
        user = UserBase(
            username="testuser",
            email="test@test.com",
            full_name="Test User",
            disabled=False
        )
        
        assert user.username == "testuser"
        assert user.email == "test@test.com"
        assert user.full_name == "Test User"
        assert user.disabled is False
    
    def test_user_create(self):
        """Test UserCreate model."""
        user = UserCreate(
            username="testuser",
            email="test@test.com",
            full_name="Test User",
            password="test-password-123!"
        )
        
        assert user.username == "testuser"
        assert user.password == "test-password-123!"
    
    def test_user_from_orm(self):
        """Test User from ORM conversion."""
        db_user = UserInDB(
            id=1,
            username="testuser",
            email="test@test.com",
            full_name="Test User",
            disabled=False,
            is_admin=True,
            created_at=datetime.now(),
            last_login=datetime.now(),
            hashed_password="hashed-password"
        )
        
        user = User.from_orm(db_user)
        
        assert user.id == 1
        assert user.username == "testuser"
        assert user.is_admin is True
        assert user.disabled is False
        # hashed_password should not be in User model
        assert not hasattr(user, "hashed_password")


# =============================================================================
# Test User Database
# =============================================================================

class TestUserDatabase:
    """Tests for user database."""
    
    @pytest.fixture(autouse=True)
    def setup_db(self, clean_users_db):
        """Setup clean database for each test."""
        self.db_path = Path(__file__).parent / "test_users.db"
        self.db = UserDatabase(self.db_path)
    
    def test_create_user(self):
        """Test user creation."""
        user_data = UserCreate(
            username="testuser",
            email="test@test.com",
            full_name="Test User",
            password="TestPassword123!"
        )
        
        created_user = self.db.create_user(user_data)
        
        assert created_user.id is not None
        assert created_user.username == "testuser"
        assert created_user.email == "test@test.com"
        assert created_user.full_name == "Test User"
        assert created_user.hashed_password != "TestPassword123!"
    
    def test_get_user_by_username(self):
        """Test getting user by username."""
        user_data = UserCreate(
            username="getuser",
            email="get@test.com",
            full_name="Get User",
            password="TestPassword123!"
        )
        
        self.db.create_user(user_data)
        user = self.db.get_user_by_username("getuser")
        
        assert user is not None
        assert user.username == "getuser"
    
    def test_get_user_by_email(self):
        """Test getting user by email."""
        user_data = UserCreate(
            username="emailuser",
            email="email@test.com",
            full_name="Email User",
            password="TestPassword123!"
        )
        
        self.db.create_user(user_data)
        user = self.db.get_user_by_email("email@test.com")
        
        assert user is not None
        assert user.email == "email@test.com"
    
    def test_get_user_not_found(self):
        """Test getting non-existent user."""
        user = self.db.get_user_by_username("nonexistent")
        assert user is None
    
    def test_duplicate_username(self):
        """Test duplicate username handling."""
        user_data = UserCreate(
            username="duplicate",
            email="dup1@test.com",
            full_name="Duplicate User 1",
            password="TestPassword123!"
        )
        
        self.db.create_user(user_data)
        
        with pytest.raises(ValueError, match="Username already exists"):
            self.db.create_user(user_data)
    
    def test_list_users(self):
        """Test listing users."""
        # Create multiple users
        for i in range(3):
            user_data = UserCreate(
                username=f"user{i}",
                email=f"user{i}@test.com",
                full_name=f"User {i}",
                password="TestPassword123!"
            )
            self.db.create_user(user_data)
        
        users = self.db.list_users(limit=10)
        assert len(users) == 3


# =============================================================================
# Test User Manager
# =============================================================================

class TestUserManager:
    """Tests for user manager."""
    
    @pytest.fixture(autouse=True)
    def setup_db(self, clean_users_db):
        """Setup clean database for each test."""
        self.db_path = Path(__file__).parent / "test_users.db"
        self.db = UserDatabase(self.db_path)
    
    def test_authenticate_user_success(self):
        """Test successful user authentication."""
        user_data = UserCreate(
            username="authuser",
            email="auth@test.com",
            full_name="Auth User",
            password="TestPassword123!"
        )
        
        self.db.create_user(user_data)
        
        user = UserManager.authenticate_user("authuser", "TestPassword123!")
        
        assert user is not None
        assert user.username == "authuser"
    
    def test_authenticate_user_failure(self):
        """Test failed user authentication."""
        user_data = UserCreate(
            username="authfailuser",
            email="authfail@test.com",
            full_name="Auth Fail User",
            password="TestPassword123!"
        )
        
        self.db.create_user(user_data)
        
        # Wrong password
        user = UserManager.authenticate_user("authfailuser", "wrong-password")
        assert user is None
        
        # Non-existent user
        user = UserManager.authenticate_user("nonexistent", "TestPassword123!")
        assert user is None


# =============================================================================
# Test AuthConfig
# =============================================================================

class TestAuthConfig:
    """Tests for authentication configuration."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = AuthConfig()
        
        assert config.ALGORITHM == "HS256"
        assert config.ACCESS_TOKEN_EXPIRE_MINUTES == 60
        assert config.REFRESH_TOKEN_EXPIRE_DAYS == 7
    
    def test_password_validation(self):
        """Test password validation."""
        config = AuthConfig()
        
        # Valid password
        valid, message = config.validate_password("ValidPassword123!")
        assert valid is True
        
        # Too short
        valid, message = config.validate_password("Short1!")
        assert valid is False
        assert "at least" in message.lower()
        
        # No uppercase
        valid, message = config.validate_password("validpassword123!")
        assert valid is False
        assert "uppercase" in message.lower()
        
        # No digit
        valid, message = config.validate_password("ValidPassword!")
        assert valid is False
        assert "digit" in message.lower()
        
        # No special character
        valid, message = config.validate_password("ValidPassword123")
        assert valid is False
        assert "special" in message.lower()
