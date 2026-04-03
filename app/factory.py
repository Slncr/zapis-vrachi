"""
Сборка FastAPI-приложения: маршруты и lifespan.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request

import config
from app.lifespan import lifespan
from app.sync_service import SyncService

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )

    app = FastAPI(title="zapis-vrachi", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, Any]:
        pool = getattr(request.app.state, "pool", None)
        if pool is None:
            return {"status": "not_ready", "error": "pool not initialized"}
        try:
            async with pool.acquire() as conn:
                v = await conn.fetchval("SELECT 1")
            return {"status": "ready", "db": v == 1}
        except Exception as e:
            logger.warning("readiness check failed: %s", e)
            return {"status": "not_ready", "error": str(e)}

    @app.post("/admin/sync")
    async def admin_sync(request: Request) -> dict[str, str]:
        sync: SyncService = request.app.state.sync_service
        await sync.sync_doctors_and_clinics()
        await sync.sync_schedule_horizon_months()
        await sync.sync_services_all_doctors()
        return {"status": "ok"}

    return app
