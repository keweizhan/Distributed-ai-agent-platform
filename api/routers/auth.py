"""
Authentication router — register, token, me.

POST /auth/register  — create user + default workspace, return user info
POST /auth/token     — OAuth2 password flow, return JWT bearer token
GET  /auth/me        — return the currently authenticated user
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_user
from api.auth.utils import create_access_token, hash_password, verify_password
from api.db.models import UserModel, WorkspaceModel
from api.db.session import get_db
from api.schemas.auth import TokenResponse, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    """
    Register a new user and create their default workspace.

    The workspace name defaults to "<email>'s workspace" when not provided.
    Every subsequent job submitted by this user will belong to that workspace.
    """
    existing = await db.execute(select(UserModel).where(UserModel.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = UserModel(
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()  # populate user.id before we reference it

    workspace_name = body.workspace_name or f"{body.email}'s workspace"
    workspace = WorkspaceModel(name=workspace_name, owner_id=user.id)
    db.add(workspace)

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    OAuth2 password flow.  Use the user's email as the *username* field.
    Returns a signed JWT bearer token valid for JWT_EXPIRE_MINUTES minutes.
    """
    result = await db.execute(select(UserModel).where(UserModel.email == form.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserResponse)
async def me(current_user: UserModel = Depends(get_current_user)) -> UserModel:
    """Return the currently authenticated user's profile."""
    return current_user
