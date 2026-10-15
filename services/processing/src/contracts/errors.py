from enum import Enum
from typing import Any

class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONTEST_NOT_FOUND = "CONTEST_NOT_FOUND"
    TEAM_NOT_FOUND = "TEAM_NOT_FOUND"
    PROBLEM_NOT_FOUND = "PROBLEM_NOT_FOUND"
    SUBMISSION_NOT_FOUND = "SUBMISSION_NOT_FOUND"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    CONTEST_NOT_OPEN = "CONTEST_NOT_OPEN"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"

class ApiError(Exception):

    def __init__(
        self,
        http_status: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            }
        }


def validation_error(message: str, field: str | None = None) -> ApiError:
    details = {"field": field} if field else {}
    return ApiError(400, ErrorCode.VALIDATION_ERROR, message, details)


def contest_not_found() -> ApiError:
    return ApiError(404, ErrorCode.CONTEST_NOT_FOUND, "Contest not found")


def team_not_found() -> ApiError:
    return ApiError(404, ErrorCode.TEAM_NOT_FOUND, "Team not found in this contest")


def problem_not_found() -> ApiError:
    return ApiError(404, ErrorCode.PROBLEM_NOT_FOUND, "Problem not found in this contest")


def submission_not_found() -> ApiError:
    return ApiError(404, ErrorCode.SUBMISSION_NOT_FOUND, "Submission not found")


def idempotency_key_reused() -> ApiError:
    return ApiError(
        409,
        ErrorCode.IDEMPOTENCY_KEY_REUSED,
        "Idempotency-Key was already used with a different payload",
    )


def contest_not_open() -> ApiError:
    return ApiError(409, ErrorCode.CONTEST_NOT_OPEN, "Contest is not open for submissions")


def temporarily_unavailable() -> ApiError:
    return ApiError(503, ErrorCode.TEMPORARILY_UNAVAILABLE, "Try again later")