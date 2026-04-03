"""
Приложение поднимает пул БД, фоновую синхронизацию МИС→PostgreSQL и polling MAX.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

import config
from app.db import close_pool, get_pool, init_schema
from app.max_bot import MaxRuntime, run_max_polling
from app.max_client import MaxClient
from app.mis_client import MisClient
from app.repositories import (
    AppointmentRepository,
    ClinicRepository,
    DoctorRepository,
    ScheduleRepository,
    SessionRepository,
)
from app.sync_service import SyncService

logger = logging.getLogger(__name__)


async def _periodic_sync(sync: SyncService) -> None:
    sched_sec = max(60, config.SCHEDULE_SYNC_INTERVAL_MINUTES * 60)
    await asyncio.sleep(15)
    while True:
        try:
            await sync.sync_doctors_and_clinics()
            await sync.sync_schedule_horizon_months()
        except Exception:
            logger.exception("periodic schedule sync failed")
        await asyncio.sleep(sched_sec)


async def _periodic_services(sync: SyncService) -> None:
    srv_sec = max(120, config.SERVICES_SYNC_INTERVAL_MINUTES * 60)
    await asyncio.sleep(30)
    while True:
        await asyncio.sleep(srv_sec)
        try:
            await sync.sync_services_all_doctors()
        except Exception:
            logger.exception("periodic services sync failed")


async def _session_ttl_loop(session_repo: SessionRepository) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            n = await session_repo.cleanup_old(older_than_hours=config.SESSION_TTL_HOURS)
            if n:
                logger.info("session cleanup removed %s rows", n)
        except Exception:
            logger.exception("session cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, read=130.0))
    app.state.http_client = http

    pool = await get_pool(config.DATABASE_URL)
    await init_schema(pool)
    session_repo = SessionRepository(pool)
    doctor_repo = DoctorRepository(pool)
    clinic_repo = ClinicRepository(pool)
    appointment_repo = AppointmentRepository(pool)
    schedule_repo = ScheduleRepository(pool)
    mis = MisClient()

    sync = SyncService(
        pool=pool,
        mis=mis,
        doctor_repo=doctor_repo,
        clinic_repo=clinic_repo,
        schedule_repo=schedule_repo,
    )

    app.state.pool = pool
    app.state.sync_service = sync
    app.state.session_repo = session_repo
    app.state.mis_client = mis

    if not (config.MIS_BASE_URL or "").strip():
        logger.error("MIS_BASE_URL не задан — загрузка врачей и расписания из МИС невозможна.")
    if config.ENABLE_MAX_BOT and not (config.MAX_BOT_TOKEN or "").strip():
        logger.error("ENABLE_MAX_BOT=true, но MAX_BOT_TOKEN пуст — бот MAX не запущен.")

    # Полная заливка расписания/услуг только при пустой таблице врачей (первый запуск / новая БД).
    # При пересборке контейнера том PostgreSQL обычно сохраняется — врачи уже есть, тяжёлую синхронизацию не дублируем.
    existing_doctors = await doctor_repo.list_employee_uids()
    if not existing_doctors:
        logger.info("Начальная синхронизация из МИС (врачи, расписание, услуги)...")
        try:
            await sync.sync_doctors_and_clinics()
            await sync.sync_schedule_horizon_months()
            await sync.sync_services_all_doctors()
            n_docs = len(await doctor_repo.list_employee_uids())
            logger.info("Начальная синхронизация завершена: врачей в БД: %s", n_docs)
            if n_docs == 0:
                logger.warning(
                    "После синхронизации врачей в БД нет — проверьте MIS_* и ответ МИС (GetEnlargementSchedule / parse)."
                )
                mis_url = (config.MIS_BASE_URL or "").lower()
                if "localhost" in mis_url or "127.0.0.1" in mis_url:
                    logger.error(
                        "MIS_BASE_URL содержит localhost/127.0.0.1 — из Docker до МИС на хосте так не подключиться. "
                        "Укажите http://host.docker.internal:ПОРТ/... (МИС на этой же машине) или IP/домен сервера 1С."
                    )
        except Exception:
            logger.exception(
                "Начальная синхронизация с МИС не удалась — справочник врачей может быть пустым"
            )
    else:
        logger.info(
            "В БД уже есть %s врач(ей) — полный стартовый импорт расписания и услуг пропущен "
            "(обновление по таймеру в фоне). Быстрое обновление справочника врачей…",
            len(existing_doctors),
        )
        try:
            await sync.sync_doctors_and_clinics()
        except Exception:
            logger.exception("Не удалось обновить врачей из МИС при старте")

    tasks: list[asyncio.Task[Any]] = []
    tasks.append(asyncio.create_task(_periodic_sync(sync)))
    tasks.append(asyncio.create_task(_periodic_services(sync)))
    tasks.append(asyncio.create_task(_session_ttl_loop(session_repo)))

    if config.ENABLE_MAX_BOT and config.MAX_BOT_TOKEN.strip():
        max_client = MaxClient(
            config.MAX_BOT_TOKEN.strip(),
            base_url=config.MAX_API_BASE_URL,
            http_client=http,
        )
        runtime = MaxRuntime(
            client=max_client,
            client_mis=mis,
            session_repo=session_repo,
            doctor_repo=doctor_repo,
            clinic_repo=clinic_repo,
            appointment_repo=appointment_repo,
            schedule_repo=schedule_repo,
        )
        app.state.max_runtime = runtime
        tasks.append(
            asyncio.create_task(
                run_max_polling(
                    runtime,
                    limit=config.MAX_POLL_LIMIT,
                    timeout_sec=config.MAX_POLL_TIMEOUT_SEC,
                )
            )
        )
    else:
        logger.warning("MAX bot выключен: задайте MAX_BOT_TOKEN и ENABLE_MAX_BOT=true")

    app.state.background_tasks = tasks

    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http.aclose()
        await close_pool()
        logger.info("lifespan shutdown complete")
