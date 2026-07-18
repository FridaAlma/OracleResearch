# =============================================================================
# Oracle - Authentication Module
# JWT-based authentication for FastAPI
# =============================================================================

"""
Authentication module for Oracle API.
Provides JWT-based authentication with user management.

Usage:
    from api.auth import get_current_active_user, router as auth_router
    
    # In FastAPI app
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    
    # Protect endpoints
    @app.get("/api/protected")
    async def protected_route(user: User = Depends(get_current_active_user)):
        ...
"""

from __future__ import annotations

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional, List
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    Request,
    Response,
    Header,
)
from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
)
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

class AuthConfig:
    """Authentication configuration."""
    
    # JWT Settings
    SECRET_KEY: str = os.getenv(
        "AUTH_SECRET_KEY",
        secrets.token_urlsafe(32)
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(
        os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
    )
    
    # User Database
    USERS_DB_PATH: Path = Path(
        os.getenv("USERS_DB_PATH", "data/users.db")
    )
    
    # Default admin user (created on first run)
    DEFAULT_ADMIN_USERNAME: str = os.getenv(
        "DEFAULT_ADMIN_USERNAME", "admin"
    )
    DEFAULT_ADMIN_EMAIL: str = os.getenv(
        "DEFAULT_ADMIN_EMAIL", "admin@oracle.local"
    )
    DEFAULT_ADMIN_PASSWORD: str = os.getenv(
        "DEFAULT_ADMIN_PASSWORD", None
    )
    
    # Security
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_DIGIT: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = True
    
    @classmethod
    def validate_password(cls, password: str) -> tuple[bool, str]:
        """Validate password strength."""
        if len(password) < cls.PASSWORD_MIN_LENGTH:
            return False, f"Password must be at least {cls.PASSWORD_MIN_LENGTH} characters"
        if cls.PASSWORD_REQUIRE_UPPERCASE and not any(c.isupper() for c in password):
            return False, "Password must contain at least one uppercase letter"
        if cls.PASSWORD_REQUIRE_DIGIT and not any(c.isdigit() for c in password):
            return False, "Password must contain at least one digit"
        if cls.PASSWORD_REQUIRE_SPECIAL and not any(
            c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password
        ):
            return False, "Password must contain at least one special character"
        return True, "Password is valid"


config = AuthConfig()


# =============================================================================
# Password Hashing
# =============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """Hash a password for storing."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against the stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


# =============================================================================
# JWT Token Management
# =============================================================================

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="api/auth/token",
    scopes={}
)

def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    })
    encoded_jwt = jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)
    return encoded_jwt


