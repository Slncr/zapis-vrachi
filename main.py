"""
Entry point: init DB, run Telegram bot, run MAX bot, schedule background jobs.
"""
import asyncio
import logging
import sys
from datetime import time as dt_time
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from app.bot import build_application
from app.db import close_pool, get_pool, init_schema
from app.max_bot import MaxRuntime, run_max_polling
from app.max_client import MaxClient
from app.mis_client import MisClient
from app.parsers import parse_enlargement_to_doctors
from app.repositories import (
    AppointmentRepository,
    ClinicRepository,
    DoctorRepository,
    SessionRepository,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def sync_doctor_services(
    mis: MisClient,
    doc_repo: DoctorRepository,
    employee_uid: str,
) -> list[dict]:
    uid = str(employee_uid or "").strip()
    if not uid:
        return []
    services: list[dict] = []
    try:
        services = await mis.get_employee_main_services(uid)
    except Exception as e:
        logger.warning("Get employee main services failed for %s: %s", uid, e)
    if not services:
        try:
            from datetime import datetime, timedelta

            before = (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")
            after = (datetime.now() + timedelta(days=3650)).strftime("%Y-%m-%d")
            services = await mis.get_doctor_services_from_tickets_http(
                uid,
                f"{before} 00:00:00",
                f"{after} 23:59:59",
            )
        except Exception as e:
            logger.warning("Get doctor services from tickets failed for %s: %s", uid, e)
    try:
        await doc_repo.set_main_services(uid, services)
    except Exception as e:
        logger.warning("Save main services failed for %s: %s", uid, e)
    return services


async def refresh_doctors_job(context) -> None:
    """Daily: load doctors from 1C and upsert to DB."""
    from datetime import datetime, timedelta, timezone

    app = context.application
    pool = app.bot_data.get("_pool")
    if not pool:
        return
    mis = app.bot_data["mis_client"]
    doc_repo = app.bot_data["doctor_repo"]
    clinic_repo = app.bot_data["clinic_repo"]
    now = datetime.now(timezone.utc)
    range1_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range1_end = (now + timedelta(days=60)).replace(hour=23, minute=59, second=59, microsecond=0)
    range2_start = range1_end + timedelta(days=1)
    range2_end = range2_start + timedelta(days=60)

    def _date(d):
        return f"{d.day}.{d.month}.{d.year} 00:00:00"

    all_doctors = []
    seen = set()
    contacts_by_uid = {}
    for start, end in [(range1_start, range1_end), (range2_start, range2_end)]:
        try:
            resp = await mis.get_enlargement_schedule(_date(start), _date(end))
            doctors = parse_enlargement_to_doctors(resp.raw)
            for d in doctors:
                uid = d.get("СотрудникID") or d.get("employee_uid")
                if uid and uid not in seen:
                    seen.add(uid)
                    all_doctors.append(d)
        except Exception as e:
            logger.warning("GetEnlargementSchedule failed %s-%s: %s", start, end, e)
    try:
        contacts_by_uid = await mis.get_employee_contacts()
    except Exception as e:
        logger.warning("Get employee contacts failed: %s", e)
    if contacts_by_uid:
        for d in all_doctors:
            uid = d.get("СотрудникID") or d.get("employee_uid")
            if uid and uid in contacts_by_uid:
                d["Телефон"] = contacts_by_uid[uid].get("phone") or ""
    await doc_repo.upsert_doctors(all_doctors)
    for d in all_doctors:
        clinics = d.get("Клиника") or d.get("clinic_uids") or []
        if isinstance(clinics, str):
            clinics = [clinics]
        for cid in clinics:
            if cid:
                await clinic_repo.upsert(cid, cid)
    logger.info("Refreshed %s doctors", len(all_doctors))


async def sync_doctor_services_daily_job(context) -> None:
    """Daily: refresh doctors main services."""
    app = context.application
    doc_repo = app.bot_data.get("doctor_repo")
    mis = app.bot_data.get("mis_client")
    if not doc_repo or not mis:
        return
    try:
        uids = await doc_repo.list_employee_uids()
    except Exception as e:
        logger.warning("List doctor uids failed: %s", e)
        return
    synced = 0
    for uid in uids:
        services = await sync_doctor_services(mis, doc_repo, uid)
        if services:
            synced += 1
    logger.info("Daily doctor services sync done: doctors=%s with_services=%s", len(uids), synced)


async def cleanup_sessions_job(context) -> None:
    app = context.application
    session_repo = app.bot_data["session_repo"]
    try:
        n = await session_repo.cleanup_old(older_than_hours=config.SESSION_TTL_HOURS)
        if n:
            logger.info("Cleaned up %s old sessions", n)
    except Exception as e:
        logger.warning("Session cleanup failed: %s", e)


def main() -> None:
    async def run_bot() -> None:
        pool = await get_pool(config.DATABASE_URL)
        await init_schema(pool)
        session_repo = SessionRepository(pool=pool)
        doctor_repo = DoctorRepository(pool=pool)
        clinic_repo = ClinicRepository(pool=pool)
        appointment_repo = AppointmentRepository(pool=pool)
        mis_client = MisClient()

        app = build_application(
            session_repo=session_repo,
            doctor_repo=doctor_repo,
            clinic_repo=clinic_repo,
            appointment_repo=appointment_repo,
            mis_client=mis_client,
        )
        app.bot_data["_pool"] = pool

        job_queue = app.job_queue
        if job_queue:
            job_queue.run_daily(refresh_doctors_job, time=dt_time(2, 0))
            job_queue.run_daily(sync_doctor_services_daily_job, time=dt_time(2, 30))
            job_queue.run_daily(cleanup_sessions_job, time=dt_time(3, 0))
        else:
            logger.warning("Job queue not available; background jobs disabled.")

        await app.initialize()
        await app.start()
        max_task = None
        if config.ENABLE_MAX_BOT:
            if not config.MAX_BOT_TOKEN:
                raise RuntimeError("ENABLE_MAX_BOT=true but MAX_BOT_TOKEN is not set")
            max_client = MaxClient(
                config.MAX_BOT_TOKEN,
                base_url=config.MAX_API_BASE_URL,
            )
            me = await max_client.get_me()
            logger.info(
                "MAX bot enabled: user_id=%s username=%s",
                me.get("user_id"),
                me.get("username"),
            )
            max_runtime = MaxRuntime(
                client=max_client,
                client_mis=mis_client,
                session_repo=session_repo,
                doctor_repo=doctor_repo,
                clinic_repo=clinic_repo,
                appointment_repo=appointment_repo,
            )
            max_task = asyncio.create_task(
                run_max_polling(
                    max_runtime,
                    limit=config.MAX_POLL_LIMIT,
                    timeout_sec=config.MAX_POLL_TIMEOUT_SEC,
                )
            )
        try:
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
        finally:
            if max_task:
                max_task.cancel()
                try:
                    await max_task
                except asyncio.CancelledError:
                    pass
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await close_pool()

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
