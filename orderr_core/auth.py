import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


# -----------------------------------------------------------------------------
# AUTH TEMPORARILY DISABLED
# The dashboard username/password prompt has been turned off for now.
# The original HTTP Basic Auth implementation is preserved below (commented out)
# so it can be re-enabled later by restoring the code and removing the no-op
# version at the bottom of this file.
# -----------------------------------------------------------------------------

# def require_auth(
#     credentials: HTTPBasicCredentials = Depends(security)
# ):
#     """
#     HTTP Basic Auth dependency.
#     Protects internal endpoints from public access.
#     """
#
#     username = os.getenv("DASHBOARD_USERNAME", "")
#     password = os.getenv("DASHBOARD_PASSWORD", "")
#
#     if not username or not password:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Auth credentials not configured"
#         )
#
#     # Use compare_digest to prevent timing attacks
#     username_ok = secrets.compare_digest(
#         credentials.username.encode("utf-8"),
#         username.encode("utf-8")
#     )
#
#     password_ok = secrets.compare_digest(
#         credentials.password.encode("utf-8"),
#         password.encode("utf-8")
#     )
#
#     if not (username_ok and password_ok):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid credentials",
#             headers={"WWW-Authenticate": "Basic"},
#         )
#
#     return credentials.username


def require_auth():
    """
    No-op auth dependency (login prompt disabled for now).

    Does not depend on HTTPBasic, so browsers will not show the
    username/password prompt. Restore the commented implementation
    above to re-enable authentication.
    """
    return "anonymous"
