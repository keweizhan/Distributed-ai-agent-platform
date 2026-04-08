"""
FastAPI dependencies for authentication and workspace resolution.

Usage in a router endpoint:
    current_workspace: WorkspaceModel = Depends(get_current_workspace)

This gives you the authenticated user's workspace, which is then used to scope
all DB queries so no tenant can access another tenant's data.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.utils import decode_access_token
from api.db.models import UserModel, WorkspaceModel
from api.db.session import get_db

# HTTPBearer shows a plain "Value" token input in Swagger UI instead of the
# OAuth2 password-flow popup.  The OAuth2 popup is unreliable when client_id
# is blank (Swagger UI sends a malformed request and mishandles the response).
# HTTPBearer extraction is identical at the HTTP level: Authorization: Bearer <token>.
_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = decode_access_token(credentials.credentials)
    except JWTError:
        raise credentials_exc

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise credentials_exc

    user = await db.get(UserModel, uid)
    if user is None:
        raise credentials_exc
    return user


async def get_current_workspace(
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceModel:
    """
    Resolve the caller's workspace from their JWT.

    Current design: one user → one workspace (created on registration).
    Future RBAC can replace this with a workspace_members lookup + role check.
    """
    result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.owner_id == current_user.id)
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found for this user")
    return workspace
