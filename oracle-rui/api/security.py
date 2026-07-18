# =============================================================================
# Oracle - Security Configuration Module
# CORS, rate limiting, and security settings
# =============================================================================

"""
Security configuration module for Oracle API.
Provides CORS middleware, rate limiting, and security settings.

Usage:
    from api.security import get_settings, apply_security_middleware
    
    # In FastAPI app initialization
    app = FastAPI()
    apply_security_middleware(app)
"""

from __future__ import annotations

import os
from typing import List, Optional
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings

# Helper: parse comma-separated string into list
def _parse_list(value: str) -> List[str]:
    if not value or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# =============================================================================
# Settings Model
# =============================================================================

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Priority (highest to lowest):
    1. Environment variables
    2. .env file
    3. Default values
    
    Note: List fields (CORS_ORIGINS, TRUSTED_HOSTS, etc.) are stored as
    comma-separated strings in .env and exposed as list properties.
    """
    
    # Application
    APP_NAME: str = "Oracle"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = "AI Coding Agent Autonomo"
    DEBUG: bool = False
    
    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # CORS Configuration (comma-separated strings from .env)
    CORS_ORIGINS_STR: str = Field(
        default="http://localhost,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000",
        alias="CORS_ORIGINS"
    )
    CORS_ORIGIN_REGEX: Optional[str] = None
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS_STR: str = Field(default="*", alias="CORS_ALLOW_METHODS")
    CORS_ALLOW_HEADERS_STR: str = Field(default="*", alias="CORS_ALLOW_HEADERS")
    CORS_ALLOW_WILDCARD: bool = False
    
    # Security Headers
    SECURITY_ENABLE_HSTS: bool = False
    SECURITY_HSTS_MAX_AGE: int = 31536000  # 1 year
    SECURITY_ENABLE_CSP: bool = False
    SECURITY_CSP_POLICY: str = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
    SECURITY_ENABLE_X_FRAME_OPTIONS: bool = True
    SECURITY_X_FRAME_OPTIONS: str = "DENY"
    SECURITY_ENABLE_X_CONTENT_TYPE_OPTIONS: bool = True
    SECURITY_X_CONTENT_TYPE_OPTIONS: str = "nosniff"
    SECURITY_ENABLE_X_XSS_PROTECTION: bool = True
    SECURITY_X_XSS_PROTECTION: str = "1; mode=block"
    SECURITY_ENABLE_REFERRER_POLICY: bool = True
    SECURITY_REFERRER_POLICY: str = "strict-origin-when-cross-origin"
    SECURITY_ENABLE_PERMISSIONS_POLICY: bool = True
    SECURITY_PERMISSIONS_POLICY: str = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:"
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_AUTHENTICATED: str = "1000/minute"
    RATE_LIMIT_AUTH_ENDPOINTS: str = "10/hour"
    RATE_LIMIT_WHITELIST_STR: str = Field(default="", alias="RATE_LIMIT_WHITELIST")
    
    # Trusted Hosts
    TRUSTED_HOSTS_STR: str = Field(default="localhost,127.0.0.1,0.0.0.0", alias="TRUSTED_HOSTS")
    ALLOW_ALL_HOSTS: bool = False
    
    # Proxy
    PROXY_ENABLED: bool = False
    PROXY_X_FORWARDED_FOR: bool = True
    PROXY_X_FORWARDED_PROTO: bool = True
    PROXY_X_FORWARDED_HOST: bool = True
    PROXY_X_FORWARDED_PORT: bool = True
    
    # API Keys
    API_KEY_HEADER_NAME: str = "X-API-Key"
    API_KEY_QUERY_NAME: str = "api_key"
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_ENABLE_CONSOLE: bool = True
    LOG_ENABLE_FILE: bool = False
    LOG_FILE_PATH: Path = Path("logs/oracle.log")
    
    class Config:
        case_sensitive = True
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True
    
    # ── Computed list properties (parse comma-separated strings) ──
    
    @property
    def CORS_ORIGINS(self) -> List[AnyHttpUrl]:
        return [AnyHttpUrl(o) for o in _parse_list(self.CORS_ORIGINS_STR)]
    
    @property
    def CORS_ALLOW_METHODS(self) -> List[str]:
        return _parse_list(self.CORS_ALLOW_METHODS_STR)
    
    @property
    def CORS_ALLOW_HEADERS(self) -> List[str]:
        return _parse_list(self.CORS_ALLOW_HEADERS_STR)
    
    @property
    def TRUSTED_HOSTS(self) -> List[str]:
        return _parse_list(self.TRUSTED_HOSTS_STR)
    
    @property
    def RATE_LIMIT_WHITELIST(self) -> List[str]:
        return _parse_list(self.RATE_LIMIT_WHITELIST_STR)


# Initialize settings
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create the settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset the settings instance (useful for testing)."""
    global _settings
    _settings = None


