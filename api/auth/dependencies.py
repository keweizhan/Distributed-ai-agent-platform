"""
FastAPI dependencies for authentication and workspace resolution.

Usage in a router endpoint:
    current_workspace: WorkspaceModel = Depends(get_current_workspace)

This gives you the authenticated user's workspace, which is then used to scope
all DB queries so no tenant can access another tenant's data.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.utils import decode_access_token
from api.db.models import UserModel, WorkspaceModel
from api.db.session import get_db

# Points at the token endpoint so Swagger UI shows the Authorize button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = decode_access_token(token)
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
