"""FastMCP instance, background pre-warm, and the server entry point."""
import os
import secrets
import threading

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .database import get_rows

mcp = FastMCP("ace-mcp")


def _prewarm() -> None:
    """Load the snapshot in the background so the first tool call is instant
    instead of paying the cold fetch. Failures are ignored — the first real
    tool call will just fetch normally (with retries)."""
    try:
        get_rows(force=True)
    except Exception:
        pass


def _run_http() -> None:
    """Serve over Streamable HTTP for remote deployment (e.g. Cloud Run).

    FastMCP's built-in `auth=` is OAuth-oriented (needs a full issuer/authorization
    server). For a simple shared-secret connector token we instead wrap the plain
    Starlette app with our own bearer-check middleware.
    """
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    token = os.getenv("MCP_AUTH_TOKEN", "").strip()

    # FastMCP auto-restricts the Host header to localhost when constructed with
    # the default host, which rejects every request behind Cloud Run's real
    # hostname. Cloud Run's own routing + MCP_AUTH_TOKEN are the real perimeter
    # here, so DNS-rebinding protection (meant for locally-bound dev servers) is
    # irrelevant for this deployment target.
    
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = mcp.streamable_http_app()

    if token:
        class BearerAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                # OAuth-discovery probes must NOT be answered with 401. Clients like
                # Claude's connector always poll these paths; a 401 makes them think
                # the server is OAuth-protected and try (and fail) to register a
                # client. Let them fall through to the app's natural 404, which
                # signals "no OAuth here" so the client uses the ?key= auth instead.
                path = request.url.path
                if path == "/register" or path.startswith("/.well-known/"):
                    return await call_next(request)

                # Two accepted forms:
                #   1. Authorization: Bearer <token>   (e.g. ChatGPT, CLI clients)
                #   2. ?key=<token> in the URL          (for clients like Claude's
                #      custom connector that can't set a static auth header)
                expected_header = f"Bearer {token}"
                supplied_header = request.headers.get("authorization", "")
                header_ok = secrets.compare_digest(supplied_header, expected_header)

                supplied_key = request.query_params.get("key", "")
                query_ok = bool(supplied_key) and secrets.compare_digest(supplied_key, token)

                if not (header_ok or query_ok):
                    print(
                        f"DEBUG auth reject: path={request.url.path} "
                        f"auth_header_present={bool(supplied_header)} "
                        f"query_key_present={bool(supplied_key)}"
                    )
                    return PlainTextResponse("Unauthorized", status_code=401)
                return await call_next(request)

        app.add_middleware(BearerAuthMiddleware)
    else:
        print("WARNING: MCP_AUTH_TOKEN is not set — the HTTP endpoint is unauthenticated.")

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


def main() -> None:
    # Importing tools registers the @mcp.tool() functions on `mcp`.
    from . import tools  # noqa: F401
    threading.Thread(target=_prewarm, daemon=True).start()

    if os.getenv("MCP_TRANSPORT", "stdio").strip().lower() == "http":
        _run_http()
    else:
        mcp.run()
