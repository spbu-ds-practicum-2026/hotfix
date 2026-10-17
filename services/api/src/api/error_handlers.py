"""Преобразование ошибок FastAPI в единый формат {"error": {...}}."""

from contracts.errors import ApiError, validation_error
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(status_code=error.http_status, content=error.to_body())

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"][1:]) or None
        api_error = validation_error(first["msg"], field)
        return JSONResponse(
            status_code=api_error.http_status, content=api_error.to_body()
        )