# =============================================================================
# CORS Middleware
# =============================================================================

def get_cors_origins() -> List[str]:
    """Get CORS origins from settings."""
    settings = get_settings()
    origins = [str(origin) for origin in settings.CORS_ORIGINS]
    
    # Add wildcard if enabled (not recommended for production)
    if settings.CORS_ALLOW_WILDCARD:
        origins.append("*")
    
    return origins


def apply_cors_middleware(app: FastAPI) -> FastAPI:
    """Apply CORS middleware to the FastAPI application."""
    settings = get_settings()
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_origin_regex=settings.CORS_ORIGIN_REGEX,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
    )
    
    return app


# =============================================================================
# Trusted Host Middleware
# =============================================================================

def apply_trusted_host_middleware(app: FastAPI) -> FastAPI:
    """Apply trusted host middleware to prevent HTTP Host header attacks."""
    settings = get_settings()
    
    if settings.ALLOW_ALL_HOSTS:
        return app
    
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.TRUSTED_HOSTS,
    )
    
    return app


# =============================================================================
# Security Headers Middleware
# =============================================================================

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""
    
    async def dispatch(self, request, call_next) -> Response:
        settings = get_settings()
        response = await call_next(request)
        
        # Add security headers
        if settings.SECURITY_ENABLE_HSTS:
            response.headers["Strict-Transport-Security"] = \
                f"max-age={settings.SECURITY_HSTS_MAX_AGE}; includeSubDomains; preload"
        
        if settings.SECURITY_ENABLE_X_FRAME_OPTIONS:
            response.headers["X-Frame-Options"] = settings.SECURITY_X_FRAME_OPTIONS
        
        if settings.SECURITY_ENABLE_X_CONTENT_TYPE_OPTIONS:
            response.headers["X-Content-Type-Options"] = settings.SECURITY_X_CONTENT_TYPE_OPTIONS
        
        if settings.SECURITY_ENABLE_X_XSS_PROTECTION:
            response.headers["X-XSS-Protection"] = settings.SECURITY_X_XSS_PROTECTION
        
        if settings.SECURITY_ENABLE_REFERRER_POLICY:
            response.headers["Referrer-Policy"] = settings.SECURITY_REFERRER_POLICY
        
        if settings.SECURITY_ENABLE_PERMISSIONS_POLICY:
            response.headers["Permissions-Policy"] = settings.SECURITY_PERMISSIONS_POLICY
        
        if settings.SECURITY_ENABLE_CSP:
            response.headers["Content-Security-Policy"] = settings.SECURITY_CSP_POLICY
        
        return response


def apply_security_headers_middleware(app: FastAPI) -> FastAPI:
    """Apply security headers middleware."""
    app.add_middleware(SecurityHeadersMiddleware)
    return app


# =============================================================================
# Apply All Security Middleware
# =============================================================================

def apply_security_middleware(app: FastAPI) -> FastAPI:
    """Apply all security middleware to the FastAPI application."""
    app = apply_cors_middleware(app)
    app = apply_trusted_host_middleware(app)
    app = apply_security_headers_middleware(app)
    return app


# =============================================================================
# Rate Limiting Configuration
# =============================================================================

def get_rate_limit_config() -> dict:
    """Get rate limiting configuration."""
    settings = get_settings()
    return {
        "enabled": settings.RATE_LIMIT_ENABLED,
        "default": settings.RATE_LIMIT_DEFAULT,
        "authenticated": settings.RATE_LIMIT_AUTHENTICATED,
        "auth_endpoints": settings.RATE_LIMIT_AUTH_ENDPOINTS,
        "whitelist": settings.RATE_LIMIT_WHITELIST,
    }


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "Settings",
    "get_settings",
    "reset_settings",
    "apply_cors_middleware",
    "apply_trusted_host_middleware",
    "apply_security_headers_middleware",
    "apply_security_middleware",
    "get_cors_origins",
    "get_rate_limit_config",
    "SecurityHeadersMiddleware",
]