def create_refresh_token(
    data: dict,
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT refresh token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=config.REFRESH_TOKEN_EXPIRE_DAYS
        )
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    })
    encoded_jwt = jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(
            token,
            config.SECRET_KEY,
            algorithms=[config.ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.JWTClaimsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# =============================================================================
# User Models
# =============================================================================

class Token(BaseModel):
    """JWT Token response model."""
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None
    expires_in: int
    user: dict


class TokenData(BaseModel):
    """Token data model."""
    username: Optional[str] = None
    email: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)


class UserBase(BaseModel):
    """Base user model."""
    username: str
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    disabled: bool = False


class UserCreate(UserBase):
    """User creation model."""
    password: str


class UserInDBBase(UserBase):
    """User in database base model."""
    id: int
    is_admin: bool = False
    created_at: datetime
    last_login: Optional[datetime] = None


class UserInDB(UserInDBBase):
    """User in database with hashed password."""
    hashed_password: str


class User(UserInDBBase):
    """User model for API responses."""
    @classmethod
    def from_orm(cls, db_user: UserInDB) -> "User":
        """Create User from ORM UserInDB."""
        return cls(
            id=db_user.id,
            username=db_user.username,
            email=db_user.email,
            full_name=db_user.full_name,
            disabled=db_user.disabled,
            is_admin=db_user.is_admin,
            created_at=db_user.created_at,
            last_login=db_user.last_login,
        )


# =============================================================================
# User Database (SQLite)
# =============================================================================

import sqlite3
from contextlib import contextmanager


class UserDatabase:
    """SQLite user database."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize the database tables."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    full_name TEXT,
                    hashed_password TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    disabled INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_username 
                ON users(username)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email 
                ON users(email)
            """)
            
            # Create default admin if not exists and password is set
            if config.DEFAULT_ADMIN_PASSWORD:
                self._create_default_admin(conn)
    
    def _create_default_admin(self, conn):
        """Create default admin user."""
        cursor = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (config.DEFAULT_ADMIN_USERNAME,)
        )
        if cursor.fetchone() is None:
            hashed_password = get_password_hash(config.DEFAULT_ADMIN_PASSWORD)
            conn.execute(
                """
                INSERT INTO users 
                (username, email, full_name, hashed_password, is_admin, disabled) 
                VALUES (?, ?, ?, ?, 1, 0)
                """,
                (
                    config.DEFAULT_ADMIN_USERNAME,
                    config.DEFAULT_ADMIN_EMAIL,
                    "Admin User",
                    hashed_password,
                )
            )
            logger.info(f"Created default admin user: {config.DEFAULT_ADMIN_USERNAME}")
    
    def get_user_by_username(self, username: str) -> Optional[UserInDB]:
        """Get user by username."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()
            if row:
                return UserInDB(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    full_name=row["full_name"],
                    disabled=bool(row["disabled"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
                    hashed_password=row["hashed_password"],
                )
            return None
    
    def get_user_by_email(self, email: str) -> Optional[UserInDB]:
        """Get user by email."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            )
            row = cursor.fetchone()
            if row:
                return UserInDB(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    full_name=row["full_name"],
                    disabled=bool(row["disabled"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
                    hashed_password=row["hashed_password"],
                )
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[UserInDB]:
        """Get user by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return UserInDB(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    full_name=row["full_name"],
                    disabled=bool(row["disabled"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
                    hashed_password=row["hashed_password"],
                )
            return None
    
    def create_user(self, user_data: UserCreate) -> UserInDB:
        """Create a new user."""
        # Validate password
        is_valid, message = config.validate_password(user_data.password)
        if not is_valid:
            raise ValueError(message)
        
        hashed_password = get_password_hash(user_data.password)
        
        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO users 
                    (username, email, full_name, hashed_password, is_admin, disabled) 
                    VALUES (?, ?, ?, ?, 0, 0)
                    """,
                    (
                        user_data.username,
                        user_data.email,
                        user_data.full_name,
                        hashed_password,
                    )
                )
                user_id = cursor.lastrowid
                
                # Fetch the created user
                created_user = self.get_user_by_id(user_id)
                if created_user:
                    return created_user
                raise RuntimeError("Failed to create user")
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed: users.username" in str(e):
                    raise ValueError("Username already exists")
                elif "UNIQUE constraint failed: users.email" in str(e):
                    raise ValueError("Email already exists")
                raise
    
    def update_user_last_login(self, user_id: int):
        """Update user's last login timestamp."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,)
            )
    
    def list_users(self, skip: int = 0, limit: int = 100) -> List[UserInDB]:
        """List all users."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, skip)
            )
            users = []
            for row in cursor:
                users.append(UserInDB(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    full_name=row["full_name"],
                    disabled=bool(row["disabled"]),
                    is_admin=bool(row["is_admin"]),
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
                    hashed_password=row["hashed_password"],
                ))
            return users
    
    def delete_user(self, user_id: int) -> bool:
        """Delete a user."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM users WHERE id = ?",
                (user_id,)
            )
            return cursor.rowcount > 0


# Initialize user database
user_db = UserDatabase(config.USERS_DB_PATH)


# =============================================================================
# User Management
# =============================================================================

class UserManager:
    """User management service."""
    
    @staticmethod
    def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
        """Authenticate a user."""
        user = user_db.get_user_by_username(username)
        if user is None:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if user.disabled:
            return None
        return user
    
    @staticmethod
    def get_user(username: str) -> Optional[UserInDB]:
        """Get user by username."""
        return user_db.get_user_by_username(username)
    
    @staticmethod
    def create_user(user_data: UserCreate) -> UserInDB:
        """Create a new user."""
        return user_db.create_user(user_data)
    
    @staticmethod
    def update_last_login(user: UserInDB):
        """Update user's last login timestamp."""
        user_db.update_user_last_login(user.id)


