from typing import Optional

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)


class DeviceIn(BaseModel):
    device_id: str = Field(..., min_length=4, max_length=200)
    name: Optional[str] = None
    app_version: Optional[str] = None


class LicensePatch(BaseModel):
    plan: Optional[str] = Field(None, pattern="^(ACTIVE|PRO)$")
    device_limit: Optional[int] = Field(None, ge=1, le=20)
    expiry: Optional[str] = None  # ISO date, "" 또는 null = 무기한


class ErrorReportIn(BaseModel):
    app_version: Optional[str] = None
    code: Optional[str] = None
    message: Optional[str] = Field(None, max_length=4000)
