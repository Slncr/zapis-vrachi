"""
Pull doctors, schedule, and services from MIS into PostgreSQL.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

import config
from app.mis_client import MisClient
from app.parsers import get_grafik_from_schedule_response, parse_enlargement_to_doctors
from app.repositories import ClinicRepository, DoctorRepository, ScheduleRepository
from app.schedule_compute import (
    clinic_uid_from_schedule,
    extract_times_for_day,
    pick_ticket_for_busy,
    ticket_window_for_month,
    tickets_rows_for_day,
)

logger = logging.getLogger(__name__)


def _month_range(y: int, m: int) -> tuple[date, date]:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


async def _touch_sync(pool: asyncpg.Pool, resource: str, detail: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sync_state(resource, last_success_at, detail)
            VALUES ($1, NOW(), $2::jsonb)
            ON CONFLICT (resource) DO UPDATE
            SET last_success_at = NOW(), detail = EXCLUDED.detail
            """,
            resource,
            json.dumps(detail, ensure_ascii=False),
        )


class SyncService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        mis: MisClient,
        doctor_repo: DoctorRepository,
        clinic_repo: ClinicRepository,
        schedule_repo: ScheduleRepository,
    ) -> None:
        self.pool = pool
        self.mis = mis
        self.doctor_repo = doctor_repo
        self.clinic_repo = clinic_repo
        self.schedule_repo = schedule_repo

    async def sync_doctors_and_clinics(self) -> int:
        now = datetime.now(timezone.utc)
        range1_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        range1_end = (now + timedelta(days=60)).replace(hour=23, minute=59, second=59, microsecond=0)
        range2_start = range1_end + timedelta(days=1)
        range2_end = range2_start + timedelta(days=60)

        def _date(d: datetime) -> str:
            return f"{d.day}.{d.month}.{d.year} 00:00:00"

        all_doctors: list[dict] = []
        seen: set[str] = set()
        for start, end in [(range1_start, range1_end), (range2_start, range2_end)]:
            try:
                resp = await self.mis.get_enlargement_schedule(_date(start), _date(end))
                doctors = parse_enlargement_to_doctors(resp.get("raw") or {})
                for d in doctors:
                    uid = str(d.get("СотрудникID") or d.get("employee_uid") or "").strip()
                    if uid and uid not in seen:
                        seen.add(uid)
                        all_doctors.append(d)
            except Exception:
                logger.exception("GetEnlargementSchedule failed %s-%s", start, end)
        contacts: dict[str, dict[str, str]] = {}
        try:
            contacts = await self.mis.get_employee_contacts()
        except Exception:
            logger.exception("get_employee_contacts failed")
        if contacts:
            for d in all_doctors:
                uid = str(d.get("СотрудникID") or d.get("employee_uid") or "").strip()
                if uid and uid in contacts:
                    d["Телефон"] = contacts[uid].get("phone") or ""
        await self.doctor_repo.upsert_doctors(all_doctors)
        for d in all_doctors:
            clinics = d.get("Клиника") or d.get("clinic_uids") or []
            if isinstance(clinics, str):
                clinics = [clinics]
            for cid in clinics:
                if cid:
                    await self.clinic_repo.upsert(str(cid), str(cid))
        await _touch_sync(self.pool, "doctors", {"count": len(all_doctors)})
        logger.info("sync_doctors_and_clinics: %s doctors", len(all_doctors))
        return len(all_doctors)

    async def sync_services_for_doctor(self, employee_uid: str) -> int:
        uid = str(employee_uid or "").strip()
        if not uid:
            return 0
        try:
            now = datetime.now()
            start = (now - timedelta(days=365)).strftime("%Y-%m-%d 00:00:00")
            finish = (now + timedelta(days=365)).strftime("%Y-%m-%d 23:59:59")
            services = await self.mis.get_doctor_services_from_tickets_http(uid, start, finish)
        except Exception:
            logger.exception("services sync failed for %s", uid)
            return 0
        await self.doctor_repo.replace_normalized_services(uid, services)
        return len(services)

    async def sync_services_all_doctors(self) -> None:
        uids = await self.doctor_repo.list_employee_uids()
        n = 0
        for uid in uids:
            if await self.sync_services_for_doctor(uid):
                n += 1
        await _touch_sync(self.pool, "services", {"doctors_with_services": n, "total_doctors": len(uids)})
        logger.info("sync_services_all_doctors: updated %s / %s", n, len(uids))

    async def sync_schedule_month(
        self,
        employee_uid: str,
        clinic_uid: str,
        year: int,
        month: int,
        *,
        tickets_raw: list[dict[str, Any]] | None = None,
    ) -> None:
        uid = str(employee_uid or "").strip()
        cid = str(clinic_uid or "").strip()
        if not uid or not cid:
            return
        d0, d1 = _month_range(year, month)
        start = d0.strftime("%d.%m.%Y 00:00:00")
        finish = d1.strftime("%d.%m.%Y 23:59:59")
        try:
            resp = await self.mis.get_schedule20(uid, start, finish)
            grafik = get_grafik_from_schedule_response(resp.get("raw") or {})
        except Exception:
            logger.exception("get_schedule20 failed %s %s %s-%s", uid, cid, year, month)
            return
        grafik = [g for g in grafik if isinstance(g, dict) and clinic_uid_from_schedule(g) == cid]
        if not grafik:
            await self.schedule_repo.replace_month(
                employee_uid=uid,
                clinic_uid=cid,
                year=year,
                month=month,
                rows=[],
            )
            return

        if tickets_raw is None:
            tickets_raw = []
            try:
                w_start, w_finish = ticket_window_for_month(d0)
                tickets_raw = await self.mis.get_patient_tickets_http(w_start, w_finish, employee_uid=uid)
                if not tickets_raw:
                    tickets_raw = await self.mis.get_patient_tickets_http(w_start, w_finish, employee_uid=None)
            except Exception:
                logger.debug("PatientTickets optional enrichment failed", exc_info=True)

        out_rows: list[tuple[date, str, str, dict[str, Any]]] = []
        day = d0
        while day <= d1:
            day_iso = day.isoformat()
            free_times, busy_entries = extract_times_for_day(grafik, day_iso)
            t_rows = tickets_rows_for_day(tickets_raw, day_iso=day_iso, clinic_uid=cid, doctor_uid=uid)
            for t in free_times:
                out_rows.append((day, t, "free", {}))
            for b in busy_entries:
                fio, srv = pick_ticket_for_busy(b.get("time") or "", b.get("end") or "", t_rows)
                out_rows.append(
                    (
                        day,
                        str(b.get("time") or ""),
                        "busy",
                        {"end": b.get("end") or "", "fio": fio, "service": srv},
                    )
                )
            day += timedelta(days=1)

        await self.schedule_repo.replace_month(
            employee_uid=uid,
            clinic_uid=cid,
            year=year,
            month=month,
            rows=out_rows,
        )

    async def sync_schedule_horizon_months(self, *, months_ahead: int | None = None) -> None:
        horizon = (
            months_ahead
            if months_ahead is not None
            else max(1, config.SCHEDULE_SYNC_MONTHS_AHEAD)
        )
        now = datetime.now()
        pairs: set[tuple[int, int]] = set()
        for delta in range(horizon):
            t = now + timedelta(days=28 * delta)
            pairs.add((t.year, t.month))
        pair_list = sorted(pairs)

        uids = await self.doctor_repo.list_employee_uids()
        total_cells = 0
        pause = max(0.0, float(config.MIS_REQUEST_PAUSE_SEC or 0.0))

        # Оценка объёма: врачи × филиалы × месяцы вызовов Schedule; PatientTickets — один раз на (врач, месяц).
        for uid in uids:
            doc = await self.doctor_repo.get_by_uid(uid)
            if not doc:
                continue
            clinics_raw = doc.get("clinic_uids") or []
            if isinstance(clinics_raw, str):
                clinics_raw = [clinics_raw]
            clinic_set = {str(x).strip() for x in clinics_raw if str(x).strip()}
            if not clinic_set:
                continue

            for y, m in pair_list:
                d0, _ = _month_range(y, m)
                tix: list[dict[str, Any]] = []
                try:
                    w_start, w_finish = ticket_window_for_month(d0)
                    tix = await self.mis.get_patient_tickets_http(w_start, w_finish, employee_uid=uid)
                    if not tix:
                        tix = await self.mis.get_patient_tickets_http(w_start, w_finish, employee_uid=None)
                except Exception:
                    logger.debug(
                        "PatientTickets failed uid=%s %04d-%02d", uid, y, m, exc_info=True
                    )
                if pause:
                    await asyncio.sleep(pause)
                for clinic_uid in sorted(clinic_set):
                    await self.sync_schedule_month(
                        uid, clinic_uid, y, m, tickets_raw=tix
                    )
                    total_cells += 1
                    if pause:
                        await asyncio.sleep(pause)

        await _touch_sync(
            self.pool,
            "schedule",
            {"month_cells": total_cells, "doctors": len(uids), "months": horizon},
        )
        logger.info(
            "sync_schedule_horizon_months: %s month-cells (doctors=%s, months=%s; PatientTickets≈doctors×months)",
            total_cells,
            len(uids),
            horizon,
        )
