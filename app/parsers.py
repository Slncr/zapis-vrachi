"""
Parsing logic from N8N: doctors filter/group, patient line, schedule slots, clinic_uid resolution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# N8N Code in JavaScript4: keywords that exclude "non-doctors"
NON_DOCTOR_KEYWORDS = [
    "рентген", "кт", "мрт", "экг", "узи", "флюорография", "флюорограф",
    "массаж", "физиотерап", "процедур", "лаборат", "анализ", "регистрат",
    "касс", "администратор", "медсест", "санитар", "оператор", "call",
    "пост", "тест", "фото", "диагност", "аппарат", "рентгенологический кабинет",
]


def filter_doctors(grafik: list[dict]) -> list[dict]:
    """Filter to real doctors only (N8N Code in JavaScript4)."""
    data = list(grafik)
    filtered = []
    for d in data:
        fio = (d.get("СотрудникФИО") or "").strip().lower()
        spec = (d.get("Специализация") or "").strip().lower()
        if not spec:
            continue
        if any(k in fio for k in NON_DOCTOR_KEYWORDS):
            continue
        if len(fio.split()) < 2:
            continue
        filtered.append(d)
    return filtered


def group_doctors_by_employee(grafik: list[dict]) -> list[dict]:
    """Group by СотрудникID, merge clinics (N8N Code in JavaScript5/7)."""
    grouped: dict[str, dict] = {}
    for d in grafik:
        eid = d.get("СотрудникID")
        if not eid:
            continue
        if eid not in grouped:
            grouped[eid] = {
                "СотрудникФИО": d.get("СотрудникФИО"),
                "СотрудникID": eid,
                "Специализация": d.get("Специализация"),
                "Клиника": [],
            }
        clinic_id = d.get("Клиника")
        if clinic_id and clinic_id not in grouped[eid]["Клиника"]:
            grouped[eid]["Клиника"].append(clinic_id)
    return list(grouped.values())


def parse_enlargement_to_doctors(raw_response: dict) -> list[dict]:
    """From get_enlargement_schedule response: extract ГрафикДляСайта, filter, group."""
    otvet = raw_response.get("Ответ") or raw_response.get("Ответы") or {}
    grafik = otvet.get("ГрафикДляСайта") or []
    if not isinstance(grafik, list):
        grafik = [grafik] if grafik else []
    filtered = filter_doctors(grafik)
    return group_doctors_by_employee(filtered)


@dataclass
class PatientParsed:
    patient_surname: str
    patient_name: str
    patient_father_name: str
    birthday: str  # YYYY-MM-DD
    phone: str
    visit_date_human: str
    visit_time_human: str


def parse_patient_line(
    text: str,
    date_ymd: str,
    time_iso: str,
) -> PatientParsed | None:
    """
    Parse "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон" (N8N Code in JavaScript9).
    date_ymd: 20251222, time_iso: 20251222T16:30:00 or 16:30:00.
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 3:
        return None
    fio_parts = parts[0].split()
    if len(fio_parts) < 2:
        return None
    patient_surname = fio_parts[0] or ""
    patient_name = fio_parts[1] if len(fio_parts) > 1 else ""
    patient_father_name = " ".join(fio_parts[2:]) if len(fio_parts) > 2 else ""
    raw_birthday = parts[1].strip()
    phone = parts[2].strip() if len(parts) > 2 else ""
    birthday = ""
    if raw_birthday:
        m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw_birthday)
        if m:
            d, mo, y = m.groups()
            birthday = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    # visitDateHuman: 20251222 -> 22.12.2025
    visit_date_human = ""
    if len(date_ymd) >= 8:
        visit_date_human = f"{date_ymd[6:8]}.{date_ymd[4:6]}.{date_ymd[:4]}"
    # visitTimeHuman: 20251222T11:00:00 -> 11:00
    visit_time_human = ""
    if "T" in time_iso:
        visit_time_human = time_iso.split("T")[1][:5]
    return PatientParsed(
        patient_surname=patient_surname,
        patient_name=patient_name,
        patient_father_name=patient_father_name,
        birthday=birthday,
        phone=phone,
        visit_date_human=visit_date_human,
        visit_time_human=visit_time_human,
    )


