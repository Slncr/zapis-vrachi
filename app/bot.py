"""
Telegram application factory.
"""
from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import config
from app.bot_handlers import (
    callback_booking_back,
    callback_booking_confirm,
    callback_calendar_ignore,
    callback_calendar_day,
    callback_day_free_slot,
    callback_back_to_calendar,
    callback_pick_service,
    callback_menu,
    callback_month_nav,
    callback_my_schedule,
    callback_pick_clinic,
    callback_reg,
    callback_schedule,
    message_text,
    start_command,
)


def build_application(
    *,
    session_repo,
    doctor_repo,
    clinic_repo,
    appointment_repo,
    mis_client,
) -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.bot_data["session_repo"] = session_repo
    app.bot_data["doctor_repo"] = doctor_repo
    app.bot_data["clinic_repo"] = clinic_repo
    app.bot_data["appointment_repo"] = appointment_repo
    app.bot_data["mis_client"] = mis_client

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_reg, pattern=r"^reg$"))
    app.add_handler(CallbackQueryHandler(callback_schedule, pattern=r"^schedule$"))
    app.add_handler(CallbackQueryHandler(callback_my_schedule, pattern=r"^my_schedule$"))
    app.add_handler(CallbackQueryHandler(callback_pick_clinic, pattern=r"^sched_clinic_.+$"))
    app.add_handler(CallbackQueryHandler(callback_month_nav, pattern=r"^cal_(y|m)_(prev|next)_\d{6}$"))
    app.add_handler(CallbackQueryHandler(callback_calendar_ignore, pattern=r"^cal_ignore$"))
    app.add_handler(CallbackQueryHandler(callback_calendar_day, pattern=r"^cal_day_\d{8}$"))
    app.add_handler(CallbackQueryHandler(callback_day_free_slot, pattern=r"^sched_free_\d{8}_\d{4}$"))
    app.add_handler(CallbackQueryHandler(callback_pick_service, pattern=r"^sched_service_.+$"))
    app.add_handler(CallbackQueryHandler(callback_back_to_calendar, pattern=r"^back_to_calendar$"))
    app.add_handler(CallbackQueryHandler(callback_booking_confirm, pattern=r"^book_confirm$"))
    app.add_handler(CallbackQueryHandler(callback_booking_back, pattern=r"^book_back$"))
    app.add_handler(CallbackQueryHandler(callback_menu, pattern=r"^menu$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_text))
    return app
