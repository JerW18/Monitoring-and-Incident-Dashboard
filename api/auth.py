import hmac
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

# auto_error=False: a missing header gets the same 401 as a wrong key.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request, provided: Annotated[str | None, Security(api_key_header)]
) -> None:
    expected = request.app.state.settings.api_key.get_secret_value()
    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    if not hmac.compare_digest((provided or "").encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")
