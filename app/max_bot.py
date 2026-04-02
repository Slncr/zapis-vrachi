"""
MAX bot runtime and polling loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.bot_shared import (
    load_clinic_names_from_file,
    STATE_REG,
    STATE_REG_PHONE,
    STATE_SCHEDULE_READY,
    STATE_BOOK_SERVICE,
    STATE_BOOK_PATIENT,
    STATE_START,
    build_month_day_grid,
    month_label_ru,
    month_start_end,
    parse_schedule_dates,
    shift_month,
)
from app.max_client import MaxClient
from app.parsers import get_grafik_from_schedule_response, parse_patient_line

logger = logging.getLogger(__name__)


@dataclass
class MaxRuntime:
    client: MaxClient
    client_mis: Any
    session_repo: Any
    doctor_repo: Any
    clinic_repo: Any
    appointment_repo: Any


def _main_menu_rows() -> list[list[dict[str, str]]]:
    return [
        [{"text": "Выбрать филиал", "callback": "my_schedule"}],
    ]


def _extract_target(update: dict[str, Any]) -> dict[str, str] | None:
    callback = update.get("callback") or {}
    cb_user = (callback.get("user") or {}).get("user_id")
    if cb_user is not None and str(cb_user).strip():
        return {"user_id": str(cb_user)}
    message = update.get("message") or {}
    sender_user = (message.get("sender") or {}).get("user_id")
    if sender_user is not None and str(sender_user).strip():
        return {"user_id": str(sender_user)}
    recipient = message.get("recipient") or {}
    chat_id = recipient.get("chat_id") or update.get("chat_id")
    if chat_id is not None and str(chat_id).strip():
        return {"chat_id": str(chat_id)}
    user_id = recipient.get("user_id")
    if user_id is not None and str(user_id).strip():
        return {"user_id": str(user_id)}
    return None


def _extract_user_id(update: dict[str, Any]) -> str | None:
    callback = update.get("callback") or {}
    cb_user = (callback.get("user") or {}).get("user_id")
    if cb_user:
        return str(cb_user)
    sender = (update.get("sender") or {}).get("user_id")
    if sender:
        return str(sender)
    msg_user = ((update.get("message") or {}).get("sender") or {}).get("user_id")
    if msg_user:
        return str(msg_user)
    return None


def _extract_text(update: dict[str, Any]) -> str:
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    text = body.get("text") or msg.get("text")
    return str(text or "").strip()


def _extract_callback_payload(update: dict[str, Any]) -> tuple[str | None, str | None]:
    cb = update.get("callback") or {}
    payload = cb.get("payload") or cb.get("data") or update.get("payload")
    callback_id = cb.get("callback_id") or cb.get("id")
    return (str(payload) if payload else None, str(callback_id) if callback_id else None)


async def _send_start(runtime: MaxRuntime, chat_id: str) -> None:
    await runtime.client.send_message(
        user_id=chat_id,
        text="Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
        buttons=[[{"text": "Регистрация", "callback": "reg"}]],
    )


async def _send_to_target(
    runtime: MaxRuntime,
    target: dict[str, str],
    text: str,
    buttons: list[list[dict[str, str]]] | None = None,
) -> None:
    await runtime.client.send_message(
        chat_id=target.get("chat_id"),
        user_id=target.get("user_id"),
        text=text,
        buttons=buttons,
    )


async def _send_schedule(
    runtime: MaxRuntime,
    target: dict[str, str],
    doctor_uid: str,
    target_ym: str | None = None,
) -> None:
    await _send_to_target(runtime, target, "⏳ Загружаю...")
    user_id = str(target.get("user_id") or "")
    s = await runtime.session_repo.get(user_id) if user_id else {"data": {}}
    sess_data = dict(s.get("data") or {})
    selected_clinic_uid = str(sess_data.get("selected_clinic_uid") or "").strip()

    doc = await runtime.doctor_repo.get_by_uid(doctor_uid)
    clinics = (doc or {}).get("clinic_uids") or []
    if isinstance(clinics, str):
        clinics = [clinics]
    clinics = [str(x).strip() for x in clinics if str(x).strip()]
    if not selected_clinic_uid:
        await _send_clinic_picker(runtime, target, user_id, clinics)
        return

    now = datetime.now()
    year = now.year
    month = now.month
    if target_ym and len(target_ym) == 6 and target_ym.isdigit():
        year = int(target_ym[:4])
        month = int(target_ym[4:6])
    start_d, finish_d = month_start_end(year, month)
    start = start_d.strftime("%d.%m.%Y 00:00:00")
    finish = finish_d.strftime("%d.%m.%Y 23:59:59")
    try:
        resp = await runtime.client_mis.get_schedule20(doctor_uid, start, finish)
        grafik = get_grafik_from_schedule_response(resp.get("raw") or {})
    except Exception as e:
        await _send_to_target(runtime, target, f"Ошибка получения расписания: {e}")
        return
    if selected_clinic_uid:
        grafik = [g for g in grafik if _extract_clinic_uid(g) == selected_clinic_uid]
        if not grafik:
            await _send_to_target(runtime, target, "По выбранному филиалу расписание не найдено.")
            return
    days = parse_schedule_dates(grafik)
    data = await runtime.session_repo.get(target.get("user_id") or target.get("chat_id") or "")
    session_data = dict(data.get("data") or {})
    clinic_name = str(session_data.get("selected_clinic_name") or session_data.get("clinic_name") or "").strip()
    prefix = f"Выбранная клиника: {clinic_name}\n" if clinic_name else ""

    py, pm = shift_month(year, month, -12)
    ny, nm = shift_month(year, month, 12)
    mpy, mpm = shift_month(year, month, -1)
    mny, mnm = shift_month(year, month, 1)
    rows: list[list[dict[str, str]]] = [
        [
            {"text": "<<<", "callback": f"cal_y_prev_{py:04d}{pm:02d}"},
            {"text": f"{year}", "callback": "cal_ignore"},
            {"text": ">>>", "callback": f"cal_y_next_{ny:04d}{nm:02d}"},
        ],
        [
            {"text": "<<", "callback": f"cal_m_prev_{mpy:04d}{mpm:02d}"},
            {"text": month_label_ru(year, month).split()[0], "callback": "cal_ignore"},
            {"text": ">>", "callback": f"cal_m_next_{mny:04d}{mnm:02d}"},
        ],
    ]
    for week in build_month_day_grid(year, month, days):
        btn_row: list[dict[str, str]] = []
        for cell in week:
            if cell == "-":
                btn_row.append({"text": "-", "callback": "cal_ignore"})
            else:
                day_num = cell[:2]
                btn_row.append({"text": cell, "callback": f"cal_day_{year:04d}{month:02d}{day_num}"})
        rows.append(btn_row)
    rows.append([{"text": "Назад", "callback": "menu"}])
    await _send_to_target(
        runtime,
        target,
        f"{prefix}Выберите дату:\n* — есть расписание",
        buttons=rows,
    )


def _slot_minutes_from_schedule_block(schedule: dict) -> int:
    raw = str(schedule.get("ДлительностьПриема") or "")
    m = re.search(r"T00:(\d{2}):", raw)
    if m:
        try:
            v = int(m.group(1))
            if v > 0:
                return v
        except Exception:
            pass
    return 20


def _extract_times_for_day(grafik: list[dict], day_iso: str) -> tuple[list[str], list[dict]]:
    free_times: set[str] = set()
    busy_entries: list[dict] = []
    for sch in grafik:
        periods = sch.get("ПериодыГрафика") or {}
        step = _slot_minutes_from_schedule_block(sch)
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
            except Exception:
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


def _time_to_minutes(hhmm: str) -> int:
    try:
        hh, mm = hhmm.split(":")[:2]
        return int(hh) * 60 + int(mm)
    except Exception:
        return -1


def _pick_ticket_for_busy(busy_start: str, busy_end: str, tickets: list[dict[str, str]]) -> tuple[str, str]:
    bs = _time_to_minutes(busy_start)
    be = _time_to_minutes(busy_end) if busy_end else bs + 1
    for t in tickets:
        ts = _time_to_minutes(t.get("time") or "")
        if ts < 0:
            continue
        if bs <= ts < be:
            return (t.get("fio") or "", t.get("service") or "")
    for t in tickets:
        if (t.get("time") or "") == busy_start:
            return (t.get("fio") or "", t.get("service") or "")
    return ("", "")


def _booking_confirmation_text(data: dict, doctor_fio: str, clinic_name: str) -> str:
    fio = " ".join(
        x
        for x in [
            str(data.get("book_patient_surname") or "").strip(),
            str(data.get("book_patient_name") or "").strip(),
            str(data.get("book_patient_father_name") or "").strip(),
        ]
        if x
    )
    phone = str(data.get("book_patient_phone") or "").strip()
    birthday = str(data.get("book_patient_birthday_human") or "не указана").strip()
    service_name = str(data.get("book_service_name") or "не указана").strip()
    day_ymd = str(data.get("book_day_ymd") or "")
    time_hhmm = str(data.get("book_time_hhmm") or "")
    date_human = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]}" if len(day_ymd) == 8 else day_ymd
    return (
        "Проверьте данные записи:\n\n"
        f"ФИО: {fio}\n"
        f"Телефон: {phone}\n"
        f"Дата рождения: {birthday}\n"
        f"Услуга: {service_name}\n"
        f"Доктор: {doctor_fio}\n"
        f"Филиал: {clinic_name}\n"
        f"Дата приёма: {date_human}\n"
        f"Время: {time_hhmm}"
    )


async def _send_day_schedule(runtime: MaxRuntime, target: dict[str, str], doctor_uid: str, day_ymd: str) -> None:
    await _send_to_target(runtime, target, "⏳ Загружаю...")
    user_id = str(target.get("user_id") or "")
    s = await runtime.session_repo.get(user_id) if user_id else {"data": {}}
    sess_data = dict(s.get("data") or {})
    selected_clinic_uid = str(sess_data.get("selected_clinic_uid") or "").strip()
    if not selected_clinic_uid:
        await _send_to_target(runtime, target, "Сначала выберите филиал.")
        return
    day_iso = f"{day_ymd[:4]}-{day_ymd[4:6]}-{day_ymd[6:8]}"
    start = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]} 00:00:00"
    finish = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]} 23:59:59"
    try:
        resp = await runtime.client_mis.get_schedule20(doctor_uid, start, finish)
        grafik = get_grafik_from_schedule_response(resp.get("raw") or {})
    except Exception as e:
        await _send_to_target(runtime, target, f"Ошибка получения расписания дня: {e}")
        return
    grafik = [g for g in grafik if _extract_clinic_uid(g) == selected_clinic_uid]
    free_times, busy_entries = _extract_times_for_day(grafik, day_iso)

    tickets_rows: list[dict[str, str]] = []
    try:
        day_dt = datetime.strptime(day_iso, "%Y-%m-%d")
        # MIS PatientTickets may return empty on tight one-day range; use wider window and filter locally.
        w_start = (day_dt.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d 00:00:00")
        w_finish = (day_dt.replace(day=28) + timedelta(days=10)).replace(day=1) + timedelta(days=40)
        w_finish = w_finish.replace(day=1) - timedelta(seconds=1)
        window_finish = w_finish.strftime("%Y-%m-%d %H:%M:%S")
        tickets = await runtime.client_mis.get_patient_tickets_http(w_start, window_finish, employee_uid=doctor_uid)
        if not tickets:
            tickets = await runtime.client_mis.get_patient_tickets_http(w_start, window_finish, employee_uid=None)
        for t in tickets:
            filial = str(t.get("Филиал") or "").strip().lower()
            if filial != selected_clinic_uid.lower():
                continue
            emp = str(t.get("Сотрудник") or "").strip().lower()
            if emp and emp != doctor_uid.lower():
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
            if works and isinstance(works, list):
                w0 = works[0] if isinstance(works[0], dict) else {}
                service = str(w0.get("Наименование") or "").strip()
            if ts:
                tickets_rows.append({"time": ts, "fio": fio, "service": service})
    except Exception:
        pass

    pretty = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]}"
    lines = [f"Записи на {pretty}:"]
    if busy_entries:
        for b in busy_entries:
            t = b["time"]
            fio, srv = _pick_ticket_for_busy(t, b.get("end") or "", tickets_rows)
            lines.append(t)
            lines.append(f"👤 {fio}" if fio else "👤 Пациент не указан")
            lines.append(f"🩺 {srv}" if srv else "🩺 Услуга не указана")
            lines.append("────────────────────")
    else:
        lines.append("Нет занятых записей.")
        lines.append("────────────────────")
    lines.append("")
    lines.append("Свободные слоты (нажмите для записи):")
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for t in free_times:
        row.append({"text": t, "callback": f"sched_free_{day_ymd}_{t.replace(':', '')}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "⬅ К календарю", "callback": "back_to_calendar"}])
    await _send_to_target(runtime, target, "\n".join(lines), buttons=rows)


async def _get_services_for_doctor(runtime: MaxRuntime, doctor_uid: str) -> list[dict]:
    if not doctor_uid:
        return []
    services = await runtime.doctor_repo.get_main_services(doctor_uid)
    if services:
        return services
    try:
        now = datetime.now()
        # Keep window moderate: very wide ranges may cause MIS 500.
        start = (now - timedelta(days=365)).strftime("%Y-%m-%d 00:00:00")
        finish = (now + timedelta(days=365)).strftime("%Y-%m-%d 23:59:59")
        services = await runtime.client_mis.get_doctor_services_from_tickets_http(doctor_uid, start, finish)
        if services:
            await runtime.doctor_repo.set_main_services(doctor_uid, services)
        return services
    except Exception:
        return []


def _extract_clinic_uid(schedule: dict) -> str:
    val = schedule.get("Клиника")
    if isinstance(val, dict):
        return str(val.get("УИД") or val.get("UID") or "").strip()
    return str(val or "").strip()


async def _get_clinic_name_map(runtime: MaxRuntime) -> dict[str, str]:
    db_map = await runtime.clinic_repo.get_all()
    file_map = load_clinic_names_from_file()
    # File mapping is authoritative (human-readable names), DB may still contain GUID placeholders.
    out = dict(db_map)
    out.update(file_map)
    # Add lowercase aliases for robust lookup.
    for k, v in list(out.items()):
        lk = str(k).lower()
        if lk not in out:
            out[lk] = v
    return out


async def _send_clinic_picker(
    runtime: MaxRuntime,
    target: dict[str, str],
    user_id: str,
    clinics: list[str],
) -> None:
    names = await _get_clinic_name_map(runtime)
    if not clinics:
        await _send_to_target(runtime, target, "Филиалы не найдены.")
        return
    rows = [[{"text": f"🏥 {names.get(cid, cid)}", "callback": f"sched_clinic_{cid}"}] for cid in clinics]
    rows.append([{"text": "Назад", "callback": "menu"}])
    await _send_to_target(runtime, target, "Выберите филиал:", buttons=rows)


async def _handle_message_created(runtime: MaxRuntime, update: dict[str, Any]) -> None:
    target = _extract_target(update)
    user_id = _extract_user_id(update)
    text = _extract_text(update)
    logger.info("MAX message_created: user_id=%s target=%s text=%s", user_id, target, text)
    if not target or not user_id:
        return
    s = await runtime.session_repo.get(user_id)
    state = s.get("state") or STATE_START
    data = dict(s.get("data") or {})
    low = text.lower()
    if low in {"/start", "start", "начать"}:
        await runtime.session_repo.set(user_id, STATE_START, {})
        await _send_to_target(
            runtime,
            target,
            "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
            buttons=[[{"text": "Регистрация", "callback": "reg"}]],
        )
        return
    if state == STATE_START:
        # Accept FIO directly in start state (callback buttons may not work on all MAX clients).
        if len(text.split()) >= 2:
            matches = await runtime.doctor_repo.search_by_fio(text)
            if matches:
                d = matches[0]
                data["doctor_uid"] = d["employee_uid"]
                data["doctor_fio"] = d["fio"]
                await runtime.session_repo.set(user_id, STATE_REG_PHONE, data)
                logger.info("MAX state set: user_id=%s -> %s (via direct FIO)", user_id, STATE_REG_PHONE)
                await _send_to_target(runtime, target, "Введите номер телефона врача:")
                return
        await _send_to_target(
            runtime,
            target,
            "Введите ФИО врача (как в МИС) или нажмите «Регистрация».",
            buttons=[[{"text": "Регистрация", "callback": "reg"}]],
        )
        return
    if state == STATE_REG:
        matches = await runtime.doctor_repo.search_by_fio(text)
        if not matches:
            await _send_to_target(runtime, target, "Врач не найден. Введите ФИО точнее.")
            return
        d = matches[0]
        data["doctor_uid"] = d["employee_uid"]
        data["doctor_fio"] = d["fio"]
        await runtime.session_repo.set(user_id, STATE_REG_PHONE, data)
        await _send_to_target(runtime, target, "Введите номер телефона врача:")
        return
    if state == STATE_REG_PHONE:
        doctor_uid = str(data.get("doctor_uid") or "")
        doc = await runtime.doctor_repo.get_by_uid(doctor_uid) if doctor_uid else None
        expected = str((doc or {}).get("employee_phone") or "").strip()
        if expected and expected != text:
            await _send_to_target(runtime, target, "Телефон не совпал. Повторите ввод.")
            return
        await runtime.session_repo.set(user_id, STATE_SCHEDULE_READY, data)
        await _send_to_target(runtime, target, text=f"Регистрация успешна: {data.get('doctor_fio','')}")
        clinics = (doc or {}).get("clinic_uids") or []
        if isinstance(clinics, str):
            clinics = [clinics]
        clinics = [str(x).strip() for x in clinics if str(x).strip()]
        await _send_clinic_picker(runtime, target, user_id, clinics)
        return
    if state == STATE_BOOK_PATIENT:
        doctor_uid = str(data.get("doctor_uid") or "").strip()
        clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
        day_ymd = str(data.get("book_day_ymd") or "").strip()
        time_hhmm = str(data.get("book_time_hhmm") or "").strip()
        if not doctor_uid or not clinic_uid or not day_ymd or not time_hhmm:
            await runtime.session_repo.set(user_id, STATE_SCHEDULE_READY, data)
            await _send_to_target(runtime, target, "Слот не найден. Выберите дату и время заново.", buttons=_main_menu_rows())
            return
        parsed = parse_patient_line(text, day_ymd, f"{day_ymd}T{time_hhmm}:00")
        if not parsed:
            await _send_to_target(
                runtime,
                target,
                "Не удалось разобрать данные.\nФормат: Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон",
            )
            return
        data["book_patient_surname"] = parsed.patient_surname
        data["book_patient_name"] = parsed.patient_name
        data["book_patient_father_name"] = parsed.patient_father_name
        data["book_patient_birthday"] = parsed.birthday
        b_human = ""
        if parsed.birthday:
            b_human = f"{parsed.birthday[8:10]}.{parsed.birthday[5:7]}.{parsed.birthday[:4]}"
        data["book_patient_birthday_human"] = b_human or "не указана"
        data["book_patient_phone"] = parsed.phone
        await runtime.session_repo.set(user_id, STATE_BOOK_CONFIRM, data)
        doc = await runtime.doctor_repo.get_by_uid(doctor_uid) if doctor_uid else None
        doctor_fio = str((doc or {}).get("fio") or "")
        clinic_name = str(data.get("selected_clinic_name") or clinic_uid)
        confirm_text = _booking_confirmation_text(data, doctor_fio, clinic_name)
        await _send_to_target(
            runtime,
            target,
            confirm_text,
            buttons=[[{"text": "Подтвердить", "callback": "book_confirm"}, {"text": "Назад", "callback": "book_back"}]],
        )
        return
    await _send_to_target(runtime, target, "Используйте /start")


async def _handle_callback(runtime: MaxRuntime, update: dict[str, Any]) -> None:
    target = _extract_target(update) or {"user_id": _extract_user_id(update) or ""}
    user_id = _extract_user_id(update)
    payload, callback_id = _extract_callback_payload(update)
    logger.info("MAX callback: user_id=%s target=%s payload=%s", user_id, target, payload)
    if not user_id:
        return
    if callback_id:
        try:
            await runtime.client.answer_callback(callback_id=callback_id)
        except Exception:
            logger.exception("MAX answer_callback failed")
    if payload == "reg":
        await runtime.session_repo.set(user_id, STATE_REG, {})
        await _send_to_target(runtime, target, "Введите ФИО врача:")
        return
    if payload == "schedule":
        s = await runtime.session_repo.get(user_id)
        doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
        if not doctor_uid:
            await _send_to_target(runtime, target, "Сначала зарегистрируйтесь.")
            return
        doc = await runtime.doctor_repo.get_by_uid(doctor_uid)
        clinics = (doc or {}).get("clinic_uids") or []
        if isinstance(clinics, str):
            clinics = [clinics]
        clinics = [str(x).strip() for x in clinics if str(x).strip()]
        await _send_clinic_picker(runtime, target, user_id, clinics)
        return
    if payload == "my_schedule":
        s = await runtime.session_repo.get(user_id)
        doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
        if not doctor_uid:
            await _send_to_target(
                runtime,
                target,
                "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
                buttons=[[{"text": "Регистрация", "callback": "reg"}]],
            )
            return
        doc = await runtime.doctor_repo.get_by_uid(doctor_uid)
        clinics = (doc or {}).get("clinic_uids") or []
        if isinstance(clinics, str):
            clinics = [clinics]
        clinics = [str(x).strip() for x in clinics if str(x).strip()]
        data = dict((s.get("data") or {}))
        data.pop("selected_clinic_uid", None)
        data.pop("selected_clinic_name", None)
        await runtime.session_repo.set(user_id, s.get("state") or STATE_SCHEDULE_READY, data)
        await _send_clinic_picker(runtime, target, user_id, clinics)
        return
    if payload and payload.startswith("sched_clinic_"):
        s = await runtime.session_repo.get(user_id)
        doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
        if not doctor_uid:
            await _send_to_target(
                runtime,
                target,
                "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
                buttons=[[{"text": "Регистрация", "callback": "reg"}]],
            )
            return
        clinic_uid = payload.replace("sched_clinic_", "", 1).strip()
        names = await _get_clinic_name_map(runtime)
        data = dict(s.get("data") or {})
        data["selected_clinic_uid"] = clinic_uid
        data["selected_clinic_name"] = names.get(clinic_uid, clinic_uid)
        await runtime.session_repo.set(user_id, s.get("state") or STATE_SCHEDULE_READY, data)
        await _send_schedule(runtime, target, doctor_uid)
        return
    if payload and payload.startswith("cal_") and payload != "cal_ignore":
        if payload.startswith("cal_day_"):
            s = await runtime.session_repo.get(user_id)
            doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
            if not doctor_uid:
                await _send_to_target(
                    runtime,
                    target,
                    "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
                    buttons=[[{"text": "Регистрация", "callback": "reg"}]],
                )
                return
            day_ymd = payload.replace("cal_day_", "", 1)
            await _send_day_schedule(runtime, target, doctor_uid, day_ymd)
            return
        s = await runtime.session_repo.get(user_id)
        doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
        if not doctor_uid:
            await _send_to_target(
                runtime,
                target,
                "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача.",
                buttons=[[{"text": "Регистрация", "callback": "reg"}]],
            )
            return
        ym = payload.split("_")[-1]
        await _send_schedule(runtime, target, doctor_uid, target_ym=ym)
        return
    if payload == "back_to_calendar":
        s = await runtime.session_repo.get(user_id)
        doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
        if not doctor_uid:
            await _send_to_target(runtime, target, "Сначала зарегистрируйтесь.")
            return
        await _send_schedule(runtime, target, doctor_uid)
        return
    if payload and payload.startswith("sched_free_"):
        m = re.fullmatch(r"sched_free_(\d{8})_(\d{4})", payload)
        if m:
            ymd, hhmm = m.group(1), m.group(2)
            pretty = f"{ymd[6:8]}.{ymd[4:6]}.{ymd[:4]} {hhmm[:2]}:{hhmm[2:]}"
            await _send_to_target(runtime, target, "⏳ Загружаю...")
            s = await runtime.session_repo.get(user_id)
            data = dict(s.get("data") or {})
            doctor_uid = str(data.get("doctor_uid") or "").strip()
            data["book_day_ymd"] = ymd
            data["book_time_hhmm"] = f"{hhmm[:2]}:{hhmm[2:]}"
            services = await _get_services_for_doctor(runtime, doctor_uid) if doctor_uid else []
            if services:
                await runtime.session_repo.set(user_id, STATE_BOOK_SERVICE, data)
                rows: list[list[dict[str, str]]] = []
                for item in services[:20]:
                    uid = str(item.get("uid") or "").strip()
                    name = str(item.get("name") or "").strip()
                    if not uid or not name:
                        continue
                    title = name if len(name) <= 58 else f"{name[:55]}..."
                    rows.append([{"text": title, "callback": f"sched_service_{uid}"}])
                rows.append([{"text": "Без услуги", "callback": "sched_service_none"}])
                await _send_to_target(runtime, target, f"Выбран свободный слот: {pretty}\nВыберите услугу:", buttons=rows)
                return
            await runtime.session_repo.set(user_id, STATE_BOOK_PATIENT, data)
            await _send_to_target(
                runtime,
                target,
                (
                    f"Выбран свободный слот: {pretty}\n"
                    "Услуги врача не найдены, продолжим без услуги.\n"
                    "Введите данные пациента одной строкой:\n"
                    "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
                    "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567"
                ),
            )
            return
    if payload and payload.startswith("sched_service_"):
        s = await runtime.session_repo.get(user_id)
        data = dict(s.get("data") or {})
        doctor_uid = str(data.get("doctor_uid") or "").strip()
        services = await _get_services_for_doctor(runtime, doctor_uid) if doctor_uid else []
        if payload == "sched_service_none":
            data.pop("book_service_uid", None)
            data.pop("book_service_name", None)
        else:
            service_uid = payload.replace("sched_service_", "", 1).strip()
            service_name = ""
            for item in services:
                if str(item.get("uid") or "").strip() == service_uid:
                    service_name = str(item.get("name") or "").strip()
                    break
            data["book_service_uid"] = service_uid
            data["book_service_name"] = service_name
        await runtime.session_repo.set(user_id, STATE_BOOK_PATIENT, data)
        suffix = f"\nУслуга: {data.get('book_service_name')}" if data.get("book_service_name") else ""
        await _send_to_target(
            runtime,
            target,
            "Введите данные пациента одной строкой:\n"
            "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
            "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567"
            f"{suffix}",
        )
        return
    if payload == "book_back":
        s = await runtime.session_repo.get(user_id)
        await runtime.session_repo.set(user_id, STATE_BOOK_PATIENT, dict(s.get("data") or {}))
        await _send_to_target(
            runtime,
            target,
            "Введите данные пациента одной строкой:\n"
            "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
            "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567",
        )
        return
    if payload == "book_confirm":
        s = await runtime.session_repo.get(user_id)
        data = dict(s.get("data") or {})
        doctor_uid = str(data.get("doctor_uid") or "").strip()
        clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
        day_ymd = str(data.get("book_day_ymd") or "").strip()
        time_hhmm = str(data.get("book_time_hhmm") or "").strip()
        service_uid = str(data.get("book_service_uid") or "").strip() or None
        service_name = str(data.get("book_service_name") or "").strip() or None
        if not doctor_uid or not clinic_uid or not day_ymd or not time_hhmm:
            await runtime.session_repo.set(user_id, STATE_SCHEDULE_READY, data)
            await _send_to_target(runtime, target, "Слот не найден. Выберите дату и время заново.", buttons=_main_menu_rows())
            return
        resp = await runtime.client_mis.create_appointment(
            employee_id=doctor_uid,
            patient_surname=str(data.get("book_patient_surname") or ""),
            patient_name=str(data.get("book_patient_name") or ""),
            patient_father_name=str(data.get("book_patient_father_name") or ""),
            birthday=str(data.get("book_patient_birthday") or ""),
            date_ymd=f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]}",
            time_begin=f"{time_hhmm}:00",
            phone=str(data.get("book_patient_phone") or ""),
            clinic=clinic_uid,
            service=service_uid,
        )
        if not resp.success:
            await _send_to_target(runtime, target, f"Ошибка записи в МИС: {resp.error or 'неизвестная ошибка'}")
            return
        try:
            await runtime.appointment_repo.create(
                mis_uid=resp.uid,
                chat_id=user_id,
                doctor_uid=doctor_uid,
                patient_surname=str(data.get("book_patient_surname") or ""),
                patient_name=str(data.get("book_patient_name") or ""),
                patient_father_name=str(data.get("book_patient_father_name") or ""),
                birthday=str(data.get("book_patient_birthday") or "") or None,
                phone=str(data.get("book_patient_phone") or ""),
                visit_date=f"{day_ymd[:4]}-{day_ymd[4:6]}-{day_ymd[6:8]}",
                visit_time=f"{time_hhmm}:00",
                clinic_uid=clinic_uid,
                service_uid=service_uid,
                service_name=service_name,
            )
        except Exception:
            pass
        for k in [
            "book_day_ymd",
            "book_time_hhmm",
            "book_service_uid",
            "book_service_name",
            "book_patient_surname",
            "book_patient_name",
            "book_patient_father_name",
            "book_patient_birthday",
            "book_patient_birthday_human",
            "book_patient_phone",
        ]:
            data.pop(k, None)
        await runtime.session_repo.set(user_id, STATE_SCHEDULE_READY, data)
        await _send_to_target(runtime, target, "Запись успешно создана.", buttons=_main_menu_rows())
        return
    if payload == "menu":
        await _send_to_target(runtime, target, "Выберите филиал:", buttons=_main_menu_rows())


async def run_max_polling(runtime: MaxRuntime, *, limit: int = 100, timeout_sec: int = 30) -> None:
    logger.info("MAX polling started")
    marker: str | None = None
    while True:
        try:
            data = await runtime.client.get_updates(
                marker=marker,
                limit=limit,
                timeout_sec=timeout_sec,
                types=["message_created", "message_callback"],
            )
            marker = data.get("marker") or marker
            updates = data.get("updates") or data.get("messages") or []
            if isinstance(updates, dict):
                updates = [updates]
            for upd in updates if isinstance(updates, list) else []:
                if not isinstance(upd, dict):
                    continue
                typ = str(upd.get("update_type") or upd.get("type") or "")
                if not typ:
                    if "callback" in upd:
                        typ = "message_callback"
                    elif "message" in upd:
                        typ = "message_created"
                if typ == "message_callback":
                    await _handle_callback(runtime, upd)
                elif typ == "message_created":
                    await _handle_message_created(runtime, upd)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MAX polling error")
            await asyncio.sleep(2)
