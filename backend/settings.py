"""Application settings helpers."""

import os


DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]


def get_cors_origins() -> list[str]:
    """Return local + configured CORS origins."""
    origins = list(DEFAULT_CORS_ORIGINS)
    extra_origins = os.environ.get("CORS_ORIGINS", "")
    if extra_origins:
        origins.extend(
            origin.strip()
            for origin in extra_origins.split(",")
            if origin.strip()
        )
    return origins
