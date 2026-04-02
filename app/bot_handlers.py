"""
Telegram handlers (recovery baseline).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot_shared import (
    load_clinic_names_from_file,
    STATE_REG,
    STATE_REG_PHONE,
    STATE_SCHEDULE_READY,
    STATE_BOOK_SERVICE,
    STATE_BOOK_PATIENT,
    STATE_BOOK_CONFIRM,
    STATE_START,
    build_month_day_grid,
    month_label_ru,
    month_start_end,
    parse_schedule_dates,
    shift_month,
)
from app.parsers import get_grafik_from_schedule_response, parse_patient_line


def _main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Выбрать филиал", callback_data="my_schedule")],
        ]
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_repo = context.bot_data["session_repo"]
    chat_id = str(update.effective_chat.id)
    await session_repo.set(chat_id, STATE_START, {})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Регистрация", callback_data="reg")]])
    text = "Здравствуйте. Нажмите «Регистрация» и введите ФИО врача."
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=kb)


async def callback_reg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    session_repo = context.bot_data["session_repo"]
    chat_id = str(update.effective_chat.id)
    await session_repo.set(chat_id, STATE_REG, {})
    await query.message.reply_text("Введите ФИО врача (как в МИС):")


async def callback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    await query.message.reply_text("Выберите филиал:", reply_markup=_main_menu_markup())


async def callback_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    await _show_clinic_picker(update, context)


async def callback_my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_clinic_picker(update, context)


async def callback_pick_clinic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    session_repo = context.bot_data["session_repo"]
    chat_id = str(update.effective_chat.id)
    payload = query.data if query else ""
    clinic_uid = payload.replace("sched_clinic_", "", 1).strip()
    name_map = await _get_clinic_name_map(context)
    clinic_name = name_map.get(clinic_uid, clinic_uid)
    s = await session_repo.get(chat_id)
    data = dict(s.get("data") or {})
    data["selected_clinic_uid"] = clinic_uid
    data["selected_clinic_name"] = clinic_name
    await session_repo.set(chat_id, s.get("state") or STATE_SCHEDULE_READY, data)
    await _render_month_calendar(update, context, None)


async def callback_month_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    payload = query.data if query else ""
    target_ym = None
    if payload:
        parts = payload.split("_")
        if len(parts) >= 4:
            target_ym = parts[-1]
    await _render_month_calendar(update, context, target_ym)


async def callback_calendar_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()


async def callback_calendar_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    payload = query.data if query else ""
    day_ymd = payload.replace("cal_day_", "", 1).strip()
    if not re.fullmatch(r"\d{8}", day_ymd):
        await query.message.reply_text("Некорректная дата.")
        return
    await _render_day_schedule(update, context, day_ymd)


async def callback_back_to_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    await _render_month_calendar(update, context, None)


async def callback_day_free_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    payload = query.data if query else ""
    m = re.fullmatch(r"sched_free_(\d{8})_(\d{4})", payload)
    if not m:
        await query.message.reply_text("Некорректный слот.")
        return
    ymd, hhmm = m.group(1), m.group(2)
    pretty = f"{ymd[6:8]}.{ymd[4:6]}.{ymd[:4]} {hhmm[:2]}:{hhmm[2:]}"
    await query.message.reply_text("⏳ Загружаю...")
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    mis = context.bot_data["mis_client"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    data = dict(s.get("data") or {})
    doctor_uid = str(data.get("doctor_uid") or "").strip()
    data["book_day_ymd"] = ymd
    data["book_time_hhmm"] = f"{hhmm[:2]}:{hhmm[2:]}"
    services = await _get_services_for_doctor(doctor_repo, mis, doctor_uid) if doctor_uid else []
    if services:
        await session_repo.set(chat_id, STATE_BOOK_SERVICE, data)
        rows: list[list[InlineKeyboardButton]] = []
        for s_item in services[:20]:
            uid = str(s_item.get("uid") or "").strip()
            name = str(s_item.get("name") or "").strip()
            if not uid or not name:
                continue
            title = name if len(name) <= 58 else f"{name[:55]}..."
            rows.append([InlineKeyboardButton(title, callback_data=f"sched_service_{uid}")])
        rows.append([InlineKeyboardButton("Без услуги", callback_data="sched_service_none")])
        await query.message.reply_text(
            f"Выбран свободный слот: {pretty}\nВыберите услугу:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    await session_repo.set(chat_id, STATE_BOOK_PATIENT, data)
    await query.message.reply_text(
        (
            f"Выбран свободный слот: {pretty}\n"
            "Услуги врача не найдены, продолжим без услуги.\n"
            "Введите данные пациента одной строкой:\n"
            "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
            "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567"
        ),
    )


async def callback_pick_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    payload = query.data if query else ""
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    mis = context.bot_data["mis_client"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    data = dict(s.get("data") or {})
    doctor_uid = str(data.get("doctor_uid") or "").strip()
    services = await _get_services_for_doctor(doctor_repo, mis, doctor_uid) if doctor_uid else []
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
    await session_repo.set(chat_id, STATE_BOOK_PATIENT, data)
    suffix = f"\nУслуга: {data.get('book_service_name')}" if data.get("book_service_name") else ""
    await query.message.reply_text(
        "Введите данные пациента одной строкой:\n"
        "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
        "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567"
        f"{suffix}"
    )


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


async def callback_booking_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    mis = context.bot_data["mis_client"]
    appointment_repo = context.bot_data["appointment_repo"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    data = dict(s.get("data") or {})
    doctor_uid = str(data.get("doctor_uid") or "").strip()
    clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
    day_ymd = str(data.get("book_day_ymd") or "").strip()
    time_hhmm = str(data.get("book_time_hhmm") or "").strip()
    service_uid = str(data.get("book_service_uid") or "").strip() or None
    service_name = str(data.get("book_service_name") or "").strip() or None
    if not doctor_uid or not clinic_uid or not day_ymd or not time_hhmm:
        await session_repo.set(chat_id, STATE_SCHEDULE_READY, data)
        await query.message.reply_text("Слот не найден. Выберите дату и время заново.", reply_markup=_main_menu_markup())
        return
    resp = await mis.create_appointment(
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
        await query.message.reply_text(f"Ошибка записи в МИС: {resp.error or 'неизвестная ошибка'}")
        return
    try:
        await appointment_repo.create(
            mis_uid=resp.uid,
            chat_id=chat_id,
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
    await session_repo.set(chat_id, STATE_SCHEDULE_READY, data)
    await query.message.reply_text("Запись успешно создана.", reply_markup=_main_menu_markup())


async def callback_booking_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    session_repo = context.bot_data["session_repo"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    await session_repo.set(chat_id, STATE_BOOK_PATIENT, dict(s.get("data") or {}))
    await query.message.reply_text(
        "Введите данные пациента одной строкой:\n"
        "Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон\n"
        "Пример: Иванов Иван Иванович, 01.01.1990, 79001234567",
    )


async def _get_clinic_name_map(context: ContextTypes.DEFAULT_TYPE) -> dict[str, str]:
    clinic_repo = context.bot_data["clinic_repo"]
    db_map = await clinic_repo.get_all()
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


def _extract_clinic_uid(schedule: dict) -> str:
    val = schedule.get("Клиника")
    if isinstance(val, dict):
        return str(val.get("УИД") or val.get("UID") or "").strip()
    return str(val or "").strip()


async def _show_clinic_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    target_message = query.message if query else update.effective_message
    if not target_message:
        return
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    session_data = dict(s.get("data") or {})
    session_data.pop("selected_clinic_uid", None)
    session_data.pop("selected_clinic_name", None)
    await session_repo.set(chat_id, s.get("state") or STATE_SCHEDULE_READY, session_data)
    s = await session_repo.get(chat_id)
    doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
    if not doctor_uid:
        await target_message.reply_text(
            "Сначала зарегистрируйтесь.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Регистрация", callback_data="reg")]]),
        )
        return
    doc = await doctor_repo.get_by_uid(doctor_uid)
    clinics = (doc or {}).get("clinic_uids") or []
    if isinstance(clinics, str):
        clinics = [clinics]
    clinics = [str(x).strip() for x in clinics if str(x).strip()]
    if not clinics:
        await target_message.reply_text("Филиалы не найдены.", reply_markup=_main_menu_markup())
        return
    name_map = await _get_clinic_name_map(context)
    rows: list[list[InlineKeyboardButton]] = []
    for cid in clinics:
        rows.append([InlineKeyboardButton(f"🏥 {name_map.get(cid, cid)}", callback_data=f"sched_clinic_{cid}")])
    rows.append([InlineKeyboardButton("Назад", callback_data="menu")])
    await target_message.reply_text("Выберите филиал:", reply_markup=InlineKeyboardMarkup(rows))


def _build_calendar_markup(year: int, month: int, days: set, back_cb: str = "menu") -> InlineKeyboardMarkup:
    py, pm = shift_month(year, month, -12)
    ny, nm = shift_month(year, month, 12)
    mpy, mpm = shift_month(year, month, -1)
    mny, mnm = shift_month(year, month, 1)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("<<<", callback_data=f"cal_y_prev_{py:04d}{pm:02d}"),
            InlineKeyboardButton(f"{year}", callback_data="cal_ignore"),
            InlineKeyboardButton(">>>", callback_data=f"cal_y_next_{ny:04d}{nm:02d}"),
        ],
        [
            InlineKeyboardButton("<<", callback_data=f"cal_m_prev_{mpy:04d}{mpm:02d}"),
            InlineKeyboardButton(month_label_ru(year, month).split()[0], callback_data="cal_ignore"),
            InlineKeyboardButton(">>", callback_data=f"cal_m_next_{mny:04d}{mnm:02d}"),
        ],
    ]
    for week in build_month_day_grid(year, month, days):
        btn_row: list[InlineKeyboardButton] = []
        for cell in week:
            if cell == "-":
                btn_row.append(InlineKeyboardButton("-", callback_data="cal_ignore"))
                continue
            day_num = cell[:2]
            day_has = cell.endswith("*")
            label = f"{day_num}*" if day_has else day_num
            btn_row.append(InlineKeyboardButton(label, callback_data=f"cal_day_{year:04d}{month:02d}{day_num}"))
        rows.append(btn_row)
    rows.append([InlineKeyboardButton("Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


async def _render_month_calendar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_ym: str | None,
) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    target_message = query.message if query else update.effective_message
    if target_message:
        await target_message.reply_text("⏳ Загружаю...")
    session_repo = context.bot_data["session_repo"]
    mis = context.bot_data["mis_client"]
    doctor_repo = context.bot_data["doctor_repo"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    doctor_uid = str((s.get("data") or {}).get("doctor_uid") or "").strip()
    if not doctor_uid:
        await query.message.reply_text(
            "Сначала зарегистрируйтесь.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Регистрация", callback_data="reg")]]),
        )
        return
    doc = await doctor_repo.get_by_uid(doctor_uid)
    data = dict(s.get("data") or {})
    selected_clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
    clinics = (doc or {}).get("clinic_uids") or []
    if isinstance(clinics, str):
        clinics = [clinics]
    clinics = [str(x).strip() for x in clinics if str(x).strip()]
    if not selected_clinic_uid and len(clinics) > 1:
        await _show_clinic_picker(update, context)
        return
    if not selected_clinic_uid and len(clinics) == 1:
        selected_clinic_uid = clinics[0]
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
        resp = await mis.get_schedule20(doctor_uid, start, finish)
        grafik = get_grafik_from_schedule_response(resp.get("raw") or {})
    except Exception as e:
        await query.message.reply_text(f"Ошибка получения расписания: {e}")
        return
    if not grafik:
        await query.message.reply_text("Расписание на этот месяц не найдено.", reply_markup=_main_menu_markup())
        return
    if selected_clinic_uid:
        grafik = [g for g in grafik if _extract_clinic_uid(g) == selected_clinic_uid]
        if not grafik:
            await query.message.reply_text("По выбранному филиалу расписание не найдено.", reply_markup=_main_menu_markup())
            return
    days = parse_schedule_dates(grafik)
    fio = (doc or {}).get("fio") or "Врач"
    clinic_name = str(data.get("selected_clinic_name") or data.get("clinic_name") or "").strip()
    prefix = f"Выбранная клиника: {clinic_name}\n" if clinic_name else ""
    text = f"{prefix}Выберите дату:\n* — есть расписание"
    await query.message.reply_text(text, reply_markup=_build_calendar_markup(year, month, days))


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


def _extract_times_for_day(
    grafik: list[dict],
    day_iso: str,
) -> tuple[list[str], list[dict]]:
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


async def _get_services_for_doctor(doctor_repo, mis, doctor_uid: str) -> list[dict]:
    if not doctor_uid:
        return []
    services = await doctor_repo.get_main_services(doctor_uid)
    if services:
        return services
    try:
        now = datetime.now()
        # Keep window moderate: very wide ranges may cause MIS 500.
        start = (now - timedelta(days=365)).strftime("%Y-%m-%d 00:00:00")
        finish = (now + timedelta(days=365)).strftime("%Y-%m-%d 23:59:59")
        services = await mis.get_doctor_services_from_tickets_http(doctor_uid, start, finish)
        if services:
            await doctor_repo.set_main_services(doctor_uid, services)
        return services
    except Exception:
        return []


def _time_to_minutes(hhmm: str) -> int:
    try:
        hh, mm = hhmm.split(":")[:2]
        return int(hh) * 60 + int(mm)
    except Exception:
        return -1


def _pick_ticket_for_busy(
    busy_start: str,
    busy_end: str,
    tickets: list[dict[str, str]],
) -> tuple[str, str]:
    bs = _time_to_minutes(busy_start)
    be = _time_to_minutes(busy_end) if busy_end else bs + 1
    # 1) interval match
    for t in tickets:
        ts = _time_to_minutes(t.get("time") or "")
        if ts < 0:
            continue
        if bs <= ts < be:
            return (t.get("fio") or "", t.get("service") or "")
    # 2) exact start match fallback
    for t in tickets:
        if (t.get("time") or "") == busy_start:
            return (t.get("fio") or "", t.get("service") or "")
    return ("", "")


async def _render_day_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    day_ymd: str,
) -> None:
    query = update.callback_query
    if query and query.message:
        await query.message.reply_text("⏳ Загружаю...")
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    mis = context.bot_data["mis_client"]
    chat_id = str(update.effective_chat.id)
    s = await session_repo.get(chat_id)
    data = dict(s.get("data") or {})
    doctor_uid = str(data.get("doctor_uid") or "").strip()
    selected_clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
    if not doctor_uid or not selected_clinic_uid:
        await query.message.reply_text("Сначала выберите филиал и дату.", reply_markup=_main_menu_markup())
        return

    day_iso = f"{day_ymd[:4]}-{day_ymd[4:6]}-{day_ymd[6:8]}"
    start = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]} 00:00:00"
    finish = f"{day_ymd[6:8]}.{day_ymd[4:6]}.{day_ymd[:4]} 23:59:59"
    try:
        resp = await mis.get_schedule20(doctor_uid, start, finish)
        grafik = get_grafik_from_schedule_response(resp.get("raw") or {})
    except Exception as e:
        await query.message.reply_text(f"Ошибка получения расписания дня: {e}")
        return
    grafik = [g for g in grafik if _extract_clinic_uid(g) == selected_clinic_uid]
    free_times, busy_entries = _extract_times_for_day(grafik, day_iso)

    # Try enriching busy entries with patient/service from PatientTickets.
    tickets_rows: list[dict[str, str]] = []
    try:
        day_dt = datetime.strptime(day_iso, "%Y-%m-%d")
        # MIS PatientTickets may return empty on tight one-day range; use wider window and filter locally.
        w_start = (day_dt.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d 00:00:00")
        w_finish = (day_dt.replace(day=28) + timedelta(days=10)).replace(day=1) + timedelta(days=40)
        w_finish = w_finish.replace(day=1) - timedelta(seconds=1)
        window_finish = w_finish.strftime("%Y-%m-%d %H:%M:%S")
        tickets = await mis.get_patient_tickets_http(w_start, window_finish, employee_uid=doctor_uid)
        if not tickets:
            # Some MIS publishes ignore Employee filter; retry globally and filter locally.
            tickets = await mis.get_patient_tickets_http(w_start, window_finish, employee_uid=None)
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

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in free_times:
        cb = f"sched_free_{day_ymd}_{t.replace(':', '')}"
        row.append(InlineKeyboardButton(t, callback_data=cb))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ К календарю", callback_data="back_to_calendar")])
    await query.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def message_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    session_repo = context.bot_data["session_repo"]
    doctor_repo = context.bot_data["doctor_repo"]
    mis = context.bot_data["mis_client"]
    appointment_repo = context.bot_data["appointment_repo"]
    chat_id = str(update.effective_chat.id)
    session = await session_repo.get(chat_id)
    state = session.get("state") or STATE_START
    data = dict(session.get("data") or {})

    if state == STATE_REG:
        matches = await doctor_repo.search_by_fio(text)
        if not matches:
            await update.message.reply_text("Врач не найден. Попробуйте точнее ввести ФИО.")
            return
        d = matches[0]
        data["doctor_uid"] = d["employee_uid"]
        data["doctor_fio"] = d["fio"]
        await session_repo.set(chat_id, STATE_REG_PHONE, data)
        await update.message.reply_text("Введите номер телефона врача:")
        return

    if state == STATE_REG_PHONE:
        phone = text
        doctor_uid = str(data.get("doctor_uid") or "")
        doc = await doctor_repo.get_by_uid(doctor_uid) if doctor_uid else None
        expected = str((doc or {}).get("employee_phone") or "").strip()
        if expected and expected != phone:
            await update.message.reply_text("Телефон не совпал. Повторите ввод телефона.")
            return
        await session_repo.set(chat_id, STATE_SCHEDULE_READY, data)
        await update.message.reply_text(f"Регистрация успешна: {data.get('doctor_fio','')}")
        await _show_clinic_picker(update, context)
        return

    if state == STATE_BOOK_PATIENT:
        doctor_uid = str(data.get("doctor_uid") or "").strip()
        clinic_uid = str(data.get("selected_clinic_uid") or "").strip()
        day_ymd = str(data.get("book_day_ymd") or "").strip()
        time_hhmm = str(data.get("book_time_hhmm") or "").strip()
        if not doctor_uid or not clinic_uid or not day_ymd or not time_hhmm:
            await session_repo.set(chat_id, STATE_SCHEDULE_READY, data)
            await update.message.reply_text("Слот не найден. Выберите дату и время заново.", reply_markup=_main_menu_markup())
            return
        parsed = parse_patient_line(
            text,
            day_ymd,
            f"{day_ymd}T{time_hhmm}:00",
        )
        if not parsed:
            await update.message.reply_text(
                "Не удалось разобрать данные.\n"
                "Формат: Фамилия Имя Отчество, ДД.ММ.ГГГГ, телефон"
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
        await session_repo.set(chat_id, STATE_BOOK_CONFIRM, data)
        doc = await doctor_repo.get_by_uid(doctor_uid) if doctor_uid else None
        doctor_fio = str((doc or {}).get("fio") or "")
        clinic_name = str(data.get("selected_clinic_name") or clinic_uid)
        confirm_text = _booking_confirmation_text(data, doctor_fio, clinic_name)
        await update.message.reply_text(
            confirm_text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Подтвердить", callback_data="book_confirm"),
                        InlineKeyboardButton("Назад", callback_data="book_back"),
                    ]
                ]
            ),
        )
        return

    await update.message.reply_text("Используйте /start")
