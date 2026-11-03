"""Domain exceptions and their HTTP status codes, all in one place."""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.incidents import IncidentNotFound, InvalidTransition


class RuleNotFound(Exception):
    def __init__(self, rule_id: int) -> None:
        super().__init__(f"rule {rule_id} not found")


class RuleConflict(Exception):
    """The change is valid on its own but conflicts with existing data."""


STATUS_CODES: dict[type[Exception], int] = {
    IncidentNotFound: status.HTTP_404_NOT_FOUND,
    RuleNotFound: status.HTTP_404_NOT_FOUND,
    InvalidTransition: status.HTTP_409_CONFLICT,
    RuleConflict: status.HTTP_409_CONFLICT,
}


def register_exception_handlers(app: FastAPI) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=STATUS_CODES[type(exc)])

    for exception_type in STATUS_CODES:
        app.add_exception_handler(exception_type, handle)

    async def handle_validation(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default handler echoes the rejected input, and rejected values like
        # 1e999 can't be written as JSON, which would turn this 422 into a 500.
        assert isinstance(exc, RequestValidationError)
        detail = [{key: e[key] for key in ("type", "loc", "msg")} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    app.add_exception_handler(RequestValidationError, handle_validation)
