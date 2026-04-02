"""
Manual doctors sync from MIS into DB.
Run: python -m scripts.load_doctors
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_pool, init_schema
from app.mis_client import MisClient
from app.parsers import parse_enlargement_to_doctors
from app.repositories import ClinicRepository, DoctorRepository


def _date_1c(d: datetime) -> str:
    return f"{d.day}.{d.month}.{d.year} 00:00:00"


async def main() -> None:
    import config

    pool = await get_pool(config.DATABASE_URL)
    await init_schema(pool)
    doctor_repo = DoctorRepository(pool)
    clinic_repo = ClinicRepository(pool)
    mis = MisClient()

    now = datetime.now(timezone.utc)
    windows = [
        (now, now + timedelta(days=60)),
        (now + timedelta(days=61), now + timedelta(days=120)),
    ]
    all_doctors: list[dict] = []
    seen: set[str] = set()
    for start, end in windows:
        resp = await mis.get_enlargement_schedule(_date_1c(start), _date_1c(end))
        doctors = parse_enlargement_to_doctors(resp.get("raw") or {})
        for d in doctors:
            uid = str(d.get("СотрудникID") or "").strip()
            if uid and uid not in seen:
                seen.add(uid)
                all_doctors.append(d)
    contacts = await mis.get_employee_contacts()
    for d in all_doctors:
        uid = str(d.get("СотрудникID") or "").strip()
        if uid in contacts:
            d["Телефон"] = contacts[uid].get("phone") or ""
    await doctor_repo.upsert_doctors(all_doctors)
    for d in all_doctors:
        clinics = d.get("Клиника") or []
        if isinstance(clinics, str):
            clinics = [clinics]
        for c in clinics:
            if c:
                await clinic_repo.upsert(str(c), str(c))
    await pool.close()
    print(f"Synced doctors: {len(all_doctors)}")


if __name__ == "__main__":
    asyncio.run(main())