# =============================================================================
# Dependency Functions
# =============================================================================

async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)]
) -> UserInDB:
    """Get the current authenticated user."""
    payload = verify_token(token)
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = user_db.get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_active_user(
    current_user: Annotated[UserInDB, Depends(get_current_user)]
) -> UserInDB:
    """Get the current active user (not disabled)."""
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


async def get_current_admin_user(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)]
) -> UserInDB:
    """Get the current admin user."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# =============================================================================
# API Routes
# =============================================================================

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    """
    OAuth2 password flow token endpoint.
    
    Returns JWT access token and refresh token.
    """
    user = UserManager.authenticate_user(
        form_data.username,
        form_data.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Update last login
    UserManager.update_last_login(user)
    
    access_token_expires = timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    refresh_token_expires = timedelta(
        days=config.REFRESH_TOKEN_EXPIRE_DAYS
    )
    
    access_token = create_access_token(
        data={"sub": user.username, "email": user.email, "is_admin": user.is_admin},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": user.username},
        expires_delta=refresh_token_expires
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        refresh_token=refresh_token,
        expires_in=int(access_token_expires.total_seconds()),
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_admin": user.is_admin,
        }
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(
    refresh_token: str
) -> Token:
    """
    Refresh access token using refresh token.
    """
    try:
        payload = verify_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
        username = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        
        user = user_db.get_user_by_username(username)
        if user is None or user.disabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or disabled"
            )
        
        access_token_expires = timedelta(
            minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        access_token = create_access_token(
            data={"sub": user.username, "email": user.email, "is_admin": user.is_admin},
            expires_delta=access_token_expires
        )
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            refresh_token=None,
            expires_in=int(access_token_expires.total_seconds()),
            user={
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
                "is_admin": user.is_admin,
            }
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )


@router.post("/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_data: UserCreate
) -> User:
    """
    Register a new user.
    Only admin can register users (for now, to prevent abuse).
    """
    # For security, only allow registration if admin user exists
    # or if this is the first user (admin)
    admin_exists = user_db.get_user_by_username(config.DEFAULT_ADMIN_USERNAME)
    
    if admin_exists is None and user_data.username != config.DEFAULT_ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin user must be created first"
        )
    
    try:
        created_user = user_db.create_user(user_data)
        return User.from_orm(created_user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/me", response_model=User)
async def read_users_me(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)]
) -> User:
    """
    Get current user information.
    """
    return User.from_orm(current_user)


@router.get("/users", response_model=List[User])
async def read_users(
    current_user: Annotated[UserInDB, Depends(get_current_admin_user)],
    skip: int = 0,
    limit: int = 100,
) -> List[User]:
    """
    List all users. Admin only.
    """
    users = user_db.list_users(skip=skip, limit=limit)
    return [User.from_orm(u) for u in users]


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "AuthConfig",
    "config",
    "User",
    "UserBase",
    "UserCreate",
    "UserInDB",
    "UserInDBBase",
    "UserDatabase",
    "UserManager",
    "Token",
    "TokenData",
    "create_access_token",
    "create_refresh_token",
    "verify_token",
    "get_password_hash",
    "verify_password",
    "get_current_user",
    "get_current_active_user",
    "get_current_admin_user",
    "oauth2_scheme",
    "router",
    "user_db",
]
