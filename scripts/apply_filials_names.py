"""
Применить названия филиалов из filials_uid.txt в БД.
Формат файла: одна строка на филиал — «UID<TAB>Название».
Первая строка может быть заголовком: clinic_uid	clinic_name
Запуск: python -m scripts.apply_filials_names (из корня проекта, DATABASE_URL задан).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_pool, init_schema
from app.repositories import ClinicRepository


FILE_NAME = "filials_uid.txt"


async def main() -> None:
    import config
    root = Path(__file__).resolve().parent.parent
    path = root / FILE_NAME
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        sys.exit(1)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    updates = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        uid = (parts[0] or "").strip()
        name = (parts[1] or "").strip() if len(parts) > 1 else uid
        if uid.lower() == "clinic_uid" and (not name or name.lower() == "clinic_name"):
            continue
        if uid:
            updates.append((uid, name or uid))
    if not updates:
        print("Нет строк для применения.", file=sys.stderr)
        sys.exit(0)
    pool = await get_pool(config.DATABASE_URL)
    await init_schema(pool)
    clinic_repo = ClinicRepository(pool=pool)
    for uid, name in updates:
        await clinic_repo.upsert(uid, name)
    await pool.close()
    print(f"Обновлено названий филиалов: {len(updates)}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
