"""
Derive free/busy slots from MIS ГрафикДляСайта (shared by sync and legacy paths).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.parsers import _extract_clinic_uid


def clinic_uid_from_schedule(schedule: dict) -> str:
    return (_extract_clinic_uid(schedule) or "").strip()


def slot_minutes_from_schedule_block(schedule: dict) -> int:
    raw = str(schedule.get("ДлительностьПриема") or "")
    m = re.search(r"T00:(\d{2}):", raw)
    if m:
        try:
            v = int(m.group(1))
            if v > 0:
                return v
        except ValueError:
            pass
    return 20


def extract_times_for_day(grafik: list[dict], day_iso: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return sorted free HH:MM list and busy entries [{time, end}, ...]."""
    free_times: set[str] = set()
    busy_entries: list[dict[str, str]] = []
    for sch in grafik:
        periods = sch.get("ПериодыГрафика") or {}
        step = slot_minutes_from_schedule_block(sch)
        for x in periods.get("СвободноеВремя") or []:
            d = str(x.get("Дата") or "").split("T")[0]
            if d != day_iso:
                continue
            b = str(x.get("ВремяНачала") or "").split("T")[-1][:5]
            e = str(x.get("ВремяОкончания") or "").split("T")[-1][:5]
            if not b or not e:
                continue
            try:
                bh, bm = map(int, b.split(":"))
                eh, em = map(int, e.split(":"))
                cur = bh * 60 + bm
                end = eh * 60 + em
                while cur < end:
                    free_times.add(f"{cur // 60:02d}:{cur % 60:02d}")
                    cur += step
            except ValueError:
                continue
        for x in periods.get("ЗанятоеВремя") or []:
            d = str(x.get("Дата") or "").split("T")[0]
            if d != day_iso:
                continue
            b = str(x.get("ВремяНачала") or "").split("T")[-1][:5]
            e = str(x.get("ВремяОкончания") or "").split("T")[-1][:5]
            if b:
                busy_entries.append({"time": b, "end": e})
    return sorted(free_times), sorted(busy_entries, key=lambda z: z["time"])


def time_to_minutes(hhmm: str) -> int:
    try:
        hh, mm = hhmm.split(":")[:2]
        return int(hh) * 60 + int(mm)
    except (ValueError, IndexError):
        return -1


def pick_ticket_for_busy(
    busy_start: str,
    busy_end: str,
    tickets: list[dict[str, str]],
) -> tuple[str, str]:
    bs = time_to_minutes(busy_start)
    be = time_to_minutes(busy_end) if busy_end else bs + 1
    for t in tickets:
        ts = time_to_minutes(t.get("time") or "")
        if ts < 0:
            continue
        if bs <= ts < be:
            return (t.get("fio") or "", t.get("service") or "")
    for t in tickets:
        if (t.get("time") or "") == busy_start:
            return (t.get("fio") or "", t.get("service") or "")
    return ("", "")


def tickets_rows_for_day(
    tickets_raw: list[dict[str, Any]],
    *,
    day_iso: str,
    clinic_uid: str,
    doctor_uid: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    c_low = clinic_uid.strip().lower()
    d_low = doctor_uid.strip().lower()
    for t in tickets_raw:
        if not isinstance(t, dict):
            continue
        filial = str(t.get("Филиал") or "").strip().lower()
        if filial != c_low:
            continue
        emp = str(t.get("Сотрудник") or "").strip().lower()
        if emp and emp != d_low:
            continue
        dt_start = str(t.get("ДатаНачала") or "")
        if not dt_start.startswith(day_iso):
            continue
        ts = str(t.get("ДатаНачала") or "").split("T")[-1][:5]
        fio = str(t.get("КлиентНаименование") or "").strip()
        works = t.get("СписокРабот") or []
        if isinstance(works, dict):
            works = [works]
        service = ""
        if works and isinstance(works, list) and isinstance(works[0], dict):
            service = str(works[0].get("Наименование") or "").strip()
        if ts:
            rows.append({"time": ts, "fio": fio, "service": service})
    return rows


def ticket_window_for_month(day_in_month: date) -> tuple[str, str]:
    """Wider window for PatientTickets request (MIS quirks)."""
    first = day_in_month.replace(day=1)
    w_start = (first.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d 00:00:00")
    w_finish = (first.replace(day=28) + timedelta(days=10)).replace(day=1) + timedelta(days=40)
    w_finish = w_finish.replace(day=1) - timedelta(seconds=1)
    return w_start, w_finish.strftime("%Y-%m-%d %H:%M:%S")

