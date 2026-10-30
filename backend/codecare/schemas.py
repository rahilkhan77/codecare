from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SupabaseAuthRequest(BaseModel):
    access_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=10, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str = Field(min_length=10, max_length=128)


class DeleteAccountRequest(BaseModel):
    password: str = ""
    confirmation: str


class SwitchOrganizationRequest(BaseModel):
    organization_id: str = Field(min_length=1, max_length=36)


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"


class UpdateMemberRequest(BaseModel):
    role: Literal["owner", "admin", "member"]


class ContactRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    topic: str = Field(min_length=2, max_length=64)
    message: str = Field(min_length=10, max_length=8000)


class SettingsUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)


class ConnectReposRequest(BaseModel):
    github_repo_ids: list[str] = Field(min_length=1, max_length=50)


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = "monthly"


class RepoScheduleUpdate(BaseModel):
    scan_interval_hours: int = Field(ge=1, le=168)


class ChangePlanRequest(BaseModel):
    plan: str
