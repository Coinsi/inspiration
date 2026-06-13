"""统一错误类型与处理器(对照 04-接口契约 第 6 章错误码表)。"""
from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, detail: dict | None = None):
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


class Unauthorized(AppError):
    code, http_status = "UNAUTHORIZED", 401


class Forbidden(AppError):
    code, http_status = "FORBIDDEN", 403


class NotFound(AppError):
    code, http_status = "NOT_FOUND", 404


class Conflict(AppError):
    code, http_status = "VERSION_CONFLICT", 409


class CapabilityUnsupported(AppError):
    code, http_status = "CAPABILITY_UNSUPPORTED", 422


class QuotaExceeded(AppError):
    code, http_status = "QUOTA_EXCEEDED", 402


class ProviderError(AppError):
    code, http_status = "PROVIDER_ERROR", 502


class Locked(AppError):
    code, http_status = "LOCKED", 423


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
    )
