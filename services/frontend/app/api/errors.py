from app.models.contracts import ApiErrorCode


class ApiError(Exception):
    
    def __init__(
        self,
        http_status: int,
        code: ApiErrorCode | str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = ApiErrorCode(code) if isinstance(code, str) else code
        self.message = message
