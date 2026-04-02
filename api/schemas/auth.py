"""Pydantic schemas for authentication endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email:          EmailStr
    password:       str = Field(..., min_length=8, description="Minimum 8 characters")
    workspace_name: str | None = Field(None, max_length=255)


class UserResponse(BaseModel):
    id:         uuid.UUID
    email:      str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
