"""
Database repositories.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

import asyncpg


class SessionRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get(self, chat_id: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state, data FROM sessions WHERE chat_id=$1",
                str(chat_id),
            )
        if not row:
            return {"state": "start", "data": {}}
        raw_data = row["data"]
        data: dict[str, Any]
        if isinstance(raw_data, dict):
            data = raw_data
        elif isinstance(raw_data, str):
            try:
                parsed = json.loads(raw_data)
                data = parsed if isinstance(parsed, dict) else {}
            except Exception:
                data = {}
        else:
            try:
                data = dict(raw_data or {})
            except Exception:
                data = {}
        return {
            "state": row["state"] or "start",
            "data": data,
        }

    async def set(self, chat_id: str, state: str, data: dict[str, Any] | None = None) -> None:
        payload = data or {}
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions(chat_id, state, data, updated_at)
                VALUES ($1, $2, $3::jsonb, NOW())
                ON CONFLICT (chat_id) DO UPDATE
                SET state=EXCLUDED.state, data=EXCLUDED.data, updated_at=NOW()
                """,
                str(chat_id),
                state,
                json.dumps(payload, ensure_ascii=False),
            )

    async def set_state(self, chat_id: str, state: str) -> None:
        session = await self.get(chat_id)
        await self.set(chat_id, state, session.get("data") or {})

    async def update_data(self, chat_id: str, patch: dict[str, Any]) -> None:
        session = await self.get(chat_id)
        data = dict(session.get("data") or {})
        data.update(patch or {})
        await self.set(chat_id, session.get("state") or "start", data)

    async def clear(self, chat_id: str) -> None:
        await self.set(chat_id, "start", {})

    async def cleanup_old(self, older_than_hours: int = 24) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM sessions
                WHERE updated_at < NOW() - ($1::text || ' hours')::interval
                """,
                int(older_than_hours),
            )
        return int(result.split()[-1]) if result else 0


class DoctorRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @staticmethod
    def _to_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        try:
            return list(value or [])
        except Exception:
            return []

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["clinic_uids"] = DoctorRepository._to_list(out.get("clinic_uids"))
        out["main_services"] = DoctorRepository._to_list(out.get("main_services"))
        return out

    async def upsert_doctors(self, doctors: list[dict]) -> None:
        if not doctors:
            return
        async with self.pool.acquire() as conn:
            for d in doctors:
                uid = str(d.get("СотрудникID") or d.get("employee_uid") or "").strip()
                if not uid:
                    continue
                fio = str(d.get("СотрудникФИО") or d.get("fio") or "").strip()
                if not fio:
                    continue
                spec = str(d.get("Специализация") or d.get("specialization") or "").strip()
                clinics = d.get("Клиника") or d.get("clinic_uids") or []
                if isinstance(clinics, str):
                    clinics = [clinics]
                phone = str(d.get("Телефон") or d.get("employee_phone") or "").strip()
                main_services = d.get("main_services")
                await conn.execute(
                    """
                    INSERT INTO doctors(employee_uid, fio, specialization, clinic_uids, employee_phone, main_services)
                    VALUES ($1, $2, $3, $4::jsonb, $5, COALESCE($6::jsonb, '[]'::jsonb))
                    ON CONFLICT (employee_uid) DO UPDATE
                    SET fio=EXCLUDED.fio,
                        specialization=EXCLUDED.specialization,
                        clinic_uids=EXCLUDED.clinic_uids,
                        employee_phone=EXCLUDED.employee_phone,
                        main_services=COALESCE($6::jsonb, doctors.main_services)
                    """,
                    uid,
                    fio,
                    spec,
                    json.dumps(list(clinics), ensure_ascii=False),
                    phone,
                    json.dumps(main_services, ensure_ascii=False) if main_services is not None else None,
                )

    async def search_by_fio(self, query: str) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT employee_uid, fio, specialization, clinic_uids, employee_phone, main_services
                FROM doctors
                WHERE fio ILIKE '%' || $1 || '%'
                ORDER BY fio
                LIMIT 20
                """,
                q,
            )
        return [self._normalize_row(dict(r)) for r in rows]

    async def get_by_uid(self, employee_uid: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT employee_uid, fio, specialization, clinic_uids, employee_phone, main_services
                FROM doctors
                WHERE employee_uid=$1
                """,
                employee_uid,
            )
        return self._normalize_row(dict(row)) if row else None

    async def get_main_services(self, employee_uid: str) -> list[dict[str, str]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT main_services FROM doctors WHERE employee_uid=$1",
                employee_uid,
            )
        return self._to_list((row["main_services"] if row else []) or [])

    async def set_main_services(self, employee_uid: str, services: list[dict[str, str]]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE doctors SET main_services=$2::jsonb WHERE employee_uid=$1",
                employee_uid,
                json.dumps(services or [], ensure_ascii=False),
            )

    async def list_employee_uids(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT employee_uid FROM doctors ORDER BY employee_uid")
        return [str(r["employee_uid"]) for r in rows]

    async def list_services_normalized(self, employee_uid: str) -> list[dict[str, str]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT service_uid, service_name
                FROM doctor_services
                WHERE employee_uid=$1
                ORDER BY service_name
                """,
                employee_uid,
            )
        return [{"uid": str(r["service_uid"]), "name": str(r["service_name"])} for r in rows]

    async def replace_normalized_services(self, employee_uid: str, services: list[dict[str, str]]) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM doctor_services WHERE employee_uid=$1", employee_uid)
                for s in services or []:
                    uid = str(s.get("uid") or "").strip()
                    name = str(s.get("name") or "").strip()
                    if not uid or not name:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO doctor_services(employee_uid, service_uid, service_name)
                        VALUES ($1, $2, $3)
                        """,
                        employee_uid,
                        uid,
                        name,
                    )
                await conn.execute(
                    "UPDATE doctors SET main_services=$2::jsonb WHERE employee_uid=$1",
                    employee_uid,
                    json.dumps(services or [], ensure_ascii=False),
                )


class ScheduleRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def replace_month(
        self,
        *,
        employee_uid: str,
        clinic_uid: str,
        year: int,
        month: int,
        rows: list[tuple[date, str, str, dict[str, Any]]],
    ) -> None:
        """Delete all slots in calendar month, then insert (slot_date, time_hhmm, kind, meta)."""
        from calendar import monthrange

        last = monthrange(year, month)[1]
        d0 = date(year, month, 1)
        d1 = date(year, month, last)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM schedule_slots
                    WHERE employee_uid=$1 AND clinic_uid=$2
                      AND slot_date >= $3 AND slot_date <= $4
                    """,
                    employee_uid,
                    clinic_uid,
                    d0,
                    d1,
                )
                for slot_d, hhmm, kind, meta in rows:
                    if kind not in {"free", "busy"}:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO schedule_slots(
                            employee_uid, clinic_uid, slot_date, time_hhmm, kind, meta, synced_at
                        )
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
                        ON CONFLICT (employee_uid, clinic_uid, slot_date, time_hhmm, kind)
                        DO UPDATE SET meta=EXCLUDED.meta, synced_at=NOW()
                        """,
                        employee_uid,
                        clinic_uid,
                        slot_d,
                        hhmm,
                        kind,
                        json.dumps(meta or {}, ensure_ascii=False),
                    )

    async def dates_with_slots_in_month(
        self,
        employee_uid: str,
        clinic_uid: str,
        year: int,
        month: int,
    ) -> set[date]:
        from calendar import monthrange

        last = monthrange(year, month)[1]
        d0 = date(year, month, 1)
        d1 = date(year, month, last)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT slot_date
                FROM schedule_slots
                WHERE employee_uid=$1 AND clinic_uid=$2
                  AND slot_date >= $3 AND slot_date <= $4
                """,
                employee_uid,
                clinic_uid,
                d0,
                d1,
            )
        return {r["slot_date"] for r in rows}

    async def list_free_times(self, employee_uid: str, clinic_uid: str, slot_date: date) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time_hhmm
                FROM schedule_slots
                WHERE employee_uid=$1 AND clinic_uid=$2 AND slot_date=$3 AND kind='free'
                ORDER BY time_hhmm
                """,
                employee_uid,
                clinic_uid,
                slot_date,
            )
        return [str(r["time_hhmm"]) for r in rows]

    async def list_busy_blocks(
        self,
        employee_uid: str,
        clinic_uid: str,
        slot_date: date,
    ) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time_hhmm, meta
                FROM schedule_slots
                WHERE employee_uid=$1 AND clinic_uid=$2 AND slot_date=$3 AND kind='busy'
                ORDER BY time_hhmm
                """,
                employee_uid,
                clinic_uid,
                slot_date,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            m: Any = r["meta"]
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except Exception:
                    m = {}
            elif not isinstance(m, dict):
                m = {}
            mm = dict(m)
            out.append(
                {
                    "time": str(r["time_hhmm"]),
                    "end": str(mm.pop("end", "") or ""),
                    "fio": str(mm.get("fio") or ""),
                    "service": str(mm.get("service") or ""),
                }
            )
        return out


class ClinicRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def upsert(self, clinic_uid: str, clinic_name: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO clinics(clinic_uid, clinic_name)
                VALUES ($1, $2)
                ON CONFLICT (clinic_uid) DO UPDATE SET clinic_name=EXCLUDED.clinic_name
                """,
                clinic_uid,
                clinic_name,
            )

    async def get_all(self) -> dict[str, str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT clinic_uid, clinic_name FROM clinics ORDER BY clinic_name")
        return {str(r["clinic_uid"]): str(r["clinic_name"]) for r in rows}


class AppointmentRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create(
        self,
        *,
        mis_uid: str | None,
        chat_id: str,
        doctor_uid: str,
        patient_surname: str,
        patient_name: str,
        patient_father_name: str = "",
        birthday: str | None = None,
        phone: str = "",
        visit_date: str,
        visit_time: str,
        clinic_uid: str | None = None,
        service_uid: str | None = None,
        service_name: str | None = None,
    ) -> None:
        try:
            visit_date_obj = datetime.strptime(visit_date, "%Y-%m-%d").date()
        except Exception:
            visit_date_obj = date.today()
        t = (visit_time or "").strip()
        if len(t) == 5:
            t = f"{t}:00"
        try:
            hh, mm, ss = t.split(":")[:3]
            visit_time_obj = time(int(hh), int(mm), int(ss))
        except Exception:
            visit_time_obj = time(0, 0, 0)
        birthday_obj = None
        if birthday:
            try:
                birthday_obj = datetime.strptime(birthday, "%Y-%m-%d").date()
            except Exception:
                birthday_obj = None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO appointments(
                    mis_uid, chat_id, doctor_uid, patient_surname, patient_name, patient_father_name,
                    birthday, phone, visit_date, visit_time, clinic_uid, service_uid, service_name
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                mis_uid,
                str(chat_id),
                doctor_uid,
                patient_surname,
                patient_name,
                patient_father_name,
                birthday_obj,
                phone,
                visit_date_obj,
                visit_time_obj,
                clinic_uid,
                service_uid,
                service_name,
            )

    async def get_by_id(self, appointment_id: int) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM appointments
                WHERE id=$1
                """,
                int(appointment_id),
            )
        return dict(row) if row else None

    async def mark_cancelled(self, appointment_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE appointments
                SET cancelled_at = NOW()
                WHERE id=$1 AND cancelled_at IS NULL
                """,
                int(appointment_id),
            )

    async def list_active_for_doctor(
        self,
        doctor_uid: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, mis_uid, visit_date, visit_time,
                       patient_surname, patient_name, patient_father_name,
                       phone, clinic_uid, service_name
                FROM appointments
                WHERE doctor_uid=$1
                  AND cancelled_at IS NULL
                  AND (
                    visit_date > CURRENT_DATE
                    OR (visit_date = CURRENT_DATE AND visit_time >= CURRENT_TIME)
                  )
                ORDER BY visit_date, visit_time
                LIMIT $2
                """,
                doctor_uid,
                int(limit),
            )
        return [dict(r) for r in rows]

    async def get_active_by_doctor_and_time(
        self,
        doctor_uid: str,
        visit_date: str,
        visit_time: str,
    ) -> dict[str, Any] | None:
        try:
            d = datetime.strptime(visit_date, "%Y-%m-%d").date()
        except Exception:
            return None
        t = visit_time if len(visit_time) == 8 else f"{visit_time}:00"
        try:
            hh, mm, ss = t.split(":")[:3]
            t_obj = time(int(hh), int(mm), int(ss))
        except Exception:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM appointments
                WHERE doctor_uid=$1
                  AND visit_date=$2
                  AND visit_time=$3
                  AND cancelled_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                doctor_uid,
                d,
                t_obj,
            )
        return dict(row) if row else None
