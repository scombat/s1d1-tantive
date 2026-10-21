"""The common error shape, `{"error": {"message", "type"}}`, and the HTTP codes of spec § 5."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    404: "invalid_request_error",
    405: "invalid_request_error",
    413: "request_too_large",
    422: "unknown_question",
    503: "overloaded",
}


class ApiError(HTTPException):
    """An HTTP error in the common shape."""

    def __init__(self, status_code: int, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code, message, headers)


def error_response(status_code: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"error": {"message": message, "type": ERROR_TYPES.get(status_code, "api_error")}}
    return JSONResponse(body, status_code=status_code, headers=headers)
