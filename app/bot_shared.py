"""
Shared constants and helpers for bot handlers.
"""
from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import Iterable

STATE_START = "start"
STATE_REG = "reg"
STATE_REG_PHONE = "reg_phone"
STATE_SCHEDULE_READY = "schedule_ready"
STATE_BOOK_SERVICE = "book_service"
STATE_BOOK_PATIENT = "book_patient"
STATE_BOOK_CONFIRM = "book_confirm"


def load_clinic_names_from_file() -> dict[str, str]:
    out: dict[str, str] = {}
    root = Path(__file__).resolve().parent.parent
    path = root / "filials_uid.txt"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        uid = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else uid
        if uid.lower() == "clinic_uid":
            continue
        if uid:
            out[uid] = name or uid
    return out


def parse_schedule_dates(grafik: list[dict]) -> set[date]:
    """Extract unique schedule dates from free and busy ranges."""
    out: set[date] = set()
    for sch in grafik or []:
        periods = sch.get("ПериодыГрафика") or {}
        for src in (periods.get("СвободноеВремя") or []) + (periods.get("ЗанятоеВремя") or []):
            raw = str(src.get("Дата") or "").split("T")[0].strip()
            if not raw:
                continue
            try:
                y, m, d = raw.split("-")
                out.add(date(int(y), int(m), int(d)))
            except Exception:
                continue
    return out


def month_start_end(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_label_ru(year: int, month: int) -> str:
    names = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    ]
    return f"{names[month - 1]} {year}"


def build_month_calendar_lines(year: int, month: int, days_with_schedule: Iterable[date]) -> list[str]:
    marked = set(days_with_schedule or [])
    cal = calendar.Calendar(firstweekday=0)  # Monday
    lines = ["Пн   Вт   Ср   Чт   Пт   Сб   Вс"]
    for week in cal.monthdayscalendar(year, month):
        cells: list[str] = []
        for day_num in week:
            if day_num == 0:
                cells.append("    ")
                continue
            cur = date(year, month, day_num)
            suffix = "*" if cur in marked else " "
            cells.append(f"{day_num:02d}{suffix}")
        lines.append(" ".join(cells))
    return lines


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def build_month_day_grid(year: int, month: int, days_with_schedule: Iterable[date]) -> list[list[str]]:
    """
    Build 7-column calendar grid with '-' placeholders.
    Day format:
      - 'DD*' for days with any schedule
      - 'DD' for days without schedule
    """
    marked = set(days_with_schedule or [])
    cal = calendar.Calendar(firstweekday=0)  # Monday
    rows: list[list[str]] = []
    for week in cal.monthdayscalendar(year, month):
        row: list[str] = []
        for day_num in week:
            if day_num == 0:
                row.append("-")
                continue
            cur = date(year, month, day_num)
            suffix = "*" if cur in marked else ""
            row.append(f"{day_num:02d}{suffix}")
        rows.append(row)
    return rows
