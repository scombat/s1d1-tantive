"""Accept request paths with or without the `S1D1_ROOT_PATH` prefix.

uvicorn's own `root_path` prepends the prefix to the path it receives, and Starlette strips it again before routing, so
a path that already starts with the prefix (a tunnel or proxy that forwards it unchanged) never matched. This
middleware does the opposite: when the path is the prefix or starts with `prefix + "/"` it records the prefix as the
scope's `root_path`, the one Starlette strips once before routing; any other path is left alone, so `/ready`,
`/health` and `/metrics` keep answering unprefixed, and so does a deployment behind a proxy that removes the prefix."""

from __future__ import annotations


def normalise_prefix(root_path: str) -> str:
    """`/api/` and `api` become `/api`; an empty or slash-only value stays empty (no prefix)."""
    root_path = root_path.strip().strip("/")
    return f"/{root_path}" if root_path else ""


class PrefixMiddleware:
    """Pure ASGI middleware for the http and websocket scopes; lifespan passes through."""

    def __init__(self, app, prefix: str) -> None:
        self.app = app
        self.prefix = normalise_prefix(prefix)
        self._raw = self.prefix.encode()

    async def __call__(self, scope, receive, send) -> None:
        if self.prefix and scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["root_path"] = self.prefix
                if path == self.prefix:  # the prefix alone is the root of the service
                    scope["path"] = self.prefix + "/"
                    raw = scope.get("raw_path")
                    if raw is not None and raw == self._raw:
                        scope["raw_path"] = self._raw + b"/"
        await self.app(scope, receive, send)
