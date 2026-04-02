"""
Получить UID филиалов из БД (таблица clinics) и записать в файл.
Перед экспортом можно обновить список из МИС: python -m scripts.load_doctors
Запуск: python -m scripts.export_clinics (из корня проекта, с заданным DATABASE_URL).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_pool, init_schema
from app.repositories import ClinicRepository


OUTPUT_FILE = "filials_uid.txt"


async def main() -> None:
    import config
    pool = await get_pool(config.DATABASE_URL)
    await init_schema(pool)
    clinic_repo = ClinicRepository(pool=pool)
    clinics = await clinic_repo.get_all()
    await pool.close()

    out_path = Path(__file__).resolve().parent.parent / OUTPUT_FILE
    header = "clinic_uid\tclinic_name"
    lines = [f"{uid}\t{name}" for uid, name in sorted(clinics.items())]
    out_path.write_text(header + "\n" + "\n".join(lines), encoding="utf-8")
    print(f"Записано {len(lines)} филиалов в {out_path}", file=sys.stderr)
    print(header)
    for line in lines:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
