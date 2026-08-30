"""Security response headers.

The API serves the built SPA same-origin in production, so these headers land on
the document that holds the session token. The token lives in localStorage, which
is a reasonable choice for a bearer-token SPA and an unforgiving one: any script
that runs on the page can read it. Nothing in the UI renders user HTML today, so
there is no injection point to pair that with. This is the layer that keeps it
that way if one is ever added.

The policy the app actually depends on is `script-src 'self'`: every script is a
built asset from our own origin, and the Vite build emits no inline script, so
nothing legitimate needs a nonce or a hash. `style-src` allows inline because the
components set `style={{...}}` attributes throughout; the CSP3 way to keep those
without opening `<style>` blocks is `style-src-attr`, which Firefox does not
support, and a policy that renders the app unstyled in one browser is a policy
someone will delete. Inline CSS is a far weaker vector than inline script, and it
is the whole of what is conceded here.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        # No plugins, no <base> rewriting, no framing, no cross-origin form posts.
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)

HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    # frame-ancestors above already covers this for anything current; kept for
    # browsers that read only the older header.
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    # Browsers ignore HSTS over plain http, so this is inert in local dev and
    # active behind Fly's TLS termination.
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}

# FastAPI's interactive docs load Swagger UI from a CDN, which `script-src 'self'`
# blocks. Exempting the two doc routes keeps that page working without weakening
# the policy on any route that touches a session: they render our own OpenAPI
# schema and nothing else. The rest of the headers still apply to them.
_CSP_EXEMPT = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        exempt = scope.get("path") in _CSP_EXEMPT

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    if name == "content-security-policy" and exempt:
                        continue
                    # setdefault: a route that has deliberately set its own is
                    # making a decision this middleware should not overrule.
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