def get_grafik_from_schedule_response(raw: dict) -> list[dict]:
    """Extract Ответ.ГрафикДляСайта from GetShedule20 response."""
    import json
    if isinstance(raw.get("data"), str):
        raw = json.loads(raw["data"])
    otvet = raw.get("Ответ") or raw.get("Ответы") or {}
    grafik = otvet.get("ГрафикДляСайта") or []
    return grafik if isinstance(grafik, list) else [grafik] if grafik else []


def find_clinic_uid_for_slot(
    schedules: list[dict],
    selected_date_ymd: str,
    selected_time_iso: str,
) -> str | None:
    """
    Code16/Code17: find which clinic (Клиника) the selected slot belongs to.
    selected_time_iso: 20251204T16:30:00 or 2025-12-04T16:30:00.
    """
    def parse_ymd_t(human: str) -> tuple[str, str] | None:
        if not human:
            return None
        parts = human.replace("-", "").split("T")
        if len(parts) != 2:
            return None
        ymd = parts[0].replace("-", "")[:8]
        t = parts[1]
        if len(t) == 5:
            t = t + ":00"
        return (ymd, t)

    def to_epoch(ymd: str, t: str) -> int:
        y, m, d = int(ymd[:4]), int(ymd[4:6]) - 1, int(ymd[6:8])
        hh, mm, ss = (int(x) for x in (t.split(":") + ["0", "0"])[:3])
        import datetime as dt
        return int(dt.datetime(y, m + 1, d, hh, mm, ss).timestamp())

    selected = parse_ymd_t(selected_time_iso)
    if not selected:
        return None
    sel_ymd, sel_t = selected
    sel_epoch = to_epoch(sel_ymd, sel_t)

    for schedule in schedules:
        periods = schedule.get("ПериодыГрафика") or {}
        free_slots = (periods.get("СвободноеВремя") or []) + (
            periods.get("ЗанятоеВремя") or []
        )
        for slot in free_slots:
            start_iso = (slot.get("ВремяНачала") or "").replace("-", "")
            end_iso = (slot.get("ВремяОкончания") or "").replace("-", "")
            if not start_iso or "T" not in start_iso:
                continue
            start_parts = start_iso.split("T")
            end_parts = end_iso.split("T") if end_iso else ["", ""]
            start_ymd = start_parts[0][:8]
            start_t = start_parts[1][:8] if len(start_parts[1]) >= 5 else start_parts[1] + ":00"
            end_ymd = end_parts[0][:8] if end_parts[0] else start_ymd
            end_t = end_parts[1][:8] if len(end_parts[1]) >= 5 else end_parts[1] + ":00"
            start_epoch = to_epoch(start_ymd, start_t)
            end_epoch = to_epoch(end_ymd, end_t)
            if start_epoch <= sel_epoch < end_epoch:
                return _extract_clinic_uid(schedule)
    return None


# UUID pattern: 8-4-4-4-12 hex (e.g. be99f92e-d8f8-11ed-8f48-ea408af4d281)
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _clinic_display_name(clinic_uid: str | None, clinic_name: str, index: int) -> str:
    """Return human-readable clinic name; if name is UID, use 'Филиал №N'."""
    if not clinic_name or clinic_name == "Неизвестный филиал":
        return f"Филиал №{index}" if index else "Филиал"
    if _UUID_RE.match(clinic_name.strip()) or clinic_name.strip() == (clinic_uid or "").strip():
        return f"Филиал №{index}"
    return clinic_name


def _extract_clinic_uid(schedule: dict) -> str | None:
    """Извлечь UID филиала: «Клиника» может быть строкой или объектом 1С (УИД/UID)."""
    val = schedule.get("Клиника")
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, dict):
        return (val.get("УИД") or val.get("UID") or val.get("GUID") or "").strip() or None
    return str(val).strip() or None


