"""
Run HTTP API + background MIS sync + MAX polling (see app.asgi).
"""
from __future__ import annotations

import uvicorn

import config

if __name__ == "__main__":
    uvicorn.run(
        "app.asgi:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=False,
        timeout_keep_alive=75,
    )
