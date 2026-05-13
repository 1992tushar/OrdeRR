import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def require_auth(
    credentials: HTTPBasicCredentials = Depends(security)
):
    """
    HTTP Basic Auth dependency.
    Protects internal endpoints from public access.
    """

    username = os.getenv("DASHBOARD_USERNAME", "")
    password = os.getenv("DASHBOARD_PASSWORD", "")

    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth credentials not configured"
        )

    # Use compare_digest to prevent timing attacks
    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        username.encode("utf-8")
    )

    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        password.encode("utf-8")
    )

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username