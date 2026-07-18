# =============================================================================
# Oracle - Rate Limiting Module
# SlowAPI-based rate limiting for FastAPI endpoints
# =============================================================================

"""
Rate limiting module for Oracle API.
Uses slowapi to provide flexible rate limiting with Redis or in-memory storage.

Usage:
    from api.rate_limit import rate_limiter, RateLimitExceededException
    from api.security import get_rate_limit_config
    
    # Apply to FastAPI app
    app.state.limiter = rate_limiter
    
    # Protect endpoints
    @app.get("/api/endpoint")
    @rate_limiter.limit("100/minute")
    async def my_endpoint(request: Request):
        ...
"""

from __future__ import annotations

import os
from typing import Optional, List, Callable, Union
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded as SlowAPIRateLimitExceeded

from .security import get_settings, get_rate_limit_config


# =============================================================================
# Rate Limit Configuration
# =============================================================================

class RateLimitConfig:
    """Rate limiting configuration."""
    
    # Storage backend: "memory" or "redis"
    STORAGE_BACKEND: str = os.getenv("RATE_LIMIT_STORAGE", "memory")
    
    # Redis configuration (if using Redis)
    REDIS_HOST: str = os.getenv("RATE_LIMIT_REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("RATE_LIMIT_REDIS_PORT", "6379"))
    REDIS_PASSWORD: Optional[str] = os.getenv("RATE_LIMIT_REDIS_PASSWORD", None)
    REDIS_DB: int = int(os.getenv("RATE_LIMIT_REDIS_DB", "0"))
    
    # In-memory storage (if using memory)
    MEMORY_STORAGE_SIZE: int = 10000  # Max number of rate limit entries
    
    # Key functions
    KEY_FUNC: Callable = get_remote_address  # Default: limit by IP
    
    # Whitelist
    WHITELIST: List[str] = []  # IPs not subject to rate limiting
    
    @classmethod
    def is_whitelisted(cls, ip: str) -> bool:
        """Check if IP is whitelisted."""
        return ip in cls.WHITELIST


# =============================================================================
# Rate Limit Exceptions
# =============================================================================

class RateLimitExceededException(HTTPException):
    """Custom rate limit exceeded exception."""
    
    def __init__(self, detail: str = "Rate limit exceeded"):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": "60"}
        )


# =============================================================================
# Rate Limiter Setup
# =============================================================================

def create_limiter() -> Limiter:
    """Create and configure the rate limiter."""
    config = get_rate_limit_config()
    
    # Determine storage backend
    storage_backend = RateLimitConfig.STORAGE_BACKEND
    
    if storage_backend == "redis":
        storage_uri = f"redis://{RateLimitConfig.REDIS_HOST}:{RateLimitConfig.REDIS_PORT}/{RateLimitConfig.REDIS_DB}"
    else:
        storage_uri = "memory://"
    
    # Create limiter with new slowapi API (storage_uri instead of storage object)
    limiter = Limiter(
        key_func=RateLimitConfig.KEY_FUNC,
        storage_uri=storage_uri,
    )
    
    # Configure whitelist
    limiter.whitelist = RateLimitConfig.WHITELIST
    
    return limiter


# Global limiter instance
_limiter: Optional[Limiter] = None


def get_limiter() -> Limiter:
    """Get or create the global rate limiter."""
    global _limiter
    if _limiter is None:
        _limiter = create_limiter()
    return _limiter


def reset_limiter() -> None:
    """Reset the global limiter (useful for testing)."""
    global _limiter
    _limiter = None


# Convenience reference
rate_limiter = get_limiter()


# =============================================================================
# Rate Limit Middleware
# =============================================================================

def apply_rate_limit_middleware(app: FastAPI) -> FastAPI:
    """Apply rate limiting middleware to the FastAPI application."""
    settings = get_settings()
    config = get_rate_limit_config()
    
    if not config["enabled"]:
        return app
    
    app.state.limiter = rate_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    
    return app


async def rate_limit_exceeded_handler(
    request: Request,
    exc: SlowAPIRateLimitExceeded
) -> JSONResponse:
    """Handler for rate limit exceeded exceptions."""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail) if exc.detail else "Too many requests",
            "retry_after": exc.retry_after,
        },
        headers={
            "Retry-After": str(exc.retry_after),
            "X-RateLimit-Limit": str(exc.limit),
            "X-RateLimit-Remaining": "0",
        }
    )


# =============================================================================
# Rate Limit Decorators
# =============================================================================

def apply_default_rate_limits(app: FastAPI) -> FastAPI:
    """
    Apply default rate limits to all API endpoints.
    This should be called after all routes are registered.
    """
    settings = get_settings()
    config = get_rate_limit_config()
    
    if not config["enabled"]:
        return app
    
    # Apply default rate limit to all routes
    default_limit = config["default"]
    
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            # Skip auth endpoints (they have their own limit)
            if "/api/auth/" in route.path or "/health" in route.path:
                continue
            
            # Apply rate limit
            if not hasattr(route, "rate_limited"):
                # Mark as rate limited to avoid double application
                route.rate_limited = True
                
                # Store original endpoint
                original_endpoint = route.endpoint
                
                # Create wrapped endpoint with rate limiting
                async def rate_limited_endpoint(*args, **kwargs):
                    request = None
                    for arg in args:
                        if isinstance(arg, Request):
                            request = arg
                            break
                    
                    if request is None:
                        # Try to get request from kwargs
                        request = kwargs.get("request")
                    
                    if request:
                        # Apply rate limit
                        @rate_limiter.limit(default_limit)
                        async def _limited(request: Request, *a, **kw):
                            return await original_endpoint(*a, **kw)
                        
                        return await _limited(request, *args, **kwargs)
                    else:
                        return await original_endpoint(*args, **kwargs)
                
                # Replace endpoint
                route.endpoint = rate_limited_endpoint
    
    return app


# =============================================================================
# Auth Endpoint Rate Limiting
# =============================================================================

def get_auth_rate_limiter():
    """Get rate limiter specifically for auth endpoints."""
    config = get_rate_limit_config()
    auth_limit = config["auth_endpoints"]
    
    def auth_limiter(func):
        return rate_limiter.limit(auth_limit)(func)
    
    return auth_limiter


# =============================================================================
# Helper Functions
# =============================================================================

def get_client_ip(request: Request) -> str:
    """Get the client IP address from the request."""
    # Check for forwarded headers (if behind proxy)
    settings = get_settings()
    
    if settings.PROXY_ENABLED:
        # Try X-Forwarded-For
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            # Take the first IP in the chain
            return x_forwarded_for.split(",")[0].strip()
        
        # Try X-Real-IP
        x_real_ip = request.headers.get("X-Real-IP")
        if x_real_ip:
            return x_real_ip
    
    # Fall back to client host
    client = request.client
    if client:
        return client.host
    
    return "unknown"


def get_rate_limit_headers(request: Request) -> dict:
    """Get rate limit headers for the current request."""
    # This would need to be implemented based on the storage backend
    # For now, return empty dict
    return {}


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "RateLimitConfig",
    "RateLimitExceededException",
    "rate_limiter",
    "get_limiter",
    "reset_limiter",
    "apply_rate_limit_middleware",
    "apply_default_rate_limits",
    "rate_limit_exceeded_handler",
    "get_client_ip",
    "get_rate_limit_headers",
    "get_auth_rate_limiter",
]