def build_date_buttons_by_clinics(
    grafik: list[dict],
    clinics_by_uid: dict[str, str],
) -> list[list[dict]]:
    """Code14: rows of inline buttons (text, callback_data). One row = list of dicts."""
    rows: list[list[dict]] = []
    clinic_index = 0
    for schedule in grafik:
        clinic_uid = _extract_clinic_uid(schedule)
        uid_norm = (clinic_uid or "").strip()
        uid_lower = uid_norm.lower() if uid_norm else ""
        raw_name = (
            (clinics_by_uid.get(clinic_uid) if clinic_uid else None)
            or clinics_by_uid.get(uid_norm)
            or clinics_by_uid.get(uid_lower)
            or "Неизвестный филиал"
        )
        clinic_index += 1
        clinic_name = _clinic_display_name(clinic_uid, raw_name, clinic_index)
        periods = schedule.get("ПериодыГрафика") or {}
        free = periods.get("СвободноеВремя") or []
        busy = periods.get("ЗанятоеВремя") or []
        free_dates = {x.get("Дата", "").split("T")[0] for x in free if x.get("Дата")}
        busy_dates = {x.get("Дата", "").split("T")[0] for x in busy if x.get("Дата")}
        all_dates = sorted(set(free_dates) | set(busy_dates))
        if not all_dates:
            continue
        rows.append([{"text": f"🏥 {clinic_name}", "callback_data": "ignore"}])
        for i in range(0, len(all_dates), 2):
            pair = all_dates[i : i + 2]
            btns = []
            for d in pair:
                parts = d.split("-")
                if len(parts) >= 3:
                    y, m, day = parts[0], parts[1], parts[2]
                    pretty = f"{day}.{m}.{y}"
                else:
                    pretty = d
                compact = d.replace("-", "")[:8]
                if d in free_dates:
                    btns.append({"text": f"✅ {pretty}", "callback_data": f"free_{compact}"})
                else:
                    btns.append({"text": f"❌ {pretty}", "callback_data": f"busy_{compact}"})
            rows.append(btns)
    rows.append([{"text": "⬅ Назад", "callback_data": "schedule"}])
    return rows


def build_time_slots_buttons(
    grafik_first: dict,
    selected_date_ymd: str,
) -> list[list[dict]]:
    """QQ: time slots for one day. selected_date_ymd: yyyymmdd."""
    periods = grafik_first.get("ПериодыГрафика") or {}
    free = periods.get("СвободноеВремя") or []
    busy = periods.get("ЗанятоеВремя") or []
    duration_str = grafik_first.get("ДлительностьПриема") or "PT30M"
    slot_minutes = 30
    if "T" in duration_str:
        part = duration_str.split("T")[1]
        if "M" in part:
            slot_minutes = int(re.search(r"(\d+)M", part).group(1)) if re.search(r"(\d+)M", part) else 30

    def to_minutes(t: str) -> int:
        parts = t.split(":")[:2]
        return int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0

    def to_time(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    slots: list[tuple[str, str]] = []
    for r in free:
        dt = (r.get("Дата") or "").split("T")[0].replace("-", "")
        if dt != selected_date_ymd:
            continue
        start_t = (r.get("ВремяНачала") or "").split("T")[1][:5]
        end_t = (r.get("ВремяОкончания") or "").split("T")[1][:5]
        start_m = to_minutes(start_t)
        end_m = to_minutes(end_t)
        for t in range(start_m, end_m, slot_minutes):
            slots.append((to_time(t), "freeTime"))
    for r in busy:
        dt = (r.get("Дата") or "").split("T")[0].replace("-", "")
        if dt != selected_date_ymd:
            continue
        start_t = (r.get("ВремяНачала") or "").split("T")[1][:5]
        end_t = (r.get("ВремяОкончания") or "").split("T")[1][:5]
        start_m = to_minutes(start_t)
        end_m = to_minutes(end_t)
        for t in range(start_m, end_m, slot_minutes):
            slots.append((to_time(t), "busy"))
    slots.sort(key=lambda x: to_minutes(x[0]))
    seen = set()
    unique_slots = []
    for t, typ in slots:
        key = (t, typ)
        if key not in seen:
            seen.add(key)
            unique_slots.append(key)

    rows = []
    for i in range(0, len(unique_slots), 2):
        pair = unique_slots[i : i + 2]
        btns = []
        for time_str, typ in pair:
            icon = "✅" if typ == "freeTime" else "❌"
            cb = f"{typ}_{selected_date_ymd}T{time_str}:00"
            btns.append({"text": f"{icon} {time_str}", "callback_data": cb})
        rows.append(btns)
    rows.append([{"text": "⬅ Назад", "callback_data": "schedule"}])
    return rows
